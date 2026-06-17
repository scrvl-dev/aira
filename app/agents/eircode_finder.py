"""
Eircode / address verification.

Batch Submission Procedure: "Address must match Eircode Finder" for every document.
There is no free public Eircode API, so we verify by **geocoding**:

  • geocode the document ADDRESS  → lat/long
  • geocode the EIRCODE           → lat/long
  • if both resolve and they are within a radius (default 1 km), the address matches
    the Eircode; if they are far apart, the address/Eircode disagree → flag.

Geocoding uses the free OpenStreetMap **Nominatim** API (no key). If a licensed
Eircode API is configured (EIRCODE_API_URL + EIRCODE_API_KEY) that is used instead.
If neither the address nor the eircode geocodes, the field is flagged for manual
verification — never silently passed.

Nominatim usage policy is respected: max ~1 request/second, descriptive User-Agent,
results cached per process. Set NOMINATIM_EMAIL for a contact address (recommended).

Env:
  EIRCODE_MATCH_RADIUS_M   match radius in metres (default 1000)
  NOMINATIM_EMAIL          contact email added to requests (Nominatim courtesy)
  EIRCODE_GEOCODE=0        disable network geocoding (→ manual-verify)
  EIRCODE_API_URL/KEY      optional licensed Eircode endpoint (overrides Nominatim)
"""
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_UA = "AIRA-IrishHomes-MTR/1.0 (Irish Homes MTR batch review; +https://github.com/scrvl-dev/aira)"
_MIN_INTERVAL = 1.1   # seconds between Nominatim calls (policy: <=1 req/sec)

_cache: dict[str, object] = {}
_last_call = [0.0]


def _norm_eircode(s) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", "", str(s)).upper()


def _haversine(a, b) -> float:
    """Distance in metres between two (lat, lon) points."""
    (lat1, lon1), (lat2, lon2) = a, b
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def _geocoding_enabled() -> bool:
    if "PYTEST_CURRENT_TEST" in os.environ:   # keep the test suite hermetic
        return False
    return os.environ.get("EIRCODE_GEOCODE", "1") != "0"


def _geocode(query: str, timeout: int):
    """Return (lat, lon) for a query via Nominatim, or None. Cached + rate-limited."""
    q = (query or "").strip()
    if not q:
        return None
    if q in _cache:
        return _cache[q]

    wait = _MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)

    params = {"q": q, "format": "jsonv2", "limit": "1", "countrycodes": "ie", "addressdetails": "0"}
    email = os.environ.get("NOMINATIM_EMAIL")
    if email:
        params["email"] = email
    url = f"{_NOMINATIM}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    pt = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if data:
            pt = (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        pt = None
    finally:
        _last_call[0] = time.time()
    _cache[q] = pt
    return pt


def _verify_via_api(eircode: str, address: str, base: str, key: str, timeout: int) -> dict:
    """Optional licensed Eircode endpoint path (generic, configurable param names)."""
    key_param = os.environ.get("EIRCODE_API_KEY_PARAM", "key")
    ec_param = os.environ.get("EIRCODE_API_EIRCODE_PARAM", "eircode")
    try:
        qs = urllib.parse.urlencode({ec_param: eircode, key_param: key})
        url = f"{base}{'&' if '?' in base else '?'}{qs}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        canonical = ""
        try:
            data = json.loads(body)
            for k in ("postalAddress", "address", "formattedAddress", "displayAddress"):
                v = data.get(k) if isinstance(data, dict) else None
                if isinstance(v, str) and v:
                    canonical = v
                    break
        except json.JSONDecodeError:
            canonical = body
        if not canonical:
            return {"checked": False, "match": None, "canonical": "",
                    "note": "Eircode API returned no address — verify manually"}
        try:
            from rapidfuzz import fuzz
            score = fuzz.token_set_ratio(address.lower(), canonical.lower())
        except Exception:
            score = 100 if address and address.lower()[:8] in canonical.lower() else 0
        match = score >= 70
        return {"checked": True, "match": match, "canonical": canonical,
                "note": "✓ matches Eircode Finder" if match
                else f"Address differs from Eircode Finder: '{canonical}'"}
    except Exception as e:
        return {"checked": False, "match": None, "canonical": "",
                "note": f"Eircode API unavailable ({type(e).__name__}) — verify manually"}


def verify_address(eircode: str, address: str, timeout: int = 8) -> dict:
    """
    Returns {checked: bool, match: Optional[bool], canonical: str, note: str}.
    checked=False → could not verify automatically; caller flags for manual check.
    """
    ec = _norm_eircode(eircode)

    # 1. Licensed Eircode endpoint, if configured.
    base = os.environ.get("EIRCODE_API_URL")
    key = os.environ.get("EIRCODE_API_KEY")
    if base and key and ec:
        return _verify_via_api(ec, address or "", base, key, timeout)

    # 2. Free Nominatim geocode-and-compare.
    if not _geocoding_enabled():
        return {"checked": False, "match": None, "canonical": "",
                "note": "Verify address against Eircode Finder (finder.eircode.ie)"}
    if not ec and not address:
        return {"checked": False, "match": None, "canonical": "",
                "note": "No address or eircode to verify"}

    radius = float(os.environ.get("EIRCODE_MATCH_RADIUS_M", "1000"))
    p_ec = _geocode(f"{ec}, Ireland", timeout) if ec else None
    p_addr = _geocode(f"{address}, Ireland", timeout) if address else None

    if p_ec and p_addr:
        d = _haversine(p_ec, p_addr)
        match = d <= radius
        return {"checked": True, "match": match, "canonical": f"~{d:.0f} m apart",
                "note": (f"✓ address & Eircode geolocate to the same place (~{d:.0f} m)" if match
                         else f"⚠ address & Eircode are ~{d:.0f} m apart (>{radius:.0f} m) — check the address matches the Eircode")}
    if p_addr and not p_ec:
        return {"checked": False, "match": None, "canonical": "",
                "note": "Eircode did not geocode on OpenStreetMap — verify the address matches the Eircode (finder.eircode.ie)"}
    if p_ec and not p_addr:
        return {"checked": False, "match": None, "canonical": "",
                "note": "Address did not geocode — verify it matches the Eircode (finder.eircode.ie)"}
    return {"checked": False, "match": None, "canonical": "",
            "note": "Neither address nor Eircode geocoded — verify manually (finder.eircode.ie)"}
