// doc-extractor reviewer — two-pane PDF + editable ledger.
// pdf.js is loaded from CDN as an ESM module. Verified against current pdf.js
// docs (context7, mozilla/pdf.js) at build time — v4.7.76, URLs confirmed live.
import * as pdfjsLib from "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf.min.mjs";
pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf.worker.min.mjs";
import { coerce } from "./coerce.mjs";
import { setPath, getPath } from "./paths.mjs";

const body = document.body;
const pdfPane = document.getElementById("pdfPane");
const dataPane = document.getElementById("dataPane");

let record = null; // the working (correctable) record

const ASSIGNABLE = [
  ["Vendor", "vendor_name"], ["Invoice №", "invoice_number"],
  ["Date", "invoice_date"], ["Currency", "currency"],
  ["Subtotal", "subtotal"], ["Tax", "tax"], ["Total", "total"],
];

let pendingSel = null; // { text, page }
let jobId = null;

async function enterReview(file, extracted, id) {
  record = structuredClone(extracted || {});
  jobId = id;
  body.dataset.state = "review";
  document.getElementById("review").hidden = false;
  document.getElementById("reviewStatus").textContent = ""; // clear stale "Saved ✓" on re-entry
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

async function renderPdf(file) {
  pdfPane.innerHTML = "";
  try {
    const buf = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument({ data: buf }).promise;
    // Fit each page to the pane width so it isn't clipped on the sides.
    // ponytail: measured once at render; no resize re-render (reload if resized).
    const avail = (pdfPane.clientWidth || 600) - 32; // minus 1rem padding each side
    const dpr = window.devicePixelRatio || 1;
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const scale = avail / base.width;
      const viewport = page.getViewport({ scale });

      const wrap = document.createElement("div");
      wrap.className = "pdf-page";
      wrap.dataset.page = String(n);
      wrap.style.width = `${viewport.width}px`;
      wrap.style.height = `${viewport.height}px`;

      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      wrap.append(canvas);

      // Text layer: positioned spans so browser selection works over the page.
      const textLayer = document.createElement("div");
      textLayer.className = "textLayer";
      wrap.append(textLayer);

      pdfPane.append(wrap);
      await page.render({
        canvasContext: canvas.getContext("2d"),
        viewport,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null, // crisp on HiDPI
      }).promise;

      // Text layer: v4 TextLayer class (confirmed current API via context7).
      const textContent = await page.getTextContent();
      const tl = new pdfjsLib.TextLayer({ textContentSource: textContent, container: textLayer, viewport });
      await tl.render();
    }
  } catch (err) {
    pdfPane.innerHTML =
      '<p class="pdf-error">Couldn’t render the PDF. The data on the right is still editable.</p>';
  }
}

function flashPage(n) {
  const el = pdfPane.querySelector(`.pdf-page[data-page="${n}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart the animation
  el.classList.add("flash");
}

const normText = (s) => s.replace(/\s+/g, " ").trim().toLowerCase();

// Highlight the cited quote's text spans on the given page. Returns true on a hit.
function highlightQuote(pageNum, quote) {
  pdfPane.querySelectorAll(".textLayer .cite-hl").forEach((s) => s.classList.remove("cite-hl"));
  const pageEl = pdfPane.querySelector(`.pdf-page[data-page="${pageNum}"]`);
  const q = normText(quote || "");
  if (!pageEl || !q) return false;

  // Concatenate the page's text spans, tracking each span's char range, then find
  // where the quote lands and light up every span that overlaps it.
  const spans = [...pageEl.querySelectorAll(".textLayer span")].filter((s) => normText(s.textContent));
  let concat = "";
  const ranges = spans.map((sp) => {
    const start = concat.length;
    concat += normText(sp.textContent) + " ";
    return { sp, start, end: concat.length };
  });

  const idx = concat.indexOf(q);
  let hits;
  if (idx >= 0) {
    const end = idx + q.length;
    hits = ranges.filter((r) => r.start < end && r.end > idx).map((r) => r.sp);
  } else {
    // Quote not found verbatim (OCR/whitespace drift): light up spans it contains.
    hits = ranges.filter((r) => q.includes(normText(r.sp.textContent))).map((r) => r.sp);
  }
  if (!hits.length) return false;
  hits.forEach((sp) => sp.classList.add("cite-hl"));
  hits[0].scrollIntoView({ behavior: "smooth", block: "center" });
  return true;
}

function wireCitations() {
  dataPane.querySelectorAll(".field-cite[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const page = Number(btn.dataset.page);
      if (!highlightQuote(page, btn.dataset.quote)) flashPage(page);
    });
  });
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

let selInitialized = false;

function initSelection() {
  if (selInitialized) return;
  selInitialized = true;
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
    const rawKey = document.getElementById("selNewKey").value.trim();
    const newKey = rawKey ? rawKey.replace(/[^A-Za-z0-9_-]/g, "_") : "";
    const extra = { page: pendingSel.page, source_quote: pendingSel.text };
    if (newKey) {
      setFieldManual(`custom_fields.${newKey}`, pendingSel.text, extra);
    } else if (path) {
      setFieldManual(path, pendingSel.text, extra);
    } else {
      return; // nothing chosen
    }
    pop.hidden = true;
    window.getSelection().removeAllRanges();
    renderLedger();
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
  // Seed from the record's raw value, not the DOM text: line-item rows display a
  // composed string ("2 × Widget @ 10.00 = 20.00"), which must not be fed to coerce().
  const cur = getPath(record, path);
  input.value = cur && cur.value != null ? String(cur.value) : "";
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

function renderLedger() {
  dataPane.innerHTML = "";
  dataPane.append(window.buildLedger(record));
  wireCitations();
  wireEditing();
}

window.enterReview = enterReview;
