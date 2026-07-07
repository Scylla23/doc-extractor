"""Invoice extraction schema — single source of truth (PRD §6).

Every field is a confidence-carrying `Field`, not a bare value. Everything is
Optional: absence is modelled as `None`, never a required scalar that would
invite hallucination. This module derives the JSON Schema fed to Claude
Structured Outputs, and (later) the API response / DB shape.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

# extra="forbid" -> additionalProperties:false in the generated JSON Schema,
# which Claude Structured Outputs requires on every object node.
_STRICT = ConfigDict(extra="forbid")

# Common ISO-4217 codes; closed set per PRD §6 (Literal for currency).
Currency = Literal["USD", "EUR", "INR", "GBP", "JPY", "CNY", "CAD", "AUD"]


class Field(BaseModel):
    """One extracted field with provenance and a confidence score."""

    model_config = _STRICT

    value: Optional[Union[str, float]] = None
    confidence: float = 0.0          # 0..1, from the multi-signal engine (T14)
    source_quote: Optional[str] = None  # verbatim text from the doc
    page: Optional[int] = None
    review_required: bool = False


class CurrencyField(Field):
    """Field whose value is constrained to a known currency code."""

    value: Optional[Currency] = None


class LineItem(BaseModel):
    model_config = _STRICT

    description: Optional[Field] = None
    quantity: Optional[Field] = None
    unit_price: Optional[Field] = None
    amount: Optional[Field] = None


class Invoice(BaseModel):
    model_config = _STRICT

    vendor_name: Optional[Field] = None
    invoice_number: Optional[Field] = None
    invoice_date: Optional[Field] = None      # normalized to ISO in validation (T7)
    currency: Optional[CurrencyField] = None
    line_items: Optional[list[LineItem]] = None
    subtotal: Optional[Field] = None
    tax: Optional[Field] = None
    total: Optional[Field] = None


def strict_json_schema() -> dict:
    """Invoice JSON Schema with every object node made strict for Claude.

    Claude Structured Outputs requires, on each object: additionalProperties
    false and *every* property listed in `required` (optionality is expressed
    by a nullable type, not by omission from `required`).
    """
    schema = Invoice.model_json_schema()
    _make_strict(schema)
    for node in schema.get("$defs", {}).values():
        _make_strict(node)
    return schema


def _make_strict(node: dict) -> None:
    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        node["required"] = list(node["properties"].keys())


def demo() -> None:
    example = {
        "vendor_name": {"value": "Acme Corp", "confidence": 0.9, "page": 1},
        "invoice_number": {"value": "INV-001", "confidence": 0.95},
        "invoice_date": {"value": "2026-07-01", "confidence": 0.8},
        "currency": {"value": "USD", "confidence": 0.99},
        "line_items": [
            {
                "description": {"value": "Widget", "confidence": 0.9},
                "quantity": {"value": 2, "confidence": 0.9},
                "unit_price": {"value": 10.0, "confidence": 0.9},
                "amount": {"value": 20.0, "confidence": 0.9},
            }
        ],
        "subtotal": {"value": 20.0, "confidence": 0.9},
        "tax": {"value": 1.6, "confidence": 0.9},
        "total": {"value": 21.6, "confidence": 0.9},
    }
    inv = Invoice.model_validate(example)
    assert inv.vendor_name.value == "Acme Corp"
    assert inv.currency.value == "USD"
    assert inv.line_items[0].amount.value == 20.0

    # Absence is expressible as None — no required scalar forces a hallucination.
    assert Invoice().total is None

    # Every object node in the strict schema is locked down for Claude.
    strict = strict_json_schema()
    assert strict["additionalProperties"] is False
    assert set(strict["required"]) == set(strict["properties"].keys())
    for node in strict["$defs"].values():
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"].keys())

    # Closed currency set rejects unknown codes.
    try:
        CurrencyField(value="XYZ")
    except Exception:
        pass
    else:
        raise AssertionError("currency Literal did not reject unknown code")

    print("schema demo OK")


if __name__ == "__main__":
    demo()
