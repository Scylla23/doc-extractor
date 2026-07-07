"""FastAPI app: POST /extract — multipart PDF upload -> validated Invoice JSON (T5).

Synchronous for now; the async job + GET /jobs/{id} split is T9. The endpoint is
a plain `def` so FastAPI runs the blocking Claude call in a threadpool rather
than stalling the event loop.
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile

from app.extract import extract_invoice
from app.schema import Invoice

# Claude's PDF request ceiling (PRD §8): reject oversized uploads before we pay
# for an extraction that would 413 at the API anyway.
MAX_BYTES = 32 * 1024 * 1024

app = FastAPI(title="doc-extractor", description="Invoice PDF -> structured JSON")


@app.post("/extract", response_model=Invoice)
def extract(file: UploadFile = File(...)) -> Invoice:
    filename = (file.filename or "").lower()
    if file.content_type != "application/pdf" and not filename.endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only PDF uploads are accepted.")
    if file.size is not None and file.size > MAX_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 32 MB limit.")

    pdf_bytes = file.file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty upload.")
    if len(pdf_bytes) > MAX_BYTES:  # size unknown until read for some clients
        raise HTTPException(status_code=413, detail="PDF exceeds the 32 MB limit.")

    return extract_invoice(pdf_bytes)
