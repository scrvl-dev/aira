from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class RAGStatus(str, Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"
    MISSING = "MISSING"


class AmendAction(str, Enum):
    """Outcome of the SS-master field-matching matrix (Amendments sheet)."""
    MATCHED = "MATCHED"          # ✓ already matches the Submission Sheet
    FLAG = "FLAG"                # discrepancy — flag for human sign-off, do NOT auto-edit
    PROPOSED = "PROPOSED"        # a proposed amendment PDF was produced for sign-off
    MISSING = "MISSING"          # value couldn't be read / document absent


class Amendment(BaseModel):
    """One field on one document checked against the Submission Sheet (the master).

    Default behaviour is to FLAG. A PROPOSED action means the bot generated a
    *draft* amended PDF (checkbox ticked / value updated) for a human to sign off
    BEFORE anything is finalised — the original is never overwritten.
    """
    document: str                       # valuation / survey / questionnaire / works
    field: str                          # e.g. "Property Type", "Number of Bedrooms"
    current_value: Optional[str] = None  # what the document currently says
    ss_value: Optional[str] = None       # the Submission Sheet value it should become
    action: AmendAction = AmendAction.FLAG
    note: Optional[str] = None
    # When a proposed amendment was produced programmatically:
    proposed_change: Optional[str] = None  # human-readable "tick Semi-Detached; set beds=3"
    auto_applicable: bool = False          # could the bot safely apply it to a PDF?
    requires_sign_off: bool = True         # ALWAYS true for sensitive reports


class FieldResult(BaseModel):
    field: str
    priority: str  # CRITICAL / HIGH / MEDIUM
    submission: Optional[str] = None
    valuation: Optional[str] = None
    survey: Optional[str] = None
    questionnaire: Optional[str] = None
    works: Optional[str] = None
    status: RAGStatus
    note: Optional[str] = None
    needs_verify: bool = False  # value drew on a scanned/OCR'd source — eyeball it


class Issue(BaseModel):
    severity: RAGStatus
    title: str
    description: str
    source: str


class WorkItem(BaseModel):
    number: int
    description: str
    in_survey: bool = False
    in_works: bool = False
    status: RAGStatus = RAGStatus.MISSING


class Comparable(BaseModel):
    """A single sale/rental comparable from the Valuation (Q12/Q14)."""
    kind: Optional[str] = None          # "sale" or "rental"
    address: Optional[str] = None
    property_type: Optional[str] = None
    bedrooms: Optional[str] = None
    date: Optional[str] = None          # date let or sold
    price: Optional[str] = None


class SubmissionData(BaseModel):
    lender: Optional[str] = None
    borrower_1: Optional[str] = None
    borrower_2: Optional[str] = None
    non_residing_borrower: Optional[str] = None
    both_borrowers_mtr: Optional[str] = None
    both_consented: Optional[str] = None
    folio: Optional[str] = None
    address: Optional[str] = None
    eircode: Optional[str] = None
    property_type: Optional[str] = None
    bedrooms: Optional[str] = None
    total_occupants: Optional[str] = None
    household_composition: Optional[str] = None
    num_dependants: Optional[str] = None
    open_market_value: Optional[str] = None
    market_rent: Optional[str] = None
    sale_price: Optional[str] = None
    negative_equity: Optional[str] = None
    positive_equity: Optional[str] = None
    over_accommodation: Optional[str] = None
    aged_65_over: Optional[str] = None
    social_housing_support_number: Optional[str] = None
    # ── Batch Submission Procedure (Amendments sheet) ──
    q2_expression_of_interest: Optional[str] = None   # SS Q2 — must be "No"
    q3_pre_assigned: Optional[str] = None             # SS Q3 — must be "Yes"
    unanswered_questions: Optional[List[str]] = None  # blank questions (None = not checked, [] = all answered)


class ValuationData(BaseModel):
    applicant: Optional[str] = None
    address: Optional[str] = None
    eircode: Optional[str] = None
    bedrooms: Optional[str] = None
    property_type: Optional[str] = None
    open_market_value: Optional[str] = None
    rebuilding_cost: Optional[str] = None
    rental: Optional[str] = None
    floor_area_sqm: Optional[str] = None
    inspection_date: Optional[str] = None
    valuer: Optional[str] = None
    condition: Optional[str] = None
    letting_demand: Optional[str] = None
    folio: Optional[str] = None
    # ── Comparables (Q12 Property Demand / Q14 General Notes) ──
    sale_comparables: Optional[List[Comparable]] = None    # None = not extracted
    rental_comparables: Optional[List[Comparable]] = None


class SurveyData(BaseModel):
    address: Optional[str] = None
    prepared_for: Optional[str] = None
    folio: Optional[str] = None
    property_type: Optional[str] = None
    bedrooms: Optional[str] = None
    construction_year: Optional[str] = None
    condition_rating_executive: Optional[str] = None
    condition_rating_actual: Optional[str] = None
    works_count: Optional[str] = None
    inspection_date: Optional[str] = None
    surveyor: Optional[str] = None
    fire_safety_issues: Optional[str] = None
    works_items: List[str] = Field(default_factory=list)


class QuestionnaireData(BaseModel):
    applicant: Optional[str] = None
    applicant_2: Optional[str] = None
    address: Optional[str] = None
    eircode: Optional[str] = None
    bedrooms: Optional[str] = None
    property_type: Optional[str] = None
    total_occupants: Optional[str] = None
    adults: Optional[str] = None
    dependents: Optional[str] = None
    household_composition: Optional[str] = None
    registered_owner: Optional[str] = None
    both_borrowers_mtr: Optional[str] = None
    consented_sale: Optional[str] = None
    planning_extensions: Optional[str] = None
    flooding: Optional[str] = None
    pyrite: Optional[str] = None
    other_interest_party: Optional[str] = None
    other_interest_name: Optional[str] = None
    signed_date: Optional[str] = None
    development_taken_in_charge: Optional[str] = None
    estate_maintained_by: Optional[str] = None
    # ── Batch Submission Procedure (Amendments sheet) ──
    q1_mtr_application: Optional[str] = None    # PQ Q1a/b (New) / Q11 (Old) — must be "Yes"
    manco_present: Optional[str] = None         # PQ Q8 (New) / Q5 (Old) — is there a management company?
    manco_name: Optional[str] = None
    manco_annual_charge: Optional[str] = None
    manco_arrears: Optional[str] = None
    signed: Optional[str] = None                # signed yes/no (signed_date already present)
    unanswered_questions: Optional[List[str]] = None  # None = not checked, [] = all answered


class WorksData(BaseModel):
    address: Optional[str] = None
    works_items: List[str] = Field(default_factory=list)
    ha_survey_items: List[str] = Field(default_factory=list)
    la_requested_items: List[str] = Field(default_factory=list)


class BatchResult(BaseModel):
    property_ref: str
    address: str
    overall_status: RAGStatus
    red_count: int
    amber_count: int
    green_count: int
    fields: List[FieldResult]
    issues: List[Issue]
    works_reconciliation: List[WorkItem]
    doc_summary: dict
    processing_notes: List[str] = Field(default_factory=list)
    # ── SS-master amend/flag matrix (Amendments sheet) ──
    amendments: List[Amendment] = Field(default_factory=list)
    # filenames of proposed (draft) amended PDFs generated for human sign-off
    proposed_pdfs: List[str] = Field(default_factory=list)
    # ── Multi-batch / run context ──
    batch_id: str = ""
    doc_completeness: str = ""        # e.g. "4/5"
    cluster_confidence: str = "high"  # high / medium / low
    ocr_docs: List[str] = Field(default_factory=list)   # doc types read via OCR
    source_files: List[str] = Field(default_factory=list)


class UnassignedFile(BaseModel):
    """A file the agent parsed but could not place into any property batch."""
    filename: str
    detected_type: str = "unknown"
    reason: str = "No matching property identity (address / eircode / folio)"


class RunResult(BaseModel):
    """A whole upload run: many property batches auto-grouped from a pile of files."""
    run_id: str
    created_at: str
    total_files: int
    properties_found: int
    batches: List[BatchResult] = Field(default_factory=list)
    unassigned: List[UnassignedFile] = Field(default_factory=list)
    # Per-property RAG roll-up
    green_properties: int = 0
    amber_properties: int = 0
    red_properties: int = 0
    processing_notes: List[str] = Field(default_factory=list)
