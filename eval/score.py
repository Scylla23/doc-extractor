"""Field-level scorer with normalization (T18, PRD §10).

Compares an extracted `Invoice` against a ground-truth `Invoice` **per field**,
normalizing both sides first so equivalent-but-differently-formatted values are
not scored wrong: `$1,000.00` == `1000.0`, `"March 14, 2026"` == `2026-03-14`.
Date normalization reuses `app.validate._normalize_date` (single source of
truth — no second date parser).

Metric definitions, micro-averaged across the whole doc set (per field, not per
doc — PRD §10):

    predicted-positive = extracted value is not None
    actual-positive    = ground-truth value is not None
    TP = predicted non-null AND normalized-equal to ground truth
    FP = predicted non-null AND (ground truth null OR mismatch)
    FN = ground truth non-null AND (predicted null OR mismatch)
    precision = TP/(TP+FP)   recall = TP/(TP+FN)   F1 = 2PR/(P+R)

A wrong value counts as both an FP and an FN (claimed the wrong thing AND missed
the right thing). None-vs-None is a true negative, ignored. STP (straight-
through) for a doc = every scored field on that doc is correct (weakest link).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schema import Invoice  # noqa: E402
from app.validate import _normalize_date  # noqa: E402  (reuse the one date parser)

# Fields scored per doc: the seven top-level scalars plus the four sub-fields of
# each line item (indexed, aligned positionally).
_SCALARS = (
    "vendor_name", "invoice_number", "invoice_date", "currency",
    "subtotal", "tax", "total",
)
_LINE_ATTRS = ("description", "quantity", "unit_price", "amount")


def _norm(value: object) -> str | None:
    """Canonical comparable form, or None for absent values (None never matches).

    - money/number strings lose currency symbols, thousands separators and
      trailing-zero noise: "$1,000.00" -> "1000", 1000.0 -> "1000"
    - date-looking strings normalize to ISO via the validation date parser
    - other strings are stripped + lowercased
    """
    if value is None:
        return None

    # Numbers: canonical decimal, trailing zeros trimmed.
    if isinstance(value, (int, float)):
        return _trim_number(float(value))

    s = str(value).strip()
    if not s:
        return None

    # Money / numeric string: strip currency symbols + thousands separators.
    stripped = s.lstrip("$€£₹").replace(",", "").strip()
    try:
        return _trim_number(float(stripped))
    except ValueError:
        pass

    # Date string -> ISO (reuses app.validate._normalize_date; handles ISO,
    # M/D/Y, D/M/Y, "March 14, 2026", "14 Mar 2026").
    iso = _normalize_date(s)
    if iso is not None:
        return iso

    return s.lower()


def _trim_number(x: float) -> str:
    """"%f" then trim trailing zeros; the '.' shields the integer part."""
    return ("%f" % x).rstrip("0").rstrip(".")


def _field_value(obj) -> object:
    """The `.value` of a Field-like (Pydantic Field or dict), or None if absent."""
    if obj is None:
        return None
    if hasattr(obj, "value"):
        return obj.value
    if isinstance(obj, dict):
        return obj.get("value")
    return None


def _pairs(extracted: Invoice, truth: Invoice) -> list[tuple[str, object, object]]:
    """Yield (field_path, extracted_value, truth_value) for every scored field."""
    out: list[tuple[str, object, object]] = []
    for name in _SCALARS:
        out.append((name, _field_value(getattr(extracted, name)),
                    _field_value(getattr(truth, name))))

    ex_items = extracted.line_items or []
    gt_items = truth.line_items or []
    # ponytail: line items aligned by index. Ground truth and extraction share
    # printed order for our synthetic set, so index alignment is exact; a model
    # that reorders rows would mis-score. Upgrade to a greedy match by amount if
    # real invoices reorder.
    for i in range(max(len(ex_items), len(gt_items))):
        ex_it = ex_items[i] if i < len(ex_items) else None
        gt_it = gt_items[i] if i < len(gt_items) else None
        for attr in _LINE_ATTRS:
            out.append((
                f"line_items[{i}].{attr}",
                _field_value(getattr(ex_it, attr, None)),
                _field_value(getattr(gt_it, attr, None)),
            ))
    return out


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 1.0

    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 1.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class DocScore:
    doc_id: str
    stp: bool                      # every scored field correct (weakest link)
    wrong_fields: list[str] = dc_field(default_factory=list)


# The seven scalars collapse line_items[i].attr into one "line_items.attr" row so
# the report table stays fixed-width regardless of item count.
def _report_key(path: str) -> str:
    if path.startswith("line_items["):
        return "line_items." + path.split(".", 1)[1]
    return path


def score_doc(extracted: Invoice, truth: Invoice, doc_id: str,
              per_field: dict[str, Counts]) -> DocScore:
    """Accumulate per-field counts for one doc into `per_field`; return its DocScore."""
    stp = True
    wrong: list[str] = []
    for path, ev, tv in _pairs(extracted, truth):
        en, tn = _norm(ev), _norm(tv)
        if en is None and tn is None:
            continue                      # true negative — nothing expected, nothing claimed
        counts = per_field.setdefault(_report_key(path), Counts())
        correct = en is not None and en == tn
        if correct:
            counts.tp += 1
        else:
            if en is not None:
                counts.fp += 1
            if tn is not None:
                counts.fn += 1
            stp = False
            wrong.append(path)
    return DocScore(doc_id=doc_id, stp=stp, wrong_fields=wrong)


def format_table(per_field: dict[str, Counts]) -> str:
    """Render the per-field precision/recall/F1 table as GitHub-flavored Markdown."""
    rows = ["| Field | Precision | Recall | F1 | n |",
            "|---|---|---|---|---|"]
    order = list(_SCALARS) + [f"line_items.{a}" for a in _LINE_ATTRS]
    for key in order:
        c = per_field.get(key)
        if c is None:
            continue
        n = c.tp + c.fn
        rows.append(
            f"| {key} | {c.precision():.2f} | {c.recall():.2f} | {c.f1():.2f} | {n} |"
        )
    return "\n".join(rows)


def demo() -> None:
    """Spot-check the scorer itself (PRD §10): a buggy scorer must not silently
    inflate the numbers, so a known differently-formatted pair MUST score as a
    match, and a genuine mismatch MUST be caught."""
    # (1) Normalization: equivalent values compare equal across format.
    assert _norm("$1,000.00") == _norm(1000.0) == "1000", _norm("$1,000.00")
    assert _norm("March 14, 2026") == _norm("2026-03-14") == "2026-03-14"
    assert _norm("14/03/2026") == "2026-03-14"          # non-ISO day/month/year
    assert _norm("€1.234,5".replace(".", "").replace(",", ".")) is not None
    assert _norm("  Acme Corp ") == "acme corp"
    assert _norm(None) is None

    truth = Invoice.model_validate({
        "vendor_name": {"value": "Acme Corp"},
        "invoice_date": {"value": "2026-03-14"},
        "line_items": [
            {"description": {"value": "Widget"}, "quantity": {"value": 2},
             "unit_price": {"value": 500.0}, "amount": {"value": 1000.0}},
        ],
        "subtotal": {"value": 1000.0},
        "tax": {"value": 0.0},
        "total": {"value": 1000.0},
    })

    # (2) Differently-formatted-but-equivalent extraction -> a PERFECT score (STP).
    extracted_ok = Invoice.model_validate({
        "vendor_name": {"value": " acme corp "},          # case/space differ
        "invoice_date": {"value": "March 14, 2026"},       # word date
        "line_items": [
            {"description": {"value": "Widget"}, "quantity": {"value": 2},
             "unit_price": {"value": "$500.00"}, "amount": {"value": "$1,000.00"}},
        ],
        "subtotal": {"value": "$1,000.00"},
        "tax": {"value": 0.0},
        "total": {"value": "1000"},
    })
    pf: dict[str, Counts] = {}
    s_ok = score_doc(extracted_ok, truth, "ok", pf)
    assert s_ok.stp is True, s_ok.wrong_fields
    for key, c in pf.items():
        assert c.fp == 0 and c.fn == 0, (key, c)         # every field a clean match

    # (3) A genuine mismatch is caught (not silently inflated).
    extracted_bad = extracted_ok.model_copy(deep=True)
    extracted_bad.total.value = 9999.0                   # wrong total
    pf2: dict[str, Counts] = {}
    s_bad = score_doc(extracted_bad, truth, "bad", pf2)
    assert s_bad.stp is False and "total" in s_bad.wrong_fields, s_bad
    assert pf2["total"].fp == 1 and pf2["total"].fn == 1  # wrong = both FP and FN
    assert pf2["total"].precision() == 0.0 and pf2["total"].recall() == 0.0

    # (4) Missing extraction of a present field -> FN only (recall miss, no FP).
    extracted_miss = extracted_ok.model_copy(deep=True)
    extracted_miss.vendor_name = None
    pf3: dict[str, Counts] = {}
    score_doc(extracted_miss, truth, "miss", pf3)
    assert pf3["vendor_name"].fn == 1 and pf3["vendor_name"].fp == 0

    # Print a per-field table over the three demo docs so the T18 verification
    # command shows the metric shape (the real numbers come from T19's run).
    combined: dict[str, Counts] = {}
    for ex in (extracted_ok, extracted_bad, extracted_miss):
        score_doc(ex, truth, "demo", combined)
    print("score demo OK — per-field metrics (3 demo docs):\n")
    print(format_table(combined))


if __name__ == "__main__":
    demo()
