"""T8 unit test: Instructor self-heal retry on schema failure — no network.

A real Anthropic client is constructed but its `messages.create` is patched to
return bad JSON first, then valid. Asserts exactly one retry occurs and a valid
Invoice comes back, plus the clean-error path once the retry cap is exhausted.
Runnable directly (`python test_extract.py`) or under pytest (T20).
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import anthropic
from anthropic.types import Message, TextBlock, Usage
from instructor.core import InstructorRetryException

import app.extract as extract_mod
from app.extract import MAX_RETRIES, extract_invoice
from app.schema import Invoice

_PDF = (Path(__file__).parent / "samples" / "sample1.pdf").read_bytes()
_GOOD = (
    '{"vendor_name":{"value":"Acme","confidence":0.9},'
    '"total":{"value":10.0,"confidence":0.9}}'
)


def _reply(text: str) -> Message:
    return Message(
        id="msg_test",
        model="claude-haiku-4-5",
        role="assistant",
        type="message",
        content=[TextBlock(type="text", text=text)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=10),
    )


def _patched_client(*replies: Message):
    client = anthropic.Anthropic(api_key="test-key-unused")  # no network at build
    return client, mock.patch.object(
        client.messages, "create", side_effect=list(replies)
    )


# Self-heal is a per-sample concern; pin to one self-consistency sample (T14) so
# the retry-count assertions isolate the retry mechanism, not the sample loop.
def test_retry_then_success() -> None:
    client, patcher = _patched_client(_reply("NOT JSON"), _reply(_GOOD))
    with mock.patch.object(extract_mod, "_N_SAMPLES", 1), patcher as m:
        inv = extract_invoice(_PDF, client=client)
    assert isinstance(inv, Invoice)
    assert inv.vendor_name.value == "Acme"
    assert m.call_count == 2  # one bad reply -> one retry -> success


def test_clean_raise_after_cap() -> None:
    client, patcher = _patched_client(*[_reply("STILL NOT JSON")] * (MAX_RETRIES + 1))
    raised = False
    with mock.patch.object(extract_mod, "_N_SAMPLES", 1), patcher as m:
        try:
            extract_invoice(_PDF, client=client)
        except InstructorRetryException:
            raised = True
    assert raised, "expected a clean InstructorRetryException after the retry cap"
    assert m.call_count == MAX_RETRIES + 1  # initial + MAX_RETRIES retries, bounded


if __name__ == "__main__":
    test_retry_then_success()
    test_clean_raise_after_cap()
    print("extract retry test OK")
