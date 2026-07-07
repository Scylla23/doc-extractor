"""End-to-end thread: PDF bytes -> Claude -> validated Invoice.

The router (T11) classifies each PDF. Native text layer → fast path: pull the
text with PyMuPDF and send it (PRD §4 Finding 1) — ~10-20x cheaper than page
images. Scanned/image-only → OCR path (T12/T13): OCR to Markdown via Mistral and
send that Markdown **plus** the page image(s) to Claude — the hybrid
parse-then-LLM pattern, giving the model reliable tokens and the original layout.

We don't use Claude's constrained-decoding structured output — its grammar
compiler times out on our confidence-wrapper-per-field schema. Instead we prompt
for plain JSON and let Instructor (T8) validate against `Invoice` and self-heal:
on a Pydantic/structural failure it re-prompts the model with the validation
error, bounded to MAX_RETRIES, rather than crashing.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import anthropic
import instructor
from dotenv import load_dotenv

from app import confidence, parse, route
from app.schema import Invoice

load_dotenv()  # so `python -m app.extract` picks up ANTHROPIC_API_KEY from .env

# ponytail: Haiku 4.5 is the MVP default per the model cascade; Sonnet/Opus
# escalation on low confidence is T15, not now.
MODEL = "claude-haiku-4-5"

# Self-consistency samples for the confidence engine (T14, PRD §4 Finding 3):
# extract N times and score field agreement. ponytail: N model calls per doc,
# bounded (no loop growth); set to 1 to disable self-consistency if cost bites.
_N_SAMPLES = 3

# Instructor self-heal cap (§9): re-prompt with the validation error at most this
# many times before raising cleanly. Bounded — no loops.
MAX_RETRIES = 2

# ponytail: append-only JSONL per-job log; a real store is Supabase in backlog.
# Never contains the API key — only prompt text, model id, raw output, usage.
JOB_LOG_PATH = Path("logs/jobs.jsonl")


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


def _log_job(
    job_id: str,
    prompt: str,
    completion: anthropic.types.Message,
    n_samples: int = 1,
) -> None:
    """Append one reproducibility record per job (§5): prompt, model, raw output,
    token usage, and the self-consistency sample count. No API key is ever written."""
    raw = next((b.text for b in completion.content if b.type == "text"), "")
    record = {
        "job_id": job_id,
        "model": completion.model,
        "n_samples": n_samples,
        "prompt": prompt,
        "raw_output": raw,
        "usage": {
            "input_tokens": completion.usage.input_tokens,
            "output_tokens": completion.usage.output_tokens,
        },
    }
    JOB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOB_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _build_content(pdf_bytes: bytes) -> tuple[list[dict], str]:
    """Route the PDF and build (content blocks, source text).

    Native → one text block (PyMuPDF text). Scanned → the OCR-hybrid: one text
    block (Mistral Markdown) plus one image block per page (PRD §4 Finding 1).
    The returned source text feeds the verbatim-in-source confidence signal (T14).
    """
    if route.classify(pdf_bytes).route == route.NATIVE:
        # Fast path: native text layer — cheap, no page images.
        text = parse.extract_text(pdf_bytes)
        return [{"type": "text", "text": f"{_prompt()}\n\nINVOICE TEXT:\n{text}"}], text

    # OCR hybrid: Markdown for reliable tokens + the page image(s) for layout.
    ocr = parse.ocr(pdf_bytes)
    content: list[dict] = [
        {"type": "text", "text": f"{_prompt()}\n\nINVOICE MARKDOWN (OCR):\n{ocr.markdown}"}
    ]
    for image in ocr.page_images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(image).decode("ascii"),
                },
            }
        )
    return content, ocr.markdown


def extract_invoice(
    pdf_bytes: bytes,
    *,
    client: anthropic.Anthropic | None = None,
    job_id: str | None = None,
) -> Invoice:
    """PDF bytes -> validated Invoice. `client` is injectable for tests; when
    `job_id` is given, a per-job reproducibility record is logged (T10)."""
    content, source_text = _build_content(pdf_bytes)

    # Instructor validates the reply against `Invoice` and, on a structural
    # failure, re-prompts with the error (bounded by MAX_RETRIES). JSON mode, not
    # tool/structured-output mode — the grammar compiler times out on our schema.
    inst = instructor.from_anthropic(
        client or anthropic.Anthropic(), mode=instructor.Mode.ANTHROPIC_JSON
    )

    # Self-consistency: sample the extraction N times; the confidence engine (T14)
    # scores how many samples agree per field. Bounded loop, no growth.
    samples: list[Invoice] = []
    completion = None
    for _ in range(_N_SAMPLES):
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
        samples.append(invoice)

    # Blend self-consistency + verbatim-in-source + passes-validation -> per-field
    # confidence + review_required on the primary sample (PRD §4 Finding 3).
    invoice = confidence.apply_confidence(samples, source_text)

    # Log token usage to stderr (last sample is representative; feeds cost view).
    print(
        f"[extract] model={completion.model} n_samples={_N_SAMPLES} "
        f"input_tokens={completion.usage.input_tokens} "
        f"output_tokens={completion.usage.output_tokens} max_retries={MAX_RETRIES}",
        file=sys.stderr,
    )
    if job_id is not None:
        prompt = next(b["text"] for b in content if b["type"] == "text")
        _log_job(job_id, prompt, completion, n_samples=_N_SAMPLES)
    return invoice


def demo() -> None:
    """Offline self-check of block assembly — no API calls.

    Native sample routes to a single text block; the scanned sample routes to a
    text block **and** an image block (T13 hybrid). `parse.ocr` is stubbed so we
    don't spend a Mistral call just to assert the block shape.
    """
    samples = Path(__file__).resolve().parent.parent / "samples"

    native, _ = _build_content((samples / "sample1.pdf").read_bytes())
    assert [b["type"] for b in native] == ["text"], native

    orig_ocr = parse.ocr
    parse.ocr = lambda _b: parse.OcrResult(markdown="# stub", page_images=[b"\x89PNGstub"])
    try:
        scanned, _ = _build_content((samples / "scanned1.pdf").read_bytes())
    finally:
        parse.ocr = orig_ocr
    types = [b["type"] for b in scanned]
    assert "text" in types and "image" in types, types  # both blocks present

    print("extract demo OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        demo()
        sys.exit(0)
    if len(args) != 1:
        sys.exit("usage: python -m app.extract [<invoice.pdf>]")
    with open(args[0], "rb") as f:
        invoice = extract_invoice(f.read())
    print(invoice.model_dump_json(indent=2))
