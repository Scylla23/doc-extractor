"""End-to-end thread: PDF bytes -> Claude -> validated Invoice.

Fast path (T6): pull the native text layer with PyMuPDF and send that Markdown/
text (PRD §4 Finding 1) — ~10-20x cheaper than page images. When a PDF has no
usable text layer (scanned/image-only) we fall back to sending the whole PDF as
a base64 `document` block. The native-vs-scanned router and OCR are T11/T12.
Either way the model's JSON reply is validated against the `Invoice` schema.

Why not Structured Outputs? Claude's constrained-decoding grammar compiler times
out on our confidence-wrapper-per-field schema (a `Field` object repeated across
every field and every line item — "grammar compilation timed out", verified on
Haiku even after slimming every leaf). So we prompt for plain JSON and validate
with Pydantic. Self-heal retries on a malformed reply are Instructor's job (T8).
"""

from __future__ import annotations

import base64
import json
import re
import sys

import anthropic
from dotenv import load_dotenv

from app import parse
from app.schema import Invoice

load_dotenv()  # so `python -m app.extract` picks up ANTHROPIC_API_KEY from .env

# ponytail: Haiku 4.5 is the MVP default per the model cascade; Sonnet/Opus
# escalation on low confidence is T15, not now.
MODEL = "claude-haiku-4-5"

# ponytail: char-count heuristic for "has a usable text layer"; below this we
# fall back to the PDF document block. The real native-vs-scanned router is T11.
_MIN_TEXT_CHARS = 100


def _prompt() -> str:
    schema = json.dumps(Invoice.model_json_schema())
    return (
        "Extract this invoice into a JSON object matching exactly this schema:\n"
        f"{schema}\n\n"
        "Rules:\n"
        "- Return ONLY the JSON object — no prose, no markdown code fences.\n"
        "- For each field set: value (or null if absent from the document — never "
        "guess), source_quote (the verbatim text you took it from), page (1-indexed), "
        "and confidence (your 0-1 certainty).\n"
        "- Include one entry in line_items per line item on the invoice."
    )


def _strip_fences(text: str) -> str:
    """Tolerate a ```json ... ``` wrapper if the model adds one anyway."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return text


def extract_invoice(pdf_bytes: bytes) -> Invoice:
    client = anthropic.Anthropic()
    text = parse.extract_text(pdf_bytes)

    if len(text.strip()) >= _MIN_TEXT_CHARS:
        # Fast path: native text layer — cheap, no page images (PRD §4 Finding 1).
        content = [{"type": "text", "text": f"{_prompt()}\n\nINVOICE TEXT:\n{text}"}]
    else:
        # Fallback: scanned/image-only PDF — send the whole doc for vision.
        b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        content = [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
            },
            {"type": "text", "text": _prompt()},
        ]

    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": content}],
    )

    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            "Extraction hit max_tokens — JSON was truncated; raise max_tokens."
        )

    text = next(b.text for b in response.content if b.type == "text")

    # Log token usage (feeds the T6 fast-path comparison and reproducibility).
    print(
        f"[extract] model={response.model} input_tokens={response.usage.input_tokens} "
        f"output_tokens={response.usage.output_tokens}",
        file=sys.stderr,
    )
    return Invoice.model_validate_json(_strip_fences(text))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.extract <invoice.pdf>")
    with open(sys.argv[1], "rb") as f:
        invoice = extract_invoice(f.read())
    print(invoice.model_dump_json(indent=2))
