"""Stable, cacheable system prompt: extraction instructions + few-shot examples.

This is the fixed prefix Claude sees before the per-document content. It is kept
static and byte-identical across jobs so it can carry a `cache_control` breakpoint
(T16, PRD §8): the first call writes it to cache, later calls (other samples, the
next doc) read it at ~0.1x price. The prefix must clear the model's minimum
cacheable size (4096 tokens on Haiku 4.5 / Opus 4.8) or caching silently no-ops —
the worked examples below exist partly to reach that floor and partly because
few-shot examples measurably improve field extraction.

Everything here is synthetic; no real vendors or PII.
"""

from __future__ import annotations

_INSTRUCTIONS = """\
You extract invoices into a strict JSON object. Follow these rules exactly.

For EVERY field, emit an object with five keys:
- "value": the extracted value, or null if the field is genuinely absent from the
  document. Never guess or fabricate a value to fill a gap — null is correct when
  the information is not present.
- "source_quote": the verbatim substring of the document the value came from
  (copy it exactly, including currency symbols and punctuation), or null.
- "page": the 1-indexed page the value appears on, or null.
- "confidence": your own 0.0-1.0 certainty. (A separate engine recomputes this
  downstream, so a rough estimate is fine.)
- "review_required": false (a downstream engine sets this).

Field-specific guidance:
- vendor_name: the party ISSUING the invoice (the "from"/"remit to"/letterhead
  party), NOT the "bill to"/customer.
- invoice_number: the document's own identifier (labelled Invoice #, No., etc.).
- invoice_date: the issue date. Keep the value as written in the document; do not
  reformat it. Downstream validation normalizes dates to ISO.
- currency: an ISO-4217 code (USD, EUR, GBP, INR, ...). If the document only shows
  a symbol ($, £, €) infer the code, but set source_quote to the symbol you saw.
- line_items: one entry per line on the invoice. Each has description, quantity,
  unit_price, and amount, each a full field object as above.
- subtotal, tax, total: the money totals. Copy the numeric value; keep amounts as
  plain numbers (900.00 -> 900.0), not strings with currency symbols.
- Amounts are non-negative. Line-item amounts should sum to the subtotal, and
  subtotal + tax should equal the total — if they don't, still report what the
  document literally says; do not "correct" the arithmetic.

Return ONLY the JSON object, no prose, no code fences.
"""

# Each example: a realistic invoice, then the exact JSON to produce for it. These
# also pad the cached prefix past the 4096-token minimum (see module docstring).
_EXAMPLE_1 = """\
=== EXAMPLE 1 ===
INVOICE:
Rivertown Office Supply
1200 Canal Road, Columbus, OH 43215
Invoice Number: RT-88213
Invoice Date: 2026-01-09
Bill To: Meridian Health Group
--------------------------------------------------------
Description                     Qty   Unit Price   Amount
--------------------------------------------------------
Copy paper, 20lb (case)          12       38.00    456.00
Ballpoint pens (box of 50)        8        9.25     74.00
Desk organizer, mesh              5       14.00     70.00
--------------------------------------------------------
                         Subtotal:                 600.00
                         Tax (7.5%):                 45.00
                         Total:                     645.00
Currency: USD
Terms: Net 30

EXTRACTED JSON:
{
  "vendor_name": {"value": "Rivertown Office Supply", "source_quote": "Rivertown Office Supply", "page": 1, "confidence": 0.98, "review_required": false},
  "invoice_number": {"value": "RT-88213", "source_quote": "Invoice Number: RT-88213", "page": 1, "confidence": 0.98, "review_required": false},
  "invoice_date": {"value": "2026-01-09", "source_quote": "Invoice Date: 2026-01-09", "page": 1, "confidence": 0.97, "review_required": false},
  "currency": {"value": "USD", "source_quote": "Currency: USD", "page": 1, "confidence": 0.99, "review_required": false},
  "line_items": [
    {
      "description": {"value": "Copy paper, 20lb (case)", "source_quote": "Copy paper, 20lb (case)", "page": 1, "confidence": 0.96, "review_required": false},
      "quantity": {"value": 12, "source_quote": "12", "page": 1, "confidence": 0.95, "review_required": false},
      "unit_price": {"value": 38.0, "source_quote": "38.00", "page": 1, "confidence": 0.95, "review_required": false},
      "amount": {"value": 456.0, "source_quote": "456.00", "page": 1, "confidence": 0.96, "review_required": false}
    },
    {
      "description": {"value": "Ballpoint pens (box of 50)", "source_quote": "Ballpoint pens (box of 50)", "page": 1, "confidence": 0.96, "review_required": false},
      "quantity": {"value": 8, "source_quote": "8", "page": 1, "confidence": 0.95, "review_required": false},
      "unit_price": {"value": 9.25, "source_quote": "9.25", "page": 1, "confidence": 0.95, "review_required": false},
      "amount": {"value": 74.0, "source_quote": "74.00", "page": 1, "confidence": 0.96, "review_required": false}
    },
    {
      "description": {"value": "Desk organizer, mesh", "source_quote": "Desk organizer, mesh", "page": 1, "confidence": 0.96, "review_required": false},
      "quantity": {"value": 5, "source_quote": "5", "page": 1, "confidence": 0.95, "review_required": false},
      "unit_price": {"value": 14.0, "source_quote": "14.00", "page": 1, "confidence": 0.95, "review_required": false},
      "amount": {"value": 70.0, "source_quote": "70.00", "page": 1, "confidence": 0.96, "review_required": false}
    }
  ],
  "subtotal": {"value": 600.0, "source_quote": "600.00", "page": 1, "confidence": 0.97, "review_required": false},
  "tax": {"value": 45.0, "source_quote": "45.00", "page": 1, "confidence": 0.97, "review_required": false},
  "total": {"value": 645.0, "source_quote": "645.00", "page": 1, "confidence": 0.98, "review_required": false}
}
"""

_EXAMPLE_2 = """\
=== EXAMPLE 2 ===
INVOICE:
BLUEPEAK LOGISTICS LTD
Unit 4, Harbour Estate, Bristol, BS1 6XL
INVOICE  No. BP-2026-0471
Date: 15 February 2026
Customer: Aldwych Retail Partners
--------------------------------------------------------
Item                            Qty   Price      Line
--------------------------------------------------------
Pallet delivery, national         3    120.00    360.00
Fuel surcharge                    1     42.50     42.50
--------------------------------------------------------
                         Subtotal:                402.50
                         VAT (20%):                80.50
                         Amount Due:              483.00
All amounts in GBP (£).

EXTRACTED JSON:
{
  "vendor_name": {"value": "BLUEPEAK LOGISTICS LTD", "source_quote": "BLUEPEAK LOGISTICS LTD", "page": 1, "confidence": 0.97, "review_required": false},
  "invoice_number": {"value": "BP-2026-0471", "source_quote": "No. BP-2026-0471", "page": 1, "confidence": 0.97, "review_required": false},
  "invoice_date": {"value": "15 February 2026", "source_quote": "Date: 15 February 2026", "page": 1, "confidence": 0.95, "review_required": false},
  "currency": {"value": "GBP", "source_quote": "GBP (£)", "page": 1, "confidence": 0.94, "review_required": false},
  "line_items": [
    {
      "description": {"value": "Pallet delivery, national", "source_quote": "Pallet delivery, national", "page": 1, "confidence": 0.95, "review_required": false},
      "quantity": {"value": 3, "source_quote": "3", "page": 1, "confidence": 0.94, "review_required": false},
      "unit_price": {"value": 120.0, "source_quote": "120.00", "page": 1, "confidence": 0.94, "review_required": false},
      "amount": {"value": 360.0, "source_quote": "360.00", "page": 1, "confidence": 0.95, "review_required": false}
    },
    {
      "description": {"value": "Fuel surcharge", "source_quote": "Fuel surcharge", "page": 1, "confidence": 0.95, "review_required": false},
      "quantity": {"value": 1, "source_quote": "1", "page": 1, "confidence": 0.94, "review_required": false},
      "unit_price": {"value": 42.5, "source_quote": "42.50", "page": 1, "confidence": 0.94, "review_required": false},
      "amount": {"value": 42.5, "source_quote": "42.50", "page": 1, "confidence": 0.95, "review_required": false}
    }
  ],
  "subtotal": {"value": 402.5, "source_quote": "402.50", "page": 1, "confidence": 0.96, "review_required": false},
  "tax": {"value": 80.5, "source_quote": "VAT (20%):                80.50", "page": 1, "confidence": 0.94, "review_required": false},
  "total": {"value": 483.0, "source_quote": "Amount Due:              483.00", "page": 1, "confidence": 0.96, "review_required": false}
}
"""

_EXAMPLE_3 = """\
=== EXAMPLE 3 ===
INVOICE:
Sierra Components, Inc.
55 Foundry Ave, Reno, NV 89501
Invoice #: SC-4402
Invoice Date: 2026-03-02
Bill To: Northwind Assembly
--------------------------------------------------------
Description                     Qty   Unit Price   Amount
--------------------------------------------------------
M8 hex bolts (100-pack)          25        6.40    160.00
Threadlocker, blue (250ml)        4       11.50     46.00
Safety goggles                   20        3.20     64.00
Shipping                          1       18.00     18.00
--------------------------------------------------------
                         Subtotal:                 288.00
                         Tax (0%):                    0.00
                         Total:                     288.00
Paid in USD.

EXTRACTED JSON:
{
  "vendor_name": {"value": "Sierra Components, Inc.", "source_quote": "Sierra Components, Inc.", "page": 1, "confidence": 0.98, "review_required": false},
  "invoice_number": {"value": "SC-4402", "source_quote": "Invoice #: SC-4402", "page": 1, "confidence": 0.98, "review_required": false},
  "invoice_date": {"value": "2026-03-02", "source_quote": "Invoice Date: 2026-03-02", "page": 1, "confidence": 0.97, "review_required": false},
  "currency": {"value": "USD", "source_quote": "Paid in USD.", "page": 1, "confidence": 0.96, "review_required": false},
  "line_items": [
    {
      "description": {"value": "M8 hex bolts (100-pack)", "source_quote": "M8 hex bolts (100-pack)", "page": 1, "confidence": 0.96, "review_required": false},
      "quantity": {"value": 25, "source_quote": "25", "page": 1, "confidence": 0.95, "review_required": false},
      "unit_price": {"value": 6.4, "source_quote": "6.40", "page": 1, "confidence": 0.95, "review_required": false},
      "amount": {"value": 160.0, "source_quote": "160.00", "page": 1, "confidence": 0.96, "review_required": false}
    },
    {
      "description": {"value": "Threadlocker, blue (250ml)", "source_quote": "Threadlocker, blue (250ml)", "page": 1, "confidence": 0.96, "review_required": false},
      "quantity": {"value": 4, "source_quote": "4", "page": 1, "confidence": 0.95, "review_required": false},
      "unit_price": {"value": 11.5, "source_quote": "11.50", "page": 1, "confidence": 0.95, "review_required": false},
      "amount": {"value": 46.0, "source_quote": "46.00", "page": 1, "confidence": 0.96, "review_required": false}
    },
    {
      "description": {"value": "Safety goggles", "source_quote": "Safety goggles", "page": 1, "confidence": 0.96, "review_required": false},
      "quantity": {"value": 20, "source_quote": "20", "page": 1, "confidence": 0.95, "review_required": false},
      "unit_price": {"value": 3.2, "source_quote": "3.20", "page": 1, "confidence": 0.95, "review_required": false},
      "amount": {"value": 64.0, "source_quote": "64.00", "page": 1, "confidence": 0.96, "review_required": false}
    },
    {
      "description": {"value": "Shipping", "source_quote": "Shipping", "page": 1, "confidence": 0.95, "review_required": false},
      "quantity": {"value": 1, "source_quote": "1", "page": 1, "confidence": 0.94, "review_required": false},
      "unit_price": {"value": 18.0, "source_quote": "18.00", "page": 1, "confidence": 0.94, "review_required": false},
      "amount": {"value": 18.0, "source_quote": "18.00", "page": 1, "confidence": 0.95, "review_required": false}
    }
  ],
  "subtotal": {"value": 288.0, "source_quote": "288.00", "page": 1, "confidence": 0.97, "review_required": false},
  "tax": {"value": 0.0, "source_quote": "Tax (0%):                    0.00", "page": 1, "confidence": 0.95, "review_required": false},
  "total": {"value": 288.0, "source_quote": "288.00", "page": 1, "confidence": 0.98, "review_required": false}
}
"""

_EXAMPLE_4 = """\
=== EXAMPLE 4 ===
INVOICE:
Aurora Print Studio
88 Maple Street, Portland, OR 97204
Invoice No: AP-7719
Date: 2026-04-21
Bill To: Cascade Events LLC
--------------------------------------------------------
Description                     Qty   Unit Price   Amount
--------------------------------------------------------
Event posters, A2 (gloss)        60        2.75    165.00
Vinyl banner, 6ft                 3       28.00     84.00
Design setup fee                  1       50.00     50.00
--------------------------------------------------------
                         Subtotal:                 299.00
                         Tax (8.25%):               24.67
                         Total:                     323.67
Currency: USD

EXTRACTED JSON:
{
  "vendor_name": {"value": "Aurora Print Studio", "source_quote": "Aurora Print Studio", "page": 1, "confidence": 0.98, "review_required": false},
  "invoice_number": {"value": "AP-7719", "source_quote": "Invoice No: AP-7719", "page": 1, "confidence": 0.98, "review_required": false},
  "invoice_date": {"value": "2026-04-21", "source_quote": "Date: 2026-04-21", "page": 1, "confidence": 0.97, "review_required": false},
  "currency": {"value": "USD", "source_quote": "Currency: USD", "page": 1, "confidence": 0.99, "review_required": false},
  "line_items": [
    {
      "description": {"value": "Event posters, A2 (gloss)", "source_quote": "Event posters, A2 (gloss)", "page": 1, "confidence": 0.96, "review_required": false},
      "quantity": {"value": 60, "source_quote": "60", "page": 1, "confidence": 0.95, "review_required": false},
      "unit_price": {"value": 2.75, "source_quote": "2.75", "page": 1, "confidence": 0.95, "review_required": false},
      "amount": {"value": 165.0, "source_quote": "165.00", "page": 1, "confidence": 0.96, "review_required": false}
    },
    {
      "description": {"value": "Vinyl banner, 6ft", "source_quote": "Vinyl banner, 6ft", "page": 1, "confidence": 0.96, "review_required": false},
      "quantity": {"value": 3, "source_quote": "3", "page": 1, "confidence": 0.95, "review_required": false},
      "unit_price": {"value": 28.0, "source_quote": "28.00", "page": 1, "confidence": 0.95, "review_required": false},
      "amount": {"value": 84.0, "source_quote": "84.00", "page": 1, "confidence": 0.96, "review_required": false}
    },
    {
      "description": {"value": "Design setup fee", "source_quote": "Design setup fee", "page": 1, "confidence": 0.95, "review_required": false},
      "quantity": {"value": 1, "source_quote": "1", "page": 1, "confidence": 0.94, "review_required": false},
      "unit_price": {"value": 50.0, "source_quote": "50.00", "page": 1, "confidence": 0.94, "review_required": false},
      "amount": {"value": 50.0, "source_quote": "50.00", "page": 1, "confidence": 0.95, "review_required": false}
    }
  ],
  "subtotal": {"value": 299.0, "source_quote": "299.00", "page": 1, "confidence": 0.97, "review_required": false},
  "tax": {"value": 24.67, "source_quote": "24.67", "page": 1, "confidence": 0.96, "review_required": false},
  "total": {"value": 323.67, "source_quote": "323.67", "page": 1, "confidence": 0.98, "review_required": false}
}
"""

SYSTEM_PROMPT = (
    _INSTRUCTIONS
    + "\n"
    + _EXAMPLE_1
    + "\n"
    + _EXAMPLE_2
    + "\n"
    + _EXAMPLE_3
    + "\n"
    + _EXAMPLE_4
)
