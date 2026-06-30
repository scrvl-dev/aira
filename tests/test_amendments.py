"""Tests for the SS-master amend/flag matrix (Amendments sheet, 29 Jun 2026)."""
from app.agents.amendments import (
    canonical_property_type, property_type_match, bedrooms_to_int,
    bedrooms_match, valuation_checkboxes_for, names_match_exact, build_amendments,
)
from app.schemas.models import AmendAction


# ─── Property-type map (transcribed from Amendments tab B5/D5) ──────────────
def test_property_type_equivalences():
    assert property_type_match("Detached House", "Detached") is True
    assert property_type_match("Detached House", "Detached Dwelling") is True
    assert property_type_match("Detached Bungalow", "Detached Dwelling") is True
    assert property_type_match("Detached Dormer", "Detached Dwelling") is True
    assert property_type_match("Terraced", "Mid Terrace") is True
    assert property_type_match("Terraced", "Terraced Dwelling") is True
    assert property_type_match("End of Terrace", "End Terrace") is True
    assert property_type_match("Semi-Detached House", "Semi-Detached Dwelling") is True
    assert property_type_match("Semi-Detached Bungalow", "Semi-Detached") is True
    assert property_type_match("Apartment", "Purpose built apartment") is True
    assert property_type_match("Duplex", "Duplex") is True
    # cross-family must NOT match
    assert property_type_match("Detached House", "Semi-Detached") is False


def test_cottage_and_townhouse_not_used():
    assert canonical_property_type("Cottage") is None
    assert canonical_property_type("Townhouse") is None


# ─── Bedroom map (1 = One, 2 = Two, …) ──────────────────────────────────────
def test_bedroom_map():
    assert bedrooms_to_int("Three") == 3
    assert bedrooms_to_int("3 bedrooms") == 3
    assert bedrooms_match("3", "Three") is True
    assert bedrooms_match("2", "3") is False
    assert bedrooms_match("3", "not legible") is None  # unreadable → flag, never wrong


# ─── Valuation two-category checkbox mapping ────────────────────────────────
def test_valuation_checkboxes():
    assert valuation_checkboxes_for("Semi-Detached House") == ("House", "Semi-Detached")
    assert valuation_checkboxes_for("Detached Bungalow") == ("Bungalow", "Detached")
    assert valuation_checkboxes_for("Apartment") == ("Purpose built apartment", None)


# ─── Names must match SS exactly (titles + middle names) ────────────────────
def test_exact_name_matching():
    assert names_match_exact("Mr John Patrick Murphy", "Mr John Patrick Murphy") is True
    assert names_match_exact("Mr John Patrick Murphy", "John Murphy") is False
    assert names_match_exact("John Murphy", "Mr John Murphy") is False


# ─── Per-document amend/flag rules ──────────────────────────────────────────
def _models():
    return {
        "submission": {"address": "12 Oak Drive, Naas, Co Kildare",
                       "property_type": "Semi-Detached House", "bedrooms": "3",
                       "borrower_1": "Mr John Patrick Murphy", "borrower_2": "Mrs Mary Murphy"},
        "valuation": {"address": "12 Oak Drive, Naas, Co Kildare",
                      "property_type": "Detached House", "bedrooms": "4",
                      "applicant": "John Murphy"},
        "questionnaire": {"address": "9 Other St", "property_type": "Terraced",
                          "bedrooms": "5", "applicant": "Different Person"},
        "survey": {"address": "99 Elm Road", "property_type": "Semi-Detached",
                   "bedrooms": "two"},
        "works": {"address": "12 Oak Drive, Naas, Co Kildare"},
    }


def test_valuation_proposes_checkboxes_and_beds():
    ams = build_amendments(_models())
    proposed = [a for a in ams if a.action == AmendAction.PROPOSED]
    # Only the Valuation may be PROPOSED
    assert proposed and all(a.document == "valuation" for a in proposed)
    fields = {a.field for a in proposed}
    assert any(f.startswith("Property Type") for f in fields)
    assert "Number of Bedrooms" in fields


def test_pq_is_flag_only_never_amended():
    ams = build_amendments(_models())
    pq = [a for a in ams if a.document == "questionnaire"]
    assert pq, "PQ should be checked"
    assert all(a.action != AmendAction.PROPOSED for a in pq), "PQ is handwritten — flag only"


def test_list_of_works_only_checks_address():
    ams = build_amendments(_models())
    works = [a for a in ams if a.document == "works"]
    assert len(works) == 1
    assert works[0].field == "Address"


def test_building_survey_only_address_type_beds():
    ams = build_amendments(_models())
    bs_fields = {a.field for a in ams if a.document == "survey"}
    assert any("Address" in f for f in bs_fields)
    assert any("Property Type" in f for f in bs_fields)
    assert any("Bedrooms" in f for f in bs_fields)
    # nothing else (e.g. no names) for the BS
    assert all(("Address" in f or "Property Type" in f or "Bedrooms" in f) for f in bs_fields)


def test_nothing_auto_finalised():
    ams = build_amendments(_models())
    # every non-matched amendment requires human sign-off
    for a in ams:
        if a.action in (AmendAction.PROPOSED, AmendAction.FLAG):
            assert a.requires_sign_off is True
