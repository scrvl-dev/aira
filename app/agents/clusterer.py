"""
Document clusterer.

Given a pile of mixed, unstructured files (many properties, any order), group
them into per-property batches by reading each document's identity — eircode,
folio, address — out of its extracted fields. Identity beats filename: eircodes
and folios survive poor OCR far better than a garbled address line.

Input  records: list of dicts {filename, doc_type, fields, ocr_used}
Output (clusters, unassigned):
  clusters    -> list of {"records": [...], "confidence": "high|medium|low"}
  unassigned  -> records with no usable identity at all (e.g. OCR produced nothing)
"""
import re

from app.agents.reconciler import norm_eircode, fuzzy_match

ADDRESS_MATCH_THRESHOLD = 85


def _norm_folio(v) -> str:
    if not v:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(v).upper())


def _identity(record: dict) -> dict:
    fields = record.get("fields") or {}
    if not isinstance(fields, dict) or "error" in fields:
        return {"eircode": "", "folio": "", "address": ""}
    return {
        "eircode": norm_eircode(fields.get("eircode")) or "",
        "folio": _norm_folio(fields.get("folio")),
        "address": (fields.get("address") or "").strip(),
    }


def _has_identity(idn: dict) -> bool:
    return bool(idn["eircode"] or idn["folio"] or idn["address"])


def _same_property(a: dict, b: dict) -> tuple[bool, str]:
    """Return (match, signal) — the strongest signal that linked the two."""
    if a["eircode"] and b["eircode"] and a["eircode"] == b["eircode"]:
        return True, "eircode"
    if a["folio"] and b["folio"] and a["folio"] == b["folio"]:
        return True, "folio"
    if a["address"] and b["address"] and fuzzy_match(a["address"], b["address"], ADDRESS_MATCH_THRESHOLD):
        return True, "address"
    return False, ""


def _confidence(records: list, signals: set) -> str:
    """How sure are we this grouping is correct?"""
    doc_types = [r.get("doc_type") for r in records if r.get("doc_type") not in (None, "unknown")]
    # Duplicate doc types (e.g. two valuations) suggest a mis-merge or a dupe upload.
    if len(doc_types) != len(set(doc_types)):
        return "low"
    if len(records) == 1:
        return "high"  # nothing was merged — can't be a mis-grouping
    if "eircode" in signals or "folio" in signals:
        return "high"
    if "address" in signals:
        return "medium"  # merged on a fuzzy address only
    return "medium"


def cluster_documents(records: list[dict]) -> tuple[list[dict], list[dict]]:
    n = len(records)
    ids = [_identity(r) for r in records]

    unassigned = [records[i] for i in range(n) if not _has_identity(ids[i])]
    valid = [i for i in range(n) if _has_identity(ids[i])]

    # Union-find over the identity-bearing records.
    parent = {i: i for i in valid}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    signals_for: dict[int, set] = {i: set() for i in valid}

    for ai in range(len(valid)):
        for bi in range(ai + 1, len(valid)):
            i, j = valid[ai], valid[bi]
            match, signal = _same_property(ids[i], ids[j])
            if match:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
                signals_for[find(j)].add(signal)

    groups: dict[int, list[int]] = {}
    for i in valid:
        groups.setdefault(find(i), []).append(i)

    clusters = []
    for root, idxs in groups.items():
        recs = [records[i] for i in idxs]
        # Gather every signal observed inside this group.
        sig = set()
        for i in idxs:
            sig |= signals_for[i]
        clusters.append({"records": recs, "confidence": _confidence(recs, sig)})

    # Stable, friendly ordering: most documents first.
    clusters.sort(key=lambda c: len(c["records"]), reverse=True)
    return clusters, unassigned
