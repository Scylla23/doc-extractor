"""End-to-end thread: PDF bytes -> Claude -> validated Invoice.

Fast path (T6): pull the native text layer with PyMuPDF and send that Markdown/
text (PRD §4 Finding 1) — ~10-20x cheaper than page images. When a PDF has no
usable text layer (scanned/image-only) we fall back to sending the whole PDF as
a base64 `document` block. The native-vs-scanned router and OCR are T11/T12.

We don't use Claude's constrained-decoding structured output — its grammar
compiler times out on our confidence-wrapper-per-field schema. Instead we prompt
for plain JSON and let Instructor (T8) validate against `Invoice` and self-heal:
on a Pydantic/structural failure it re-prompts the model with the validation
error, bounded to MAX_RETRIES, rather than crashing.
"""

from __future__ import annotations

import base64
import sys

import anthropic
import instructor
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

# Instructor self-heal cap (§9): re-prompt with the validation error at most this
# many times before raising cleanly. Bounded — no loops.
MAX_RETRIES = 2


def _prompt() -> str:
    # Instructor injects the JSON schema itself (response_model=Invoice), so we
    # only give field-fill guidance here — embedding the schema too would double
    # it in the prompt and undo T6's token savings.
    return (
        "Extract this invoice as a JSON object.\n"
        "- For each field set: value (or null if absent from the document — never "
        "guess), source_quote (the verbatim text you took it from), page (1-indexed), "
        "and confidence (your 0-1 certainty).\n"
        "- Include one entry in line_items per line item on the invoice."
    )


def extract_invoice(
    pdf_bytes: bytes, *, client: anthropic.Anthropic | None = None
) -> Invoice:
    """PDF bytes -> validated Invoice. `client` is injectable for tests."""
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

    # Instructor validates the reply against `Invoice` and, on a structural
    # failure, re-prompts with the error (bounded by MAX_RETRIES). JSON mode, not
    # tool/structured-output mode — the grammar compiler times out on our schema.
    inst = instructor.from_anthropic(
        client or anthropic.Anthropic(), mode=instructor.Mode.ANTHROPIC_JSON
    )
    invoice, completion = inst.messages.create_with_completion(
        model=MODEL,
        max_tokens=8192,
        max_retries=MAX_RETRIES,
        response_model=Invoice,
        messages=[{"role": "user", "content": content}],
    )

    if completion.stop_reason == "max_tokens":
        raise RuntimeError(
            "Extraction hit max_tokens — JSON was truncated; raise max_tokens."
        )

    # Log token usage + retry cap (feeds the fast-path comparison and
    # reproducibility; structured per-job logging is T10).
    print(
        f"[extract] model={completion.model} "
        f"input_tokens={completion.usage.input_tokens} "
        f"output_tokens={completion.usage.output_tokens} max_retries={MAX_RETRIES}",
        file=sys.stderr,
    )
    return invoice


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.extract <invoice.pdf>")
    with open(sys.argv[1], "rb") as f:
        invoice = extract_invoice(f.read())
    print(invoice.model_dump_json(indent=2))
