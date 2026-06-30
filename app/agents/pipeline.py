"""
Run pipeline — orchestrates a whole upload of mixed files into a RunResult.

Per file:   parse → (OCR if scanned) → Claude field extraction
Per pile:   cluster files into property batches → reconcile each → roll up stats
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from app.agents.parser import parse_file
from app.agents.ocr import pdf_is_scanned, render_pdf_to_images, png_to_base64
from app.agents.extractor import extract_fields_from_doc, to_typed_models
from app.agents.clusterer import cluster_documents
from app.agents.reconciler import reconcile
from app.agents.pdf_amender import propose_valuation_pdf
from app.agents.amendments import _get as _amend_get
from app.schemas.models import (
    BatchResult, RunResult, UnassignedFile, RAGStatus, AmendAction,
)

# Proposed (draft) amended PDFs generated for human sign-off, keyed by batch_id:
#   { batch_id: { "Valuation_<file>.pdf": pdf_bytes, ... } }
# Held in memory only — originals are never modified or written to disk.
PROPOSED_PDFS: dict[str, dict[str, bytes]] = {}

ProgressCB = Optional[Callable[[str, int, str], None]]

DOC_TYPES = ["submission", "valuation", "survey", "questionnaire", "works"]


def process_document(filename: str, file_bytes: bytes) -> dict:
    """Parse one file, OCR it if scanned, and extract its fields with Claude."""
    doc_type, text = parse_file(filename, file_bytes)

    images = None
    ocr_used = False
    if Path(filename).suffix.lower() == ".pdf" and pdf_is_scanned(file_bytes, text):
        try:
            rendered = render_pdf_to_images(file_bytes)
            if rendered:
                images = [png_to_base64(b) for b in rendered]
                ocr_used = True
        except Exception:
            images = None

    if doc_type == "unknown":
        fields = {"error": "Unrecognised document type"}
    else:
        fields = extract_fields_from_doc(doc_type, text, images=images)

    # Keep the original PDF bytes so we can render proposed amended copies later
    # (Valuation / BS). Originals are never modified.
    is_pdf = Path(filename).suffix.lower() == ".pdf"
    return {"filename": filename, "doc_type": doc_type, "fields": fields,
            "ocr_used": ocr_used, "raw_bytes": file_bytes if is_pdf else None}


def _best_address(records: list[dict]) -> Optional[str]:
    # Prefer the submission's address, then any non-empty address.
    by_type = {r["doc_type"]: r for r in records}
    for dt in ["submission", "valuation", "questionnaire", "survey", "works"]:
        f = (by_type.get(dt) or {}).get("fields") or {}
        if isinstance(f, dict) and f.get("address"):
            return f["address"]
    return None


def _build_batch(cluster: dict, run_id: str, index: int) -> BatchResult:
    records = cluster["records"]

    # One record per doc type (longest field set wins on duplicates).
    extracted: dict[str, dict] = {}
    ocr_docs: list[str] = []
    for r in records:
        dt, fields = r["doc_type"], r["fields"]
        if dt in DOC_TYPES and isinstance(fields, dict) and "error" not in fields:
            if dt not in extracted or len(str(fields)) > len(str(extracted[dt])):
                extracted[dt] = fields
            if r.get("ocr_used") and dt not in ocr_docs:
                ocr_docs.append(dt)

    models = to_typed_models(extracted)

    batch_id = f"{run_id}-P{index+1}"
    result = reconcile(models, property_ref=f"BATCH-{batch_id}")

    # Address fallback when there's no submission sheet.
    if not result.address or result.address == "Unknown Property":
        result.address = _best_address(records) or "Unknown Property"

    # Flag any field whose value came from a scanned/OCR'd source.
    for f in result.fields:
        if any(getattr(f, dt, None) for dt in ocr_docs):
            f.needs_verify = True

    present = sorted({r["doc_type"] for r in records if r["doc_type"] in DOC_TYPES})
    result.batch_id = batch_id
    result.doc_completeness = f"{len(present)}/5"
    result.cluster_confidence = cluster["confidence"]
    result.ocr_docs = ocr_docs
    result.source_files = [r["filename"] for r in records]

    # ── Produce PROPOSED amended PDFs for human sign-off (Valuation only) ──
    # Only when there is a PROPOSED amendment for the Valuation. Never overwrites
    # the original; the draft lives in memory under PROPOSED_PDFS[batch_id].
    val_proposed = [a for a in result.amendments
                    if a.document == "valuation" and a.action == AmendAction.PROPOSED]
    if val_proposed:
        val_rec = next((r for r in records
                        if r["doc_type"] == "valuation" and r.get("raw_bytes")), None)
        if val_rec:
            ss = models.get("submission")
            pdf_bytes, applied, unapplied = propose_valuation_pdf(
                val_rec["raw_bytes"], result.amendments,
                _amend_get(ss, "property_type"), _amend_get(ss, "bedrooms"),
            )
            if pdf_bytes:
                base = Path(val_rec["filename"]).stem
                name = f"PROPOSED_{base}.pdf"
                PROPOSED_PDFS.setdefault(batch_id, {})[name] = pdf_bytes
                result.proposed_pdfs.append(name)
            # Any change that couldn't be applied stays FLAGGED, not dropped.
            for note in unapplied:
                for a in val_proposed:
                    a.note = (a.note or "") + f" | NOT auto-applied: {note}"
        else:
            for a in val_proposed:
                a.action = AmendAction.FLAG
                a.auto_applicable = False
                a.note = (a.note or "") + " | Original Valuation PDF unavailable — FLAG only."
    return result


def process_run(file_data: dict[str, bytes], on_progress: ProgressCB = None,
                run_id: Optional[str] = None) -> RunResult:
    run_id = run_id or str(uuid.uuid4())[:8]
    total = len(file_data)

    # Drop any stale proposed PDFs from a previous run with this id.
    for k in list(PROPOSED_PDFS):
        if k.startswith(run_id):
            PROPOSED_PDFS.pop(k, None)

    def progress(stage: str, pct: int, detail: str):
        if on_progress:
            on_progress(stage, pct, detail)

    progress("parsing", 10, f"Reading {total} files…")

    # ── Parse + OCR + extract every file (threaded; bounded concurrency) ──
    records: list[dict] = []
    done = {"n": 0}
    lock = threading.Lock()

    def work(item):
        name, data = item
        rec = process_document(name, data)
        with lock:
            done["n"] += 1
            pct = 10 + int(55 * done["n"] / max(total, 1))
            progress("extracting", pct, f"Read {done['n']}/{total} — {name}")
        return rec

    with ThreadPoolExecutor(max_workers=5) as pool:
        records = list(pool.map(work, list(file_data.items())))

    # ── Cluster into property batches ──
    progress("clustering", 70, "Grouping documents into property batches…")
    clusters, unassigned_recs = cluster_documents(records)

    # ── Reconcile each batch ──
    batches: list[BatchResult] = []
    for i, cluster in enumerate(clusters):
        progress("reconciling", 75 + int(15 * (i + 1) / max(len(clusters), 1)),
                 f"Reviewing property {i+1}/{len(clusters)}…")
        batches.append(_build_batch(cluster, run_id, i))

    # Order: RED first, then AMBER, then GREEN — most urgent on top.
    rank = {RAGStatus.RED: 0, RAGStatus.AMBER: 1, RAGStatus.GREEN: 2, RAGStatus.MISSING: 3}
    batches.sort(key=lambda b: rank.get(b.overall_status, 4))

    unassigned = [
        UnassignedFile(
            filename=r["filename"],
            detected_type=r["doc_type"],
            reason=("Could not read a property identity (address / eircode / folio)"
                    if r["doc_type"] != "unknown" else "Unrecognised document type"),
        )
        for r in unassigned_recs
    ]

    green = sum(1 for b in batches if b.overall_status == RAGStatus.GREEN)
    amber = sum(1 for b in batches if b.overall_status == RAGStatus.AMBER)
    red = sum(1 for b in batches if b.overall_status == RAGStatus.RED)

    notes = []
    if unassigned:
        notes.append(f"{len(unassigned)} file(s) could not be matched to a property.")
    incomplete = [b for b in batches if b.doc_completeness != "5/5"]
    if incomplete:
        notes.append(f"{len(incomplete)} property batch(es) are missing one or more documents.")

    progress("done", 100, f"{len(batches)} properties reviewed")

    return RunResult(
        run_id=run_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        total_files=total,
        properties_found=len(batches),
        batches=batches,
        unassigned=unassigned,
        green_properties=green,
        amber_properties=amber,
        red_properties=red,
        processing_notes=notes,
    )
