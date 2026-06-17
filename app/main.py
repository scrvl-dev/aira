"""
Irish Homes MTR Batch Review Agent — FastAPI Backend
File upload → parse → extract (Claude) → reconcile → Excel output
"""
import os
import asyncio
import base64
import json
import secrets
import uuid
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse, StreamingResponse, JSONResponse, FileResponse, Response
)
from fastapi.staticfiles import StaticFiles

from app.agents.pipeline import process_run
from app.agents.reporter import generate_excel, generate_combined_excel
from app.schemas.models import RunResult, BatchResult


# ─── App setup ───────────────────────────────────────────────────────────────

APP_VERSION = "2.0.0-batch-submission-procedure"

app = FastAPI(
    title="Irish Homes MTR Batch Review Agent",
    description="AI-powered document reconciliation for MTR batch submissions",
    version=APP_VERSION
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Basic auth gate ───────────────────────────────────────────────────────────
# Protects the whole app behind a shared username/password when APP_PASSWORD is
# set (e.g. on the hosted instance). Left open when unset (local development).
# /health is always open so platform health checks succeed.
APP_USERNAME = os.environ.get("APP_USERNAME", "team")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if not APP_PASSWORD or request.url.path == "/health":
        return await call_next(request)

    header = request.headers.get("Authorization", "")
    if header.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            if (secrets.compare_digest(user, APP_USERNAME)
                    and secrets.compare_digest(pw, APP_PASSWORD)):
                return await call_next(request)
        except Exception:
            pass

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Irish Homes MTR Review"'},
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
    return {"status": "ok", "service": "ih-batch-review", "version": APP_VERSION}


MAX_FILES = 60


@app.post("/api/review")
async def review_batch(files: list[UploadFile] = File(...)):
    """Upload a pile of mixed documents (one or many properties).

    The agent auto-groups them into property batches. Returns a run_id for SSE
    progress tracking.
    """
    if not files:
        raise HTTPException(400, "No files uploaded")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Maximum {MAX_FILES} files per run")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")

    run_id = str(uuid.uuid4())[:8]
    jobs[run_id] = {
        "status": "queued", "progress": 0, "steps": [],
        "result": None, "run_obj": None, "error": None,
    }

    # Read files into memory immediately (before async handoff)
    file_data = {}
    for f in files:
        file_data[f.filename] = await f.read()

    asyncio.create_task(process_run_job(run_id, file_data))
    return {"run_id": run_id}


@app.get("/api/progress/{run_id}")
async def progress_stream(run_id: str):
    """SSE stream for real-time run progress."""
    if run_id not in jobs:
        raise HTTPException(404, f"Run {run_id} not found")

    async def event_generator():
        while True:
            job = jobs.get(run_id, {})
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


@app.get("/api/run/{run_id}")
async def get_run(run_id: str):
    """Full run result — summary, all property batches, unassigned files."""
    job = jobs.get(run_id)
    if not job:
        raise HTTPException(404, "Run not found")
    if job["status"] != "complete":
        raise HTTPException(400, f"Run not complete. Status: {job['status']}")
    return job["result"]


def _find_batch(run_id: str, batch_id: str) -> BatchResult:
    job = jobs.get(run_id)
    if not job or job["status"] != "complete" or not job.get("run_obj"):
        raise HTTPException(404, "Run not found or not complete")
    for b in job["run_obj"].batches:
        if b.batch_id == batch_id:
            return b
    raise HTTPException(404, "Property batch not found")


@app.get("/api/result/{run_id}/{batch_id}")
async def get_batch_result(run_id: str, batch_id: str):
    """One property's full reconciliation detail."""
    return _find_batch(run_id, batch_id).model_dump()


def _xlsx_response(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.get("/api/download/{run_id}/{batch_id}")
async def download_batch_excel(run_id: str, batch_id: str):
    """Download one property's control sheet."""
    batch = _find_batch(run_id, batch_id)
    data = await asyncio.get_event_loop().run_in_executor(None, generate_excel, batch)
    return _xlsx_response(data, f"IH_Control_Sheet_{batch_id}.xlsx")


@app.get("/api/download/{run_id}")
async def download_run_excel(run_id: str):
    """Download the whole run as one workbook (summary + one sheet per property)."""
    job = jobs.get(run_id)
    if not job or job["status"] != "complete" or not job.get("run_obj"):
        raise HTTPException(404, "Run not found or not complete")
    data = await asyncio.get_event_loop().run_in_executor(
        None, generate_combined_excel, job["run_obj"]
    )
    return _xlsx_response(data, f"IH_Batch_Review_{run_id}.xlsx")


@app.get("/api/runs")
async def list_runs():
    """List all runs (lightweight)."""
    return [
        {
            "run_id": rid,
            "status": j.get("status"),
            "progress": j.get("progress", 0),
            "properties_found": (j.get("result") or {}).get("properties_found"),
            "red_properties": (j.get("result") or {}).get("red_properties"),
        }
        for rid, j in jobs.items()
    ]


# ─── Background processing ────────────────────────────────────────────────────

async def process_run_job(run_id: str, file_data: dict[str, bytes]):
    """Run the full multi-batch pipeline as a background task."""
    job = jobs[run_id]

    def on_progress(stage: str, pct: int, detail: str):
        job["status"] = "running"
        job["progress"] = pct
        job["steps"].append(detail)

    try:
        job["status"] = "running"
        run: RunResult = await asyncio.get_event_loop().run_in_executor(
            None, lambda: process_run(file_data, on_progress, run_id=run_id)
        )
        job["run_obj"] = run
        job["result"] = run.model_dump()
        job["progress"] = 100
        job["status"] = "complete"
        job["steps"].append(
            f"Complete — {run.properties_found} properties: "
            f"{run.red_properties} RED / {run.amber_properties} AMBER / {run.green_properties} GREEN"
        )
    except Exception as e:
        import traceback
        job["status"] = "error"
        job["error"] = str(e)
        job["steps"].append(f"ERROR: {str(e)}")
        print(f"Run {run_id} failed: {traceback.format_exc()}")
