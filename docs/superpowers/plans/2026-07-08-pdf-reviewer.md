# PDF Reviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After an invoice is extracted, show the uploaded PDF on the left and an editable cited-field ledger on the right; clicking a citation flashes its page, selecting PDF text sets an existing field or adds a new one, and edits save back to the API.

**Architecture:** Grow the existing static storefront (`web/`). `body[data-state]` gains a `review` view: a two-pane split rendered by a new `web/review.js` using pdf.js (CDN, ESM). The PDF renders from the local `File` the user dropped — no server round-trip. Corrections are held in a client-side record object and PATCHed to a new `corrected` blob on the in-memory job. `app.js` hands off to the reviewer on extraction success.

**Tech Stack:** Vanilla JS (no bundler), pdf.js v4 (CDN ESM build), FastAPI, Pydantic, existing in-memory job dict.

## Global Constraints

- **Invoices only.** No new doc types, no schema fields on `Invoice`/`Field`. (CLAUDE.md scope)
- **In-memory job store.** Corrections live in `_jobs` and are lost on restart — mark with a `# ponytail:` comment naming the ceiling. (CLAUDE.md)
- **No new backend dependency.** pdf.js is a browser CDN script, not a Python package. (PRD §7 stack is fixed)
- **`Invoice` and `Field` are `extra="forbid"`.** Do not add `manual`, `custom_fields`, or any key to them — corrections are stored as a free-form `corrected: Optional[dict]` on `Job`.
- **Module self-checks are the test convention.** Non-trivial backend logic ships an assert-based `demo()` run under `if __name__ == "__main__":`; `python -m app.<module>` must exit 0. Frontend is vanilla with no test harness — verify via the manual checklist in each task.
- **API base is config.** The UI reads `window.API_BASE` from `web/config.js`; never hardcode a URL in `app.js`/`review.js`.
- **pdf.js API churns across versions.** Before writing any pdf.js code (Task 2), pull current docs via context7 (`resolve-library-id` → `query-docs` for "pdfjs-dist render page and text layer, ESM CDN worker setup"). Do not trust a pinned snippet in this plan verbatim — verify the render + text-layer API against current docs.

---

## File Structure

- `app/main.py` — **modify**: add `corrected` to `Job`, add `PATCH /jobs/{job_id}/result`, add a `demo()` self-check.
- `web/index.html` — **modify**: add the two-pane review container, reviewer controls (Save / Download / new-file), and the pdf.js CDN module script; load `review.js`.
- `web/styles.css` — **modify**: `data-state="review"` split layout, page-flash keyframe, selection popover, custom-fields group, manual-field styling.
- `web/review.js` — **create**: `enterReview(file, record)` — pdf.js render, citation flash, inline edit, selection popover, save/download.
- `web/app.js` — **modify**: on job `done`, call `enterReview(theFile, job.result)` instead of the inline `renderResult`; export/expose the shared field-row builder for reuse.

---

## Task 1: Backend — `PATCH /jobs/{job_id}/result`

**Files:**
- Modify: `app/main.py`

**Interfaces:**
- Produces: `PATCH /jobs/{job_id}/result` accepting a JSON object body (the corrected record, free-form) → stores it on `Job.corrected` and returns the updated `Job`. `GET /jobs/{job_id}` now also returns `corrected`.
- Produces: `Job.corrected: Optional[dict]` — the client-corrected record, or `None`.

- [ ] **Step 1: Add `corrected` to the `Job` model**

In `app/main.py`, add the field to `Job` (after `error`):

```python
class Job(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "done", "error"]
    result: Optional[Invoice] = None
    error: Optional[str] = None
    # ponytail: free-form corrected record from the reviewer UI; kept off the
    # strict Invoice schema so manual markers + custom_fields round-trip.
    corrected: Optional[dict] = None
```

- [ ] **Step 2: Add the PATCH route**

Add after `get_job`:

```python
from fastapi import Body  # add to the existing fastapi import line


@app.patch("/jobs/{job_id}/result", response_model=Job)
def patch_result(job_id: str, corrected: dict = Body(...)) -> Job:
    """Store the reviewer's corrected record. Free-form: it carries manual
    markers and custom_fields that the strict Invoice schema forbids."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job_id.")
    if job.status != "done":
        raise HTTPException(status_code=409, detail="Job is not done yet.")
    # ponytail: in-memory; correction lost on restart — same ceiling as _jobs.
    job.corrected = corrected
    return job
```

- [ ] **Step 3: Add a `demo()` self-check**

Append to `app/main.py`:

```python
def demo() -> None:
    """Self-check for the PATCH round-trip (extraction is not exercised)."""
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Unknown job -> 404
    assert client.patch("/jobs/nope/result", json={}).status_code == 404

    # Seed a job that is still processing -> PATCH 409
    _jobs["j1"] = Job(job_id="j1", status="processing")
    assert client.patch("/jobs/j1/result", json={"x": 1}).status_code == 409

    # A done job accepts a free-form correction (manual marker + custom_fields)
    _jobs["j2"] = Job(job_id="j2", status="done", result=Invoice())
    record = {
        "vendor_name": {"value": "Acme", "manual": True},
        "custom_fields": {"po_number": {"value": "PO-9", "manual": True, "page": 2}},
    }
    r = client.patch("/jobs/j2/result", json=record)
    assert r.status_code == 200, r.text
    assert r.json()["corrected"] == record

    # GET now returns the correction alongside result
    got = client.get("/jobs/j2/result".replace("/result", "")).json()
    assert got["corrected"]["custom_fields"]["po_number"]["value"] == "PO-9"

    _jobs.clear()
    print("main demo OK")


if __name__ == "__main__":
    demo()
```

- [ ] **Step 4: Run the self-check**

Run: `.venv/bin/python -m app.main`
Expected: exits 0, prints `main demo OK`.

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "feat(api): PATCH /jobs/{id}/result stores reviewer corrections"
```

---

## Task 2: Two-pane shell + PDF render

Deliverable: after extraction, the page switches to a split view — the dropped PDF renders (all pages, selectable text) on the left, the existing read-only ledger on the right.

**Files:**
- Modify: `web/index.html`, `web/styles.css`, `web/app.js`
- Create: `web/review.js`

**Interfaces:**
- Consumes (from `app.js`): the raw `File` object and `job.result` (the Invoice record).
- Produces: `window.enterReview(file, record)` — global entry point defined in `review.js`.
- Produces: `window.buildLedger(record, opts)` — returns a `DocumentFragment` of the field rows (extracted from `app.js`'s current `renderResult`), reused read-only here and editable in Task 4.

- [ ] **Step 0: Pull current pdf.js docs**

Before writing pdf.js code, use context7: `resolve-library-id` for "pdfjs-dist", then `query-docs` for "render all pages to canvas, render text layer, set GlobalWorkerOptions.workerSrc, ESM build from CDN". Confirm the v4 ESM CDN URLs resolve (cdnjs `pdf.min.mjs` + `pdf.worker.min.mjs`) — bump the version in the URLs if a request 404s.

- [ ] **Step 1: Add the review container + pdf.js script to `index.html`**

In `web/index.html`, inside `<main>` after the `#result` section, add the review view:

```html
      <!-- Review view: shown when body[data-state="review"]. Populated by review.js. -->
      <section class="review" id="review" hidden>
        <div class="review-bar">
          <button type="button" class="btn" id="newFileBtn">← New file</button>
          <span class="review-status" id="reviewStatus" aria-live="polite"></span>
          <span class="review-actions">
            <button type="button" class="btn" id="downloadBtn">Download JSON</button>
            <button type="button" class="btn btn-primary" id="saveBtn">Save corrections</button>
          </span>
        </div>
        <div class="review-split">
          <div class="pdf-pane" id="pdfPane"></div>
          <div class="data-pane" id="dataPane"></div>
        </div>
      </section>
```

Replace the two `<script>` tags at the end of `<body>` with (note `type="module"` on review.js so it can `import` pdf.js):

```html
  <script src="config.js"></script>
  <script src="app.js"></script>
  <script type="module" src="review.js"></script>
```

- [ ] **Step 2: Extract the shared ledger builder from `app.js`**

In `web/app.js`, keep `money`, `val`, `fieldRow`, and `group` but refactor `renderResult` so the row-building is reusable. Replace `renderResult` with a `buildLedger` that returns a fragment, and expose the helpers on `window`:

```javascript
function buildLedger(inv) {
  inv = inv || {};
  const frag = document.createDocumentFragment();

  frag.append(
    group("Invoice", [
      fieldRow("Vendor", inv.vendor_name),
      fieldRow("Invoice №", inv.invoice_number),
      fieldRow("Date", inv.invoice_date),
      fieldRow("Currency", inv.currency),
    ])
  );

  const items = inv.line_items || [];
  if (items.length) {
    const rows = items.map((it, i) => {
      const desc = val(it.description);
      const qty = it.quantity && it.quantity.value != null ? `${val(it.quantity)} × ` : "";
      const unit = it.unit_price && it.unit_price.value != null ? ` @ ${money(it.unit_price)}` : "";
      return fieldRow(`Item ${i + 1}`, it.amount, `${qty}${desc}${unit} = ${money(it.amount)}`);
    });
    frag.append(group(`Line items (${items.length})`, rows));
  }

  frag.append(
    group("Totals", [
      fieldRow("Subtotal", inv.subtotal, money(inv.subtotal)),
      fieldRow("Tax", inv.tax, money(inv.tax)),
      fieldRow("Total", inv.total, money(inv.total)),
    ])
  );
  return frag;
}

// Expose for review.js (loaded as a module).
window.buildLedger = buildLedger;
window.ledgerHelpers = { money, val, fieldRow, group };
```

- [ ] **Step 3: Hand off to the reviewer on success**

In `web/app.js`, `handleFile` must remember the current file, and the poll loop must open the reviewer instead of rendering inline. Add a module-level `let currentFile = null;`, set it in `handleFile` (`currentFile = file;`), and in `extractFile` replace the `done` branch:

```javascript
      if (job.status === "done") {
        window.enterReview(currentFile, job.result);
        return;
      }
```

Remove the now-unused `renderResult` call. (Leave the `#result` section in the DOM; it is simply unused now.)

- [ ] **Step 4: Create `web/review.js` — PDF render + enterReview**

```javascript
// doc-extractor reviewer — two-pane PDF + editable ledger.
// pdf.js is loaded from CDN as an ESM module. VERIFY these URLs against current
// pdf.js docs (context7) at build time; bump the version if a request 404s.
import * as pdfjsLib from "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf.min.mjs";
pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf.worker.min.mjs";

const body = document.body;
const pdfPane = document.getElementById("pdfPane");
const dataPane = document.getElementById("dataPane");

let record = null; // the working (correctable) record

async function enterReview(file, extracted) {
  record = structuredClone(extracted || {});
  body.dataset.state = "review";
  document.getElementById("review").hidden = false;
  renderLedger();
  await renderPdf(file);
}

async function renderPdf(file) {
  pdfPane.innerHTML = "";
  try {
    const buf = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument({ data: buf }).promise;
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n);
      const viewport = page.getViewport({ scale: 1.4 });

      const wrap = document.createElement("div");
      wrap.className = "pdf-page";
      wrap.dataset.page = String(n);
      wrap.style.width = `${viewport.width}px`;
      wrap.style.height = `${viewport.height}px`;

      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      wrap.append(canvas);

      // Text layer: positioned spans so browser selection works over the page.
      const textLayer = document.createElement("div");
      textLayer.className = "textLayer";
      wrap.append(textLayer);

      pdfPane.append(wrap);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;

      // VERIFY the text-layer API against current pdf.js docs — v4 uses the
      // TextLayer class; older builds use renderTextLayer(). Example (v4):
      const textContent = await page.getTextContent();
      const tl = new pdfjsLib.TextLayer({ textContentSource: textContent, container: textLayer, viewport });
      await tl.render();
    }
  } catch (err) {
    pdfPane.innerHTML =
      '<p class="pdf-error">Couldn’t render the PDF. The data on the right is still editable.</p>';
  }
}

function renderLedger() {
  dataPane.innerHTML = "";
  dataPane.append(window.buildLedger(record));
}

window.enterReview = enterReview;
```

- [ ] **Step 5: Add the two-pane + PDF styles to `styles.css`**

pdf.js ships its own `.textLayer` CSS. Import it at the top of `styles.css` (verify the URL alongside the pdf.js version):

```css
@import url("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf_viewer.min.css");
```

Then add:

```css
/* ---- Review view ---- */
.review { margin-top: 1.5rem; }
body[data-state="review"] .sheet { max-width: 1200px; }
body[data-state="review"] .masthead,
body[data-state="review"] .dropzone,
body[data-state="review"] .colophon,
body[data-state="review"] #result { display: none; }

.review-bar {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}
.review-actions { margin-left: auto; display: flex; gap: 0.5rem; }
.review-status { font-family: var(--mono); font-size: 0.8rem; color: var(--ink-soft); }

.btn {
  font-family: var(--ui);
  font-size: 0.85rem;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--rule);
  background: var(--panel);
  border-radius: 6px;
  cursor: pointer;
}
.btn:hover { border-color: var(--accent); }
.btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }

.review-split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 1.25rem;
  align-items: start;
}
.pdf-pane {
  max-height: 80vh;
  overflow: auto;
  background: var(--rule);
  border-radius: 8px;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
}
.pdf-page { position: relative; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.15); }
.pdf-page canvas { display: block; }
.pdf-error { color: var(--warn); font-family: var(--mono); font-size: 0.85rem; }
.data-pane { max-height: 80vh; overflow: auto; }

@media (max-width: 780px) {
  .review-split { grid-template-columns: 1fr; }
}
```

- [ ] **Step 6: Verify manually**

Run the API locally and serve the UI, pointing `config.js` at localhost:

```bash
# terminal 1 — API
ALLOWED_ORIGINS="http://localhost:5173" .venv/bin/uvicorn app.main:app --port 8000
# terminal 2 — UI
cd web && python3 -m http.server 5173
```

Temporarily set `window.API_BASE = "http://localhost:8000"` in `web/config.js` (revert before commit). Open `http://localhost:5173`, extract a sample invoice from `eval/dataset/`.
Expected: page switches to the split view; PDF renders on the left with selectable text; the ledger shows on the right; "New file" / "Download JSON" / "Save corrections" buttons are visible.

- [ ] **Step 7: Commit** (revert the `config.js` localhost edit first)

```bash
git checkout web/config.js
git add web/index.html web/styles.css web/app.js web/review.js
git commit -m "feat(ui): two-pane reviewer renders the PDF beside the ledger"
```

---

## Task 3: Citation flash

Deliverable: each field's citation is a button; clicking it scrolls the cited page into view and flashes it.

**Files:**
- Modify: `web/app.js` (make cites buttons), `web/review.js` (flash handler), `web/styles.css` (flash keyframe)

**Interfaces:**
- Consumes: `.field-cite[data-page]` buttons emitted by `fieldRow`.
- Produces: `flashPage(n)` in `review.js`, invoked on cite click.

- [ ] **Step 1: Make the citation a button carrying its page**

In `web/app.js` `fieldRow`, replace the `field-cite` `<span>` block with a button when a page is known:

```javascript
  if (field && field.source_quote) {
    const c = document.createElement(field.page != null ? "button" : "span");
    c.className = "field-cite";
    c.type = "button";
    c.textContent = `“${field.source_quote}”${field.page != null ? ` ·p${field.page}` : ""}`;
    if (field.page != null) {
      c.dataset.page = String(field.page);
      c.title = "Jump to this page";
    }
    row.append(c);
  }
```

- [ ] **Step 2: Wire the flash in `review.js`**

Add to `review.js`, and call `wireCitations()` at the end of `renderLedger()`:

```javascript
function flashPage(n) {
  const el = pdfPane.querySelector(`.pdf-page[data-page="${n}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart the animation
  el.classList.add("flash");
}

function wireCitations() {
  dataPane.querySelectorAll(".field-cite[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => flashPage(btn.dataset.page));
  });
}
```

Update `renderLedger`:

```javascript
function renderLedger() {
  dataPane.innerHTML = "";
  dataPane.append(window.buildLedger(record));
  wireCitations();
}
```

- [ ] **Step 3: Add the flash keyframe to `styles.css`**

```css
.field-cite[data-page] { cursor: pointer; text-align: left; width: 100%; }
.field-cite[data-page]:hover { color: var(--accent); border-left-color: var(--accent); }

@media (prefers-reduced-motion: no-preference) {
  .pdf-page.flash { animation: page-flash 1.1s ease-out; }
}
@keyframes page-flash {
  0%   { box-shadow: 0 0 0 3px var(--accent), 0 1px 4px rgba(0,0,0,0.15); }
  100% { box-shadow: 0 0 0 0 transparent, 0 1px 4px rgba(0,0,0,0.15); }
}
```

- [ ] **Step 4: Verify manually**

Reload the reviewer (same local setup as Task 2). Click several citations across pages.
Expected: the cited page scrolls into view and flashes an indigo border; multi-page docs jump to the right page.

- [ ] **Step 5: Commit**

```bash
git add web/app.js web/review.js web/styles.css
git commit -m "feat(ui): click a citation to flash its PDF page"
```

---

## Task 4: Inline editing + manual marker

Deliverable: every value on the right is click-to-edit; editing updates the working record and marks that field `manual` (shown instead of a confidence meter).

**Files:**
- Modify: `web/review.js` (edit wiring, coercion, manual rendering), `web/styles.css` (manual styling)

**Interfaces:**
- Consumes: `record` (the working correction object), `.field-value` nodes.
- Produces: `setFieldManual(path, text)` — writes `{value, manual:true, review_required:false, source_quote?, page?}` into `record` at a dotted path; `coerce(text)` — string→number when numeric.
- Note: rows must know which record path they edit. `buildLedger`/`fieldRow` currently don't emit a path. Add a `path` to each row (see Step 1).

- [ ] **Step 1: Emit a record path on each editable row**

In `web/app.js` `fieldRow`, accept an optional `path` and stamp it on the value node:

```javascript
function fieldRow(label, field, valueText, path) {
  // ... existing code through creating `v` ...
  v.className = "field-value";
  v.textContent = valueText != null ? valueText : val(field);
  if (path) { v.dataset.path = path; v.tabIndex = 0; v.classList.add("editable"); }
  // ... rest unchanged ...
}
```

Pass paths from `buildLedger` (top-level fields and line items):

```javascript
      fieldRow("Vendor", inv.vendor_name, undefined, "vendor_name"),
      fieldRow("Invoice №", inv.invoice_number, undefined, "invoice_number"),
      fieldRow("Date", inv.invoice_date, undefined, "invoice_date"),
      fieldRow("Currency", inv.currency, undefined, "currency"),
```

```javascript
      return fieldRow(`Item ${i + 1}`, it.amount, `${qty}${desc}${unit} = ${money(it.amount)}`, `line_items.${i}.amount`);
```

```javascript
      fieldRow("Subtotal", inv.subtotal, money(inv.subtotal), "subtotal"),
      fieldRow("Tax", inv.tax, money(inv.tax), "tax"),
      fieldRow("Total", inv.total, money(inv.total), "total"),
```

- [ ] **Step 2: Add coercion + path-write + edit wiring to `review.js`**

```javascript
// "1,234.50" -> 1234.5 ; "PO-9" -> "PO-9"
function coerce(text) {
  const t = text.trim();
  return /^-?[\d,]+(\.\d+)?$/.test(t) ? Number(t.replace(/,/g, "")) : t;
}

function setPath(obj, path, value) {
  const keys = path.split(".");
  let node = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (node[k] == null) node[k] = /^\d+$/.test(keys[i + 1]) ? [] : {};
    node = node[k];
  }
  node[keys[keys.length - 1]] = value;
}

function getPath(obj, path) {
  return path.split(".").reduce((n, k) => (n == null ? n : n[k]), obj);
}

function setFieldManual(path, text, extra = {}) {
  const prev = getPath(record, path) || {};
  setPath(record, path, {
    ...prev,
    value: coerce(text),
    manual: true,
    review_required: false,
    ...extra,
  });
}

function wireEditing() {
  dataPane.querySelectorAll(".field-value.editable").forEach((v) => {
    v.addEventListener("click", () => beginEdit(v));
  });
}

function beginEdit(node) {
  const path = node.dataset.path;
  const input = document.createElement("input");
  input.className = "field-edit";
  input.value = node.textContent === "—" ? "" : node.textContent;
  node.replaceWith(input);
  input.focus();
  const commit = () => {
    setFieldManual(path, input.value);
    renderLedger(); // re-render so meter→manual pill updates
  };
  input.addEventListener("blur", commit, { once: true });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
    if (e.key === "Escape") { input.removeEventListener("blur", commit); renderLedger(); }
  });
}
```

Update `renderLedger` to also wire editing:

```javascript
function renderLedger() {
  dataPane.innerHTML = "";
  dataPane.append(window.buildLedger(record));
  wireCitations();
  wireEditing();
}
```

- [ ] **Step 3: Show a "manual" pill instead of the confidence meter**

In `web/app.js` `fieldRow`, when `field.manual` is set, render a pill rather than the meter. Replace the meter block:

```javascript
  if (field && field.manual) {
    const pill = document.createElement("span");
    pill.className = "field-manual";
    pill.textContent = "manual";
    row.append(pill);
  } else if (has) {
    const m = document.createElement("span");
    m.className = "field-meter";
    const pct = Math.round(((field.confidence ?? 0) * 100));
    m.style.setProperty("--pct", `${pct}%`);
    m.title = `confidence ${pct}%${field.review_required ? " · review" : ""}`;
    row.append(m);
  }
```

- [ ] **Step 4: Style the editable value, edit input, and manual pill**

```css
.field-value.editable { cursor: text; border-bottom: 1px dashed transparent; }
.field-value.editable:hover { border-bottom-color: var(--accent); }
.field-edit {
  font-family: var(--mono);
  font-size: 0.98rem;
  padding: 0.1rem 0.3rem;
  border: 1px solid var(--accent);
  border-radius: 4px;
  width: 100%;
}
.field-manual {
  font-family: var(--mono);
  font-size: 0.68rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--accent);
  background: var(--accent-tint);
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  align-self: center;
}
```

- [ ] **Step 5: Verify manually**

Reload the reviewer. Click a value (e.g. Total), type a new number, press Enter.
Expected: value updates, the confidence meter is replaced by a "manual" pill. Editing a text field (Vendor) keeps it a string; editing a money field to `1,234.50` stores the number (check via Download JSON in Task 6, or `console` inspect).

- [ ] **Step 6: Commit**

```bash
git add web/app.js web/review.js web/styles.css
git commit -m "feat(ui): inline-edit any field; mark corrected fields manual"
```

---

## Task 5: Select-to-field popover

Deliverable: selecting text over the PDF shows a popover with **Set value of…** (existing fields) and **Add new field** (creates a Custom Fields entry). Both capture the selection's page and mark the field manual.

**Files:**
- Modify: `web/index.html` (popover markup), `web/review.js` (selection + popover logic), `web/app.js` (`buildLedger` renders Custom Fields), `web/styles.css` (popover + custom-fields styles)

**Interfaces:**
- Consumes: `window.getSelection()`, `.pdf-page[data-page]` ancestry to resolve the page.
- Produces: custom fields stored under `record.custom_fields[key] = {value, manual, page, source_quote}`; `buildLedger` renders a "Custom Fields" group when `record.custom_fields` is non-empty.

- [ ] **Step 1: Add the popover markup to `index.html`**

Inside `#review`, after `.review-split`:

```html
        <div class="sel-popover" id="selPopover" hidden>
          <label class="sel-row">
            Set value of
            <select id="selField"></select>
          </label>
          <label class="sel-row">
            or add field
            <input id="selNewKey" type="text" placeholder="e.g. po_number" />
          </label>
          <div class="sel-actions">
            <button type="button" class="btn" id="selApply">Apply</button>
            <button type="button" class="btn" id="selCancel">Cancel</button>
          </div>
        </div>
```

- [ ] **Step 2: Render Custom Fields in `buildLedger` (`app.js`)**

Before `return frag;` in `buildLedger`:

```javascript
  const custom = inv.custom_fields || {};
  const keys = Object.keys(custom);
  if (keys.length) {
    const rows = keys.map((k) =>
      fieldRow(k, custom[k], undefined, `custom_fields.${k}`)
    );
    frag.append(group(`Custom fields (${keys.length})`, rows));
  }
```

- [ ] **Step 3: Selection + popover logic in `review.js`**

The list of assignable existing fields (label → record path):

```javascript
const ASSIGNABLE = [
  ["Vendor", "vendor_name"], ["Invoice №", "invoice_number"],
  ["Date", "invoice_date"], ["Currency", "currency"],
  ["Subtotal", "subtotal"], ["Tax", "tax"], ["Total", "total"],
];

let pendingSel = null; // { text, page }

function currentSelection() {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) return null;
  const text = sel.toString().trim();
  if (!text) return null;
  let node = sel.anchorNode;
  while (node && !(node.dataset && node.dataset.page)) node = node.parentElement;
  if (!node) return null; // selection not inside the PDF pane
  return { text, page: Number(node.dataset.page) };
}

function initSelection() {
  const pop = document.getElementById("selPopover");
  const fieldSel = document.getElementById("selField");
  fieldSel.innerHTML =
    '<option value="">—</option>' +
    ASSIGNABLE.map(([label, path]) => `<option value="${path}">${label}</option>`).join("");

  pdfPane.addEventListener("mouseup", () => {
    const s = currentSelection();
    if (!s) { pop.hidden = true; return; }
    pendingSel = s;
    document.getElementById("selNewKey").value = "";
    fieldSel.value = "";
    pop.hidden = false;
  });

  document.getElementById("selCancel").addEventListener("click", () => { pop.hidden = true; });

  document.getElementById("selApply").addEventListener("click", () => {
    if (!pendingSel) return;
    const path = fieldSel.value;
    const newKey = document.getElementById("selNewKey").value.trim();
    const extra = { page: pendingSel.page, source_quote: pendingSel.text };
    if (newKey) {
      setFieldManual(`custom_fields.${newKey}`, pendingSel.text, extra);
    } else if (path) {
      setFieldManual(path, pendingSel.text, extra);
    } else {
      return; // nothing chosen
    }
    pop.hidden = true;
    renderLedger();
  });
}
```

Call `initSelection()` once inside `enterReview` after the ledger renders:

```javascript
async function enterReview(file, extracted) {
  record = structuredClone(extracted || {});
  body.dataset.state = "review";
  document.getElementById("review").hidden = false;
  renderLedger();
  initSelection();
  await renderPdf(file);
}
```

- [ ] **Step 4: Style the popover + custom fields**

```css
.sel-popover {
  position: fixed;
  right: 2rem;
  bottom: 2rem;
  z-index: 20;
  background: var(--panel);
  border: 1px solid var(--accent);
  border-radius: 8px;
  box-shadow: 0 6px 24px rgba(0,0,0,0.18);
  padding: 0.9rem;
  display: grid;
  gap: 0.6rem;
  width: 260px;
}
.sel-row { display: grid; gap: 0.25rem; font-size: 0.8rem; color: var(--ink-soft); }
.sel-row select, .sel-row input {
  font-family: var(--mono);
  padding: 0.3rem;
  border: 1px solid var(--rule);
  border-radius: 4px;
}
.sel-actions { display: flex; gap: 0.5rem; justify-content: flex-end; }
```

- [ ] **Step 5: Verify manually**

Reload the reviewer. Select a run of text on the PDF.
Expected: popover appears. (a) Pick an existing field → Apply → that field takes the selected text, marked manual, with the citation showing the selected page. (b) Type a new key, Apply → a "Custom fields" group appears with the new key/value.

- [ ] **Step 6: Commit**

```bash
git add web/index.html web/app.js web/review.js web/styles.css
git commit -m "feat(ui): select PDF text to set a field or add a custom one"
```

---

## Task 6: Save + Download + new-file

Deliverable: **Save corrections** PATCHes the working record to the API; **Download JSON** saves it locally; **New file** returns to the drop zone.

**Files:**
- Modify: `web/review.js` (wire the three buttons), `web/app.js` (expose `job_id`; reset on new file)

**Interfaces:**
- Consumes: `window.API_BASE`, the working `record`, the current `job_id`.
- Produces: `PATCH ${API_BASE}/jobs/${jobId}/result` with `record` as the JSON body.

- [ ] **Step 1: Pass the job id into the reviewer**

In `web/app.js`, `enterReview` is called with the result; also pass the job id. Change the `done` branch:

```javascript
      if (job.status === "done") {
        window.enterReview(currentFile, job.result, job.job_id);
        return;
      }
```

- [ ] **Step 2: Wire Save / Download / New file in `review.js`**

Store the job id and wire the buttons in `enterReview`:

```javascript
let jobId = null;

async function enterReview(file, extracted, id) {
  record = structuredClone(extracted || {});
  jobId = id;
  body.dataset.state = "review";
  document.getElementById("review").hidden = false;
  renderLedger();
  initSelection();
  wireReviewButtons();
  await renderPdf(file);
}

function wireReviewButtons() {
  const status = document.getElementById("reviewStatus");

  document.getElementById("saveBtn").onclick = async () => {
    status.textContent = "Saving…";
    try {
      const res = await fetch(`${window.API_BASE}/jobs/${jobId}/result`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(record),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      status.textContent = "Saved ✓";
    } catch (err) {
      status.textContent = "Save failed — your edits are kept; try again or download.";
    }
  };

  document.getElementById("downloadBtn").onclick = () => {
    const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `invoice-${jobId || "record"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  document.getElementById("newFileBtn").onclick = () => {
    document.getElementById("review").hidden = true;
    pdfPane.innerHTML = "";
    dataPane.innerHTML = "";
    document.getElementById("selPopover").hidden = true;
    body.dataset.state = "idle";
  };
}
```

- [ ] **Step 3: Verify manually (end to end)**

With the API + UI running locally (Task 2 setup): extract → edit a field → **Save corrections**.
Expected: status shows "Saved ✓". Confirm server-side:

```bash
curl -s http://localhost:8000/jobs/<job_id> | python3 -m json.tool
```

Expected: the `corrected` object contains your edit (and any custom field), with `manual: true` and the captured `page`. Click **Download JSON** → the file matches the on-screen record. Click **New file** → returns to the drop zone; a second extraction opens a fresh reviewer.

- [ ] **Step 4: Re-run the backend self-check** (guards the PATCH contract the UI depends on)

Run: `.venv/bin/python -m app.main`
Expected: exits 0, `main demo OK`.

- [ ] **Step 5: Commit**

```bash
git add web/app.js web/review.js
git commit -m "feat(ui): save corrections to the API, download JSON, reset for new file"
```

---

## Self-Review Notes

- **Spec coverage:** two-pane layout (T2), render local File (T2), citation flash page-level (T3), select-to-field set/add (T5), custom_fields group (T5), inline edit + manual marker (T4), PATCH persistence (T1), Save/Download (T6), error handling for pdf render (T2) and PATCH (T6) — all mapped.
- **Deviation from spec, intentional:** corrections stored under `Job.corrected` (free-form) instead of overwriting `Job.result`, because `Invoice`/`Field` are `extra="forbid"` and cannot hold `manual`/`custom_fields`. Same round-trip behavior; documented at the top of this plan.
- **Type consistency:** `enterReview(file, record, id)` signature is introduced in T2 (2-arg) and extended in T6 (3-arg) — T6 Step 1 updates both call site and definition together. `setFieldManual(path, text, extra)`, `coerce`, `setPath`/`getPath` all defined in T4 and reused in T5. `buildLedger`/`fieldRow` `path` param added in T4 and used in T5.
- **pdf.js caveat:** the exact render/text-layer API and CDN URLs are verified against current docs at build (Task 2 Step 0) — the snippets here target v4 and may need the version bumped.
