// doc-extractor UI — T21: drag/drop + state only. No network yet.
// T22 replaces the body of extractFile() with the real POST /extract + poll loop.

const body = document.body;
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const errorEl = document.getElementById("error");
const loadingFile = document.getElementById("loadingFile");
const resultEl = document.getElementById("result");

function setState(name) {
  body.dataset.state = name;
}

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.hidden = false;
  setState("idle");
}

function clearError() {
  errorEl.hidden = true;
}

function isPdf(file) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

let currentFile = null;

function handleFile(file) {
  clearError();
  if (!file) return;
  if (!isPdf(file)) {
    showError(`That's not a PDF. ${file.name} — drop an invoice PDF instead.`);
    return;
  }
  currentFile = file;
  resultEl.innerHTML = "";
  loadingFile.textContent = file.name;
  setState("loading");
  extractFile(file);
}

const POLL_MS = 1750;
const TIMEOUT_MS = 120000; // scanned docs + model cascade can run 60s+ (see PRD §4)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function extractFile(file) {
  const api = window.API_BASE;
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${api}/extract`, { method: "POST", body: form });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Upload failed (HTTP ${res.status}).`);
    }
    const { job_id } = await res.json();

    const deadline = Date.now() + TIMEOUT_MS;
    while (Date.now() < deadline) {
      await sleep(POLL_MS);
      const jr = await fetch(`${api}/jobs/${job_id}`);
      if (!jr.ok) throw new Error(`Lost the job (HTTP ${jr.status}).`);
      const job = await jr.json();
      if (job.status === "done") {
        window.enterReview(currentFile, job.result);
        return;
      }
      if (job.status === "error") {
        throw new Error(job.error || "Extraction failed.");
      }
    }
    throw new Error("Extraction timed out. Try a smaller or clearer PDF.");
  } catch (err) {
    const msg = /fetch/i.test(err.message || "")
      ? "Couldn't reach the API. Is it running?"
      : err.message || "Something went wrong.";
    showError(msg);
  }
}

// --- Result ledger: every field with its confidence + source citation ---

const money = (f) =>
  f && typeof f.value === "number" ? f.value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }) : val(f);

const val = (f) => (f && f.value != null && f.value !== "" ? String(f.value) : "—");

function fieldRow(label, field, valueText) {
  const row = document.createElement("div");
  row.className = "field-row";
  const has = field && field.value != null && field.value !== "";
  row.dataset.review = String(Boolean(field && field.review_required));

  const l = document.createElement("span");
  l.className = "field-label";
  l.textContent = label;

  const v = document.createElement("span");
  v.className = "field-value";
  v.textContent = valueText != null ? valueText : val(field);

  row.append(l, v);

  if (has) {
    const m = document.createElement("span");
    m.className = "field-meter";
    const pct = Math.round(((field.confidence ?? 0) * 100));
    m.style.setProperty("--pct", `${pct}%`);
    m.title = `confidence ${pct}%${field.review_required ? " · review" : ""}`;
    row.append(m);
  }

  if (field && field.source_quote) {
    const c = document.createElement(field.page != null ? "button" : "span");
    c.className = "field-cite";
    c.textContent = `“${field.source_quote}”${field.page != null ? ` ·p${field.page}` : ""}`;
    if (field.page != null) {
      c.type = "button";
      c.dataset.page = String(field.page);
      c.title = "Jump to this page";
    }
    row.append(c);
  }
  return row;
}

function group(title, rows) {
  const frag = document.createDocumentFragment();
  const h = document.createElement("h2");
  h.className = "result-group";
  h.textContent = title;
  frag.append(h, ...rows);
  return frag;
}

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

// --- wiring ---
fileInput.addEventListener("change", () => handleFile(fileInput.files[0]));

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragging");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragging");
  handleFile(e.dataTransfer.files[0]);
});
