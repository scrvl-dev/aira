# Irish Homes MTR Batch Review Agent

AI-powered document reconciliation for Mortgage-to-Rent batch submissions.

## What it does
Upload 5 documents for a property → AI extracts key fields from each →
cross-references for conflicts → returns GREEN/AMBER/RED per field →
downloads styled Excel control sheet.

## Quick start (local)

```bash
git clone <your-repo>
cd ih-batch-agent
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --reload --port 8000
# Open http://localhost:8000
```

## Deploy on Render.com (recommended — free tier works)

1. Push to GitHub
2. Go to https://render.com → New → Web Service
3. Connect your GitHub repo
4. Settings auto-loaded from `render.yaml`
5. Add `ANTHROPIC_API_KEY` in Environment Variables
6. Deploy — live URL in ~3 minutes

## Deploy on Railway.app (alternative)

```bash
npm install -g @railway/cli
railway login
railway init
railway add
railway up
railway variables set ANTHROPIC_API_KEY=sk-ant-...
```

## Deploy on Fly.io (for more control)

```bash
fly launch
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly deploy
```

## GitHub repos used

| Package | Repo | Purpose |
|---------|------|---------|
| anthropic | anthropics/anthropic-sdk-python | Claude API |
| fastapi | tiangolo/fastapi | Web framework |
| pdfplumber | jsvine/pdfplumber | PDF extraction |
| openpyxl | openpyxl/openpyxl | Excel I/O |
| rapidfuzz | maxbachmann/RapidFuzz | Fuzzy string matching |
| pydantic | pydantic/pydantic | Data validation |
| python-docx | python-openxml/python-docx | Word documents |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| ANTHROPIC_API_KEY | YES | Your Anthropic API key |
| PORT | Auto | Set by hosting platform |

## Document naming — auto-detection

The agent detects document type from filename. Recommended naming:
- `Submission_Sheet_[address].xlsx`
- `IH_-_List_of_Works_[address].xlsx`
- `Condition_Survey_Report_[address].pdf`
- `Property_Questionnaire_[address].pdf`
- `Valuation_[address].pdf`

Any file containing "submission", "works", "survey", "questionnaire", or "valuation"
in the name will be auto-detected correctly.

## Security notes
- API key never stored — passed per-request only
- Uploaded files processed in memory, not saved to disk
- CORS configured for production deployment
