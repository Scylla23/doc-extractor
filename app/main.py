"""FastAPI app: async invoice extraction (T5 + T9).

POST /extract validates the upload, enqueues a background job, and returns
{job_id, status} immediately; GET /jobs/{job_id} reports status and, once done,
the Invoice result. Per-job logging is T10.
"""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.extract import extract_invoice
from app.schema import Invoice

# Claude's PDF request ceiling (PRD §8): reject oversized uploads before we pay
# for an extraction that would 413 at the API anyway.
MAX_BYTES = 32 * 1024 * 1024

app = FastAPI(title="doc-extractor", description="Invoice PDF -> structured JSON")

# ponytail: in-memory job dict; swap for Supabase in backlog when multi-instance.
_jobs: dict[str, "Job"] = {}


class Job(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "done", "error"]
    result: Optional[Invoice] = None
    error: Optional[str] = None


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
