# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Greenfield. The repo currently holds only two planning docs — no code, no git repo yet:

- `PRD.md` — full product spec. §4 (2026 tech decisions), §6 (schema), §7 (stack), §8 (Claude API specifics) are the sections implementation depends on.
- `tasks.md` — the ordered, dependency-sequenced build plan (T1–T25). This is the source of truth for *what to build next and in what order*.

`git init` before the first commit (T1).

## Build workflow (from tasks.md)

One task per session. For each task:

1. Read the task in `tasks.md` + the PRD sections it references.
2. Enter plan mode; confirm approach against the PRD.
3. Implement that task and nothing beyond it (strict YAGNI — see scope rules below).
4. Run the task's **Verification** command — it must pass.
5. Have a fresh subagent review the diff against the PRD for scope creep / stack drift.
6. `git commit` (one task = one commit).
7. Tick the checkbox in `tasks.md`.

**Load the `claude-api` skill before writing any Anthropic/Claude code** — model IDs, pricing, and structured-output syntax move fast and must be re-verified at build time (don't trust PRD §8 verbatim).

## Scope guardrails

- **Invoices only** for the MVP. Do not build an "any document" tool or add doc types (receipts/resumes/bank statements) — those are Backlog.
- Anything in `tasks.md` **Backlog** (Supabase, review UI, async queue upgrade, Files API, Batch API, dual-LLM verify, security pack) stays out of the main sequence until the MVP is live.
- MVP job store is **in-memory** (a dict), not a database. Mark it `# ponytail: in-memory job dict; swap for Supabase in backlog when multi-instance`.
- No new dependency for what a few lines can do. The intended stack is fixed in PRD §7 — don't drift.

## Architecture (the big picture)

Async extraction pipeline, single thin thread that each task extends — no orphan layers:

```
POST /extract → INGEST → ROUTE+PARSE → EXTRACT (Claude) → VALIDATE → OUTPUT / HUMAN REVIEW
```

Intended module layout under `app/` (created task-by-task):
- `schema.py` — Pydantic `Field` / `LineItem` / `Invoice`. **Single source of truth**: derives the JSON Schema fed to Claude, the API response shape, and (later) the DB shape.
- `extract.py` — `extract_invoice(pdf_bytes) -> Invoice` via Claude Structured Outputs.
- `parse.py` — PyMuPDF native-text extraction (the cheap fast path) + OCR fallback.
- `route.py` — native-vs-scanned classifier; routes to the cheapest engine that works.
- `validate.py` — business rules (line items Σ = subtotal, subtotal + tax = total, dates→ISO, amounts ≥ 0).
- `confidence.py` — multi-signal per-field score → `review_required` threshold.
- `main.py` — FastAPI app.

Cross-cutting design rules that only make sense across several files:

- **Schema-first.** Every field is a confidence-carrying `Field` object (value/confidence/source_quote/page/review_required), not a bare value. Everything `Optional` — model absence as `None`, never force a required scalar that invites hallucination.
- **Hybrid parse-then-LLM, not vision-first.** Born-digital PDFs → extract native text (PyMuPDF), ~10–20× cheaper. Scanned → OCR to Markdown, then send Markdown **plus** page image. Vision-LLM is the last-resort fallback for the ~20% that fail parsing.
- **Confidence is a blended engine**, not "ask the model how sure it is": validation-pass + verbatim-in-source + self-consistency across samples → one score.
- **Citations and Structured Outputs are mutually exclusive in one Claude call** (both = HTTP 400). MVP uses self-cite (`source_quote` + `page` in the schema), one call. Two-pass citations is Backlog.
- **Model cascade:** default Haiku 4.5 → escalate to Sonnet 5 → Opus 4.8 only on low confidence. Bounded, no loops.
- **Reproducibility:** log the exact prompt, model id, raw output, and token usage per job. Never log the API key.

## Verification conventions

- Modules with non-trivial logic ship an assert-based `demo()`/`__main__` self-check; `python -m app.<module>` must exit 0.
- Eval scripts live under `eval/`; the golden subset runs in `pytest` / CI as a regression guard.
- Each task in `tasks.md` has an explicit **Verification** command — that is the gate, run it.
