from app.agents.reconciler import (
    reconcile, norm_eircode, norm_numeric, numeric_rag,
)
from app.schemas.models import RAGStatus


def test_norm_eircode_strips_space_and_uppercases():
    assert norm_eircode("w91 ab12") == "W91AB12"
    assert norm_eircode("W91AB12") == "W91AB12"
    assert norm_eircode(None) == ""


def test_norm_numeric_strips_currency():
    assert norm_numeric("€405,000") == 405000.0
    assert norm_numeric("1,500") == 1500.0
    assert norm_numeric(None) is None
    assert norm_numeric("n/a") is None


def test_numeric_rag_bands():
    # identical -> GREEN
    assert numeric_rag(250000, 250000) == RAGStatus.GREEN
    # within 2% -> GREEN
    assert numeric_rag(250000, 252000) == RAGStatus.GREEN
    # between 2% and 5% -> AMBER
    assert numeric_rag(250000, 260000) == RAGStatus.AMBER
    # over 5% -> RED
    assert numeric_rag(250000, 320000) == RAGStatus.RED
    # one missing -> AMBER
    assert numeric_rag(250000, None) == RAGStatus.AMBER
    # both missing -> MISSING
    assert numeric_rag(None, None) == RAGStatus.MISSING


def test_clean_batch_has_no_red(clean_models):
    result = reconcile(clean_models, "BATCH-CLEAN")
    assert result.red_count == 0
    assert result.overall_status in (RAGStatus.GREEN, RAGStatus.AMBER)
    assert result.address.startswith("12 Oak Drive")


def test_conflicting_batch_flags_red(conflicting_models):
    result = reconcile(conflicting_models, "BATCH-CONFLICT")
    assert result.overall_status == RAGStatus.RED
    assert result.red_count >= 1

    by_field = {f.field: f for f in result.fields}
    assert by_field["Open Market Value"].status == RAGStatus.RED
    assert by_field["Number of Bedrooms"].status == RAGStatus.RED
    assert by_field["Condition Rating"].status == RAGStatus.RED
    assert by_field["Fire Safety Issues"].status == RAGStatus.RED


def test_survey_prepared_for_mismatch_is_red(clean_models):
    m = dict(clean_models)
    m["survey"] = m["survey"].model_copy(update={"prepared_for": "ACME Property Holdings Ltd"})
    result = reconcile(m, "BATCH-NAME")
    name_field = next(f for f in result.fields if f.field == "Applicant Name")
    assert name_field.status == RAGStatus.RED


def test_missing_documents_recorded():
    result = reconcile({}, "BATCH-EMPTY")
    assert any("Missing documents" in n for n in result.processing_notes)
    assert result.doc_summary["submission"] is False


def test_issues_generated_for_flagged_fields(conflicting_models):
    result = reconcile(conflicting_models, "BATCH-CONFLICT")
    assert len(result.issues) >= 1
    assert any(i.severity == RAGStatus.RED for i in result.issues)
