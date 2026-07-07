"""Multi-signal per-field confidence (T14, PRD §4 Finding 3).

Confidence is a *blended engine*, not "ask the model how sure it is" (self-
reported confidence is poorly calibrated). We blend three signals per field:

1. **self-consistency** — sample the extraction 2-3x; how many samples agree on
   this field's value?
2. **verbatim-in-source** — does the value appear verbatim in the OCR/native text?
3. **passes-validation** — is the field free of business-rule violations (T7)?

Blend -> one 0-1 score per field -> `review_required` when the score falls below
a single tunable threshold. A field that fails validation or is absent from the
source text scores lower than a clean, agreed-upon verbatim match.
"""

from __future__ import annotations

from collections import Counter

from app import validate
from app.schema import Invoice

# Blend weights (sum to 1) — each signal contributes about a third. Tune on the
# eval set (T18/T19). ponytail: flat-ish weights until the eval says otherwise.
_W_AGREE, _W_VERBATIM, _W_VALID = 0.34, 0.33, 0.33

# The one knob: fields scoring below this route to human review. Tune against
# the target accuracy/coverage on the eval set (PRD §10).
REVIEW_THRESHOLD = 0.7

# Top-level scalar fields, in schema order.
_SCALARS = (
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "currency",
    "subtotal",
    "tax",
    "total",
)
_LINE_ATTRS = ("description", "quantity", "unit_price", "amount")


def _norm(value: object) -> str:
    """Canonical comparable form: numbers lose trailing-zero noise (270.0 ->
    "270"), strings are stripped/lowercased. `None`/absent -> "" (never matches)."""
    if value is None:
        return ""
    if isinstance(value, float):
        # "%f" then trim: the '.' shields integer-part zeros from rstrip.
        return ("%f" % value).rstrip("0").rstrip(".")
    return str(value).strip().lower()


def _iter_fields(invoice: Invoice):
    """Yield (path, Field) for every present Field on the invoice."""
    for name in _SCALARS:
        field = getattr(invoice, name)
        if field is not None:
            yield name, field
    for i, item in enumerate(invoice.line_items or []):
        for attr in _LINE_ATTRS:
            field = getattr(item, attr)
            if field is not None:
                yield f"line_items[{i}].{attr}", field


def _blend(agree: float, found: float, valid: float) -> float:
    return round(_W_AGREE * agree + _W_VERBATIM * found + _W_VALID * valid, 3)


def apply_confidence(samples: list[Invoice], source_text: str) -> Invoice:
    """Score each field of the primary sample and set confidence/review_required.

    `samples[0]` is the returned invoice; the rest feed the self-consistency
    signal. `source_text` is the native text or OCR Markdown the model saw.
    """
    primary = samples[0]
    src = _norm(source_text)
    violations = {v.field for v in validate.validate(primary)}
    # path -> value per sample, for the agreement count.
    per_sample = [dict(_iter_fields(s)) for s in samples]

    for path, field in _iter_fields(primary):
        pnorm = _norm(field.value)
        others = [sf.get(path) for sf in per_sample]
        agree = sum(1 for f in others if f is not None and _norm(f.value) == pnorm) / len(
            per_sample
        )
        found = 1.0 if pnorm and pnorm in src else 0.0
        valid = 0.0 if path in violations else 1.0

        field.confidence = _blend(agree, found, valid)
        field.review_required = field.confidence < REVIEW_THRESHOLD

    return primary


def demo() -> None:
    """Assert-based self-check: a clean agreed verbatim field scores high; a
    field that disagrees, is absent from source, and fails validation scores low."""
    source = "Acme Corp  Invoice INV-1  Subtotal 90.00  Tax 10.00  Total 100.00"

    def mk(total: float) -> Invoice:
        return Invoice.model_validate(
            {
                "vendor_name": {"value": "Acme Corp"},
                "subtotal": {"value": 90.0},
                "tax": {"value": 10.0},
                "total": {"value": total},
            }
        )

    # Primary total=777 (not in source, samples disagree, 90+10 != 777 -> violation).
    out = apply_confidence([mk(777.0), mk(250.0), mk(999.0)], source)

    # High: agrees across samples, verbatim in source, no violation.
    assert out.vendor_name.confidence >= REVIEW_THRESHOLD, out.vendor_name
    assert out.vendor_name.review_required is False
    assert out.subtotal.confidence >= REVIEW_THRESHOLD, out.subtotal

    # Low: fails all three signals -> flagged for review.
    assert out.total.confidence < REVIEW_THRESHOLD, out.total
    assert out.total.review_required is True
    assert out.vendor_name.confidence > out.total.confidence

    # Every scored field lands in [0, 1].
    for _, f in _iter_fields(out):
        assert 0.0 <= f.confidence <= 1.0

    print("confidence demo OK")


if __name__ == "__main__":
    demo()
