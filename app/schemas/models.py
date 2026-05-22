from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class RAGStatus(str, Enum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    RED = "RED"
    MISSING = "MISSING"


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
