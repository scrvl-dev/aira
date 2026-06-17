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

## Batch Submission Procedure checks (Amendments sheet, May 2026)
In addition to the cross-references above, the reconciler enforces the Batch Submission Procedure:

| Check | Rule | Flag |
|-------|------|------|
| Address vs **Eircode Finder** | Address must match the Eircode Finder — verified by geocoding the address and the Eircode (free OpenStreetMap/Nominatim) and checking they're within ~1 km | RED if >1 km apart; AMBER "verify manually" if either won't geocode |
| **Borrower name vs Folio** | Borrower must match PQ, V *and* the Folio registered owner | RED if ≠ registered owner |
| **All questions answered** (SS+PQ) | Every question answered or "N/A" | RED if any blank |
| SS Q2 Expression of Interest | Must be **"No"** | RED if not |
| SS Q3 Pre-Assigned | Must be **"Yes"** | RED if not |
| PQ Q1a/b (or Q11) | Must be **"Yes"** | RED if not |
| **Management Company** (PQ Q8/Q5) | If a ManCo exists, confirm name + annual charge + arrears | RED if present but details missing |
| **PQ signed & dated** | Must be signed and dated | AMBER until confirmed |
| **Sale price** (SS Q30) | Added from the MTR Database | AMBER until present |
| **Valuation comparables** (Q12/Q14) | 3 sale + 3 rental, each matching property type & beds, with let/sold date | RED if <3 of either; AMBER on type/beds/date issues |

Building Survey & List of Works: only address/type/beds are auto-checked — the rest is reviewed manually by AG & ND (noted in the report).

Rule of thumb for new checks: **RED only on a real violation in present data; AMBER ("verify") when a field simply wasn't extracted** — so a clean batch never goes falsely RED.

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
- Eircode/address verification (the "Address vs Eircode Finder" check):
  - **Default: free OpenStreetMap / Nominatim geocoding** — no key needed. Geocodes the address and the Eircode and flags them if >~1 km apart.
  - `EIRCODE_MATCH_RADIUS_M` — match radius in metres (default `1000`).
  - `NOMINATIM_EMAIL` — contact email added to Nominatim requests (courtesy; recommended for volume).
  - `EIRCODE_GEOCODE=0` — disable network geocoding (→ manual-verify flag).
  - `EIRCODE_API_URL` + `EIRCODE_API_KEY` — optional licensed Eircode endpoint that **overrides** Nominatim (param-name overrides: `EIRCODE_API_KEY_PARAM` default `key`, `EIRCODE_API_EIRCODE_PARAM` default `eircode`).

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
