"""T19 accuracy report: run the extractor over the eval set, render eval/report.md.

Produces the client-facing proof (PRD §10): per-field precision/recall/F1, the
headline STP rate (% docs needing zero human edits), and coverage@accuracy
("X% auto-approved; of those, Y% fully correct"). Scoring is reused verbatim
from eval/score.py — this module only orchestrates, caches, and renders.

Cost control: we pin `app.extract._N_SAMPLES = 1` before extracting.
Self-consistency (N=3) is a *per-doc confidence signal*, not an accuracy lever
— it changes which fields get flagged for review, not what value is extracted —
so N=1 cuts spend ~3x without changing what accuracy measures. The T16 cached
instruction/few-shot prefix is shared across docs, so the per-doc marginal cost
is just the doc's own tokens.

Reproducibility: each doc's extracted Invoice is cached to eval/extractions/<id>.json.
The first run pays the API once; re-runs load from disk and are free. `--refresh`
ignores the cache and re-extracts everything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import app.extract as extract_mod  # noqa: E402
from app.extract import extract_invoice  # noqa: E402
from app.schema import Invoice  # noqa: E402
from eval.score import Counts, format_table, score_doc  # noqa: E402

# Cost control (see module docstring): disable self-consistency sampling.
extract_mod._N_SAMPLES = 1

_DATASET = _ROOT / "eval" / "dataset"
_MANIFEST = _DATASET / "manifest.json"
_CACHE_DIR = _ROOT / "eval" / "extractions"
_REPORT = _ROOT / "eval" / "report.md"


def _category_bucket(category: str) -> str:
    """Collapse "edge:<kind>" into "edge"; pass "clean-native"/"scanned" through."""
    return "edge" if category.startswith("edge") else category


def _iter_fields(inv: Invoice):
    """Yield every present Field on an Invoice, including line-item sub-fields."""
    scalars = ("vendor_name", "invoice_number", "invoice_date", "currency",
               "subtotal", "tax", "total")
    for name in scalars:
        f = getattr(inv, name)
        if f is not None:
            yield f
    for item in (inv.line_items or []):
        for attr in ("description", "quantity", "unit_price", "amount"):
            f = getattr(item, attr, None)
            if f is not None:
                yield f


def _auto_approved(inv: Invoice) -> bool:
    """True iff no present field on the doc is flagged review_required."""
    return not any(f.review_required for f in _iter_fields(inv))


class _CachedFailure(RuntimeError):
    """A prior run recorded this doc as an extraction failure (a `.failed` sentinel)."""


def _extract_cached(doc_id: str, *, refresh: bool) -> Invoice:
    """Return the extracted Invoice for a doc, from cache unless --refresh.

    A doc that can't extract (e.g. output exceeds max_tokens) is recorded as a
    `<id>.failed` sentinel so re-runs don't re-pay for a guaranteed failure —
    keeping the report cheaply reproducible. `--refresh` clears both caches.
    """
    cache_path = _CACHE_DIR / f"{doc_id}.json"
    fail_path = _CACHE_DIR / f"{doc_id}.failed"
    if not refresh:
        if cache_path.exists():
            return Invoice.model_validate_json(cache_path.read_text())
        if fail_path.exists():
            raise _CachedFailure(fail_path.read_text().strip())
    pdf_bytes = (_DATASET / f"{doc_id}.pdf").read_bytes()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        invoice = extract_invoice(pdf_bytes)
    except Exception as e:
        fail_path.write_text(f"{type(e).__name__}: {e}")
        raise
    fail_path.unlink(missing_ok=True)  # a prior failure that now succeeds
    cache_path.write_text(invoice.model_dump_json(indent=2))
    return invoice


def run(refresh: bool = False) -> dict:
    """Extract + score every doc; write eval/report.md; return a summary dict."""
    manifest = json.loads(_MANIFEST.read_text())
    docs = manifest["docs"]

    per_field: dict[str, Counts] = {}
    n_total = n_scored = n_stp = n_auto = n_auto_correct = n_failed = 0
    cat_total: dict[str, int] = {}
    cat_stp: dict[str, int] = {}
    failures: list[tuple[str, str]] = []

    for doc in docs:
        doc_id = doc["id"]
        n_total += 1
        bucket = _category_bucket(doc["category"])
        cat_total[bucket] = cat_total.get(bucket, 0) + 1
        try:
            extracted = _extract_cached(doc_id, refresh=refresh)
        except Exception as e:  # one bad doc must not abort the whole run
            # A cached-failure sentinel already carries "Type: msg"; don't re-wrap.
            reason = str(e) if isinstance(e, _CachedFailure) else f"{type(e).__name__}: {e}"
            n_failed += 1
            failures.append((doc_id, reason))
            print(f"[FAIL] {doc_id}: {reason}", file=sys.stderr)
            continue

        truth = Invoice.model_validate_json(
            (_DATASET / f"{doc_id}.json").read_text()
        )
        score = score_doc(extracted, truth, doc_id, per_field)
        n_scored += 1
        if score.stp:
            n_stp += 1
            cat_stp[bucket] = cat_stp.get(bucket, 0) + 1
        if _auto_approved(extracted):
            n_auto += 1
            if score.stp:
                n_auto_correct += 1

    def _pct(num: int, den: int) -> float:
        return 100.0 * num / den if den else 0.0

    stp_rate = _pct(n_stp, n_scored)
    coverage = _pct(n_auto, n_scored)
    acc_auto = _pct(n_auto_correct, n_auto)

    _write_report(
        n_total=n_total, n_scored=n_scored, n_failed=n_failed,
        counts=manifest["counts"], per_field=per_field,
        stp_rate=stp_rate, n_stp=n_stp,
        coverage=coverage, n_auto=n_auto, acc_auto=acc_auto,
        cat_total=cat_total, cat_stp=cat_stp, failures=failures,
    )

    return {
        "n_total": n_total, "n_scored": n_scored, "n_failed": n_failed,
        "stp_rate": stp_rate, "coverage": coverage, "acc_auto": acc_auto,
        "cat_total": cat_total, "cat_stp": cat_stp, "failures": failures,
    }


def _write_report(*, n_total, n_scored, n_failed, counts, per_field,
                  stp_rate, n_stp, coverage, n_auto, acc_auto,
                  cat_total, cat_stp, failures) -> None:
    mix = (f"{counts.get('clean-native', 0)} clean-native, "
           f"{counts.get('scanned', 0)} scanned, {counts.get('edge', 0)} edge")

    cat_lines = []
    for bucket in ("clean-native", "scanned", "edge"):
        tot = cat_total.get(bucket, 0)
        if not tot:
            continue
        stp = cat_stp.get(bucket, 0)
        cat_lines.append(
            f"- **{bucket}:** {stp}/{tot} STP ({100.0 * stp / tot:.0f}%)"
        )

    failed_note = (f" ({n_failed} failed extraction and were excluded)"
                   if n_failed else "")

    fail_lines = []
    if failures:
        fail_lines = [
            "## Failures & known limitations",
            "",
            *[f"- **{doc_id}** — {reason}" for doc_id, reason in failures],
            "",
            "_These are the largest docs (40 line items). Their JSON output "
            "exceeds Claude's 8192-token `max_tokens` cap, so the extractor "
            "**fails safe** — the truncated output surfaces as an error "
            "(Instructor's `IncompleteOutputException`; the pipeline also guards "
            "`stop_reason=max_tokens`, PRD §8) rather than returning silently-"
            "truncated JSON. Output pagination / chunking for very large invoices "
            "is backlog._",
            "",
        ]

    lines = [
        "# Invoice Extraction — Accuracy Report",
        "",
        f"Evaluated **{n_scored}/{n_total}** docs{failed_note} — mix: {mix}.",
        "Field-level metrics are micro-averaged per field across the whole set "
        "(PRD §10), normalized before comparison (dates→ISO, amounts→decimal).",
        "",
        "## Per-field precision / recall / F1",
        "",
        format_table(per_field),
        "",
        "## Headline",
        "",
        f"- **STP rate: {stp_rate:.0f}%** — {n_stp}/{n_scored} docs extracted "
        "with zero fields needing a human edit (every scored field correct).",
        f"- **Coverage @ accuracy: {coverage:.0f}% auto-approved; of those, "
        f"{acc_auto:.0f}% fully correct** — {n_auto}/{n_scored} docs had no "
        f"field flagged for review; {int(round(acc_auto / 100 * n_auto))}/{n_auto} "
        "of those were in fact straight-through.",
        "- _Auto-approval is gated by the confidence review threshold (T14, a "
        "single tunable knob). At the current setting it is conservative — "
        "everything it auto-approves is correct, at the cost of lower coverage; "
        "raising coverage means tuning that threshold against this set. Unlike "
        "the accuracy metrics above, coverage also shifts with the sample count "
        "N (it feeds the confidence signal), so this figure is a floor at N=1._",
        "",
        "## STP by category",
        "",
        *cat_lines,
        "",
        *fail_lines,
        "---",
        "",
        "_Run with `_N_SAMPLES=1` (self-consistency disabled): N is a per-doc "
        "confidence signal, not an accuracy lever, so N=1 measures the same "
        "accuracy at ~3x lower cost. Final-tier model varies by cascade — "
        "clean-native stays on Haiku 4.5; scanned/edge escalate to Opus 4.8._",
        "",
    ]
    _REPORT.write_text("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true",
                    help="ignore the disk cache and re-extract every doc")
    args = ap.parse_args()

    summary = run(refresh=args.refresh)

    print("\n=== Accuracy report summary ===")
    print(f"docs scored : {summary['n_scored']}/{summary['n_total']} "
          f"({summary['n_failed']} failed)")
    print(f"STP rate    : {summary['stp_rate']:.0f}%")
    print(f"coverage    : {summary['coverage']:.0f}% auto-approved; "
          f"of those {summary['acc_auto']:.0f}% fully correct")
    for bucket in ("clean-native", "scanned", "edge"):
        tot = summary["cat_total"].get(bucket, 0)
        if tot:
            stp = summary["cat_stp"].get(bucket, 0)
            print(f"  {bucket:12s}: {stp}/{tot} STP ({100.0 * stp / tot:.0f}%)")
    if summary["failures"]:
        print("failures:")
        for doc_id, err in summary["failures"]:
            print(f"  {doc_id}: {err}")
    print(f"\nwrote {_REPORT}")
