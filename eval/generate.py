"""Synthetic labeled invoice generator (T17, PRD §10).

We GENERATE the eval set instead of hand-labeling public invoices: because we
author the content, the ground-truth JSON is free and exact, there is no PII,
and the whole set is reproducible from this one seeded script. Each invoice is
emitted as a matched pair under `eval/dataset/`:

    inv_000.pdf   — the document the extractor sees
    inv_000.json  — the ground truth, an `Invoice`-shaped JSON (value per field)

The mix (documented in `dataset/manifest.json`) spans the three things that
exercise the pipeline differently:

  * clean-native — born-digital PDF with a real text layer (the cheap fast path)
  * scanned      — the same content rasterized to an image-only PDF (no text
                   layer), forcing the router onto the Mistral OCR path
  * edge         — native but messy: broken arithmetic (printed totals that
                   don't foot), missing fields, word / non-ISO dates, $-only
                   currency (the verbatim-miss case), many line items, 2 pages

Ground truth records what is *printed* on the doc (e.g. a broken total is stored
as printed) — this measures extraction fidelity, not arithmetic correction.

    python eval/generate.py            # (re)generate eval/dataset/

ponytail: PDFs drawn with PyMuPDF (already the parse/render dependency) — no
reportlab. Layout is a plain coordinate-placed invoice, enough for the model to
read; it is not trying to look pretty.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import fitz  # PyMuPDF

DATASET = Path(__file__).resolve().parent / "dataset"

# (code, symbol). $-only edge docs print the symbol but not the code, so the
# model must infer the currency — the T14 verbatim-miss case.
_CURRENCIES = [("USD", "$"), ("EUR", "€"), ("GBP", "£"), ("INR", "₹")]

_VENDORS = [
    "Acme Industrial Supply", "Northwind Traders", "Globex Corporation",
    "Umbrella Logistics", "Stark Components", "Wayne Enterprises",
    "Initech Software", "Soylent Foods", "Hooli Cloud Services",
    "Cyberdyne Systems", "Vandelay Imports", "Pied Piper Data",
]

_ITEMS = [
    "Consulting services", "Widget assembly kit", "Cloud hosting (monthly)",
    "Steel bracket, 4in", "Design retainer", "Printer toner cartridge",
    "License renewal", "Freight & handling", "Installation labor",
    "Extended warranty", "Copper piping, 10ft", "Support hours",
    "Data migration", "Security audit", "Training session",
]

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _money(x: float) -> float:
    return round(x, 2)


def _fmt_amount(x: float, symbol: str) -> str:
    """Printed money string: symbol + thousands-separated cents (e.g. $1,234.50)."""
    return f"{symbol}{x:,.2f}"


def _iso_date(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def _make_line_items(rng: random.Random, n: int) -> list[dict]:
    items = []
    for _ in range(n):
        qty = rng.randint(1, 9)
        unit = _money(rng.uniform(5, 500))
        items.append(
            {
                "description": rng.choice(_ITEMS),
                "quantity": qty,
                "unit_price": unit,
                "amount": _money(qty * unit),
            }
        )
    return items


def _base_invoice(rng: random.Random, idx: int, *, n_items: int) -> dict:
    """A well-formed invoice as a plain dict of printed values (arithmetic foots)."""
    code, symbol = rng.choice(_CURRENCIES)
    items = _make_line_items(rng, n_items)
    subtotal = _money(sum(i["amount"] for i in items))
    tax = _money(subtotal * rng.choice([0.0, 0.05, 0.08, 0.10, 0.20]))
    total = _money(subtotal + tax)
    y, m, d = 2026, rng.randint(1, 12), rng.randint(1, 28)
    return {
        "vendor_name": rng.choice(_VENDORS),
        "invoice_number": f"INV-{1000 + idx}",
        "invoice_date": _iso_date(y, m, d),
        "_date_ymd": (y, m, d),
        "currency": code,
        "_symbol": symbol,
        "_currency_printed": True,   # whether the "Currency: USD" line is drawn
        "line_items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
    }


def _ground_truth(inv: dict) -> dict:
    """Invoice-shaped ground-truth JSON: {value: ...} per present field. Dates are
    stored ISO (the scorer normalizes both sides), amounts as floats. Missing
    fields (value None) are omitted so the GT is a clean `Invoice`."""
    def f(v):
        return {"value": v}

    gt: dict = {}
    if inv["vendor_name"] is not None:
        gt["vendor_name"] = f(inv["vendor_name"])
    if inv["invoice_number"] is not None:
        gt["invoice_number"] = f(inv["invoice_number"])
    if inv["invoice_date"] is not None:
        gt["invoice_date"] = f(inv["invoice_date"])
    if inv["currency"] is not None:
        gt["currency"] = f(inv["currency"])
    gt["line_items"] = [
        {
            "description": f(i["description"]),
            "quantity": f(i["quantity"]),
            "unit_price": f(i["unit_price"]),
            "amount": f(i["amount"]),
        }
        for i in inv["line_items"]
    ]
    for k in ("subtotal", "tax", "total"):
        if inv[k] is not None:
            gt[k] = f(inv[k])
    return gt


# --- rendering -------------------------------------------------------------

_LEFT, _TOP, _LINE = 56, 60, 16
_PAGE_W, _PAGE_H, _BOTTOM = 595, 842, 780  # A4 pts


def _printed_date(inv: dict, style: str) -> str:
    y, m, d = inv["_date_ymd"]
    if style == "word":
        return f"{_MONTHS[m - 1]} {d}, {y}"
    if style == "dmy":            # non-ISO 14/03/2026
        return f"{d:02d}/{m:02d}/{y}"
    return _iso_date(y, m, d)     # ISO


def _render_pdf(inv: dict, *, date_style: str = "iso") -> bytes:
    """Draw the invoice to a (possibly multi-page) born-digital PDF."""
    doc = fitz.open()
    symbol = inv["_symbol"]

    def new_page():
        return doc.new_page(width=_PAGE_W, height=_PAGE_H)

    page = new_page()
    y = _TOP

    def line(text: str, *, size: int = 11, dx: int = 0, bold: bool = False):
        nonlocal page, y
        if y > _BOTTOM:                       # overflow -> next page
            page = new_page()
            y = _TOP
        page.insert_text(
            (_LEFT + dx, y), text, fontsize=size,
            fontname="hebo" if bold else "helv",
        )
        y += _LINE

    line(inv["vendor_name"], size=16, bold=True)
    line("123 Commerce Way, Springfield")
    y += _LINE
    line("INVOICE", size=14, bold=True)
    if inv["invoice_number"] is not None:
        line(f"Invoice Number: {inv['invoice_number']}")
    if inv["invoice_date"] is not None:
        line(f"Invoice Date: {_printed_date(inv, date_style)}")
    if inv["currency"] is not None and inv["_currency_printed"]:
        line(f"Currency: {inv['currency']}")
    y += _LINE
    line("Description                         Qty    Unit Price      Amount", bold=True)
    for it in inv["line_items"]:
        row = (
            f"{it['description'][:32]:<34}"
            f"{it['quantity']:>3}    "
            f"{_fmt_amount(it['unit_price'], symbol):>10}    "
            f"{_fmt_amount(it['amount'], symbol):>10}"
        )
        line(row)
    y += _LINE
    if inv["subtotal"] is not None:
        line(f"Subtotal: {_fmt_amount(inv['subtotal'], symbol)}")
    if inv["tax"] is not None:
        line(f"Tax: {_fmt_amount(inv['tax'], symbol)}")
    if inv["total"] is not None:
        line(f"Total: {_fmt_amount(inv['total'], symbol)}", bold=True)

    out = doc.tobytes()
    doc.close()
    return out


def _rasterize(pdf_bytes: bytes, dpi: int = 150) -> bytes:
    """Re-emit a PDF as image-only pages (no text layer) — a synthetic scan that
    the router (T11) must classify `scanned` and send down the OCR path."""
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()
    try:
        for pg in src:
            pix = pg.get_pixmap(dpi=dpi)
            op = out.new_page(width=pg.rect.width, height=pg.rect.height)
            op.insert_image(op.rect, stream=pix.tobytes("png"))
        return out.tobytes()
    finally:
        src.close()
        out.close()


# --- edge-case mutators ----------------------------------------------------

def _edge(rng: random.Random, inv: dict, kind: str) -> tuple[dict, str]:
    """Apply one edge mutation; return (invoice, date_style). Ground truth always
    reflects what ends up printed."""
    if kind == "broken_arithmetic":
        inv["total"] = _money(inv["total"] + rng.choice([10.0, -12.5, 100.0]))
        return inv, "iso"
    if kind == "missing_fields":
        for k in rng.sample(["invoice_number", "tax", "invoice_date"], k=2):
            inv[k] = None
        return inv, "iso"
    if kind == "word_date":
        return inv, "word"
    if kind == "non_iso_date":
        return inv, "dmy"
    if kind == "dollar_only":
        inv["currency"], inv["_symbol"] = "USD", "$"
        inv["_currency_printed"] = False  # symbol only, no "Currency: USD" line
        return inv, "iso"
    raise ValueError(kind)


# --- driver ----------------------------------------------------------------

_N_CLEAN, _N_SCANNED, _N_EDGE = 30, 10, 10
_EDGE_KINDS = [
    "broken_arithmetic", "missing_fields", "word_date", "non_iso_date",
    "dollar_only", "broken_arithmetic", "missing_fields", "word_date",
    "dollar_only", "non_iso_date",
]  # 10, spanning every kind (some twice)


def generate() -> list[dict]:
    """Emit the full dataset + manifest. Deterministic (seeded)."""
    rng = random.Random(42)
    DATASET.mkdir(parents=True, exist_ok=True)
    # Clean out any stale pairs so the set always matches this generator.
    for old in DATASET.glob("inv_*"):
        old.unlink()

    manifest = []
    idx = 0

    def emit(inv: dict, category: str, pdf: bytes):
        nonlocal idx
        stem = f"inv_{idx:03d}"
        (DATASET / f"{stem}.pdf").write_bytes(pdf)
        (DATASET / f"{stem}.json").write_text(
            json.dumps(_ground_truth(inv), indent=2)
        )
        manifest.append({"id": stem, "category": category,
                         "n_line_items": len(inv["line_items"])})
        idx += 1

    for _ in range(_N_CLEAN):
        inv = _base_invoice(rng, idx, n_items=rng.randint(1, 4))
        emit(inv, "clean-native", _render_pdf(inv))

    for _ in range(_N_SCANNED):
        inv = _base_invoice(rng, idx, n_items=rng.randint(1, 4))
        emit(inv, "scanned", _rasterize(_render_pdf(inv)))

    for kind in _EDGE_KINDS:
        # many-line-item + multi-page ride along on the arithmetic edges: 40 rows
        # overflow past _BOTTOM, forcing a genuine second page (asserted below).
        n_items = 40 if kind == "broken_arithmetic" else rng.randint(2, 4)
        inv = _base_invoice(rng, idx, n_items=n_items)
        inv, date_style = _edge(rng, inv, kind)
        emit(inv, f"edge:{kind}", _render_pdf(inv, date_style=date_style))

    (DATASET / "manifest.json").write_text(
        json.dumps(
            {
                "counts": {"clean-native": _N_CLEAN, "scanned": _N_SCANNED,
                           "edge": _N_EDGE, "total": idx},
                "docs": manifest,
            },
            indent=2,
        )
    )
    return manifest


if __name__ == "__main__":
    m = generate()
    # Assert the claims the docstring makes actually hold on disk: >=1 multi-page
    # doc and >=1 image-only (scanned) doc.
    multipage = scanned_pages0 = 0
    for d in m:
        doc = fitz.open(DATASET / f"{d['id']}.pdf")
        try:
            if doc.page_count > 1:
                multipage += 1
            if d["category"] == "scanned" and sum(
                len(p.get_text().strip()) for p in doc
            ) == 0:
                scanned_pages0 += 1
        finally:
            doc.close()
    assert multipage >= 1, "no multi-page edge doc produced"
    assert scanned_pages0 == _N_SCANNED, "scanned docs are not image-only"
    print(f"generated {len(m)} invoice pairs into {DATASET} "
          f"({multipage} multi-page, {scanned_pages0} image-only scans)")
