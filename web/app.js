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

function handleFile(file) {
  clearError();
  if (!file) return;
  if (!isPdf(file)) {
    showError(`That's not a PDF. ${file.name} — drop an invoice PDF instead.`);
    return;
  }
  resultEl.innerHTML = "";
  loadingFile.textContent = file.name;
  setState("loading");
  extractFile(file);
}

async function extractFile(file) {
  // ponytail: T22 wires the real fetch here — POST /extract, then poll
  // GET /jobs/{id} to "done"/"error", then renderResult(invoice). For the T21
  // shell we just hold the loading state so the upload flow is visible/testable.
}

// renderResult(invoice) — the cited-field ledger. Built in T22 against the live
// Invoice shape; the .field-row / .field-meter / .field-cite styles are ready.

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
