"""Write a run's result: the matching properties of source records and the rule's Match Pair nodes.

Records: Golden UID, Match Score, Matched By, Matched At, _match_hash — only where something changed.
Match Pairs (one node per pair for its whole life, keyed by rule + both record UIDs):
    review-band pair, no node      -> new node, Status Open
    review-band pair, Open node    -> score updated
    decided node (Same / Not)      -> never touched
    Open node rescored this run but no longer in the review band (or already one entity) -> hard-deleted
"""

from __future__ import annotations

from ..model import K, clone, new_node
from .spec import STATUS_OPEN

NAME_MAX = 120


def pair_key(a: str, b: str) -> str:
    return f"{min(a, b)}|{max(a, b)}"


def record_writes(nodes: dict[str, dict], results: dict[str, dict], stamp: str) -> list[dict]:
    """results: uid -> {golden, score, by, hash}. Returns changed record nodes."""
    out = []
    for uid, res in results.items():
        n = nodes.get(uid)
        if n is None:
            continue
        p = n["properties"] or {}
        old = (p.get("Golden UID"), _f(p.get("Match Score")), p.get("Matched By"), p.get("_match_hash"))
        new = (res["golden"], res["score"], res["by"], res["hash"])
        if old == new:
            continue
        m = clone(n)
        mp = m["properties"]
        mp["Golden UID"] = res["golden"]
        if res["score"] is None:
            mp.pop("Match Score", None)
        else:
            mp["Match Score"] = res["score"]
        mp["Matched By"], mp["_match_hash"], mp["Matched At"] = res["by"], res["hash"], stamp
        out.append(m)
    return out


def _f(v):
    try:
        return None if v in (None, "") else round(float(v), 4)
    except (TypeError, ValueError):
        return None


def pair_writes(folder: dict, existing: dict[str, dict], review: dict[str, dict], rescored: set[str],
                names: dict[str, str], stamp: str, allocate) -> tuple[list[dict], list[str], dict]:
    """existing: pair key -> Match Pair node; review: pair key -> {a, b, score, rule};
    rescored: pair keys looked at in this run. Returns (nodes to upsert, uids to hard-delete, counts)."""
    ups, dels = [], []
    counts = {"new": 0, "updated": 0, "removed": 0}
    new_keys = [k for k in review if k not in existing]
    nums = iter(allocate(len(new_keys)) if new_keys else [])
    for k, r in review.items():
        node = existing.get(k)
        if node is None:
            name = f"{names.get(r['a'], r['a'])} ↔ {names.get(r['b'], r['b'])}"[:NAME_MAX]
            n = new_node(next(nums), name, [K.NT_MATCH_PAIR], folder, icon="pair", max_child_depth=0, properties={
                "Record A": r["a"], "Record B": r["b"], "Score": r["score"], "Status": STATUS_OPEN,
                "Scored At": stamp, "Reason": r.get("rule") or "", "_pair": k})
            ups.append(n)
            counts["new"] += 1
        elif (node["properties"] or {}).get("Status") in (None, "", STATUS_OPEN):
            p = node["properties"] or {}
            if _f(p.get("Score")) != _f(r["score"]) or p.get("Reason") != (r.get("rule") or ""):
                m = clone(node)
                m["properties"].update({"Score": r["score"], "Scored At": stamp, "Reason": r.get("rule") or ""})
                ups.append(m)
                counts["updated"] += 1
    for k, node in existing.items():
        status = (node["properties"] or {}).get("Status")
        if status in (None, "", STATUS_OPEN) and k in rescored and k not in review:
            dels.append(node["uid"])
            counts["removed"] += 1
    return ups, dels, counts
