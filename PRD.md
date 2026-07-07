# PRD — Document → Structured Data Extractor

**Project codename:** `doc-extractor`
**Owner:** Pavan Kushnure
**Status:** Draft v1 · 2026-07-07
**One-liner:** Upload a messy document (invoice, receipt, resume, bank statement) → get clean, validated, structured JSON back, with a source citation for every field.

This is both a **portfolio/proof project** to win freelance work and a **sellable product** for clients who need document data extraction (accounts-payable automation, onboarding, recruiting, finance ops).

---

## 1. Problem & goal

Businesses drown in semi-structured documents (PDFs, scans, photos) and pay humans to retype the data into spreadsheets and systems. That work is slow, error-prone, and expensive.

**Goal:** ship a service that takes a document and returns structured, schema-valid data with a confidence score and a source citation per field — accurate enough to auto-approve the easy 80% and route the uncertain 20% to a human.

**Definition of done (MVP):** a live URL where I can drag-drop an invoice PDF and get back correct JSON (vendor, date, line items, totals) in under ~15 seconds, with an accuracy report on a labeled test set to show clients.

---

## 2. Users & use cases

| User | Job to be done |
|---|---|
| Founder / ops team | "Turn our incoming invoices into rows in our system automatically." |
| Recruiter / HR tech | "Parse 500 resumes into structured candidate profiles." |
| Finance / accounting | "Extract transactions from bank statements into CSV." |
| Regulated client (later) | "Extract fields from medical/lab reports — but it must be HIPAA-safe." |

**Wedge:** nail **invoices** end-to-end first (high value, self-validating, easy to demo), then expand to receipts, resumes, bank statements. Do NOT build a shallow "any document" tool — one vertical done well with a real accuracy report beats ten done badly.

---

## 3. Scope

### MVP (v1 — the thing to ship first)
- Single document type: **invoices** (born-digital + scanned).
- `POST /extract` API + a minimal drag-drop web UI.
- Extraction to a fixed **invoice schema** (Pydantic), schema-validated JSON output.
- Per-field **confidence** + **source citation** (page + quote).
- Business-rule validation (line items sum to total, dates valid, amounts ≥ 0).
- A labeled **eval set** (~50–100 invoices) + a field-level accuracy report.
- Deployed live (Railway API + Vercel UI).

### v2 (after first client / first reviews)
- More doc types (receipts, resumes, bank statements) via pluggable schemas.
- Async job queue + webhook callback for bulk.
- Human-in-the-loop **review UI** (doc image + highlighted bbox, one-click correct).
- Batch processing (50% cheaper) for backfills.

### Out of scope (for now — YAGNI)
- Multi-tenant billing, SSO, org management.
- Training custom models. (Use frontier models + good schemas instead.)
- Every file format on earth. Start with PDF + image.

---

## 4. ⭐ LATEST TECH (2026) — what to use and why *(highlighted, from research)*

> This is the section that makes the project current instead of a 2023-era tutorial. Three findings drive the whole design.

### 🔑 Finding 1 — The winning pattern is **hybrid "parse-then-LLM"**, not "throw the PDF at a vision model"
The 2026 production consensus: **OCR/parse to Markdown first, then feed Markdown _plus_ the page image to a vision LLM under a strict schema.** The transcription gives the model reliable tokens; the image preserves layout and tables.

- **Born-digital PDFs** (most invoices/contracts): extract the native text layer directly — it's **faster, more accurate, AND ~10–20× cheaper** than sending page images to a vision model. Don't default to vision.
- **Scanned/image PDFs**: OCR to Markdown, then LLM.
- **Vision-LLM only as fallback** for the ~20% of docs where parsing fails (bad scans, weird layouts).
- **Build an intelligent router:** classify each doc (native vs scanned vs complex-table) → route to the cheapest engine that works → vision-LLM last resort. This is the single biggest cost/accuracy lever.

### 🔑 Finding 2 — Claude now reads PDFs natively and enforces JSON schemas at the token level
This collapses most of the build. (Full API detail in §8.)
- **Native PDF support** — send a `document` block (base64 / URL / Files API `file_id`). Each page is processed as **both text and image**, so tables/scans/charts work out of the box. Limits: 32 MB, 100 pages (200K models) / 600 pages (1M-context models).
- **Structured Outputs** via `output_config.format` with a JSON schema → **constrained decoding guarantees valid JSON** matching your Pydantic/Zod schema. No more regex-parsing model output.
- **Files API** — upload a doc once, run many extractions against the same `file_id`.
- **Prompt caching** — cache the schema + instructions prefix → **~90% cost cut** across many docs.
- **Batch API** — 50% off for bulk/backfill jobs.
- ⚠️ **Key constraint:** Claude's native **Citations** feature and **Structured Outputs are mutually exclusive in one call** (enabling both = HTTP 400). See §8 for the two workarounds.

### 🔑 Finding 3 — Confidence is a *multi-signal engine*, not "ask the model how sure it is"
Self-reported confidence and raw logprobs are poorly calibrated. 2026 best practice blends several signals per field:
1. token/logprob signal, 2. **self-consistency** (sample 2–3×, check agreement), 3. passes validation/business rules, 4. value found **verbatim** in the OCR text, 5. an **LLM-judge** cross-check.
Blend → one per-field score → threshold routes to auto-approve or human review. Published pipelines hit **~99% accuracy at ~80% auto-approval coverage** this way.

### Tooling shortlist (from research — pick per need)
| Need | First choice | Notes |
|---|---|---|
| OSS parser (self-host) | **Docling** (IBM, MIT) | Best OSS, ~98% table accuracy |
| Hosted OCR, best value | **Mistral OCR** | ~$1 / 1,000 pages |
| Agentic parse for RAG/tables | **LlamaParse** | 10k free credits/mo; preserves cell semantics |
| Fast raw text (native PDFs) | **PyMuPDF / pdfplumber** | The cheap fast-path |
| Structured LLM output | **Claude Structured Outputs** + **Instructor** + **Pydantic** | Instructor adds retries/self-heal |
| Field-level source grounding | **LangExtract** (or self-cite in schema) | provenance/citations |

**Cost anchor:** budget **~$1 per 1,000 pages** on the bulk path (Mistral OCR / LlamaParse fast / Claude Haiku); reserve vision-LLM (10–20× pricier) for the fallback only.

---

## 5. Architecture

```
Upload (PDF/img)
      │   FastAPI  POST /extract  (returns job_id; async)
      ▼
[1] INGEST         type/size/virus check · page count · store raw · create job row
      ▼
[2] ROUTE + PARSE  native-text? → fast path (PyMuPDF)
                   scanned/complex? → OCR to Markdown (Mistral OCR / Docling)
                   keep page image alongside Markdown
      ▼
[3] EXTRACT (LLM)  Claude + JSON Schema (Structured Outputs)
                   input = Markdown + page image
                   default model Haiku 4.5 → escalate on low confidence
                   return fields + per-field source quote + page
      ▼
[4] VALIDATE       Pydantic (types/enums/required)
                   business rules (line items Σ == total, dates, checksums)
                   multi-signal confidence per field
      ├── pass & high-confidence ─► [5] OUTPUT  JSON / DB / webhook / CSV
      └── fail or low-confidence ─► [6] HUMAN REVIEW QUEUE
                                     doc image + highlighted field · 1-click fix
                                     corrections → eval set + few-shot examples
                                     └─► back to [5]
```

**Production rules baked in:** extraction is an **async job** (queue + worker), not a blocking request; keep the **native-text fast path** to skip OCR/LLM cost; **log the exact prompt, model id, and raw output per job** for reproducibility.

---

## 6. Data model (schema-first)

One source of truth in **Pydantic** → derive JSON Schema → feed to Claude's structured output → also the API response and DB shape.

**Rules from research:**
- Everything **`Optional`**; model absence explicitly (`None` = not in doc; a separate flag = present-but-unreadable). Never force the model to hallucinate a required field.
- Wrap each field in a **confidence-carrying object**, not a bare value.
- `Literal`/`Enum` for closed sets (currency, doc type); constraints via description (Claude strips numeric/string JSON-schema constraints — see §8).

```python
# invoice schema sketch
class Field(BaseModel):
    value: str | float | None
    confidence: float           # 0..1, from the multi-signal engine
    source_quote: str | None    # verbatim text from the doc
    page: int | None
    review_required: bool = False

class LineItem(BaseModel):
    description: Field
    quantity: Field
    unit_price: Field
    amount: Field

class Invoice(BaseModel):
    vendor_name: Field
    invoice_number: Field
    invoice_date: Field         # normalized to ISO in validation
    currency: Field             # Literal["USD","EUR","INR",...]
    line_items: list[LineItem]
    subtotal: Field
    tax: Field
    total: Field
```

---

## 7. Tech stack (minimal, ships fast)

| Layer | Choice | Why |
|---|---|---|
| API | **FastAPI** | async, auto OpenAPI docs, Pydantic-native |
| Worker/queue | FastAPI BackgroundTasks (MVP) → arq/RQ (v2) | non-blocking extraction |
| Parse/OCR | PyMuPDF (fast path) + **Mistral OCR** or **Docling** (scans) | pluggable |
| Extraction LLM | **Claude** (Haiku 4.5 default → Sonnet 5 → Opus 4.8) | native PDF + structured output |
| Structured output | Claude Structured Outputs + **Instructor** + **Pydantic** | validated JSON + self-heal retries |
| DB + storage + auth | **Supabase** (Postgres + RLS + object storage + signed URLs) | one service does it all |
| UI | Next.js (or Streamlit/Gradio for a fast demo) on **Vercel** | drag-drop + JSON + review view |
| Deploy | **Railway** (API/worker, sleeps to \$0 idle) + Vercel (UI) | cheap, bursty-friendly |
| Eval/observability | log to Postgres + a notebook; golden set in CI | prove accuracy |

> Before writing any Anthropic-specific code, load the **`claude-api`** skill for current model IDs, pricing, and structured-output syntax.

---

## 8. Claude API specifics (implementation notes)

**Models & pricing** (per 1M tokens, in/out):
- **Haiku 4.5** `claude-haiku-4-5` — \$1 / \$5. Default for clean, templated docs. 200K context.
- **Sonnet 5** `claude-sonnet-5` — \$3 / \$15 (intro \$2 / \$10 through 2026-08-31). Best for variable-layout/scanned. 1M context, high-res vision.
- **Opus 4.8** `claude-opus-4-8` — \$5 / \$25. Hardest docs / fallback tier.
- **Model cascade:** default Haiku → escalate to Sonnet/Opus only on low confidence or complex layout.

**Native PDF:** `document` content block **before** the text block. Source = base64 / url / Files API `file_id`. 32 MB, 100 pages (200K) / 600 pages (1M). Each page = text + image (~1,500–3,000 tokens/page). Opus 4.7+/Sonnet 5 have high-res vision (≤2576px) — better on dense tables.

**Structured Outputs:** `output_config={"format":{"type":"json_schema","schema":{...,"additionalProperties":false,"required":[...]}}}`; or SDK helper `client.messages.parse(..., output_format=InvoiceModel)` → `response.parsed_output` is a validated instance. Constrained decoding = guaranteed valid JSON.
- Schema limits: no recursion; numeric (`minimum`/`maximum`) and string (`minLength`) constraints are stripped into descriptions; `additionalProperties:false` required; caps of 20 strict tools / 24 optional params. First use of a schema pays one-time grammar-compile latency, then cached 24h.

**Files API:** beta header `files-api-2025-04-14`. Upload once → reference `file_id` across many extraction calls. 500 MB/file. Keeps payloads under the 32 MB limit. (Not on Bedrock/Vertex.)

**Citations vs Structured Outputs (the key tension):** can't have both in one call. Two workarounds:
1. **Self-cite in schema (default for MVP):** include `source_quote` + `page` fields in the JSON schema — one call, cheap, "good enough" provenance.
2. **Two-pass (for high-stakes/regulated):** call #1 citations-enabled for grounded page/char locations; call #2 structured output for clean JSON.

**Prompt caching:** put `cache_control:{type:"ephemeral"}` on the stable prefix (instructions + schema + few-shot); per-doc content after the breakpoint. Cache reads ~0.1× input price → ~90% cut on the fixed prompt across many docs. Min cacheable prefix: 4096 tokens (Haiku 4.5 / Opus 4.8). Verify via `usage.cache_read_input_tokens`.

**Batch API:** 50% off, async, <1hr typical. Use for bulk backfills. Combines with caching.

**Robustness:** always check `stop_reason` — `max_tokens` (truncated JSON → raise `max_tokens`), `refusal` (won't match schema). Size `max_tokens` generously with structured output.

---

## 9. Validation, confidence & human-in-the-loop

- **Two-layer validation:** (a) structural — Pydantic parse; on failure re-prompt the model with the error (self-heal, cap 1–2 retries via Instructor). (b) semantic — regex on dates/IDs, arithmetic (line items Σ = subtotal; subtotal + tax = total), checksums (IBAN/routing).
- **Multi-signal confidence** (§4, Finding 3) → threshold routes auto-approve vs review. Tune the threshold on the eval set for target accuracy at acceptable review volume.
- **Review UX (v2):** doc image + bbox highlight on flagged fields only, value pre-filled, one-click accept/correct. **Every correction feeds the eval set + few-shot examples** — the "it learns from your corrections" flywheel (great demo line).
- Optional **dual-LLM verify** (one extracts, another checks) for high-stakes fields.

---

## 10. Evaluation (this is your client-facing proof)

- **Labeled ground-truth set from day one** — 50–200 docs/type spanning clean/scanned/edge cases. The most credible thing to show a client.
- **Field-level precision/recall/F1**, per field (not per doc). Report per field: "totals 99%, dates 92%." Dates are usually weakest.
- **Normalize before comparing** (dates→ISO, amounts→decimal) so equivalent values aren't scored wrong. Spot-check the scorer itself.
- **Business KPI = STP rate** (% docs needing zero human edits) + **coverage @ accuracy** ("80% auto-approved at 99% accuracy").
- **Golden set runs in CI** on every prompt/model change (regression guard). Weekly random-sample human scoring.

---

## 11. Security & compliance (unlocks regulated clients)

Your federal-contracting background is a selling point here — lead with it.
- **Zero Data Retention (ZDR)** with the LLM provider is the linchpin; sign a **BAA** (HIPAA) / **DPA** (GDPR). Be a documented sub-processor to the client.
- **Redact/tokenize PII/PHI** before the prompt where feasible; prefer a **stateless in-memory pass-through** — process and discard raw docs.
- **Retention/deletion:** short/zero retention of raw docs; auto-delete after processing or client TTL; expose a delete endpoint; store extractions/metadata, not raw PII.
- **Encryption everywhere** (TLS + at rest), signed time-limited URLs, no public buckets.
- **Access control + audit log** (Supabase RLS, per-tenant isolation, who-exported-what).
- **Advertise:** SOC 2, HIPAA (medical), GDPR (EU). A one-page security/data-flow doc + a DPA template often wins regulated buyers more than the accuracy number.

---

## 12. Cost model (rough)

- Bulk path (native text or Mistral OCR + Haiku): **~\$1 / 1,000 pages**.
- Vision-LLM fallback: 10–20× that — keep to the hard ~20%.
- Levers: native-text fast path, dedupe by document hash, prompt-cache the schema, model cascade, Batch API (−50%), per-user rate limits + monthly spend cap/alert on the LLM key.

---

## 13. Build plan / milestones

| Milestone | Deliverable | Target |
|---|---|---|
| **M0 — Proof (this week)** | Script: invoice PDF → validated JSON via Claude Structured Outputs, on 1 sample. README + 2 screenshots. Post to LinkedIn Featured. | Week 1 |
| M1 — Core extractor | FastAPI `POST /extract`, invoice Pydantic schema, native-text fast path, business-rule validation | Week 2 |
| M2 — Robustness | OCR fallback (Mistral/Docling), router, confidence engine, prompt caching | Week 3 |
| M3 — Proof of accuracy | 50–100 labeled invoices + field-level accuracy report + golden CI test | Week 4 |
| M4 — Ship it | Minimal UI, deploy (Railway + Vercel), live demo URL | Week 5 |
| M5 — Sellable | Review UI, 2nd doc type, security one-pager, case study write-up | Week 6+ |

**M0 is the "one step this week"** — everything else is optional until that exists.

---

## 14. Success metrics
- MVP: ≥95% field-level accuracy on clean invoices; ≥90% including scanned; <15s p50 latency.
- Business: a live demo URL + an accuracy report I can send to a prospect. First freelance conversation that references this project.

---

# 15. Ship it publicly — GitHub README & LinkedIn

The build only creates income if people can *see* it. Two concrete deliverables.

## 15a. GitHub README

Create a public repo `doc-extractor` and make the README the storefront — a stranger should grasp value in 20 seconds. Structure:

1. **Title + one-line pitch** — "Turn messy invoices into clean, validated JSON — with a source citation for every field."
2. **Hero demo** — a GIF or screenshot of drag-drop → JSON (top of the README; this is what recruiters/clients actually look at). Record with any screen-capture tool; keep it <10s.
3. **Badges** — Python version, license (MIT), "live demo" link.
4. **What it does** — 3–4 bullets: native PDF + scan support, schema-validated output, per-field confidence + citations, human-in-the-loop review.
5. **Live demo link** — the Railway/Vercel URL. A working link beats any description.
6. **How it works** — paste the §5 architecture diagram (renders as a code block). Shows senior thinking.
7. **The interesting engineering** — call out the 2026 choices: hybrid parse-then-LLM routing, Claude Structured Outputs (constrained decoding), multi-signal confidence, prompt caching for ~90% cost cut. This is what separates you from a tutorial clone.
8. **Accuracy report** — a small table (field-level precision/recall on your eval set). Proof, not claims.
9. **Quickstart** — `git clone` → `pip install -r requirements.txt` → set `ANTHROPIC_API_KEY` → `uvicorn app:main`. Must actually work on a clean machine.
10. **Tech stack** + **Roadmap** + **License (MIT)**.

**README checklist**
- [ ] Repo is **public**, has a clear name + description + topics (`llm`, `document-extraction`, `claude`, `fastapi`).
- [ ] Demo GIF/screenshot at the very top.
- [ ] Live demo link works (test in incognito).
- [ ] `.env.example` present; **no real API keys committed** (add `.gitignore`, scan before pushing).
- [ ] Quickstart works from a clean clone.
- [ ] Accuracy table included.
- [ ] Pin the repo on your GitHub profile.

*(Claude can draft the entire README from this PRD once M0 exists — ask.)*

## 15b. Add to LinkedIn

1. **Featured section** — add the repo + live demo link with the demo screenshot. This is the first thing profile visitors see; highest-leverage placement.
2. **Projects section** — add "AI Document Extractor," 2–3 lines: what it does + the stack (Claude, FastAPI, Supabase), link both GitHub and live demo.
3. **A build-in-public post** (drives inbound leads):
   > "Weekend build: an AI service that turns messy invoices into clean, validated JSON — with a source citation for every field. Uses the 2026 hybrid pattern (parse-then-LLM routing) + Claude structured outputs, hitting ~95% field accuracy at ~$1 per 1,000 pages. Live demo + code 👇 [links]. If your team retypes documents by hand, this replaces it — DM me."
4. **Update your headline/About** to reference it — moves you from "I code" to "I ship AI products," matching the freelance offer.
5. **Skills** — ensure *LLM Integration*, *AI Engineering*, *Document AI/OCR* are listed and pinned.

**LinkedIn checklist**
- [ ] Project in Featured (with image + both links).
- [ ] Project entry in Projects section.
- [ ] One build-in-public post published.
- [ ] Skills updated + pinned.
- [ ] Same one-line pitch as GitHub (consistent story across platforms).

---

*This PRD folds in mid-2026 research on Claude's document/vision API, the OSS/hosted parser landscape, and production extraction architecture. Revisit §4 and §8 before each milestone — the model IDs, pricing, and API shapes move fast; re-check the `claude-api` skill and official docs at build time.*
