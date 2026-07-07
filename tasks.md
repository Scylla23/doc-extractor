# tasks.md — doc-extractor build

Ordered, atomic, dependency-sequenced task list for the **invoices-first MVP** defined in `PRD.md`.

## How to use this file

Work **one task per fresh Claude Code session**:

1. Open a new session, read the task + the PRD sections it references.
2. **Plan mode** first — confirm approach against `PRD.md` (esp. §4, §7, §8; load the `claude-api` skill before any Anthropic code).
3. Implement the task and nothing beyond it.
4. Run the task's **Verification** command/check — it must pass.
5. Have a **fresh subagent review the diff** against `PRD.md` (scope creep, stack drift, YAGNI).
6. `git commit` (one task = one commit).
7. Tick the box below, move to the next task.

Rules baked in: MVP is **invoices only**. Everything runs the same thin thread the earlier tasks built — no orphan layers. Persistent infra (Supabase), review UI, extra doc types, async queue upgrades → **Backlog**, not the main sequence. MVP job store is **in-memory / local** (`# ponytail: in-memory job dict; swap for Supabase in backlog when multi-instance`).

---

## Checklist index

- [x] T1 Project scaffold, deps, env, gitignore
- [x] T2 Invoice Pydantic schema (Field / LineItem / Invoice)
- [x] T3 Walking skeleton: PDF → Claude Structured Outputs → validated JSON
- [x] T4 Sample invoice + M0 README & screenshots
- [x] T5 FastAPI `POST /extract` wrapping the extractor
- [x] T6 PyMuPDF native-text fast path
- [x] T7 Business-rule validation layer
- [x] T8 Instructor self-heal retries on schema failure
- [x] T9 Async job (BackgroundTasks) + `job_id` + `GET /jobs/{id}`
- [x] T10 Per-job logging (prompt, model id, raw output)
- [x] T11 Doc router: native vs scanned classifier
- [x] T12 Mistral OCR fallback → Markdown
- [x] T13 Feed Markdown + page image to Claude
- [x] T14 Multi-signal confidence engine (per field)
- [ ] T15 Model cascade escalation on low confidence
- [ ] T16 Prompt caching on schema/instructions prefix
- [ ] T17 Labeled eval set (~50–100 invoices + ground-truth JSON)
- [ ] T18 Field-level scorer with normalization
- [ ] T19 Accuracy report (precision/recall/F1 per field + STP)
- [ ] T20 Golden-set CI regression test
- [ ] T21 Minimal drag-drop web UI
- [ ] T22 Wire UI to `/extract` + render JSON
- [ ] T23 Deploy API to Railway
- [ ] T24 Deploy UI to Vercel
- [ ] T25 Storefront README (hero demo, badges, accuracy table, quickstart)

---

# M0 — Proof (walking skeleton)

## T1 Project scaffold, deps, env, gitignore
- **Depends on:** none
- **Parallelizable with:** none
- **Estimate:** 1h
- **Milestone:** M0
- **Description:** Create the repo skeleton: `requirements.txt` (anthropic, pydantic, python-dotenv, pymupdf, fastapi, uvicorn, instructor), `.env.example` with `ANTHROPIC_API_KEY=`, `.gitignore`, empty `app/` package.
- **Acceptance criteria:**
  - `pip install -r requirements.txt` succeeds in a clean venv.
  - `.env.example` exists; `.env`, `__pycache__`, `*.pdf` (samples dir excepted) are gitignored.
  - No real API key is committed anywhere.
- **Verification:** `python -c "import anthropic, pydantic, fitz, fastapi, instructor"` exits 0 and `git status` shows no `.env`.

## T2 Invoice Pydantic schema (Field / LineItem / Invoice)
- **Depends on:** T1
- **Parallelizable with:** none
- **Estimate:** 2h
- **Milestone:** M0
- **Description:** Implement `app/schema.py` exactly per PRD §6 — `Field` (value/confidence/source_quote/page/review_required), `LineItem`, `Invoice`. Everything `Optional`; `currency` a `Literal` of common codes. Derive the JSON Schema with `additionalProperties:false`.
- **Acceptance criteria:**
  - `Invoice.model_json_schema()` returns a dict; every object node can be made strict (`additionalProperties:false`, `required` listed).
  - No required scalar fields that would force hallucination — absence expressible as `None`.
  - A `demo()`/`__main__` asserts an example dict parses into `Invoice`.
- **Verification:** `python -m app.schema` runs the self-check and exits 0.

## T3 Walking skeleton: PDF → Claude Structured Outputs → validated JSON
- **Depends on:** T2
- **Parallelizable with:** none
- **Estimate:** 3h
- **Milestone:** M0
- **Description:** `app/extract.py` with `extract_invoice(pdf_bytes) -> Invoice`: send the PDF as a base64 `document` block + instruction, use Claude Structured Outputs (Haiku 4.5 default) bound to the `Invoice` schema, return the parsed instance. Crudest end-to-end thread — native-PDF, no OCR, no routing yet. Check `stop_reason`.
- **Acceptance criteria:**
  - Running against one sample invoice PDF returns a valid `Invoice` instance.
  - Output includes vendor, invoice number, date, ≥1 line item, total.
  - Raises a clear error if `stop_reason == "max_tokens"`.
- **Verification:** `python -m app.extract samples/sample1.pdf` prints valid `Invoice` JSON with populated `total`.

## T4 Sample invoice + M0 README & screenshots
- **Depends on:** T3
- **Parallelizable with:** T5, T6
- **Estimate:** 1h
- **Milestone:** M0
- **Description:** Add 1 sample invoice PDF under `samples/`, a short `README.md` (what/why + how to run the M0 script), and 2 screenshots of the script output. This is the LinkedIn-Featured M0 deliverable.
- **Acceptance criteria:**
  - `samples/sample1.pdf` committed; no PII/real customer data.
  - README documents the exact command from T3 and shows expected JSON.
  - 2 screenshots committed under `docs/`.
- **Verification:** Follow the README from a clean clone → the documented command produces the shown JSON.

---

# M1 — Core extractor

## T5 FastAPI `POST /extract` wrapping the extractor
- **Depends on:** T3
- **Parallelizable with:** T4, T6
- **Estimate:** 2h
- **Milestone:** M1
- **Description:** `app/main.py` FastAPI app exposing `POST /extract` that accepts a multipart PDF upload, calls `extract_invoice`, returns the `Invoice` JSON. Reject non-PDF / oversized (>32 MB) with 4xx. Sync for now.
- **Acceptance criteria:**
  - `uvicorn app.main:app` serves `/docs` (auto OpenAPI).
  - `POST /extract` with a PDF returns 200 + JSON matching the `Invoice` schema.
  - Non-PDF or >32 MB upload returns a 4xx with a clear message.
- **Verification:** `curl -F file=@samples/sample1.pdf localhost:8000/extract` returns valid `Invoice` JSON.

## T6 PyMuPDF native-text fast path
- **Depends on:** T3
- **Parallelizable with:** T4, T5
- **Estimate:** 3h
- **Milestone:** M1
- **Description:** Add `app/parse.py` extracting the native text layer via PyMuPDF. Refactor `extract_invoice` to send extracted **Markdown/text** (cheap fast path per §4 Finding 1) instead of the raw PDF image when a usable text layer exists.
- **Acceptance criteria:**
  - Born-digital sample extracts text without OCR.
  - Token usage on the sample drops vs T3's image path (log `usage.input_tokens`).
  - Extraction accuracy on the sample is unchanged or better.
- **Verification:** `python -m app.extract samples/sample1.pdf` still returns valid `Invoice`; logged input tokens are lower than T3.

## T7 Business-rule validation layer
- **Depends on:** T2, T3
- **Parallelizable with:** T5, T6, T8
- **Estimate:** 3h
- **Milestone:** M1
- **Description:** `app/validate.py`: given an `Invoice`, check semantic rules — Σ(line item amounts) ≈ subtotal, subtotal + tax ≈ total (tolerance for rounding), dates normalize to ISO, amounts ≥ 0. Return a list of rule violations per field.
- **Acceptance criteria:**
  - Passing invoice → empty violation list.
  - Tampered total (off by $10) → a `total` violation reported.
  - Non-ISO date is normalized or flagged.
  - Self-check with assert-based `demo()` covering pass + each failure.
- **Verification:** `python -m app.validate` self-check exits 0.

## T8 Instructor self-heal retries on schema failure
- **Depends on:** T3
- **Parallelizable with:** T5, T6, T7
- **Estimate:** 2h
- **Milestone:** M1
- **Description:** Wrap the extraction call with Instructor (cap 1–2 retries) so a Pydantic/structural failure re-prompts the model with the validation error rather than crashing (§9).
- **Acceptance criteria:**
  - On a forced schema mismatch, the call retries and either succeeds or fails cleanly after the cap.
  - Retry count is bounded (≤2) and logged.
- **Verification:** Unit test injecting a bad first response asserts a retry occurs and final result is a valid `Invoice` (or a clean raised error).

## T9 Async job (BackgroundTasks) + `job_id` + `GET /jobs/{id}`
- **Depends on:** T5
- **Parallelizable with:** T7, T8
- **Estimate:** 3h
- **Milestone:** M1
- **Description:** Make `/extract` non-blocking per §5: enqueue via FastAPI BackgroundTasks, return `{job_id, status}` immediately; add `GET /jobs/{job_id}` returning status + result. In-memory job store (`# ponytail: in-memory dict; Supabase in backlog`).
- **Acceptance criteria:**
  - `POST /extract` returns a `job_id` and `status: queued/processing` quickly.
  - `GET /jobs/{id}` transitions to `done` with the `Invoice` result.
  - Unknown `job_id` → 404.
- **Verification:** `curl` POST returns `job_id`; polling `GET /jobs/{id}` eventually returns `status:"done"` + valid JSON.

## T10 Per-job logging (prompt, model id, raw output)
- **Depends on:** T9
- **Parallelizable with:** T7, T8
- **Estimate:** 1h
- **Milestone:** M1
- **Description:** Log the exact prompt, model id, and raw model output per job to a local file/JSONL for reproducibility (§5 production rule).
- **Acceptance criteria:**
  - Each job writes one log record with prompt, model id, raw output, token usage.
  - No API key present in logs.
- **Verification:** After one extraction, the log file contains a record with `model`, `prompt`, `raw_output` keys.

---

# M2 — Robustness

## T11 Doc router: native vs scanned classifier
- **Depends on:** T6
- **Parallelizable with:** T14, T16
- **Estimate:** 2h
- **Milestone:** M2
- **Description:** `app/route.py`: decide per doc whether a usable native text layer exists (PyMuPDF char count / coverage heuristic). Native → fast path; else → OCR path (built next). Route to cheapest engine that works (§4 Finding 1).
- **Acceptance criteria:**
  - Born-digital sample classified `native`.
  - A scanned/image-only sample classified `scanned`.
  - Decision + reason logged per job.
- **Verification:** `python -m app.route samples/sample1.pdf samples/scanned1.pdf` prints `native` then `scanned`.

## T12 Mistral OCR fallback → Markdown
- **Depends on:** T11
- **Parallelizable with:** T14, T16
- **Estimate:** 3h
- **Milestone:** M2
- **Description:** For `scanned` docs, OCR to Markdown via Mistral OCR (per §4/§7 shortlist). Add `MISTRAL_API_KEY` to `.env.example`. Return Markdown + page images.
- **Acceptance criteria:**
  - Scanned sample produces non-empty Markdown.
  - Cost/time logged; falls back gracefully with a clear error if OCR fails.
- **Verification:** `python -m app.parse --ocr samples/scanned1.pdf` prints Markdown containing recognizable invoice fields.

## T13 Feed Markdown + page image to Claude
- **Depends on:** T12
- **Parallelizable with:** T16
- **Estimate:** 2h
- **Milestone:** M2
- **Description:** For the OCR path, send **Markdown + the page image** together to Claude under the schema (hybrid parse-then-LLM, §4 Finding 1). Wire router output into `extract_invoice`.
- **Acceptance criteria:**
  - Scanned sample end-to-end returns a valid `Invoice`.
  - Both text and image blocks are present in the request payload.
- **Verification:** `python -m app.extract samples/scanned1.pdf` returns valid `Invoice` with populated total.

## T14 Multi-signal confidence engine (per field)
- **Depends on:** T7, T13
- **Parallelizable with:** T16
- **Estimate:** 4h
- **Milestone:** M2
- **Description:** `app/confidence.py` blending signals per §4 Finding 3: passes validation, value found **verbatim** in OCR/text, self-consistency (2–3 samples agree). Produce one 0–1 score per field and set `review_required` against a threshold.
- **Acceptance criteria:**
  - Each `Field` gets a `confidence` in [0,1] and a `review_required` flag.
  - A field failing validation or absent from source text scores lower than a clean verbatim match.
  - Threshold is a single tunable constant.
  - Assert-based `demo()` covers a high- and low-confidence field.
- **Verification:** `python -m app.confidence` self-check exits 0; extraction output shows varied per-field confidence.

## T15 Model cascade escalation on low confidence
- **Depends on:** T14
- **Parallelizable with:** T16
- **Estimate:** 2h
- **Milestone:** M2
- **Description:** Default Haiku 4.5; if overall/critical-field confidence is low, re-run on Sonnet 5 (then Opus 4.8) per §8 cascade. Log which tier produced the final result.
- **Acceptance criteria:**
  - Clean doc stays on Haiku (no escalation).
  - A forced low-confidence result triggers one escalation and logs the tier.
  - Escalation is bounded (Haiku→Sonnet→Opus, no loops).
- **Verification:** Test with a low-confidence stub asserts exactly one escalation and a logged `model_used`.

## T16 Prompt caching on schema/instructions prefix
- **Depends on:** T3
- **Parallelizable with:** T11, T12, T13, T14, T15
- **Estimate:** 2h
- **Milestone:** M2
- **Description:** Put `cache_control:{type:"ephemeral"}` on the stable prefix (instructions + schema + any few-shot); per-doc content after the breakpoint (§8). Verify cache hits.
- **Acceptance criteria:**
  - First call writes cache; second call reports `usage.cache_read_input_tokens > 0`.
  - Prefix ≥ 4096 tokens (min cacheable) or documented why not.
- **Verification:** Run two extractions; assert the second logs non-zero `cache_read_input_tokens`.

---

# M3 — Proof of accuracy

## T17 Labeled eval set (~50–100 invoices + ground-truth JSON)
- **Depends on:** T2
- **Parallelizable with:** T5–T16 (can start any time after schema)
- **Estimate:** 4h
- **Milestone:** M3
- **Description:** Assemble `eval/` with 50–100 invoices spanning clean/scanned/edge cases, each with a hand-labeled ground-truth JSON matching the `Invoice` schema (§10). Use public/synthetic invoices — no real PII.
- **Acceptance criteria:**
  - ≥50 invoice PDFs + one ground-truth JSON each.
  - Ground-truth validates against the `Invoice` schema.
  - Mix documented (N clean / N scanned / N edge).
- **Verification:** `python eval/validate_labels.py` confirms every ground-truth file parses as `Invoice`.

## T18 Field-level scorer with normalization
- **Depends on:** T17
- **Parallelizable with:** T21
- **Estimate:** 3h
- **Milestone:** M3
- **Description:** `eval/score.py` comparing extraction vs ground truth **per field** with normalization first (dates→ISO, amounts→decimal) per §10. Compute precision/recall/F1 per field.
- **Acceptance criteria:**
  - Equivalent-but-differently-formatted values (e.g. `$1,000.00` vs `1000.0`) score as matches.
  - Outputs per-field precision/recall/F1.
  - Scorer itself spot-checked with an assert-based case.
- **Verification:** `python eval/score.py` prints a per-field metrics table; self-check assertions pass.

## T19 Accuracy report (precision/recall/F1 per field + STP)
- **Depends on:** T18
- **Parallelizable with:** none
- **Estimate:** 2h
- **Milestone:** M3
- **Description:** Run the full extractor over the eval set and render a report: per-field metrics table + STP rate + coverage@accuracy (§10). Save as Markdown for the README.
- **Acceptance criteria:**
  - `eval/report.md` generated with a per-field table and headline STP number.
  - Report reproducible from one command.
- **Verification:** `python eval/run_report.py` produces `eval/report.md` with populated per-field rows.

## T20 Golden-set CI regression test
- **Depends on:** T18
- **Parallelizable with:** T21
- **Estimate:** 2h
- **Milestone:** M3
- **Description:** A small golden subset test (`pytest`) that runs on every prompt/model change and fails if per-field accuracy drops below a set floor (§10 regression guard). Add a GitHub Actions workflow.
- **Acceptance criteria:**
  - `pytest` runs the golden subset and asserts accuracy ≥ threshold.
  - CI workflow runs the test on push.
  - Uses a small subset (cost-bounded), not the full set.
- **Verification:** `pytest eval/test_golden.py` passes; CI shows green on a push.

---

# M4 — Ship it

## T21 Minimal drag-drop web UI
- **Depends on:** none
- **Parallelizable with:** T18, T20
- **Estimate:** 3h
- **Milestone:** M4
- **Description:** Minimal drag-drop upload page (Next.js, or Streamlit/Gradio for speed per §7) — file drop zone + a "Extract" button + a JSON output panel. Static/local first.
- **Acceptance criteria:**
  - Page renders a drag-drop zone and result panel locally.
  - Accepts a PDF and shows a placeholder/loading state.
- **Verification:** Run the UI locally; dropping a PDF shows the upload + loading state.

## T22 Wire UI to `/extract` + render JSON
- **Depends on:** T21, T5
- **Parallelizable with:** none
- **Estimate:** 2h
- **Milestone:** M4
- **Description:** Connect the UI to `POST /extract` + `GET /jobs/{id}`, poll to completion, render the returned `Invoice` JSON (and per-field confidence) in the panel.
- **Acceptance criteria:**
  - Dropping a real invoice returns and renders valid JSON end-to-end.
  - Errors (bad file, server error) shown to the user, not silent.
- **Verification:** Local UI + local API: drop `samples/sample1.pdf` → rendered `Invoice` JSON with totals.

## T23 Deploy API to Railway
- **Depends on:** T15, T10
- **Parallelizable with:** T21
- **Estimate:** 3h
- **Milestone:** M4
- **Description:** Deploy the FastAPI app/worker to Railway with env vars (`ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`). Confirm the full pipeline runs in the cloud.
- **Acceptance criteria:**
  - Public API URL responds on `/docs`.
  - `POST /extract` against the live URL returns a `job_id` and completes.
  - Secrets set as Railway env vars, not in the repo.
- **Verification:** `curl -F file=@samples/sample1.pdf https://<railway-url>/extract` → job completes with valid JSON.

## T24 Deploy UI to Vercel
- **Depends on:** T22, T23
- **Parallelizable with:** none
- **Estimate:** 2h
- **Milestone:** M4
- **Description:** Deploy the UI to Vercel pointed at the live Railway API URL. This is the demo link.
- **Acceptance criteria:**
  - Public UI URL loads.
  - Drag-drop → JSON works against the live API from the deployed UI.
  - Works in an incognito window (no local state).
- **Verification:** In incognito, load the Vercel URL, drop an invoice, see valid JSON in <~15s.

## T25 Storefront README (hero demo, badges, accuracy table, quickstart)
- **Depends on:** T19, T23, T24
- **Parallelizable with:** none
- **Estimate:** 3h
- **Milestone:** M4
- **Description:** Rewrite `README.md` as the storefront per §15a: one-line pitch, hero demo GIF, badges, "what it does", live demo link, §5 architecture block, the 2026 engineering call-outs, the accuracy table (from T19), working quickstart, tech stack, roadmap, MIT license.
- **Acceptance criteria:**
  - Hero demo GIF/screenshot at the top; live demo link works in incognito.
  - Accuracy table pulled from `eval/report.md`.
  - `.env.example` present, no real keys; quickstart works from a clean clone.
- **Verification:** Fresh clone → follow quickstart → `uvicorn app.main:app` serves and extracts; every README link resolves.

---

# Backlog (out of MVP scope)

v2 / later per PRD §3, §9, §11, §15b — **do not** pull into the main sequence until the MVP above is live.

- **Persistent storage (Supabase):** Postgres job/result rows, object storage for raw docs, RLS, signed URLs — replaces the in-memory MVP job store.
- **Files API integration:** upload once → reuse `file_id` across calls; keeps payloads under 32 MB (§8).
- **Human-in-the-loop review UI:** doc image + bbox highlight on flagged fields, one-click accept/correct; corrections feed eval set + few-shot (§9, M5).
- **Second doc type:** receipts (then resumes, bank statements) via pluggable schemas (§3 v2).
- **Async queue upgrade:** arq/RQ worker + webhook callback for bulk (§3, §7).
- **Batch API:** 50% off bulk backfills, combined with caching (§8, §12).
- **Two-pass citations:** citations-enabled call #1 + structured-output call #2 for regulated/high-stakes provenance (§8).
- **Dual-LLM verify:** one extracts, another checks high-stakes fields (§9).
- **Docling** as a self-host OCR alternative to Mistral (§4 shortlist).
- **Cost controls:** dedupe by document hash, per-user rate limits, monthly spend cap + alert on the LLM key (§12).
- **Security/compliance pack:** ZDR + BAA/DPA, PII redaction, retention/delete endpoint, audit log, one-page security/data-flow doc (§11).
- **LinkedIn launch:** Featured + Projects entries, build-in-public post, skills update (§15b).
- **Model IDs / pricing re-check:** revisit §4 and §8 and reload the `claude-api` skill before each milestone.
