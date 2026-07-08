// doc-extractor reviewer — two-pane PDF + editable ledger.
// pdf.js is loaded from CDN as an ESM module. Verified against current pdf.js
// docs (context7, mozilla/pdf.js) at build time — v4.7.76, URLs confirmed live.
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

function wireCitations() {
  dataPane.querySelectorAll(".field-cite[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => flashPage(btn.dataset.page));
  });
}

function renderLedger() {
  dataPane.innerHTML = "";
  dataPane.append(window.buildLedger(record));
  wireCitations();
}

window.enterReview = enterReview;
