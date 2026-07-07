"""PDF parsing: native text fast path + OCR fallback (PRD §4 Finding 1).

Born-digital PDFs carry a real text layer; pulling it out with PyMuPDF
(`extract_text`) is ~10-20x cheaper than a vision model. Scanned/image-only
PDFs have no usable text layer — the router (T11) sends those here to `ocr`,
which OCRs to Markdown via Mistral OCR and renders page rasters, so T13 can
send Markdown **plus** the page image to Claude (the hybrid parse-then-LLM
pattern).
"""

from __future__ import annotations

import base64
import os
import sys
import time
from dataclasses import dataclass

import fitz  # PyMuPDF
from dotenv import load_dotenv

load_dotenv()  # so `python -m app.parse --ocr` picks up MISTRAL_API_KEY from .env

# Mistral OCR: hosted, best-value OCR (~$1/1000 pages, PRD §7 shortlist / §12).
_OCR_MODEL = "mistral-ocr-latest"


def extract_text(pdf_bytes: bytes) -> str:
    """Return the PDF's native text layer, one labelled block per page.

    Page markers (`--- Page N ---`) give the model 1-indexed page numbers for
    the `page` / `source_quote` fields in the schema.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n\n".join(
            f"--- Page {i} ---\n{page.get_text()}"
            for i, page in enumerate(doc, start=1)
        )
    finally:
        doc.close()


@dataclass
class OcrResult:
    markdown: str
    page_images: list[bytes]  # one PNG raster per page, for T13's Markdown+image hybrid


def render_page_images(pdf_bytes: bytes, dpi: int = 150) -> list[bytes]:
    """Render each page to a PNG raster — the layout-preserving image the vision
    LLM sees alongside the OCR Markdown (T13). 150 DPI is the usual OCR/vision floor.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]
    finally:
        doc.close()


def ocr_markdown(pdf_bytes: bytes) -> str:
    """OCR a scanned PDF to Markdown via Mistral OCR.

    Raises a clear `RuntimeError` on any failure (missing key, API error) rather
    than crashing cryptically — the caller decides the fallback (T13). Logs
    pages + elapsed + rough cost per job (PRD §5 reproducibility; never the key).
    """
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise RuntimeError("MISTRAL_API_KEY not set; cannot OCR scanned document.")

    # Lazy import: the native fast path must not pay Mistral's import cost.
    from mistralai.client import Mistral

    b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    t0 = time.monotonic()
    try:
        with Mistral(api_key=key) as client:
            resp = client.ocr.process(
                model=_OCR_MODEL,
                document={
                    "type": "document_url",
                    "document_url": f"data:application/pdf;base64,{b64}",
                },
            )
    except Exception as exc:  # network / auth / API error -> one clear message
        raise RuntimeError(f"Mistral OCR failed: {exc}") from exc

    pages = resp.pages
    markdown = "\n\n".join(
        f"--- Page {i} ---\n{p.markdown}" for i, p in enumerate(pages, start=1)
    )
    print(
        f"[ocr] {_OCR_MODEL} pages={len(pages)} "
        f"elapsed={time.monotonic() - t0:.1f}s ~cost=${len(pages) / 1000:.4f}",
        file=sys.stderr,
    )
    return markdown


def ocr(pdf_bytes: bytes) -> OcrResult:
    """Full OCR path: Mistral Markdown + PyMuPDF page rasters (hybrid input, T13)."""
    return OcrResult(
        markdown=ocr_markdown(pdf_bytes),
        page_images=render_page_images(pdf_bytes),
    )


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--ocr":
        with open(args[1], "rb") as f:
            pdf = f.read()
        md = ocr_markdown(pdf)
        # ponytail: assert-based smoke check — both halves of the OCR path.
        assert md.strip(), "OCR returned empty Markdown"
        assert render_page_images(pdf), "no page rasters rendered"
        print(md)
    elif len(args) == 1:
        with open(args[0], "rb") as f:
            text = extract_text(f.read())
        assert text.strip(), "no native text layer extracted"
        print(text)
    else:
        sys.exit("usage: python -m app.parse [--ocr] <invoice.pdf>")
