# doc-extractor

**Turn a messy invoice PDF into clean, validated JSON — with a confidence score and a source citation for every field.**

Upload an invoice, get back structured data (vendor, dates, line items, totals) that matches a fixed schema, where every field carries its own `confidence`, `source_quote`, and `page` — so you can auto-approve the easy ones and route the uncertain ones to a human.

> **Status: M0 (walking skeleton).** A single command extracts one invoice PDF end-to-end via Claude. The API, OCR fallback, router, confidence engine, and accuracy eval come next (see `tasks.md`).

## What it does (today)

- Reads a **born-digital PDF** natively (no OCR needed for the text-layer path).
- Sends it to **Claude Haiku 4.5** and gets back JSON matching a fixed **invoice schema** (Pydantic).
- **Validates** the result into a typed `Invoice` object — every field wrapped as `{value, confidence, source_quote, page, review_required}`.

## Quickstart

```bash
git clone <this-repo> doc-extractor && cd doc-extractor

# Python 3.12 recommended. Any venv tool works; uv shown here.
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt

cp .env.example .env
# edit .env and set your key:
#   ANTHROPIC_API_KEY=sk-ant-...

.venv/bin/python -m app.extract samples/sample1.pdf
```

## Expected output

The command prints a validated `Invoice` as JSON. Abbreviated (full output is ~145 lines — one block per field and line item):

```json
{
  "vendor_name":    { "value": "ACME WIDGETS INC.", "confidence": 0.95, "source_quote": "ACME WIDGETS INC.", "page": 1, "review_required": false },
  "invoice_number": { "value": "INV-2026-0042",     "confidence": 0.95, "source_quote": "Invoice Number: INV-2026-0042", "page": 1, "review_required": false },
  "invoice_date":   { "value": "2026-06-15",         "confidence": 0.95, "source_quote": "Invoice Date: 2026-06-15", "page": 1, "review_required": false },
  "currency":       { "value": "USD",                "confidence": 0.95, "source_quote": "Currency: USD", "page": 1, "review_required": false },
  "line_items": [
    { "description": { "value": "Steel widget, 10mm", ... }, "quantity": { "value": 100.0, ... }, "unit_price": { "value": 2.5, ... }, "amount": { "value": 250.0, ... } }
    /* + Aluminum bracket ($200.00), Assembly labor ($450.00) */
  ],
  "subtotal": { "value": 900.0, ... },
  "tax":      { "value": 72.0,  ... },
  "total":    { "value": 972.0, "confidence": 0.95, "source_quote": "Total: $972.00", "page": 1, "review_required": false }
}
```

A `[extract] model=... input_tokens=... output_tokens=...` line is printed to **stderr** for reproducibility.

> The extracted `value`s (vendor, dates, amounts, totals) are stable; `confidence` is currently the model's own 0–1 estimate and varies slightly between runs (the multi-signal confidence engine is a later task). See `docs/screenshot-1-run.png` and `docs/screenshot-2-output.png` for a full run.

## How it works

```
PDF bytes ──► Claude Haiku 4.5 (native PDF document block + Invoice JSON schema)
          ──► model returns JSON ──► Pydantic validates ──► Invoice
```

- **Schema-first.** `app/schema.py` is the single source of truth: `Field` / `LineItem` / `Invoice`. It derives the JSON schema handed to the model and (later) the API + DB shapes.
- **Plain JSON + validation, not constrained decoding.** Claude's Structured-Outputs grammar compiler times out on the confidence-wrapper-per-field schema, so we prompt for JSON and validate with Pydantic; self-heal retries come with Instructor (next milestone).
- **Haiku by default.** The cheap tier handles clean invoices; escalation to Sonnet/Opus on low confidence is a later task.

## License

MIT (to be added).
