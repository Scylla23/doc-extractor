"""T17 verification: every ground-truth label parses as an `Invoice` (PRD §10).

Confirms the eval set is well-formed against the single-source-of-truth schema
before anything scores against it, and prints the documented mix so the counts
are visible at a glance.

    python eval/validate_labels.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schema import Invoice  # noqa: E402

DATASET = Path(__file__).resolve().parent / "dataset"


def main() -> int:
    jsons = sorted(DATASET.glob("inv_*.json"))
    if not jsons:
        print("no labels found — run `python eval/generate.py` first", file=sys.stderr)
        return 1

    for jf in jsons:
        pdf = jf.with_suffix(".pdf")
        assert pdf.exists(), f"label {jf.name} has no matching PDF"
        Invoice.model_validate_json(jf.read_text())  # raises if it doesn't parse

    manifest = json.loads((DATASET / "manifest.json").read_text())
    counts = manifest["counts"]
    assert counts["total"] == len(jsons), (counts["total"], len(jsons))
    assert len(jsons) >= 50, f"eval set has {len(jsons)} docs, need >= 50"

    print(f"OK — {len(jsons)} ground-truth labels parse as Invoice")
    print(f"     mix: {counts['clean-native']} clean-native / "
          f"{counts['scanned']} scanned / {counts['edge']} edge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
