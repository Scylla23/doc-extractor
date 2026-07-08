"""FastAPI app: async invoice extraction (T5 + T9).

POST /extract validates the upload, enqueues a background job, and returns
{job_id, status} immediately; GET /jobs/{job_id} reports status and, once done,
the Invoice result. Per-job logging is T10.
"""

from __future__ import annotations

import os
import uuid
from typing import Literal, Optional

from fastapi import BackgroundTasks, Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.extract import extract_invoice
from app.schema import Invoice

# Claude's PDF request ceiling (PRD §8): reject oversized uploads before we pay
# for an extraction that would 413 at the API anyway.
MAX_BYTES = 32 * 1024 * 1024

app = FastAPI(title="doc-extractor", description="Invoice PDF -> structured JSON")

# Browser-origin allowlist: the UI is served from a different origin (localhost
# in dev, Vercel in prod), so cross-origin calls need CORS. Origins come from the
# ALLOWED_ORIGINS env var (comma-separated); T23 sets the Vercel origin in prod.
_origins = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ponytail: in-memory job dict; swap for Supabase in backlog when multi-instance.
_jobs: dict[str, "Job"] = {}


class Job(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "done", "error"]
    result: Optional[Invoice] = None
    error: Optional[str] = None
    # ponytail: free-form corrected record from the reviewer UI; kept off the
    # strict Invoice schema so manual markers + custom_fields round-trip.
    corrected: Optional[dict] = None


def _process_job(job_id: str, pdf_bytes: bytes) -> None:
    """Runs in a threadpool after the response is sent (blocking Claude call)."""
    job = _jobs[job_id]
    job.status = "processing"
    try:
        job.result = extract_invoice(pdf_bytes, job_id=job_id)
        job.status = "done"
    except Exception as exc:  # record any failure so the poller sees it
        job.status = "error"
        job.error = str(exc)


@app.post("/extract", response_model=Job)
def extract(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> Job:
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

    job_id = uuid.uuid4().hex
    _jobs[job_id] = Job(job_id=job_id, status="queued")
    background_tasks.add_task(_process_job, job_id, pdf_bytes)
    return _jobs[job_id]


@app.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job_id.")
    return job


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
