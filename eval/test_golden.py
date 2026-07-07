"""Golden-set CI regression test (T20, PRD §10).

The golden subset is the FIRST 6 `clean-native` docs from the eval manifest —
native-text path only, so CI needs only ANTHROPIC_API_KEY (no Mistral OCR), which
keeps it cheap and non-flaky. On every prompt/model change this asserts overall
field accuracy has not regressed below a floor (the tripwire), reusing the T18
scorer (`eval.score`) so there is one metric definition, not two.

Runnable under pytest (`pytest eval/test_golden.py`) or directly
(`python eval/test_golden.py`). Skips gracefully when no API key is present.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.extract as extract_mod  # noqa: E402
from app.schema import Invoice  # noqa: E402
from eval.score import Counts, format_table, score_doc  # noqa: E402

# Regression tripwire, not a target. The T19 run scores clean-native at 100%
# field accuracy (this golden subset is clean-native), so 0.80 leaves ~0.20 of
# headroom: normal model jitter won't trip CI, but a real regression (a prompt/
# model change dropping several fields) still will.
GOLDEN_FLOOR = 0.80

_DATASET = Path(__file__).resolve().parent / "dataset"


def _golden_ids(n: int = 6) -> list[str]:
    """First `n` ids whose category == 'clean-native' (native-only, cost-bounded)."""
    manifest = json.loads((_DATASET / "manifest.json").read_text())
    ids = [d["id"] for d in manifest["docs"] if d["category"] == "clean-native"]
    return ids[:n]


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="no ANTHROPIC_API_KEY — skip live golden test (forks / local without key)",
)
def test_golden_accuracy_floor() -> None:
    # Self-consistency sampling is a quality knob, not what this test guards; pin
    # to one sample so the golden run stays to ~6 Claude calls (cost).
    extract_mod._N_SAMPLES = 1

    per_field: dict[str, Counts] = {}
    for doc_id in _golden_ids():
        pdf_bytes = (_DATASET / f"{doc_id}.pdf").read_bytes()
        truth = Invoice.model_validate_json((_DATASET / f"{doc_id}.json").read_text())
        extracted = extract_mod.extract_invoice(pdf_bytes)
        score_doc(extracted, truth, doc_id, per_field)

    # Overall field accuracy = correct scored fields / total scored fields,
    # micro-averaged across every field of every golden doc.
    tp = sum(c.tp for c in per_field.values())
    total = sum(c.tp + c.fp + c.fn for c in per_field.values())
    accuracy = tp / total if total else 0.0

    wrong = [k for k, c in per_field.items() if c.fp or c.fn]
    print(f"\ngolden overall field accuracy: {accuracy:.4f} "
          f"({tp}/{total} scored fields correct) over {len(_golden_ids())} docs")
    print(f"floor: {GOLDEN_FLOOR:.2f}  wrong fields: {wrong or 'none'}")
    print(format_table(per_field))

    assert accuracy >= GOLDEN_FLOOR, (
        f"golden regression: overall field accuracy {accuracy:.4f} "
        f"< floor {GOLDEN_FLOOR:.2f} ({tp}/{total} correct); wrong fields: {wrong}"
    )


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("no ANTHROPIC_API_KEY — skipping live golden test")
        sys.exit(0)
    test_golden_accuracy_floor()
    print("golden test OK")
