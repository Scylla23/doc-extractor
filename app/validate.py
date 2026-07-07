"""Business-rule validation for extracted invoices (PRD §6 / T7).

Semantic checks the LLM can't self-guarantee: line-item arithmetic, subtotal +
tax = total, a parseable date, and non-negative amounts. Everything on an
`Invoice` is `Optional[Field]` and every `Field.value` may be `None`, so each
rule skips silently when its inputs are absent — absence is not a violation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app.schema import Field, Invoice

# Money compares as floats; treat sums equal within one cent of rounding noise.
_TOL = 0.01

# ponytail: explicit format ceiling — extend this list if real invoices bring a
# date shape none of these match (stdlib only, no python-dateutil).
_DATE_FORMATS = ("%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y", "%d %b %Y")


@dataclass
class Violation:
    field: str
    message: str


def _num(f: Optional[Field]) -> Optional[float]:
    """Numeric value of a Field, or None if the field/value is absent or non-numeric."""
    if f is None or f.value is None:
        return None
    try:
        return float(f.value)
    except (TypeError, ValueError):
        return None


def _normalize_date(raw: str) -> Optional[str]:
    """Return ISO YYYY-MM-DD if `raw` parses under a known format, else None."""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def validate(invoice: Invoice) -> list[Violation]:
    """Check business rules; return one Violation per broken rule (empty = clean)."""
    violations: list[Violation] = []

    subtotal = _num(invoice.subtotal)
    tax = _num(invoice.tax)
    total = _num(invoice.total)

    # Non-negative amounts (top-level).
    for name, val in (("subtotal", subtotal), ("tax", tax), ("total", total)):
        if val is not None and val < 0:
            violations.append(Violation(name, f"{name} is negative ({val})"))

    # Line items: non-negative, and Σ(amount) ≈ subtotal.
    line_sum = None
    if invoice.line_items:
        line_sum = 0.0
        for i, item in enumerate(invoice.line_items):
            for attr in ("quantity", "unit_price", "amount"):
                v = _num(getattr(item, attr))
                if v is not None and v < 0:
                    violations.append(
                        Violation(f"line_items[{i}].{attr}", f"{attr} is negative ({v})")
                    )
            amt = _num(item.amount)
            if amt is not None:
                line_sum += amt

    if line_sum is not None and subtotal is not None:
        if abs(line_sum - subtotal) > _TOL:
            violations.append(
                Violation(
                    "subtotal",
                    f"line-item sum {line_sum} != subtotal {subtotal}",
                )
            )

    # subtotal + tax ≈ total (tax defaults to 0 if absent but subtotal/total present).
    if subtotal is not None and total is not None:
        expected = subtotal + (tax or 0.0)
        if abs(expected - total) > _TOL:
            violations.append(
                Violation("total", f"subtotal+tax {expected} != total {total}")
            )

    # invoice_date must normalize to ISO.
    if invoice.invoice_date is not None and invoice.invoice_date.value is not None:
        raw = str(invoice.invoice_date.value)
        if _normalize_date(raw) is None:
            violations.append(
                Violation("invoice_date", f"unparseable date: {raw!r}")
            )

    return violations


def demo() -> None:
    valid = Invoice.model_validate(
        {
            "invoice_date": {"value": "2026-07-01"},
            "line_items": [
                {"amount": {"value": 20.0}, "quantity": {"value": 2}, "unit_price": {"value": 10.0}},
                {"amount": {"value": 5.0}},
            ],
            "subtotal": {"value": 25.0},
            "tax": {"value": 2.0},
            "total": {"value": 27.0},
        }
    )
    assert validate(valid) == [], validate(valid)

    # (2) Tampered total, off by $10 -> a `total` violation.
    tampered = valid.model_copy(deep=True)
    tampered.total.value = 37.0
    fields = {v.field for v in validate(tampered)}
    assert "total" in fields, fields

    # (3a) Non-ISO date that DOES parse -> no date violation.
    parseable = valid.model_copy(deep=True)
    parseable.invoice_date.value = "07/01/2026"
    assert _normalize_date("07/01/2026") == "2026-07-01"
    assert not any(v.field == "invoice_date" for v in validate(parseable))

    # (3b) Garbage date -> flagged.
    garbage = valid.model_copy(deep=True)
    garbage.invoice_date.value = "not a date"
    assert any(v.field == "invoice_date" for v in validate(garbage))

    # Negative amount is a violation; absence (empty Invoice) is not.
    neg = valid.model_copy(deep=True)
    neg.subtotal.value = -1.0
    assert any(v.field == "subtotal" for v in validate(neg))
    assert validate(Invoice()) == []

    print("validate demo OK")


if __name__ == "__main__":
    demo()
