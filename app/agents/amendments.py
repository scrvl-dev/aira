"""
Amend / Flag engine — Submission Sheet (SS) is the single source of truth.

Implements the field-matching matrix from the "Amendments" tab of
`Batch Tool @ 29 June 2026.xlsx` (Alex Hromova, 29 Jun 2026):

  * SS is the master. Every other document is checked AGAINST the SS.
  * Default behaviour is to FLAG a discrepancy for human sign-off.
    Sensitive reports are NEVER silently finalised.
  * Per-document rules (authoritative — from the client's email):
      - Property Questionnaire (PQ): handwritten → FLAG only, never amend.
      - Valuation (V):  proposed-amend the property-type checkboxes
                        (Type of Property + Type of Building) and the
                        number of bedrooms ONLY — template untouched — and
                        always require human sign-off before any change.
      - List of Works:  ONLY the address is checked. Nothing else.
      - Building Survey (BS): only address, property type, number of bedrooms.

The property-type and bedroom value maps below are transcribed cell-for-cell
from the Amendments tab. Cottage and Townhouse are deliberately NOT mapped —
those property types are not used (per the client).
"""
from __future__ import annotations

import re
from typing import Optional

from app.schemas.models import Amendment, AmendAction


# ─── Bedroom map (Amendments tab B6): 1 = One, 2 = Two, … ────────────────────
_NUM_TO_WORD = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}
_WORD_TO_NUM = {w: n for n, w in _NUM_TO_WORD.items()}


def bedrooms_to_int(value) -> Optional[int]:
    """Normalise a bedroom value (numeric OR word) to an int.

    '3', '3 bedrooms', 'Three', 'three bed' → 3. Unreadable text → None.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if s == "":
        return None
    m = re.search(r"\d+", s)
    if m:
        return int(m.group())
    # word form — match the first number-word token present
    for token in re.findall(r"[a-z]+", s):
        if token in _WORD_TO_NUM:
            return _WORD_TO_NUM[token]
    return None


def bedrooms_match(a, b) -> Optional[bool]:
    """True/False if both readable (numeric↔word equivalent), None if either unreadable."""
    na, nb = bedrooms_to_int(a), bedrooms_to_int(b)
    if na is None or nb is None:
        return None
    return na == nb


# ─── Property-type map (Amendments tab B5 + D5) ──────────────────────────────
# Each canonical key maps every equivalent phrase that may appear across SS / PQ /
# Valuation / BS. Transcribed exactly from the sheet. NOTE: Duplex and Apartment
# are included; Cottage and Townhouse are intentionally excluded (not used).
#
# The Valuation splits property type into TWO categories that must BOTH be
# answered: "Type of Property" (House / Bungalow / Dormer / Purpose built
# apartment / Duplex) and "Type of Building" (Detached / Semi-Detached /
# Terraced / End of Terrace). We therefore model both a "form" (house shape)
# and an "attachment" (how it attaches) where the source distinguishes them.

# Canonical property-type families — the "attachment" level the Amendments tab
# (B5/D5) treats as equivalent. The sheet collapses house/bungalow/dormer onto
# the *building* type ("... = Detached Dwelling"), so equivalence is decided at
# this attachment level. The house-FORM (House/Bungalow/Dormer) is tracked
# separately below, only for ticking the Valuation "Type of Property" box.
#
# Sheet, verbatim:
#   Detached House = Detached = Detached Dwelling
#   Detached Bungalow = Detached Dwelling
#   Detached Dormer = Detached Dwelling
#   Terraced = Mid Terrace = Terraced House = Terraced Dwelling
#   End of Terrace = End Terrace
#   Semi-Detached House = Semi-Detached = Semi-Detached Dwelling
#   Semi-Detached Bungalow = Semi-Detached Dwelling
#   Apartment = Purpose built apartment
#   Duplex
# (Cottage and Townhouse are intentionally absent — not used.)
PROPERTY_TYPE_EQUIVALENCES: dict[str, list[str]] = {
    "Detached": [
        "detached house", "detached dwelling", "detached bungalow",
        "detached dormer", "detached",
    ],
    "Semi-Detached": [
        "semi-detached house", "semi-detached dwelling", "semi-detached bungalow",
        "semi detached house", "semi detached dwelling", "semi detached bungalow",
        "semi-detached", "semi detached",
    ],
    "Terraced": [
        "terraced house", "terraced dwelling", "mid terrace", "terraced",
    ],
    "End of Terrace": [
        "end of terrace", "end terrace",
    ],
    "Apartment": [
        "purpose built apartment", "apartment",
    ],
    "Duplex": [
        "duplex",
    ],
}

# House-FORM lookup (for the Valuation "Type of Property" checkbox only).
# Order matters — longest/most-specific phrase first.
_FORM_PHRASES = [
    ("Bungalow", ["bungalow"]),
    ("Dormer", ["dormer"]),
    ("Purpose built apartment", ["purpose built apartment", "apartment"]),
    ("Duplex", ["duplex"]),
    ("House", ["house", "dwelling"]),
]

# Valuation "Type of Property" checkbox labels (image3 — page 1).
# (Townhouse + Converted apartment exist on the form but are not in the map,
#  so we never tick those automatically.)
VAL_TYPE_OF_PROPERTY = ["House", "Bungalow", "Dormer", "Purpose built apartment", "Duplex"]
# Valuation "Type of Building" checkbox labels (image3 — page 1).
VAL_TYPE_OF_BUILDING = ["Detached", "Semi-Detached", "Terraced", "End of Terrace"]


def _norm(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).lower().replace("-", "-").strip())


def _phrase_in(phrase: str, text: str) -> bool:
    """True if `phrase` occurs in `text` on word boundaries (so 'detached' does
    not match inside 'semi-detached')."""
    if not phrase:
        return False
    # treat '-' as a word char so "semi-detached" is one token, but a boundary
    # exists between "semi-detached" and a preceding space.
    pat = r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])"
    return re.search(pat, text) is not None


def canonical_property_type(value) -> Optional[str]:
    """Return the canonical family key for a property-type phrase, else None.

    Matches the most specific (longest) variant first so e.g.
    "Semi-Detached House" maps to the Semi-Detached family, not Detached.
    """
    n = _norm(value)
    if not n:
        return None
    # exact match on a variant or key name wins outright
    for key, variants in PROPERTY_TYPE_EQUIVALENCES.items():
        if n == _norm(key) or any(n == v for v in variants):
            return key
    # otherwise, longest variant phrase that appears (word-boundary) in the text
    best_key, best_len = None, -1
    for key, variants in PROPERTY_TYPE_EQUIVALENCES.items():
        for v in variants:
            if _phrase_in(v, n) and len(v) > best_len:
                best_key, best_len = key, len(v)
    return best_key


def property_type_match(ss_value, other_value) -> Optional[bool]:
    """True/False if both readable and map to the same family, None if unreadable."""
    cs = canonical_property_type(ss_value)
    co = canonical_property_type(other_value)
    if cs is None or co is None:
        return None
    return cs == co


def valuation_checkboxes_for(ss_property_type) -> tuple[Optional[str], Optional[str]]:
    """Given the SS property type, return the (Type of Property, Type of Building)
    Valuation checkboxes that should be ticked, or (None, None) if undeterminable.

    Maps the canonical family to the Valuation's two-category form (image3).
    """
    key = canonical_property_type(ss_property_type)
    if key is None:
        return None, None

    n = _norm(ss_property_type)
    # form (Type of Property) — read the house-shape word from the SS phrase.
    form = None
    for label, phrases in _FORM_PHRASES:
        if any(_phrase_in(p, n) for p in phrases):
            form = label
            break
    # attachment (Type of Building) — from the canonical family.
    if key in ("Detached", "Semi-Detached", "Terraced", "End of Terrace"):
        building = key
        if form is None:
            form = "House"   # default house-form when SS gives only the attachment
    else:
        # Apartment / Duplex have no "Type of Building" on the standard form.
        building = None
        form = form or key

    return form, building


# ─── Address normalisation (List of Works / BS / V / PQ vs SS Q13) ───────────
def _norm_addr(s) -> str:
    if s is None:
        return ""
    s = str(s).lower()
    s = re.sub(r"[,.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def address_match(a, b) -> Optional[bool]:
    if not a or not b:
        return None
    na, nb = _norm_addr(a), _norm_addr(b)
    if not na or not nb:
        return None
    # token overlap — robust to line-break / ordering differences
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return None
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= 0.7


# ─── Name matching — must match SS EXACTLY incl. titles & middle names ───────
def names_match_exact(ss_name, other_name) -> Optional[bool]:
    """Names must match the SS exactly — including all titles and middle names.

    We compare on a light normalisation (case, punctuation, whitespace) only;
    we do NOT drop titles or middle names. None if either side is empty.
    """
    if not ss_name or not other_name:
        return None
    def norm(x):
        x = str(x).lower()
        x = re.sub(r"[.,]", " ", x)
        x = re.sub(r"\s+", " ", x).strip()
        return x
    return norm(ss_name) == norm(other_name)


# ─── Helper to read a field off a model-or-dict ──────────────────────────────
def _get(m, *keys):
    if m is None:
        return None
    for k in keys:
        v = getattr(m, k, None) if not isinstance(m, dict) else m.get(k)
        if v not in (None, ""):
            return v
    return None


# ─── Build the amend/flag matrix for a property ──────────────────────────────
def build_amendments(models: dict) -> list[Amendment]:
    """Compare every secondary document against the Submission Sheet (master) and
    return a list of Amendment records. Default action is FLAG; the Valuation and
    BS produce PROPOSED amendments (drafts for sign-off) for the specific
    fields the client authorised. The PQ is handwritten → FLAG only.

    NOTE: this only decides WHAT should happen. Actually rendering the proposed
    PDFs is done by pdf_amender.py during the pipeline.
    """
    ss = models.get("submission")
    out: list[Amendment] = []

    ss_address = _get(ss, "address")
    ss_type = _get(ss, "property_type")
    ss_beds = _get(ss, "bedrooms")
    ss_b1 = _get(ss, "borrower_1")
    ss_b2 = _get(ss, "borrower_2")

    def add(document, field, current, ss_value, matcher_result, *,
            amendable=False, proposed_change=None, flag_only_reason=None):
        """matcher_result: True (match) / False (mismatch) / None (unreadable)."""
        if matcher_result is True:
            out.append(Amendment(document=document, field=field,
                                  current_value=_s(current), ss_value=_s(ss_value),
                                  action=AmendAction.MATCHED,
                                  note="Matches Submission Sheet",
                                  requires_sign_off=False))
            return
        if matcher_result is None:
            # could not read one side — never auto-edit, flag for a human
            out.append(Amendment(document=document, field=field,
                                  current_value=_s(current), ss_value=_s(ss_value),
                                  action=AmendAction.MISSING,
                                  note=(flag_only_reason or
                                        "Could not read this field — verify against SS manually"),
                                  requires_sign_off=True))
            return
        # mismatch
        if amendable:
            out.append(Amendment(
                document=document, field=field,
                current_value=_s(current), ss_value=_s(ss_value),
                action=AmendAction.PROPOSED,
                note="Discrepancy vs SS — PROPOSED amendment drafted for human sign-off "
                     "before anything is finalised.",
                proposed_change=proposed_change,
                auto_applicable=True, requires_sign_off=True))
        else:
            out.append(Amendment(
                document=document, field=field,
                current_value=_s(current), ss_value=_s(ss_value),
                action=AmendAction.FLAG,
                note=(flag_only_reason or
                      "Discrepancy vs SS — FLAGGED for human sign-off (not auto-amended)."),
                auto_applicable=False, requires_sign_off=True))

    # ── Property Questionnaire (PQ) — handwritten → FLAG ONLY, never amend ──
    pq = models.get("questionnaire")
    if pq is not None:
        add("questionnaire", "Address", _get(pq, "address"), ss_address,
            address_match(ss_address, _get(pq, "address")),
            flag_only_reason="PQ is handwritten — FLAG only, do NOT amend. Verify address vs SS.")
        add("questionnaire", "Property Type", _get(pq, "property_type"), ss_type,
            property_type_match(ss_type, _get(pq, "property_type")),
            flag_only_reason="PQ is handwritten — FLAG only, do NOT amend. Verify property type vs SS.")
        add("questionnaire", "Number of Bedrooms", _get(pq, "bedrooms"), ss_beds,
            bedrooms_match(ss_beds, _get(pq, "bedrooms")),
            flag_only_reason="PQ is handwritten — FLAG only, do NOT amend. Verify bedrooms vs SS.")
        # Names: PQ Applicant 1/2 must match SS Borrower 1/2 EXACTLY.
        add("questionnaire", "Name of Applicant 1", _get(pq, "applicant"), ss_b1,
            names_match_exact(ss_b1, _get(pq, "applicant")),
            flag_only_reason="PQ handwritten — FLAG only. Applicant 1 must match SS Borrower 1 "
                             "exactly (incl. titles & middle names).")
        if ss_b2:
            add("questionnaire", "Name of Applicant 2", _get(pq, "applicant_2"), ss_b2,
                names_match_exact(ss_b2, _get(pq, "applicant_2")),
                flag_only_reason="PQ handwritten — FLAG only. Applicant 2 must match SS Borrower 2 "
                                 "exactly (incl. titles & middle names).")

    # ── Valuation (V) — proposed-amend checkboxes + bedrooms; sign-off first ──
    v = models.get("valuation")
    if v is not None:
        add("valuation", "Address", _get(v, "address"), ss_address,
            address_match(ss_address, _get(v, "address")),
            flag_only_reason="Valuation address differs from SS — FLAG (address is not auto-amended).")
        # property type → tick correct Type of Property + Type of Building boxes
        form, building = valuation_checkboxes_for(ss_type)
        change = None
        if form or building:
            ticks = []
            if form:
                ticks.append(f"Type of Property → {form}")
            if building:
                ticks.append(f"Type of Building → {building}")
            change = "Tick " + "; ".join(ticks)
        add("valuation", "Property Type (Type of Property + Type of Building)",
            _get(v, "property_type"), ss_type,
            property_type_match(ss_type, _get(v, "property_type")),
            amendable=bool(change), proposed_change=change,
            flag_only_reason="Property type differs from SS — could not map to checkboxes; FLAG.")
        # bedrooms → update numeric value
        beds_target = bedrooms_to_int(ss_beds)
        add("valuation", "Number of Bedrooms", _get(v, "bedrooms"), ss_beds,
            bedrooms_match(ss_beds, _get(v, "bedrooms")),
            amendable=beds_target is not None,
            proposed_change=(f"Set Bedrooms = {beds_target}" if beds_target is not None else None),
            flag_only_reason="Bedrooms differ from SS — value unreadable; FLAG.")
        # Names: Applicant Names must match SS exactly (FLAG — not in amend list)
        add("valuation", "Applicant Names", _get(v, "applicant"), ss_b1,
            names_match_exact(ss_b1, _get(v, "applicant")),
            flag_only_reason="Valuation Applicant Names must match SS Borrower(s) exactly "
                             "(incl. titles & middle names) — FLAG.")

    # ── Building Survey (BS) — only address, property type, bedrooms ──
    bs = models.get("survey")
    if bs is not None:
        add("survey", "Address (Relating to)", _get(bs, "address"), ss_address,
            address_match(ss_address, _get(bs, "address")),
            amendable=False,
            flag_only_reason="BS 'Relating to' address differs from SS — FLAG for sign-off.")
        add("survey", "Property Type (Description of Property)",
            _get(bs, "property_type"), ss_type,
            property_type_match(ss_type, _get(bs, "property_type")),
            amendable=False,
            flag_only_reason="BS property type differs from SS — FLAG for sign-off "
                             "(BS description is free-text; not safely auto-amendable).")
        add("survey", "Number of Bedrooms (Accommodation)",
            _get(bs, "bedrooms"), ss_beds,
            bedrooms_match(ss_beds, _get(bs, "bedrooms")),
            amendable=False,
            flag_only_reason="BS bedrooms differ from SS — FLAG for sign-off "
                             "(beds appear in prose under Accommodation; not safely auto-amendable).")

    # ── List of Works — ONLY the address is checked ──
    w = models.get("works")
    if w is not None:
        add("works", "Address", _get(w, "address"), ss_address,
            address_match(ss_address, _get(w, "address")),
            amendable=False,
            flag_only_reason="List of Works address differs from SS — FLAG (only the address is reviewed).")

    return out


def _s(v):
    return None if v is None else str(v)
