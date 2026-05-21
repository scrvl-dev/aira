import pytest

from app.schemas.models import (
    SubmissionData, ValuationData, SurveyData, QuestionnaireData, WorksData
)


@pytest.fixture
def clean_models():
    """A fully-agreeing batch — should reconcile to GREEN."""
    return {
        "submission": SubmissionData(
            borrower_1="John Murphy", folio="KE12345",
            address="12 Oak Drive, Naas, Co Kildare", eircode="W91 AB12",
            bedrooms="3", property_type="Semi-detached",
            open_market_value="250000", market_rent="1500",
            total_occupants="4", num_dependants="2",
            both_borrowers_mtr="Yes", both_consented="Yes",
        ),
        "valuation": ValuationData(
            applicant="John Murphy", address="12 Oak Drive, Naas, Kildare",
            eircode="W91AB12", bedrooms="3", property_type="Semi-detached",
            open_market_value="252000", rental="1500",
        ),
        "survey": SurveyData(
            prepared_for="John Murphy", folio="KE12345", bedrooms="3",
            condition_rating_executive="Fair", condition_rating_actual="Fair",
            works_items=["Repair roof", "Replace window", "Rewire kitchen"],
            fire_safety_issues=None,
        ),
        "questionnaire": QuestionnaireData(
            applicant="John Murphy", bedrooms="3",
            adults="2", dependents="2",
            both_borrowers_mtr="Yes", consented_sale="Yes",
        ),
        "works": WorksData(
            works_items=["Repair roof", "Replace window", "Rewire kitchen"],
        ),
    }


@pytest.fixture
def conflicting_models(clean_models):
    """A batch with deliberate conflicts — should reconcile to RED."""
    m = dict(clean_models)
    # OMV >5% apart -> RED
    m["valuation"] = m["valuation"].model_copy(update={"open_market_value": "320000"})
    # Bedrooms conflict -> RED
    m["survey"] = m["survey"].model_copy(update={
        "bedrooms": "4",
        "condition_rating_executive": "Good",
        "condition_rating_actual": "Poor",  # internal inconsistency -> RED
        "fire_safety_issues": "Party wall fire stopping missing",
    })
    return m
