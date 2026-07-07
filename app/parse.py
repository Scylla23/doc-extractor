"""Native text-layer extraction via PyMuPDF — the cheap fast path (PRD §4 Finding 1).

Born-digital PDFs carry a real text layer; pulling it out with PyMuPDF is
~10-20x cheaper than sending page images to a vision model. Scanned/image-only
PDFs have no usable text layer and fall back to the document-block / OCR path
(the native-vs-scanned router is T11, OCR is T12).
"""

from __future__ import annotations

import sys

import fitz  # PyMuPDF


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


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.parse <invoice.pdf>")
    with open(sys.argv[1], "rb") as f:
        text = extract_text(f.read())
    # ponytail: assert-based smoke check — a born-digital sample must yield text.
    assert text.strip(), "no native text layer extracted"
    print(text)
