# CLAUDE.md — Irish Homes MTR Batch Review Agent

## What this project does
AI agent that reviews Mortgage-to-Rent (MTR) property submission batches for Irish Homes.
For each property, 5 documents are uploaded → parsed → key fields extracted via Claude API →
cross-referenced against each other → RAG status (Green/Amber/Red) assigned per field →
Excel control sheet generated with colour-coded output.

## The 5 documents per property

| Doc | Type | Key fields |
|-----|------|-----------|
| Submission Sheet | Excel (.xlsx) | All master data: borrower, folio, address, OMV, rent, household |
| List of Works | Excel (.xlsx) | 24+ works items extracted from condition survey |
| Condition Survey | PDF | Property condition, works required, surveyor rating, fire safety |
| Property Questionnaire | PDF | Borrower-completed MTR form, household, planning, legal |
| Valuation Report | PDF | OMV, rental value, valuer details, inspection date |

## Critical fields to cross-reference

| Field | CRITICAL sources | Flag if... |
|-------|-----------------|-----------|
| Property address | All 5 docs | Any doc has a different address |
| Eircode | All docs | Formatting differs or conflict |
| Applicant name | Submission, Valuation, Questionnaire | Survey "prepared_for" ≠ borrower name |
| Folio number | Submission, Survey | Not in both = AMBER; different = RED |
| Bedrooms | Submission, Valuation, Survey, Q'aire | Any conflict = RED |
| Open Market Value | Submission, Valuation | >2% difference = AMBER; >5% = RED |
| Monthly rent | Submission, Valuation | Same thresholds |
| Condition rating | Survey (internal) | Exec summary ≠ signed ranking page = RED |
| Household composition | Submission, Questionnaire | Adult/dependent count conflict = RED |
| Works count | Survey, List of Works | Different count = RED |
| Fire safety issues | Survey, List of Works | Present but not in submission = RED |

## RAG Status definitions
- **GREEN**: All sources agree (after normalisation)
- **AMBER**: Minor formatting difference, or field missing from one secondary source
- **RED**: Value conflict, mandatory field missing, or internal inconsistency

## Project structure
```
app/
  main.py           ← FastAPI app, upload endpoint, SSE progress, download
  agents/
    parser.py       ← Routes files to PDF/Excel/Word parsers
    extractor.py    ← Claude API field extraction (one system prompt per doc type)
    reconciler.py   ← Rules-based RAG logic, all field comparisons
    reporter.py     ← Styled Excel output (openpyxl)
  schemas/
    models.py       ← Pydantic models for all doc types + BatchResult
  config/
    field_rules.yaml ← Which fields to check, match types, tolerances
frontend/
  index.html        ← Drag & drop UI, SSE progress, results display
```

## Key commands for Claude Code
```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
uvicorn app.main:app --reload --port 8000

# Run with env var
ANTHROPIC_API_KEY=sk-ant-... uvicorn app.main:app --reload

# Test with sample files
# Upload the 5 files from /test_data/ to http://localhost:8000
```

## Environment variables
- `ANTHROPIC_API_KEY` — required, never commit

## Irish Homes context
- Properties are in Republic of Ireland
- MTR = Mortgage to Rent scheme (Dept of Housing)
- Lender is typically Pepper, PTSB, AIB, BOI
- Folio format: KE##### (Kildare) etc — county code + number + letter
- Eircode format: [A-Z][0-9]{2} [A-Z0-9]{4}
- Works items follow HAP/minimum rental standards
- HA = Housing Authority; LA = Local Authority; COT = Certificate of Title

## Common issues to catch
1. Survey "prepared for" company name ≠ borrower personal name
2. Executive summary condition rating ≠ signed ranking page rating
3. Questionnaire household comp ≠ Submission Sheet (adult vs dependent count)
4. OMV in Submission Sheet matches rebuilding cost not market value from valuation
5. Fire safety party wall issue flagged in survey but absent from submission
6. Eircode with/without space (W12C966 vs W12 C966)
7. Non-residing borrower in submission but absent from other docs

## Do not
- Commit ANTHROPIC_API_KEY to git
- Store uploaded files to disk in production (process in memory)
- Use the rebuilding cost as the OMV — they are different fields in the valuation
