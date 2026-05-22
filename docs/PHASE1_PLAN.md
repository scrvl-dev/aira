# Phase 1 Plan — The Agent Brain

**Goal:** Move from *"upload exactly 5 files for one property → click Run"* to
*"drop a pile of mixed files (many properties, scanned or digital) → the agent
reads everything, sorts the documents into property batches by itself, reviews
each one, and shows a dashboard of all of them."*

No external/IT access required for this phase — it runs on the app we already host.

---

## Scope

In scope:
1. **Vision OCR** so scanned Survey/Questionnaire documents actually read.
2. **Auto-clustering** — group an arbitrary pile of files into property batches by
   reading the address / eircode / folio out of each document.
3. **Multi-batch processing** — run the existing reconcile pipeline on every
   detected property in one go.
4. **Dashboard** — a summary view listing every property batch with its RAG
   status, document completeness, and downloads; drill into the existing detail view.

Out of scope (later phases): SharePoint ingestion (Phase 2), Teams/Slack
notifications + approval (Phase 3), persistent database (Phase 2).

---

## 1. Vision OCR  (`app/agents/ocr.py` — new; touches `parser.py`, `extractor.py`)

**Problem:** `pdfplumber` only extracts embedded text. Scanned/handwritten PDFs
(the Condition Survey and Property Questionnaire in our test batch) return empty
or gibberish text.

**Design — hybrid text-first, vision-fallback (keeps cost down):**
1. `parse_pdf()` runs as today.
2. New `pdf_is_scanned(file_bytes, extracted_text) -> bool`: true when the
   extracted text is empty or below a per-page threshold (e.g. < ~80 chars/page).
3. If scanned: `render_pdf_to_images(file_bytes, max_pages=15, dpi=170)` using
   **PyMuPDF (fitz)** (already a dependency) → list of PNG bytes.
4. `extractor.extract_fields_from_doc()` gains an image path: when given images,
   it sends them as Claude **vision** content blocks (base64) with the *same*
   per-doc-type system prompt. Model already supports vision (`claude-sonnet-4-6`).

**Controls:** cap pages (15), downscale to ~1500px long edge, so token cost and
latency stay bounded. Flag low-confidence extractions so the dashboard can show
"verify this one".

**Cost note:** vision pages cost more than text. Digital docs still go the cheap
text route; only scanned docs hit vision.

---

## 2. Auto-clustering  (`app/agents/clusterer.py` — new)

The core new intelligence. Input: N parsed+extracted files. Output: property
batches, each ideally holding one of each doc type.

**Flow:**
1. Parse + OCR + extract **every** file first (we need the fields anyway).
   Each file yields: `doc_type`, plus identity fields `address`, `eircode`,
   `folio`, `applicant`.
2. **Cluster by identity strength (most reliable first):**
   - **Eircode** (exact, normalised — strongest signal)
   - **Folio** (exact, normalised)
   - **Address** (fuzzy match ≥ threshold, via rapidfuzz — already a dependency)
   Use a union-find / greedy merge: files sharing any strong key join the same
   property cluster.
3. **Validate each cluster:**
   - At most one of each doc type. Two "submission" docs in one cluster →
     ambiguity flag.
   - Missing doc types → record completeness (e.g. 4/5) but still process.
4. **Unassigned bucket:** files with no extractable identity (e.g. OCR failed)
   go to an "unassigned / needs human" list shown on the dashboard.

**Why identity-based, not filename-based:** filenames are unreliable and the pile
is unstructured. Eircode/folio survive bad OCR better than a garbled address, so
clustering stays robust even when one document scanned poorly.

**Optional later:** use Claude as a tie-breaker for genuinely ambiguous groupings.
Phase 1 ships deterministic fuzzy clustering.

---

## 3. Multi-batch pipeline & job model  (`app/main.py`, `schemas/models.py`)

- `/api/review` accepts **N files** (raise the current 10-file cap to e.g. 60),
  creates a **Run** with multiple property batches.
- New schema `RunResult`: `run_id`, `batches: list[BatchResult]`,
  `unassigned_files`, run-level stats (properties found, green/amber/red counts).
- Each `BatchResult` gains `batch_id`, `doc_completeness` ("4/5"),
  `cluster_confidence`.
- Processing order: parse+extract all files (parallelised) → cluster →
  reconcile each cluster → generate Excel per batch.
- SSE progress reports overall stage + per-batch completion.
- Storage: **in-memory** for Phase 1 (a run lives for the session). Persistence
  is Phase 2.

**New/changed endpoints:**
| Endpoint | Purpose |
|---|---|
| `POST /api/review` | upload pile → returns `run_id` |
| `GET /api/run/{run_id}` | run summary + all batch verdicts |
| `GET /api/result/{run_id}/{batch_id}` | one property's full detail |
| `GET /api/download/{run_id}/{batch_id}` | one property's Excel |
| `GET /api/download/{run_id}` | combined workbook (summary + one sheet/property) |

---

## 4. Dashboard UI  (`frontend/index.html`)

- After a run: a **summary grid** — one row per property: address, RAG badge,
  doc completeness, red/amber/green counts, download, "open" → drill-down.
- Run header: "N properties detected · X green / Y amber / Z red · M unassigned files".
- **Unassigned files** panel: lists files the agent couldn't place, for manual help.
- Keep the existing fields / issues / works detail view as the per-property drill-down.
- Same clean light theme just built.

---

## 5. Reporter  (`app/agents/reporter.py`)

- Keep per-property Excel.
- Add a **combined workbook**: a summary sheet (all properties + RAG) plus one
  detail sheet per property. Lets the team download a whole run as one file.

---

## Dependencies
- No new hard dependencies (PyMuPDF/fitz, rapidfuzz, anthropic already present).
- Optional: `Pillow` for image downscaling (can use PyMuPDF's own scaling instead).

---

## Testing
- **Need ≥ 2 full property document sets** to properly test clustering (grouping
  only matters with multiple properties in the pile). We currently have one.
  → Either provide a second property's 5 docs, or I generate synthetic sets
    (incl. a deliberately scanned/image PDF) to exercise OCR + clustering.
- Unit tests: `pdf_is_scanned` thresholds; clustering on mixed piles incl.
  missing-doc and ambiguous cases; identity normalisation.

---

## Rough effort
| Piece | Estimate |
|---|---|
| Vision OCR | ~0.5 day |
| Clustering | ~1 day |
| Multi-batch pipeline + schema | ~0.5 day |
| Dashboard UI | ~1 day |
| Tests + sample data | ~0.5 day |
| **Total** | **~3–4 focused days** |

---

## Open decisions
1. **Combined workbook vs per-property only** — build the combined run workbook now?
2. **Max files per run** — cap (e.g. 60) to bound cost/latency?
3. **Test data** — provide a 2nd property set, or generate synthetic sets?
4. **Low-confidence handling** — show a "verify" flag on OCR'd docs, or hard-gate
   them as AMBER automatically?
