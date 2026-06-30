"""
Reconciler
Cross-references extracted fields across documents and assigns RAG status.
Rules-based engine — no Claude needed here, just deterministic logic.
"""
import re
from typing import Optional
from app.schemas.models import (
    RAGStatus, FieldResult, Issue, WorkItem, BatchResult,
    SubmissionData, ValuationData, SurveyData, QuestionnaireData, WorksData
)
from app.agents.eircode_finder import verify_address
from app.agents.amendments import (
    property_type_match, bedrooms_match, bedrooms_to_int,
    canonical_property_type, names_match_exact, build_amendments,
)

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


# ─── Normalisation helpers ───────────────────────────────────────────────────

def norm_str(s) -> str:
    """Normalise string for comparison."""
    if s is None:
        return ""
    return str(s).lower().strip().replace(",", "").replace(".", "")


def norm_eircode(s) -> str:
    """Normalise eircode — remove spaces, uppercase."""
    if s is None:
        return ""
    return re.sub(r"\s+", "", str(s)).upper()


# Irish eircode: routing key (letter + 2 alnum) + 4-char unique identifier.
_EIRCODE_RE = re.compile(r"[A-Za-z]\d[\dA-Za-z]\s?[A-Za-z0-9]{4}")


def extract_eircode(s) -> str:
    """Pull a real eircode out of a string (e.g. an address line), normalised.

    Returns "" when no eircode pattern is present — so a plain address is never
    mistaken for an eircode.
    """
    if s is None:
        return ""
    m = _EIRCODE_RE.search(str(s))
    return norm_eircode(m.group()) if m else ""


def norm_numeric(s) -> Optional[float]:
    """Extract numeric value from string like '€405,000' → 405000.0"""
    if s is None:
        return None
    cleaned = re.sub(r"[€,£$\s]", "", str(s))
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def norm_int(s) -> Optional[int]:
    """Extract the first integer from a value like '4' or '4 bedrooms' → 4.
    Returns None for free text such as 'not clearly legible' so unreadable
    (e.g. scanned-doc) values are ignored rather than crashing the reconciler."""
    if s is None:
        return None
    m = re.search(r"\d+", str(s))
    return int(m.group()) if m else None


def fuzzy_match(a: str, b: str, threshold: int = 80) -> bool:
    """Fuzzy string match using rapidfuzz if available."""
    if not a or not b:
        return False
    if HAS_RAPIDFUZZ:
        return fuzz.partial_ratio(norm_str(a), norm_str(b)) >= threshold
    # Fallback: simple substring check
    a_n, b_n = norm_str(a), norm_str(b)
    return a_n in b_n or b_n in a_n or a_n == b_n


def numeric_rag(val1: Optional[float], val2: Optional[float],
                amber_pct: float = 0.02, red_pct: float = 0.05) -> RAGStatus:
    """Compare two numeric values with tolerance bands."""
    if val1 is None and val2 is None:
        return RAGStatus.MISSING
    if val1 is None or val2 is None:
        return RAGStatus.AMBER
    if val1 == 0 and val2 == 0:
        return RAGStatus.GREEN
    max_val = max(abs(val1), abs(val2))
    if max_val == 0:
        return RAGStatus.GREEN
    pct_diff = abs(val1 - val2) / max_val
    if pct_diff <= amber_pct:
        return RAGStatus.GREEN
    if pct_diff <= red_pct:
        return RAGStatus.AMBER
    return RAGStatus.RED


# ─── Individual field reconcilers ────────────────────────────────────────────

def check_address(models: dict) -> FieldResult:
    s = models.get("submission")
    v = models.get("valuation")
    su = models.get("survey")
    q = models.get("questionnaire")
    w = models.get("works")

    vals = {
        "submission": getattr(s, "address", None) if hasattr(s, "address") else s.get("address") if isinstance(s, dict) else None,
        "valuation":  getattr(v, "address", None) if hasattr(v, "address") else v.get("address") if isinstance(v, dict) else None,
        "survey":     getattr(su, "address", None) if hasattr(su, "address") else su.get("address") if isinstance(su, dict) else None,
        "questionnaire": getattr(q, "address", None) if hasattr(q, "address") else q.get("address") if isinstance(q, dict) else None,
        "works":      getattr(w, "address", None) if hasattr(w, "address") else w.get("address") if isinstance(w, dict) else None,
    }

    non_null = [v for v in vals.values() if v]
    note = None
    needs_verify = False
    if not non_null:
        status = RAGStatus.RED
        note = "No address found in any document"
    elif len(non_null) == 1:
        status = RAGStatus.AMBER
    else:
        # All present addresses should fuzzy match
        base = non_null[0]
        all_match = all(fuzzy_match(base, other, 70) for other in non_null[1:])
        status = RAGStatus.GREEN if all_match else RAGStatus.AMBER
        if not all_match:
            note = "Addresses differ across documents"

    # Procedure: address MUST match Eircode Finder (applies to every document).
    base_addr = vals["submission"] or (non_null[0] if non_null else "")
    sub = models.get("submission")
    sub_ec = (getattr(sub, "eircode", None) if sub is not None and not isinstance(sub, dict)
              else (sub.get("eircode") if isinstance(sub, dict) else None))
    ec = (extract_eircode(sub_ec) or extract_eircode(base_addr)
          or next((extract_eircode(x) for x in non_null if extract_eircode(x)), ""))
    finder = verify_address(ec, base_addr)
    if finder.get("checked"):
        if finder.get("match") is False:
            status = RAGStatus.RED
        note = (note + " | " if note else "") + finder["note"]
    else:
        needs_verify = True
        note = (note + " | " if note else "") + finder["note"]

    return FieldResult(
        field="Property Address", priority="CRITICAL",
        submission=vals["submission"], valuation=vals["valuation"],
        survey=vals["survey"], questionnaire=vals["questionnaire"], works=vals["works"],
        status=status, note=note, needs_verify=needs_verify
    )


def check_eircode(models: dict) -> FieldResult:
    def get(m, key):
        if m is None: return None
        return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)

    def src_eircode(m):
        # Prefer an explicit eircode field; otherwise pull one out of the address.
        return extract_eircode(get(m, "eircode")) or extract_eircode(get(m, "address"))

    vals = {
        "submission":    src_eircode(models.get("submission")),
        "valuation":     src_eircode(models.get("valuation")),
        "survey":        src_eircode(models.get("survey")),
        "questionnaire": src_eircode(models.get("questionnaire")),
        "works":         src_eircode(models.get("works")),
    }

    non_null = [v for v in vals.values() if v]
    if not non_null:
        return FieldResult(field="Eircode", priority="CRITICAL", status=RAGStatus.RED,
                          note="No eircode found in any document")

    unique = set(non_null)
    if len(unique) == 1:
        status = RAGStatus.GREEN
        note = None
    elif len(unique) == 2:
        status = RAGStatus.AMBER
        note = f"Formatting difference: {', '.join(unique)}"
    else:
        status = RAGStatus.RED
        note = f"Multiple different eircodes: {', '.join(unique)}"

    return FieldResult(
        field="Eircode", priority="CRITICAL",
        submission=vals["submission"] or None,
        valuation=vals["valuation"] or None,
        survey=vals["survey"] or None,
        questionnaire=vals["questionnaire"] or None,
        status=status, note=note
    )


def check_applicant_name(models: dict) -> FieldResult:
    def get(m, *keys):
        if m is None: return None
        for k in keys:
            v = getattr(m, k, None) if not isinstance(m, dict) else m.get(k)
            if v: return v
        return None

    sub_name = get(models.get("submission"), "borrower_1")
    val_name = get(models.get("valuation"), "applicant")
    sur_name = get(models.get("survey"), "prepared_for")
    q_name   = get(models.get("questionnaire"), "applicant")

    # SS Borrower 1 is the master. Names must match the SS EXACTLY — including
    # all titles and middle names (per Alex Hromova's authoritative rules). The
    # old loose fuzzy match is replaced with an exact (normalised) comparison.
    status = RAGStatus.GREEN
    note = None
    conflicts = []
    if sub_name:
        for label, n in (("Valuation", val_name), ("PQ", q_name)):
            if not n:
                continue
            if names_match_exact(sub_name, n) is False:
                conflicts.append(f"{label}: '{n}'")
        if conflicts:
            status = RAGStatus.RED
            note = (f"Name must match SS exactly (incl. titles & middle names). "
                    f"SS Borrower 1: '{sub_name}' ≠ " + "; ".join(conflicts))
    else:
        # No SS name — fall back to checking the secondaries agree with each other.
        names = [n for n in [val_name, q_name] if n]
        if names and not all(names_match_exact(names[0], n) for n in names[1:]):
            status = RAGStatus.RED
            note = f"Name conflict: {', '.join(set(names))}"
        elif not names:
            status = RAGStatus.AMBER

    # Survey "prepared for" must be the borrower — a clear mismatch (e.g. a
    # company name) is RED (existing behaviour, preserved).
    ref_name = sub_name or val_name or q_name
    if sur_name and ref_name and not fuzzy_match(sur_name, ref_name, 70):
        status = RAGStatus.RED
        note = ((note + " | ") if note else "") + \
            f"Survey prepared for '{sur_name}' but borrower is '{ref_name}'"

    return FieldResult(
        field="Applicant Name", priority="CRITICAL",
        submission=sub_name, valuation=val_name, survey=sur_name,
        questionnaire=q_name, status=status, note=note
    )


def check_folio(models: dict) -> FieldResult:
    def get(m, key):
        if m is None: return None
        return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)

    sub_folio = get(models.get("submission"), "folio")
    sur_folio = get(models.get("survey"), "folio")
    val_folio = get(models.get("valuation"), "folio")
    q_folio   = get(models.get("questionnaire"), "folio")

    critical_vals = [v for v in [sub_folio, sur_folio] if v]

    if not critical_vals:
        status = RAGStatus.RED
        note = "Folio missing from submission sheet and condition survey"
    elif len(set(norm_str(v) for v in critical_vals)) == 1:
        status = RAGStatus.GREEN if len(critical_vals) >= 2 else RAGStatus.AMBER
        note = "Folio absent from valuation report" if not val_folio else None
    else:
        status = RAGStatus.RED
        note = f"Folio conflict: {sub_folio} vs {sur_folio}"

    return FieldResult(
        field="Folio Number", priority="CRITICAL",
        submission=sub_folio, valuation=val_folio,
        survey=sur_folio, questionnaire=q_folio,
        status=status, note=note
    )


def check_bedrooms(models: dict) -> FieldResult:
    def raw(m, key):
        if m is None: return None
        return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)

    def show(m, key):
        # Normalise numeric↔word (per Amendments bedroom map) to an int for display.
        n = bedrooms_to_int(raw(m, key))
        return str(n) if n is not None else None

    beds = {
        "submission":    show(models.get("submission"), "bedrooms"),
        "valuation":     show(models.get("valuation"), "bedrooms"),
        "survey":        show(models.get("survey"), "bedrooms"),
        "questionnaire": show(models.get("questionnaire"), "bedrooms"),
    }
    # SS is master: every readable value must equal the SS value (1=One etc).
    ss = beds["submission"]
    vals = [v for v in beds.values() if v]
    if ss:
        mismatches = [k for k in ("valuation", "survey", "questionnaire")
                      if beds[k] and beds[k] != ss]
        status = RAGStatus.RED if mismatches else RAGStatus.GREEN
        note = (f"Differs from SS ({ss}): " +
                ", ".join(f"{k}={beds[k]}" for k in mismatches)) if mismatches else None
    else:
        status = RAGStatus.GREEN if len(set(vals)) <= 1 else RAGStatus.RED
        note = None if len(set(vals)) <= 1 else "Bedroom counts differ across documents"

    return FieldResult(
        field="Number of Bedrooms", priority="CRITICAL",
        submission=beds["submission"], valuation=beds["valuation"],
        survey=beds["survey"], questionnaire=beds["questionnaire"],
        status=status, note=note
    )


def check_property_type(models: dict) -> FieldResult:
    def get(m, key):
        if m is None: return None
        return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)

    types = {k: get(models.get(k), "property_type")
             for k in ["submission", "valuation", "survey", "questionnaire"]}
    vals = [v for v in types.values() if v]

    if not vals:
        return FieldResult(field="Property Type", priority="HIGH",
                          submission=None, status=RAGStatus.AMBER)

    # Use the authoritative property-type map (Amendments tab). SS is master:
    # every readable secondary type must map to the same family as the SS.
    ss_type = types["submission"]
    note = None
    if ss_type:
        ss_key = canonical_property_type(ss_type)
        mismatches = []
        for k in ("valuation", "survey", "questionnaire"):
            if types[k] and property_type_match(ss_type, types[k]) is False:
                mismatches.append(f"{k}='{types[k]}'")
        if ss_key is None:
            status = RAGStatus.AMBER
            note = (f"SS property type '{ss_type}' not in the equivalence map — "
                    "verify manually (Cottage/Townhouse are not used).")
        elif mismatches:
            status = RAGStatus.RED
            note = (f"Differs from SS ({ss_type} = {ss_key}): " + ", ".join(mismatches))
        else:
            status = RAGStatus.GREEN
    else:
        keys = {canonical_property_type(v) for v in vals}
        keys.discard(None)
        status = RAGStatus.GREEN if len(keys) <= 1 else RAGStatus.RED

    return FieldResult(
        field="Property Type", priority="HIGH",
        submission=types["submission"], valuation=types["valuation"],
        survey=types["survey"], questionnaire=types["questionnaire"],
        status=status, note=note
    )


def check_omv(models: dict) -> FieldResult:
    def get(m, key):
        if m is None: return None
        return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)

    sub_val = get(models.get("submission"), "open_market_value")
    val_val = get(models.get("valuation"), "open_market_value")

    sub_num = norm_numeric(sub_val)
    val_num = norm_numeric(val_val)

    status = numeric_rag(sub_num, val_num)
    note = None
    if sub_num and val_num and sub_num != val_num:
        note = f"Difference: €{abs(sub_num - val_num):,.0f}"

    return FieldResult(
        field="Open Market Value", priority="CRITICAL",
        submission=f"€{sub_num:,.0f}" if sub_num else sub_val,
        valuation=f"€{val_num:,.0f}" if val_num else val_val,
        status=status, note=note
    )


def check_rental(models: dict) -> FieldResult:
    def get(m, key):
        if m is None: return None
        return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)

    sub_val = get(models.get("submission"), "market_rent")
    val_val = get(models.get("valuation"), "rental")

    sub_num = norm_numeric(sub_val)
    val_num = norm_numeric(val_val)

    status = numeric_rag(sub_num, val_num)

    return FieldResult(
        field="Monthly Rental", priority="CRITICAL",
        submission=f"€{sub_num:,.0f}" if sub_num else sub_val,
        valuation=f"€{val_num:,.0f}" if val_num else val_val,
        status=status
    )


def check_condition_rating(models: dict) -> FieldResult:
    su = models.get("survey")
    if su is None:
        return FieldResult(field="Condition Rating", priority="HIGH",
                          status=RAGStatus.MISSING, note="No survey document provided")

    exec_r = getattr(su, "condition_rating_executive", None) if not isinstance(su, dict) else su.get("condition_rating_executive")
    actual_r = getattr(su, "condition_rating_actual", None) if not isinstance(su, dict) else su.get("condition_rating_actual")

    if exec_r and actual_r:
        status = RAGStatus.GREEN if norm_str(exec_r) == norm_str(actual_r) else RAGStatus.RED
        note = f"Executive summary: {exec_r} | Signed ranking: {actual_r}" if status == RAGStatus.RED else None
    elif exec_r or actual_r:
        status = RAGStatus.AMBER
        note = "Only one rating found — verify consistency"
    else:
        status = RAGStatus.MISSING
        note = "No condition rating extracted"

    return FieldResult(
        field="Condition Rating", priority="HIGH",
        survey=f"Exec: {exec_r} / Ranking: {actual_r}",
        status=status, note=note
    )


def check_household(models: dict) -> FieldResult:
    def get(m, *keys):
        if m is None: return None
        for k in keys:
            v = getattr(m, k, None) if not isinstance(m, dict) else m.get(k)
            if v: return str(v)
        return None

    sub_comp = get(models.get("submission"), "household_composition")
    sub_adults = get(models.get("submission"), "total_occupants")
    sub_dep = get(models.get("submission"), "num_dependants")

    q_adults = get(models.get("questionnaire"), "adults")
    q_dep    = get(models.get("questionnaire"), "dependents")
    q_comp   = get(models.get("questionnaire"), "household_composition")

    # Build comparable strings
    sub_str = f"{sub_comp} ({sub_adults} occupants, {sub_dep} dependants)" if sub_comp else sub_adults
    q_str   = f"Adults:{q_adults} Dependents:{q_dep}" if q_adults else q_comp

    status = RAGStatus.AMBER
    note = None

    if sub_dep and q_dep:
        sub_dep_num = norm_numeric(sub_dep)
        q_dep_num   = norm_numeric(q_dep)
        if sub_dep_num is not None and q_dep_num is not None:
            status = RAGStatus.GREEN if sub_dep_num == q_dep_num else RAGStatus.RED
            if status == RAGStatus.RED:
                note = f"Submission: {sub_dep} dependants | Questionnaire: {q_dep} dependants"

    return FieldResult(
        field="Household Composition", priority="HIGH",
        submission=sub_str, questionnaire=q_str,
        status=status, note=note
    )


def check_both_borrowers(models: dict) -> FieldResult:
    def get(m, key):
        if m is None: return None
        return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)

    sub_val = get(models.get("submission"), "both_borrowers_mtr")
    q_val   = get(models.get("questionnaire"), "both_borrowers_mtr")

    if sub_val and q_val:
        status = RAGStatus.GREEN if norm_str(sub_val) == norm_str(q_val) else RAGStatus.RED
    elif sub_val or q_val:
        status = RAGStatus.AMBER
    else:
        status = RAGStatus.MISSING

    return FieldResult(
        field="Both Borrowers MTR", priority="HIGH",
        submission=sub_val, questionnaire=q_val, status=status
    )


def check_consent(models: dict) -> FieldResult:
    def get(m, key):
        if m is None: return None
        return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)

    sub_val = get(models.get("submission"), "both_consented")
    q_val   = get(models.get("questionnaire"), "consented_sale")

    status = RAGStatus.GREEN
    if sub_val and q_val:
        status = RAGStatus.GREEN if norm_str(sub_val)[0] == norm_str(q_val)[0] else RAGStatus.RED
    elif not sub_val and not q_val:
        status = RAGStatus.MISSING

    return FieldResult(
        field="Consent to Sale", priority="HIGH",
        submission=sub_val, questionnaire=q_val, status=status
    )


def check_works_count(models: dict) -> tuple[FieldResult, list[WorkItem]]:
    su = models.get("survey")
    w  = models.get("works")

    survey_items = (getattr(su, "works_items", []) if not isinstance(su, dict)
                   else su.get("works_items", [])) or []
    works_items  = (getattr(w, "works_items", []) if not isinstance(w, dict)
                   else w.get("works_items", [])) or []

    survey_count = len(survey_items) if survey_items else (
        norm_int(getattr(su, "works_count", 0)) or 0 if not isinstance(su, dict) else
        norm_int(su.get("works_count", 0)) or 0
    )
    works_count = len(works_items)

    if survey_count == 0 and works_count == 0:
        status = RAGStatus.MISSING
        note = "No works items found in either document"
    elif survey_count == works_count:
        status = RAGStatus.GREEN
        note = None
    else:
        diff = abs(survey_count - works_count)
        status = RAGStatus.AMBER if diff <= 2 else RAGStatus.RED
        note = f"Survey: {survey_count} items | List of Works: {works_count} items"

    field_result = FieldResult(
        field="Works Items Count", priority="HIGH",
        survey=str(survey_count) if survey_count else None,
        works=str(works_count) if works_count else None,
        status=status, note=note
    )

    # Build work item reconciliation
    work_items_out = []
    all_items = list(set(survey_items + works_items))
    for i, item in enumerate(all_items[:30], 1):  # cap at 30
        in_s = any(fuzzy_match(item, si, 75) for si in survey_items)
        in_w = any(fuzzy_match(item, wi, 75) for wi in works_items)
        if in_s and in_w:
            item_status = RAGStatus.GREEN
        elif in_s or in_w:
            item_status = RAGStatus.AMBER
        else:
            item_status = RAGStatus.MISSING
        work_items_out.append(WorkItem(
            number=i, description=item[:120],
            in_survey=in_s, in_works=in_w, status=item_status
        ))

    return field_result, work_items_out


def check_fire_safety(models: dict) -> FieldResult:
    su = models.get("survey")
    fire_issues = (getattr(su, "fire_safety_issues", None) if not isinstance(su, dict)
                  else su.get("fire_safety_issues")) if su else None

    if fire_issues:
        status = RAGStatus.RED
        note = f"⚠ Fire safety issue in survey: {str(fire_issues)[:100]}"
    else:
        status = RAGStatus.GREEN
        note = None

    return FieldResult(
        field="Fire Safety Issues", priority="HIGH",
        survey=fire_issues, status=status, note=note
    )


# ─── Batch Submission Procedure checks (Amendments sheet) ────────────────────

def _get(m, *keys):
    if m is None:
        return None
    for k in keys:
        v = getattr(m, k, None) if not isinstance(m, dict) else m.get(k)
        if v not in (None, ""):
            return v
    return None


def _list(m, key):
    if m is None:
        return None
    return getattr(m, key, None) if not isinstance(m, dict) else m.get(key)


def _yn(s):
    """Normalise a yes/no/N-A answer. Returns 'yes'|'no'|'na'|'other'|None."""
    if s is None:
        return None
    raw = str(s).strip().lower()
    if raw == "":
        return None
    if "n/a" in raw or raw in ("na", "n.a", "n.a."):
        return "na"
    if raw[0] == "y":
        return "yes"
    if raw[0] == "n":
        return "no"
    return "other"


def check_borrower_vs_folio(models: dict) -> FieldResult:
    """Borrower name must match the Folio (registered owner). Procedure: SS borrower
    must match PQ, V AND Folio. We match against the questionnaire's registered owner
    (the name carried from the folio / Land Direct)."""
    borrower = _get(models.get("submission"), "borrower_1") or \
        _get(models.get("valuation"), "applicant") or \
        _get(models.get("questionnaire"), "applicant")
    folio_owner = _get(models.get("questionnaire"), "registered_owner")

    if not borrower:
        status, note = RAGStatus.AMBER, "No borrower name extracted"
    elif not folio_owner:
        status, note = RAGStatus.AMBER, "Folio registered owner not extracted — verify borrower matches Folio (Land Direct)"
    elif fuzzy_match(borrower, folio_owner, 80):
        status, note = RAGStatus.GREEN, None
    else:
        status, note = RAGStatus.RED, f"Borrower '{borrower}' does not match Folio registered owner '{folio_owner}'"

    return FieldResult(field="Borrower Name vs Folio", priority="CRITICAL",
                       submission=borrower, questionnaire=folio_owner, status=status, note=note)


def check_all_answered(models: dict) -> FieldResult:
    """ALL questions must be answered (use 'N/A' if not relevant) on SS and PQ."""
    sub_u = _list(models.get("submission"), "unanswered_questions")
    q_u = _list(models.get("questionnaire"), "unanswered_questions")
    flagged, unknown = [], []
    for label, u in (("SS", sub_u), ("PQ", q_u)):
        if u is None:
            unknown.append(label)
        elif len(u) > 0:
            flagged.append(f"{label}: {len(u)} blank")
    if flagged:
        status, note = RAGStatus.RED, "Unanswered questions — " + ", ".join(flagged) + " (add 'N/A' if not relevant)"
    elif unknown:
        status, note = RAGStatus.AMBER, f"Could not confirm all answered ({', '.join(unknown)}) — check no blank questions"
    else:
        status, note = RAGStatus.GREEN, "All questions answered"
    return FieldResult(field="All Questions Answered (SS+PQ)", priority="HIGH",
                       submission=(f"{len(sub_u)} blank" if sub_u else None),
                       questionnaire=(f"{len(q_u)} blank" if q_u else None),
                       status=status, note=note)


def _value_rule(models: dict, doc: str, key: str, expected: str, label: str,
                priority: str = "HIGH") -> FieldResult:
    """Generic 'this question must say X' check."""
    val = _get(models.get(doc), key)
    yn = _yn(val)
    cols = {"submission": None, "valuation": None, "questionnaire": None}
    if doc in cols:
        cols[doc] = val
    if val is None:
        status, note = RAGStatus.AMBER, f"Not extracted — confirm answer is '{expected.upper()}'"
    elif yn == expected:
        status, note = RAGStatus.GREEN, None
    elif yn == "na":
        status, note = RAGStatus.AMBER, f"Marked N/A — confirm should be '{expected.upper()}'"
    else:
        status, note = RAGStatus.RED, f"Answer is '{val}' — must be '{expected.upper()}'"
    return FieldResult(field=label, priority=priority,
                       submission=cols["submission"], valuation=cols["valuation"],
                       questionnaire=cols["questionnaire"], status=status, note=note)


def check_manco(models: dict) -> FieldResult:
    """PQ Q8 (New)/Q5 (Old): if a Management Company exists, confirm name, annual charge and arrears."""
    q = models.get("questionnaire")
    present = _get(q, "manco_present")
    yn = _yn(present)
    name = _get(q, "manco_name")
    charge = _get(q, "manco_annual_charge")
    arrears = _get(q, "manco_arrears")
    if present is None:
        status, note = RAGStatus.AMBER, "Confirm whether a management company applies (PQ Q8/Q5)"
    elif yn in ("no", "na"):
        status, note = RAGStatus.GREEN, "No management company"
    elif yn == "yes":
        missing = [lbl for lbl, v in (("name", name), ("annual charge", charge), ("arrears", arrears)) if not v]
        if missing:
            status, note = RAGStatus.RED, "Management company present — missing: " + ", ".join(missing)
        else:
            status, note = RAGStatus.GREEN, f"{name} · charge {charge} · arrears {arrears}"
    else:
        status, note = RAGStatus.AMBER, f"Unclear management-company answer: '{present}'"
    detail = "; ".join(x for x in [name, charge, arrears] if x) or present
    return FieldResult(field="Management Company (PQ Q8/Q5)", priority="HIGH",
                       questionnaire=detail, status=status, note=note)


def check_pq_signed(models: dict) -> FieldResult:
    """PQ must be signed and dated."""
    q = models.get("questionnaire")
    signed = _get(q, "signed")
    date = _get(q, "signed_date")
    if date or _yn(signed) == "yes":
        status, note = RAGStatus.GREEN, (f"Dated {date}" if date else "Signed")
    elif signed is None and date is None:
        status, note = RAGStatus.AMBER, "Confirm PQ is signed and dated"
    else:
        status, note = RAGStatus.RED, "PQ not signed/dated"
    return FieldResult(field="PQ Signed & Dated", priority="HIGH",
                       questionnaire=(date or signed), status=status, note=note)


def check_sale_price(models: dict) -> FieldResult:
    """SS Q30 — Sale price of the property (from MTR Database)."""
    sp = _get(models.get("submission"), "sale_price")
    if sp:
        status, note = RAGStatus.GREEN, "Verify matches MTR Database"
    else:
        status, note = RAGStatus.AMBER, "Add sale price from MTR Database (SS Q30)"
    return FieldResult(field="Sale Price (SS Q30)", priority="HIGH",
                       submission=sp, status=status, note=note)


def check_comparables(models: dict) -> FieldResult:
    """Valuation Q12/Q14: 3 sale + 3 rental comparables, each matching MTR property
    type & beds, with a date let/sold."""
    v = models.get("valuation")
    sale = _list(v, "sale_comparables")
    rent = _list(v, "rental_comparables")

    if sale is None and rent is None:
        return FieldResult(field="Valuation Comparables (3 sale + 3 rental)", priority="HIGH",
                           valuation="not extracted", status=RAGStatus.AMBER,
                           note="Verify 3 sale + 3 rental comparables (Q12/Q14), each matching type & beds with a let/sold date")

    sale = sale or []
    rent = rent or []
    beds = norm_int(_get(models.get("submission"), "bedrooms") or _get(v, "bedrooms"))
    ptype = _get(models.get("submission"), "property_type") or _get(v, "property_type")

    problems = []
    if len(sale) < 3:
        problems.append(f"only {len(sale)} sale (need 3)")
    if len(rent) < 3:
        problems.append(f"only {len(rent)} rental (need 3)")

    def cval(c, attr):
        return getattr(c, attr, None) if not isinstance(c, dict) else c.get(attr)

    no_date = 0
    bed_mismatch = 0
    type_mismatch = 0
    for c in list(sale) + list(rent):
        if not cval(c, "date"):
            no_date += 1
        cb = norm_int(cval(c, "bedrooms"))
        if beds is not None and cb is not None and cb != beds:
            bed_mismatch += 1
        ct = cval(c, "property_type")
        if ptype and ct and not fuzzy_match(ptype, ct, 70):
            type_mismatch += 1
    if no_date:
        problems.append(f"{no_date} missing let/sold date")
    if bed_mismatch:
        problems.append(f"{bed_mismatch} with different beds")
    if type_mismatch:
        problems.append(f"{type_mismatch} with different property type")

    if len(sale) < 3 or len(rent) < 3:
        status = RAGStatus.RED
    elif problems:
        status = RAGStatus.AMBER
    else:
        status = RAGStatus.GREEN
    note = "; ".join(problems) if problems else f"{len(sale)} sale + {len(rent)} rental — all match type, beds & dated"
    return FieldResult(field="Valuation Comparables (3 sale + 3 rental)", priority="HIGH",
                       valuation=f"{len(sale)} sale / {len(rent)} rental", status=status, note=note)


# ─── Issue generator ─────────────────────────────────────────────────────────

def generate_issues(fields: list[FieldResult], models: dict) -> list[Issue]:
    """Generate human-readable issues from RED/AMBER fields."""
    issues = []
    for f in fields:
        if f.status == RAGStatus.RED:
            issues.append(Issue(
                severity=RAGStatus.RED,
                title=f"RED: {f.field}",
                description=f.note or f"Conflict detected across documents for {f.field}.",
                source=f"Fields: submission={f.submission} | valuation={f.valuation} | survey={f.survey} | questionnaire={f.questionnaire}"
            ))
        elif f.status == RAGStatus.AMBER:
            issues.append(Issue(
                severity=RAGStatus.AMBER,
                title=f"AMBER: {f.field}",
                description=f.note or f"Minor issue or missing data for {f.field}.",
                source=f"Check documents for this field."
            ))
    return issues


# ─── Main reconcile function ──────────────────────────────────────────────────

def reconcile(models: dict, property_ref: str = "Unknown") -> BatchResult:
    """
    Run full reconciliation across all extracted document models.
    Returns a BatchResult with RAG status per field.
    """
    fields = []
    processing_notes = []

    # Check which documents are present
    doc_summary = {
        "submission":    "submission" in models and "error" not in str(models.get("submission", {})),
        "valuation":     "valuation" in models and "error" not in str(models.get("valuation", {})),
        "survey":        "survey" in models and "error" not in str(models.get("survey", {})),
        "questionnaire": "questionnaire" in models and "error" not in str(models.get("questionnaire", {})),
        "works":         "works" in models and "error" not in str(models.get("works", {})),
    }

    missing_docs = [k for k, v in doc_summary.items() if not v]
    if missing_docs:
        processing_notes.append(f"Missing documents: {', '.join(missing_docs)}")

    # Run all field checks
    fields.append(check_address(models))
    fields.append(check_eircode(models))
    fields.append(check_applicant_name(models))
    fields.append(check_folio(models))
    fields.append(check_bedrooms(models))
    fields.append(check_property_type(models))
    fields.append(check_omv(models))
    fields.append(check_rental(models))
    fields.append(check_condition_rating(models))
    fields.append(check_household(models))
    fields.append(check_both_borrowers(models))
    fields.append(check_consent(models))
    works_field, work_items = check_works_count(models)
    fields.append(works_field)
    fields.append(check_fire_safety(models))

    # ── Batch Submission Procedure (Amendments sheet) checks ──
    fields.append(check_borrower_vs_folio(models))
    fields.append(check_all_answered(models))
    fields.append(_value_rule(models, "submission", "q2_expression_of_interest", "no",
                              "SS Q2 Expression of Interest = No"))
    fields.append(_value_rule(models, "submission", "q3_pre_assigned", "yes",
                              "SS Q3 Pre-Assigned = Yes"))
    fields.append(_value_rule(models, "questionnaire", "q1_mtr_application", "yes",
                              "PQ Q1/Q11 MTR Application = Yes"))
    fields.append(check_manco(models))
    fields.append(check_pq_signed(models))
    fields.append(check_sale_price(models))
    fields.append(check_comparables(models))

    processing_notes.append(
        "Building Survey & List of Works: address, property type and beds are cross-checked "
        "here; the remainder of those two documents is reviewed manually by AG & ND."
    )

    # ── SS-master amend/flag matrix (Amendments sheet, 29 Jun 2026) ──
    amendments = build_amendments(models)
    n_flag = sum(1 for a in amendments if a.action.value == "FLAG")
    n_prop = sum(1 for a in amendments if a.action.value == "PROPOSED")
    if amendments:
        processing_notes.append(
            f"Amend/flag matrix: {n_prop} proposed amendment(s) (Valuation checkboxes/beds — "
            f"draft PDF for sign-off), {n_flag} discrepancy(ies) flagged for human sign-off. "
            "PQ is handwritten → flag-only; nothing is auto-finalised."
        )

    # Determine address for result
    sub = models.get("submission")
    address = (getattr(sub, "address", None) if not isinstance(sub, dict)
               else sub.get("address", "Unknown Property")) or "Unknown Property"

    # Count statuses
    red   = sum(1 for f in fields if f.status == RAGStatus.RED)
    amber = sum(1 for f in fields if f.status == RAGStatus.AMBER)
    green = sum(1 for f in fields if f.status == RAGStatus.GREEN)

    overall = RAGStatus.RED if red > 0 else (RAGStatus.AMBER if amber > 0 else RAGStatus.GREEN)

    issues = generate_issues(fields, models)

    return BatchResult(
        property_ref=property_ref,
        address=address,
        overall_status=overall,
        red_count=red,
        amber_count=amber,
        green_count=green,
        fields=fields,
        issues=issues,
        works_reconciliation=work_items,
        doc_summary=doc_summary,
        processing_notes=processing_notes,
        amendments=amendments,
    )
