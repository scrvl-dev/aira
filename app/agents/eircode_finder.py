"""
Eircode Finder verification.

Batch Submission Procedure requires: "Address must match Eircode Finder" for every
document. There is no free public Eircode API (the official ECAD is licensed via
Autoaddress / Capita), so this module:

  • calls a real lookup IF an API is configured via env vars, and
  • otherwise returns a "not checked — verify manually" result so the requirement is
    always surfaced to the reviewer (never silently passed).

Configure live verification with:
  EIRCODE_API_URL   e.g. https://api.autoaddress.com/3.0/finder/findbyeircode
  EIRCODE_API_KEY   your licensed key
  EIRCODE_API_KEY_PARAM   query-param name for the key   (default: "key")
  EIRCODE_API_EIRCODE_PARAM   query-param name for the eircode (default: "eircode")
The handler is intentionally generic: it sends {eircode_param: eircode, key_param: key},
parses the JSON response, and fuzzy-matches any returned address text against the
document address. Adapt _extract_address_from_response() to your provider if needed.
"""
import json
import os
import re
import urllib.parse
import urllib.request

try:
    from rapidfuzz import fuzz
    _HAS_FUZZ = True
except ImportError:
    _HAS_FUZZ = False


def _norm_eircode(s) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", "", str(s)).upper()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(s or "").lower()).strip()


def _similar(a: str, b: str) -> int:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0
    if _HAS_FUZZ:
        return int(fuzz.token_set_ratio(a, b))
    # crude fallback: proportion of shared tokens
    ta, tb = set(a.split()), set(b.split())
    return int(100 * len(ta & tb) / max(1, len(ta | tb)))


def _extract_address_from_response(data) -> str:
    """Best-effort pull of a postal address string out of an arbitrary JSON body."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("postalAddress", "address", "formattedAddress", "displayAddress", "ecadAddress"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, list) and v:
                return ", ".join(str(x) for x in v)
        # search one level down
        for v in data.values():
            got = _extract_address_from_response(v)
            if got:
                return got
    if isinstance(data, list) and data:
        return _extract_address_from_response(data[0])
    return ""


def verify_address(eircode: str, address: str, timeout: int = 6) -> dict:
    """
    Returns:
      {checked: bool, match: Optional[bool], canonical: str, note: str}
    checked=False means we could not verify (no key / no eircode / lookup failed) →
    caller should flag for MANUAL verification, not pass or fail automatically.
    """
    ec = _norm_eircode(eircode)
    if not ec:
        return {"checked": False, "match": None, "canonical": "",
                "note": "No eircode found to check against Eircode Finder — verify manually"}

    base = os.environ.get("EIRCODE_API_URL")
    key = os.environ.get("EIRCODE_API_KEY")
    if not base or not key:
        return {"checked": False, "match": None, "canonical": "",
                "note": "Verify address against Eircode Finder (finder.eircode.ie) — no API key configured"}

    key_param = os.environ.get("EIRCODE_API_KEY_PARAM", "key")
    ec_param = os.environ.get("EIRCODE_API_EIRCODE_PARAM", "eircode")
    try:
        qs = urllib.parse.urlencode({ec_param: ec, key_param: key})
        url = f"{base}{'&' if '?' in base else '?'}{qs}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = body
        canonical = _extract_address_from_response(data)
        if not canonical:
            return {"checked": False, "match": None, "canonical": "",
                    "note": "Eircode Finder returned no address — verify manually"}
        score = _similar(address or "", canonical)
        match = score >= 70
        return {"checked": True, "match": match, "canonical": canonical,
                "note": (f"✓ matches Eircode Finder" if match
                         else f"Address differs from Eircode Finder: '{canonical}'")}
    except Exception as e:  # network / auth / parse — degrade to manual verify
        return {"checked": False, "match": None, "canonical": "",
                "note": f"Eircode Finder lookup unavailable ({type(e).__name__}) — verify manually"}
