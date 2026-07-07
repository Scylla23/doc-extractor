"""Native-vs-scanned document router (T11, PRD §4 Finding 1).

Classify each PDF: does it carry a usable native text layer? Born-digital
invoices do — take the cheap PyMuPDF fast path (~10-20x cheaper than vision).
Scanned/photographed ones don't — route to OCR (T12). Route to the cheapest
engine that works.

The heuristic is text chars per page: a scanned page yields ~0 extractable
chars, a born-digital invoice yields hundreds. We read `page.get_text()`
directly (not `parse.extract_text`, whose per-page markers would inflate the
count on a blank scan).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

# ponytail: chars-per-page threshold, not total, so page count doesn't skew it.
# A scanned page extracts ~0 chars; a born-digital invoice hundreds. 50 sits
# well clear of both. Tune if real docs land in the gap (sparse native pages,
# or scans with a thin junk text layer).
_MIN_CHARS_PER_PAGE = 50

NATIVE = "native"
SCANNED = "scanned"


@dataclass
class Route:
    route: str  # NATIVE | SCANNED
    reason: str
    chars: int
    pages: int


def classify(pdf_bytes: bytes) -> Route:
    """Decide whether `pdf_bytes` has a usable native text layer.

    Logs the decision + reason to stderr (per-job reproducibility, PRD §5);
    when wired into `extract_invoice` (T13) this fires once per job.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = doc.page_count
        chars = sum(len(page.get_text().strip()) for page in doc)
    finally:
        doc.close()

    per_page = chars / pages if pages else 0
    if per_page >= _MIN_CHARS_PER_PAGE:
        r = Route(
            NATIVE,
            f"{chars} text chars over {pages} page(s) "
            f"({per_page:.0f}/page >= {_MIN_CHARS_PER_PAGE}) -- native text layer",
            chars,
            pages,
        )
    else:
        r = Route(
            SCANNED,
            f"only {chars} text chars over {pages} page(s) "
            f"({per_page:.0f}/page < {_MIN_CHARS_PER_PAGE}) -- no usable text layer, OCR",
            chars,
            pages,
        )
    print(f"[route] {r.route}: {r.reason}", file=sys.stderr)
    return r


def demo() -> None:
    """Assert-based self-check: the two curated samples must split correctly."""
    samples = Path(__file__).resolve().parent.parent / "samples"
    native = classify((samples / "sample1.pdf").read_bytes())
    scanned = classify((samples / "scanned1.pdf").read_bytes())
    assert native.route == NATIVE, native
    assert scanned.route == SCANNED, scanned
    print("route demo OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        demo()
    else:
        for path in args:
            r = classify(Path(path).read_bytes())
            print(f"{r.route}\t{path}")
