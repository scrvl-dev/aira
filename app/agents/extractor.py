"""
Field Extractor
Uses Claude API to extract structured fields from each document type.
One focused system prompt per document type for precision.
"""
import json
import os
from typing import Optional
import anthropic
from app.schemas.models import (
    SubmissionData, ValuationData, SurveyData,
    QuestionnaireData, WorksData
)


# Current Claude model for extraction (claude-sonnet-4-20250514 is deprecated, EOL 2026-06-15)
EXTRACTION_MODEL = "claude-sonnet-4-6"


class ExtractionAPIError(RuntimeError):
    """Raised when the Claude API itself fails (auth, connection, bad model, rate
    limit). Distinct from a per-document data/JSON problem — this aborts the batch
    so the failure is surfaced to the user instead of masquerading as a false RED."""


def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionAPIError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=api_key)


SYSTEM_PROMPTS = {
    "submission": """You extract structured data from Irish Homes MTR Submission Sheets.
These are Excel documents with Question | Answer format.
Extract ALL fields you can find. Return ONLY a JSON object with these exact keys:
lender, borrower_1, borrower_2, non_residing_borrower, both_borrowers_mtr,
both_consented, folio, address, eircode, property_type, bedrooms,
total_occupants, household_composition, num_dependants, open_market_value,
market_rent, sale_price, negative_equity, positive_equity, over_accommodation,
aged_65_over, social_housing_support_number,
q2_expression_of_interest (the answer to "Expression of Interest Application" — Yes/No),
q3_pre_assigned (the answer to "Pre-Assigned Application" — Yes/No),
unanswered_questions (array of the exact question labels/numbers that are left BLANK —
not answered and not marked 'N/A'. Return [] if every question is answered or marked N/A).
Use null for missing fields. Strip currency symbols from numeric values (return as string number).
Return ONLY valid JSON, no markdown.""",

    "valuation": """You extract structured data from Irish property Valuation Reports.
These are formal valuation documents from registered valuers.
Extract ALL fields. Return ONLY a JSON object with these exact keys:
applicant, address, eircode, bedrooms, property_type, open_market_value,
rebuilding_cost, rental, floor_area_sqm, inspection_date, valuer, condition,
letting_demand, folio,
sale_comparables, rental_comparables.
For open_market_value: extract the "Market value (at present)" figure, NOT the rebuilding cost.
sale_comparables and rental_comparables: look in Q12 (Property Demand) and Q14 (General Notes).
Each is an array of objects with keys: address, property_type, bedrooms, date (the date the
comparable was let or sold), price. Capture every comparable you can find (there should be
about 3 of each). Use [] if a section genuinely has none — do not invent comparables.
Strip currency symbols from numeric values (return as string number).
Return ONLY valid JSON, no markdown.""",

    "survey": """You extract structured data from Irish Building Condition Survey reports.
These are formal surveyor reports assessing property condition.
Pay special attention to:
1. Who the survey is PREPARED FOR (the named client, may differ from borrower)
2. The condition rating in the EXECUTIVE SUMMARY (may differ from ranking page)  
3. The condition rating on the RANKING/SIGNED PAGE (the official rating)
4. ALL works items listed in the "Minimum Rental Standards Works Required" section
Return ONLY a JSON object with these exact keys:
address, prepared_for, folio, property_type, bedrooms, construction_year,
condition_rating_executive (from executive summary), condition_rating_actual (from ranking page),
works_count (integer count of works items), inspection_date, surveyor, 
fire_safety_issues (description if any, else null),
works_items (array of strings, each work item description).
Return ONLY valid JSON, no markdown.""",

    "questionnaire": """You extract structured data from Irish MTR Property Questionnaires.
These are handwritten/typed forms completed by the borrower.
Return ONLY a JSON object with these exact keys:
applicant, address, eircode, bedrooms, property_type, total_occupants,
adults, dependents, household_composition, registered_owner,
both_borrowers_mtr, consented_sale, planning_extensions, flooding, pyrite,
other_interest_party (yes/no), other_interest_name (name if yes, else null),
signed_date, development_taken_in_charge, estate_maintained_by,
q1_mtr_application (the answer to Q1 a/b in the new form, or Q11 in the old form, about the
MTR application — Yes/No),
manco_present (is there a Management Company? — Q8 new / Q5 old — Yes/No),
manco_name, manco_annual_charge, manco_arrears (only if a management company exists, else null),
signed (Yes/No — is the questionnaire signed),
registered_owner (the registered owner / name on the Folio),
unanswered_questions (array of question labels/numbers left BLANK — not answered and not
marked 'N/A'. Return [] if every question is answered or marked N/A).
Return ONLY valid JSON, no markdown.""",

    "works": """You extract structured data from Irish Homes List of Works documents.
These list works identified by condition survey, HA survey, and LA requests.
Return ONLY a JSON object with these exact keys:
address,
works_items (array of strings from "Works Identified By Conditional Survey" section),
ha_survey_items (array of strings from "Works Identified By HA Survey" section, empty if none),
la_requested_items (array of strings from "Works requested by LA" section, empty if none).
Return ONLY valid JSON, no markdown."""
}


def extract_fields_from_doc(doc_type: str, text: str,
                            images: Optional[list[str]] = None) -> dict:
    """Extract structured fields from a document using Claude.

    When `images` (base64 PNG strings) are supplied, the document is read with
    Claude's vision model — used for scanned/handwritten PDFs that have no usable
    embedded text.
    """
    if doc_type not in SYSTEM_PROMPTS:
        return {"error": f"No extraction prompt for doc type: {doc_type}"}

    client = get_client()

    if images:
        # Vision path: send page images instead of (empty/garbled) text.
        content = [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png", "data": img}}
            for img in images
        ]
        content.append({
            "type": "text",
            "text": ("This document was scanned. Read the page image(s) carefully "
                     "and extract all fields. If a value is genuinely illegible, "
                     "use null — do not guess."),
        })
        user_content = content
    else:
        # Text path: trim to avoid token limits (keep header + summary).
        max_chars = 15000
        if len(text) > max_chars:
            text = text[:10000] + "\n...[middle truncated]...\n" + text[-5000:]
        user_content = f"Extract all fields from this document:\n\n{text}"

    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPTS[doc_type],
            messages=[{"role": "user", "content": user_content}]
        )
    except anthropic.AuthenticationError as e:
        raise ExtractionAPIError(
            "Claude API authentication failed (401 — invalid x-api-key). "
            "Check that ANTHROPIC_API_KEY is a valid key from console.anthropic.com."
        ) from e
    except anthropic.APIError as e:
        # Bad model, rate limit, overloaded, connection — all abort the batch
        # so the user sees the real cause instead of an empty false-RED report.
        raise ExtractionAPIError(f"Claude API error during extraction: {e}") from e

    raw = response.content[0].text.strip()

    # Clean up any markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # A malformed JSON response is a per-document data issue, not a batch failure.
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}", "raw": raw[:500]}


def extract_all(parsed_docs: dict[str, str]) -> dict[str, dict]:
    """
    Extract fields from all parsed documents.
    Input:  {doc_type: raw_text}
    Output: {doc_type: extracted_fields_dict}
    """
    extracted = {}
    
    for doc_type, text in parsed_docs.items():
        if doc_type.startswith("error_") or doc_type == "unknown":
            extracted[doc_type] = {"error": "Document type could not be identified"}
            continue
        
        fields = extract_fields_from_doc(doc_type, text)
        extracted[doc_type] = fields
    
    return extracted


def to_typed_models(extracted: dict[str, dict]) -> dict:
    """Convert raw extracted dicts to typed Pydantic models."""
    models = {}
    
    type_map = {
        "submission": SubmissionData,
        "valuation": ValuationData,
        "survey": SurveyData,
        "questionnaire": QuestionnaireData,
        "works": WorksData,
    }
    
    for doc_type, data in extracted.items():
        if doc_type in type_map and "error" not in data:
            try:
                models[doc_type] = type_map[doc_type](**{
                    k: v for k, v in data.items()
                    if k in type_map[doc_type].model_fields
                })
            except Exception:
                models[doc_type] = data  # Fall back to raw dict
        else:
            models[doc_type] = data
    
    return models
