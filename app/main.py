"""
Irish Homes MTR Batch Review Agent — FastAPI Backend
File upload → parse → extract (Claude) → reconcile → Excel output
"""
import os
import asyncio
import json
import uuid
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse, StreamingResponse, JSONResponse, FileResponse
)
from fastapi.staticfiles import StaticFiles

from app.agents.parser import parse_batch
from app.agents.extractor import extract_all, to_typed_models
from app.agents.reconciler import reconcile
from app.agents.reporter import generate_excel
from app.schemas.models import BatchResult


# ─── App setup ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Irish Homes MTR Batch Review Agent",
    description="AI-powered document reconciliation for MTR batch submissions",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store (use Redis for multi-worker production)
jobs: dict[str, dict] = {}

# Serve frontend
FRONTEND_PATH = Path(__file__).parent.parent / "frontend"
if FRONTEND_PATH.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_PATH)), name="static")


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the frontend."""
    index = FRONTEND_PATH / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text())
    return HTMLResponse("<h1>Irish Homes Batch Review Agent</h1><p>Frontend not found.</p>")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ih-batch-review"}


@app.post("/api/review")
async def review_batch(files: list[UploadFile] = File(...)):
    """
    Upload 5 documents for a single property.
    Returns job_id for SSE progress tracking.
    """
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > 10:
        raise HTTPException(400, "Maximum 10 files per batch")

    # Check API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "steps": [],
        "result": None,
        "error": None,
    }

    # Read files into memory immediately (before async handoff)
    file_data = {}
    for f in files:
        content = await f.read()
        file_data[f.filename] = content

    # Kick off processing in background
    asyncio.create_task(process_batch(job_id, file_data))

    return {"job_id": job_id}


@app.get("/api/progress/{job_id}")
async def progress_stream(job_id: str):
    """SSE stream for real-time job progress."""
    if job_id not in jobs:
        raise HTTPException(404, f"Job {job_id} not found")

    async def event_generator():
        while True:
            job = jobs.get(job_id, {})
            data = {
                "status": job.get("status"),
                "progress": job.get("progress", 0),
                "steps": job.get("steps", []),
                "error": job.get("error"),
            }
            yield f"data: {json.dumps(data)}\n\n"

            if job.get("status") in ("complete", "error"):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/api/result/{job_id}")
async def get_result(job_id: str):
    """Get the full JSON result for a completed job."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "complete":
        raise HTTPException(400, f"Job not complete. Status: {job['status']}")
    return job["result"]


@app.get("/api/download/{job_id}")
async def download_excel(job_id: str):
    """Download the Excel control sheet for a completed job."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] != "complete":
        raise HTTPException(400, "Job not complete")
    if "excel_bytes" not in job:
        raise HTTPException(400, "Excel not generated")

    excel_bytes = job["excel_bytes"]
    result = job["result"]
    filename = f"IH_Control_Sheet_{job_id}.xlsx"

    return StreamingResponse(
        iter([excel_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/jobs")
async def list_jobs():
    """List all jobs (for dashboard)."""
    return [
        {
            "job_id": jid,
            "status": j.get("status"),
            "progress": j.get("progress", 0),
            "address": j.get("result", {}).get("address", "Unknown") if j.get("result") else "Processing...",
            "overall_status": j.get("result", {}).get("overall_status") if j.get("result") else None,
        }
        for jid, j in jobs.items()
    ]


# ─── Background processing ────────────────────────────────────────────────────

async def process_batch(job_id: str, file_data: dict[str, bytes]):
    """Full processing pipeline run as background task."""
    job = jobs[job_id]

    def update(status: str, progress: int, step: str):
        job["status"] = status
        job["progress"] = progress
        job["steps"].append(step)

    try:
        update("running", 10, f"Received {len(file_data)} files")

        # Step 1: Parse documents
        update("running", 20, "Parsing documents...")
        parsed = await asyncio.get_event_loop().run_in_executor(
            None, parse_batch, file_data
        )
        detected = [k for k in parsed if not k.startswith("error_")]
        update("running", 35, f"Detected: {', '.join(detected)}")

        # Step 2: Extract fields via Claude
        update("running", 45, "Extracting fields with Claude AI...")
        extracted = await asyncio.get_event_loop().run_in_executor(
            None, extract_all, parsed
        )
        update("running", 65, f"Extracted fields from {len(extracted)} documents")

        # Step 3: Convert to typed models
        update("running", 70, "Validating extracted data...")
        models = await asyncio.get_event_loop().run_in_executor(
            None, to_typed_models, extracted
        )

        # Step 4: Reconcile
        update("running", 80, "Running reconciliation checks...")
        property_ref = f"BATCH-{job_id}"
        result: BatchResult = await asyncio.get_event_loop().run_in_executor(
            None, reconcile, models, property_ref
        )
        update("running", 90, f"Reconciliation complete: {result.red_count} RED, {result.amber_count} AMBER, {result.green_count} GREEN")

        # Step 5: Generate Excel
        update("running", 95, "Generating Excel control sheet...")
        excel_bytes = await asyncio.get_event_loop().run_in_executor(
            None, generate_excel, result
        )

        # Store results
        job["result"] = result.model_dump()
        job["excel_bytes"] = excel_bytes
        update("complete", 100, f"Complete — {result.overall_status.value} overall status")

    except Exception as e:
        import traceback
        job["status"] = "error"
        job["error"] = str(e)
        job["steps"].append(f"ERROR: {str(e)}")
        print(f"Job {job_id} failed: {traceback.format_exc()}")
