"""One matching run of a Match Rule, and the on-demand pieces the app needs (details, decisions, records).

run(repo, rule_uid, mode="incremental" | "full", preview=False)
    1  spec            Match Rule -> MatchSpec (validated)
    2  what changed    a record is changed when the hash of its match values + the rule's fingerprint differs
                       from its _match_hash (new records have none); also records whose steward decision is
                       not yet applied, and the remaining members of entities that lost a record
    3  standardise     DuckDB: raw -> recs (cleaned)
    4  candidates      exact-key edges and blocking pairs with at least one changed record
    5  score           fuzzy rules or the Splink model (exact keys only: nothing to score)
    6  affected set    changed records + all members of the entities they were in or now link to;
                       re-score the pairs inside that set
    7  cluster         constrained union-find (must-links, cannot-links, links by score)
    8  keys            stable Golden UIDs (majority), new ones from the counter
    9  write           records that changed, Match Pair nodes; a preview writes nothing
"""

from __future__ import annotations

import hashlib
import json

from ..hierarchy import RuleError
from ..model import K, clone, new_node, now_iso
from . import candidates, cluster as clustering, fuzzy_rules, keys, splink_model, writer
from .spec import (MATCH_PROPS, METHOD_LABELS, PAIRS_FOLDER, STATUS_LABELS, STATUS_NOT, STATUS_OPEN, STATUS_SAME, MatchSpec,
                   ensure_pairs_folder, load_spec, masters_for_rule)

ROWS_SHOWN = 200


# ---------------------------------------------------------------------------- records
def all_records(repo, spec: MatchSpec) -> list[dict]:
    out = []
    for s in spec.sources:
        out += [r for r in repo.children(s["uid"]) if r["origin"] == "loaded" or K.NT_RECORD in r["types"]]
    return out


def source_records(repo, spec: MatchSpec) -> list[dict]:
    """The records that take part: those that pass the record filter of their source."""
    from .filters import passes
    keep = passes(spec.filters)
    return [r for r in all_records(repo, spec) if keep(r)]


def record_values(spec: MatchSpec, rec: dict) -> dict[str, str | None]:
    p = rec.get("properties") or {}
    out = {}
    for f in spec.fields:
        col = f.mapping.get(rec["parent_uid"])
        v = p.get(col) if col else None
        out[f.key] = None if v is None or str(v).strip() == "" else str(v)
    return out


def match_hash(fp: str, values: dict) -> str:
    return hashlib.sha1((fp + json.dumps(values, sort_keys=True)).encode()).hexdigest()[:16]


def record_label(spec: MatchSpec, rec: dict) -> str:
    src = next((s["name"] for s in spec.sources if s["uid"] == rec["parent_uid"]), "?")
    return f"{src} · {rec.get('source_key') or rec['name']}"


def _con():
    import duckdb
    return duckdb.connect()


def _score(con, spec: MatchSpec) -> list[tuple]:
    """Score cmp -> [(a, b, score, band, rule)] with band link/review."""
    if spec.method == "fuzzy":
        candidates.build_cmp(con, spec)
        rows = con.execute(fuzzy_rules.score_sql(spec)).fetchall()
        return [(a, b, float(s), band, f"Fuzzy rule · {rule}") for a, b, s, band, rule in rows if band]
    if spec.method == "splink":
        if not spec.model:
            raise RuleError("no trained Splink model — train the model (Model & thresholds) or use another method")
        candidates.build_cmp(con, spec)
        rows = con.execute(splink_model.score_sql(spec, spec.model)).fetchall()
        return [(a, b, float(s), band, "Splink") for a, b, s, band, _r, _w in rows if band]
    return []


def decisions(repo, spec: MatchSpec) -> dict[str, dict]:
    """pair key -> Match Pair node (all statuses)."""
    if not spec.pairs_folder:
        return {}
    out = {}
    for n in repo.children(spec.pairs_folder["uid"]):
        if K.NT_MATCH_PAIR not in n["types"]:
            continue
        p = n["properties"] or {}
        a, b = p.get("Record A"), p.get("Record B")
        if a and b:
            out[writer.pair_key(a, b)] = n
    return out


# ---------------------------------------------------------------------------- the run
def run(repo, rule_uid: str, mode: str = "incremental", preview: bool = False, user: str = "mdm_match",
        overrides: dict | None = None) -> dict:
    spec = load_spec(repo, rule_uid, overrides)
    errs = spec.validate()
    if errs:
        raise RuleError("; ".join(errs))
    full = mode == "full"
    fp = spec.fingerprint()
    every = all_records(repo, spec)
    from .filters import passes
    keep = passes(spec.filters)
    recs = [r for r in every if keep(r)]
    # filtered out: they leave their entity (Golden UID cleared), so they get no golden record
    dropped = [r for r in every if not keep(r) and any((r["properties"] or {}).get(k) for k in MATCH_PROPS)]
    by_uid = {r["uid"]: r for r in recs}
    vals = {r["uid"]: record_values(spec, r) for r in recs}
    new_hash = {u: match_hash(fp, v) for u, v in vals.items()}
    old_key = {r["uid"]: (r["properties"] or {}).get("Golden UID") or None for r in recs}
    members_of: dict[str, set[str]] = {}
    for u, k in old_key.items():
        if k:
            members_of.setdefault(k, set()).add(u)

    pairs_nodes = decisions(repo, spec)
    same, cannot = [], []
    for k, n in pairs_nodes.items():
        p = n["properties"] or {}
        a, b = p["Record A"], p["Record B"]
        if p.get("Status") == STATUS_SAME:
            same.append((a, b, _float(p.get("Score"))))
        elif p.get("Status") == STATUS_NOT:
            cannot.append((a, b))

    # 2 — what changed
    if full:
        changed = set(by_uid)
        why = {"full": len(changed)}
    else:
        changed = {u for u in by_uid if (by_uid[u]["properties"] or {}).get("_match_hash") != new_hash[u]
                   or not old_key[u]}
        why = {"new_or_changed": len(changed)}
        pending = set()
        for a, b, _ in same:
            if a in by_uid and b in by_uid and (old_key[a] != old_key[b] or not old_key[a]):
                pending |= {a, b}
        for a, b in cannot:
            if a in by_uid and b in by_uid and old_key[a] and old_key[a] == old_key[b]:
                pending |= {a, b}
        why["decisions"] = len(pending - changed)
        changed |= pending
        lost = set()
        current = set(by_uid)
        for m in masters_for_rule(repo, spec.uid):
            for g in repo.children(m["uid"]):
                had = set((g["properties"] or {}).get("_members") or [])
                if had - current:
                    lost |= members_of.get(g["uid"], set())
        why["entity_lost_record"] = len(lost - changed)
        changed |= lost

    con = _con()
    try:
        candidates.load_raw(con, spec, [{"uid": r["uid"], "src": r["parent_uid"], "values": vals[r["uid"]]} for r in recs])
        candidates.build_recs(con, spec, None if full else changed)
        n_pairs = 0
        exact1 = candidates.exact_edges(con, spec) if changed else []
        if changed:
            n_pairs += candidates.build_pairs(con, spec) if spec.method != "exact" else 0
        scored1 = _score(con, spec) if changed and spec.method != "exact" else []

        # 6 — affected set
        if full:
            affected = set(by_uid)
            exact = list({(a, b): (a, b, f) for a, b, f in exact1}.values())   # one edge per pair, like incremental
            scored = scored1
        else:
            touch = set(changed)
            for a, b, _ in exact1:
                touch |= {a, b}
            for a, b, _s, band, _r in scored1:
                if band == "link":
                    touch |= {a, b}
            for a, b, _ in same:
                if a in changed or b in changed:
                    touch |= {a, b}
            affected = set()
            for u in touch:
                if u in by_uid:
                    affected.add(u)
                    if old_key[u]:
                        affected |= members_of.get(old_key[u], set())
            if affected - changed:
                candidates.set_scope(con, affected)
                exact2 = candidates.exact_edges(con, spec, both_in_scope=True)
                if spec.method != "exact":
                    n_pairs += candidates.build_pairs(con, spec, both_in_scope=True)
                scored2 = _score(con, spec) if spec.method != "exact" else []
            else:
                exact2, scored2 = [], []
            exact = list({(a, b): (a, b, f) for a, b, f in exact1 + exact2}.values())
            scored = list({(a, b): (a, b, s, band, r) for a, b, s, band, r in scored1 + scored2}.values())
    finally:
        con.close()

    # 7 — cluster the affected part
    inside = lambda a, b: a in affected and b in affected  # noqa: E731
    groups, evidence, blocked = clustering.cluster(
        sorted(affected), [e for e in exact if inside(e[0], e[1])],
        [s for s in same if inside(s[0], s[1])], [c for c in cannot if inside(c[0], c[1])],
        [(a, b, s, r) for a, b, s, band, r in scored if band == "link" and inside(a, b)])

    # 8 — stable keys
    if preview:
        counter = iter(range(1, 1_000_000))
        alloc = lambda n: [next(counter) for _ in range(n)]  # noqa: E731
    else:
        alloc = repo.allocate
    assigned, released = keys.assign(groups, {u: old_key[u] for u in affected}, alloc)
    if preview:
        assigned = [(f"new #{i + 1}" if is_new else k, g, is_new) for i, (k, g, is_new) in enumerate(assigned)]
    new_key = dict(old_key)
    stamp = now_iso()
    results = {}
    for k, g, _ in assigned:
        for u in g:
            new_key[u] = k
            score, by = evidence.get(u, (None, "Alone"))
            if len(g) == 1:
                score, by = None, "Alone"
            results[u] = {"golden": k, "score": None if score is None else round(float(score), 4), "by": by,
                          "hash": new_hash[u]}

    # review queue: review-band pairs of different entities
    review = {}
    rescored = set()
    for a, b, s, band, r in sorted(scored, key=lambda x: writer.pair_key(x[0], x[1])):  # a fixed order (see cluster)
        pk = writer.pair_key(a, b)
        rescored.add(pk)
        if band == "review" and new_key.get(a) != new_key.get(b):
            review[pk] = {"a": min(a, b), "b": max(a, b), "score": round(s, 4), "rule": r}
    # one question per pair of entities: the best-scoring pair; none where a steward already said "Not the same"
    said_not = {tuple(sorted((str(new_key.get(a)), str(new_key.get(b))))) for a, b in cannot}
    best: dict[tuple, str] = {}
    for pk, r in review.items():
        ek = tuple(sorted((str(new_key.get(r["a"])), str(new_key.get(r["b"])))))
        if ek in said_not:
            continue
        if ek not in best or r["score"] > review[best[ek]]["score"]:
            best[ek] = pk
    review = {pk: review[pk] for pk in sorted(best.values())}
    for pk, n in pairs_nodes.items():  # open pairs of re-matched records that were not scored again
        p = n["properties"] or {}
        if p.get("Record A") in changed or p.get("Record B") in changed or \
                (p.get("Record A") in affected and p.get("Record B") in affected):
            rescored.add(pk)

    filtered_of: dict[str, set[str]] = {}  # entity -> its records the record filter now leaves out
    for r in dropped:
        filtered_of.setdefault((r["properties"] or {}).get("Golden UID") or "", set()).add(r["uid"])
    filtered_of.pop("", None)
    summary = _summary(spec, by_uid, assigned, released, old_key, members_of, results, blocked, preview, filtered_of)
    stats = {"mode": "full" if full else "incremental", "preview": preview, "method": spec.method,
             "records": len(by_uid), "filtered_out": len(every) - len(recs), "left_by_filter": len(dropped),
             "changed": len(changed), "affected": len(affected), "why": why,
             "pairs_scored": n_pairs, "exact_edges": len(exact),
             "links": sum(1 for x in scored if x[3] == "link"), "review_band": len(review),
             "blocked_by_decisions": len(blocked), **summary["counts"],
             "model": (spec.model or {}).get("_version") if spec.method == "splink" else None}
    out = {"rule": {"uid": spec.uid, "name": spec.rule["name"], "method": METHOD_LABELS[spec.method]},
           "stats": stats, "rows": summary["rows"], "fingerprint": fp}
    if preview:
        out["pairs"] = {"review": len(review)}
        return out

    # 9 — write
    names = {u: by_uid[u]["name"] for u in by_uid}
    rec_writes = writer.record_writes(by_uid, results, stamp)
    for r in dropped:
        c = clone(r)
        for k in MATCH_PROPS:
            c["properties"].pop(k, None)
        rec_writes.append(c)
    folder = ensure_pairs_folder(repo, spec, user) if review else spec.pairs_folder
    ups, dels, pc = writer.pair_writes(folder, pairs_nodes, review, rescored, names, stamp, repo.allocate) \
        if folder else ([], [], {"new": 0, "updated": 0, "removed": 0})
    rule = clone(repo.get(spec.uid) or spec.rule)
    rule["properties"]["_config_hash"] = fp
    rule["properties"]["Last Run"] = (f"{stamp} · {stats['mode']} · {stats['records']} records, "
                                      f"{stats['changed']} re-matched, {pc['new']} new pairs to review")
    repo.upsert(rec_writes + ups + [rule], user)
    if dels:
        repo.hard_delete(dels)
    stats.update(records_written=len(rec_writes), pairs_new=pc["new"], pairs_updated=pc["updated"],
                 pairs_removed=pc["removed"], open_pairs=_open_count(pairs_nodes, pc, dels))
    out["pairs"] = pc
    return out


def _open_count(pairs_nodes, pc, dels) -> int:
    open_before = sum(1 for n in pairs_nodes.values() if (n["properties"] or {}).get("Status") in (None, "", STATUS_OPEN))
    return open_before + pc["new"] - len(dels)


def _float(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _summary(spec, by_uid, assigned, released, old_key, members_of, results, blocked, preview,
             filtered_of: dict | None = None) -> dict:
    filtered_of = filtered_of or {}
    rows, counts = [], {"new_entities": 0, "changed_entities": 0, "merged": 0, "split": 0, "unchanged": 0,
                        "retired": len(released)}
    kept = {k for k, _, _ in assigned} | {old_key[u] for u in by_uid if old_key[u]}
    for k, g, is_new in assigned:
        olds = {old_key[u] for u in g if old_key[u]}
        before = members_of.get(k, set()) if not is_new else set()
        out = filtered_of.get(k, set()) if not is_new else set()
        if not is_new and before == g and not out:
            counts["unchanged"] += 1
            continue
        if is_new and not olds:
            action = "New entity"
            counts["new_entities"] += 1
        elif is_new:
            action = "Split off"
            counts["split"] += 1
        elif olds - {k}:
            action = "Merged"
            counts["merged"] += 1
        else:
            joined, left = g - before, (before - g) | out
            action = "Changed" if joined and left else "Record joined" if joined else "Record left"
            counts["changed_entities"] += 1
        if len(rows) < ROWS_SHOWN:
            ms = sorted(g, key=lambda u: (by_uid[u]["parent_uid"], u))
            scores = [results[u]["score"] for u in ms if results[u]["score"] is not None]
            whys = sorted({results[u]["by"] for u in ms if results[u]["by"] != "Alone"})
            if olds - {k}:
                whys.append("joined " + ", ".join(sorted(olds - {k})))
            if out:
                whys.append(f"{len(out)} record(s) left out by the record filter")
            rows.append({"uid": k, "name": by_uid[ms[0]]["name"], "action": action, "new": is_new,
                         "records": [{"uid": u, "label": record_label(spec, by_uid[u]), "name": by_uid[u]["name"],
                                      "moved": old_key[u] != k} for u in ms],
                         "score": min(scores) if scores else None, "why": "; ".join(whys) or "no candidate linked"})
    for k in sorted(released):
        if len(rows) < ROWS_SHOWN:
            rows.append({"uid": k, "name": k, "action": "Retired", "new": False, "records": [], "score": None,
                         "why": "no records left — survivorship retires its golden record"})
    for k in sorted(set(filtered_of) - kept - set(released)):  # every record of the entity is filtered out
        counts["retired"] += 1
        if len(rows) < ROWS_SHOWN:
            rows.append({"uid": k, "name": k, "action": "Retired", "new": False, "records": [], "score": None,
                         "why": f"all {len(filtered_of[k])} record(s) left out by the record filter — "
                                "survivorship retires its golden record"})
    order = {"Merged": 0, "Split off": 1, "Changed": 2, "Record joined": 3, "Record left": 4, "New entity": 5, "Retired": 6}
    rows.sort(key=lambda r: (order.get(r["action"], 9), r["name"]))
    return {"rows": rows, "counts": counts}


# ---------------------------------------------------------------------------- app helpers
def rule_for_table(repo, table_uid: str) -> dict | None:
    for r in repo.by_types([K.NT_MATCH_RULE]):
        if table_uid in ((r["properties"] or {}).get("Sources") or []):
            return r
    return None


def rule_for_record(repo, rec: dict) -> dict | None:
    return rule_for_table(repo, rec["parent_uid"]) if rec else None


def details(repo, rule_uid: str, a: str, b: str, overrides: dict | None = None) -> dict:
    """Why do (or don't) two records match? Cleaned values per field, exact keys, the method's explanation."""
    spec = load_spec(repo, rule_uid, overrides)
    ra, rb = repo.get(a), repo.get(b)
    if not ra or not rb:
        raise RuleError("record not found")
    src = {s["uid"] for s in spec.sources}
    if ra["parent_uid"] not in src or rb["parent_uid"] not in src:
        raise RuleError("both records must come from the rule's sources")
    con = _con()
    try:
        candidates.load_raw(con, spec, [{"uid": r["uid"], "src": r["parent_uid"], "values": record_values(spec, r)}
                                        for r in (ra, rb)])
        candidates.build_recs(con, spec, None)
        cleaned = {row[0]: row for row in con.execute(
            "SELECT uid, " + ", ".join(f'"{f.key}"' for f in spec.fields) + " FROM recs").fetchall()} if spec.fields else {}
        fields = []
        for i, f in enumerate(spec.fields):
            va, vb = record_values(spec, ra)[f.key], record_values(spec, rb)[f.key]
            ca, cb = cleaned[a][i + 1], cleaned[b][i + 1]
            fields.append({"field": f.name, "role": f.role, "comparison": f.comparison, "a": va, "b": vb,
                           "a_clean": ca, "b_clean": cb, "equal": ca is not None and ca == cb})
        exact = [x["field"] for x in fields if x["role"] == "exact" and x["equal"]]
        if spec.method == "fuzzy":
            expl = fuzzy_rules.explain(spec, con, a, b)
        elif spec.method == "splink" and spec.model:
            expl = splink_model.explain(spec, con, spec.model, a, b)
        else:
            expl = {"method": spec.method}
    finally:
        con.close()
    pairs = decisions(repo, spec)
    dec = pairs.get(writer.pair_key(a, b))
    dp = (dec or {}).get("properties") or {}
    ka, kb = (ra["properties"] or {}).get("Golden UID"), (rb["properties"] or {}).get("Golden UID")
    if exact:
        verdict = f"linked: equal exact key ({', '.join(exact)})"
    elif dp.get("Status") == STATUS_SAME:
        verdict = "linked by a steward decision"
    elif expl.get("band") == "link":
        verdict = f"linked: score {expl.get('score')}"
    elif expl.get("band") == "review":
        verdict = f"review: score {expl.get('score')}"
    else:
        verdict = "not linked"
    if dp.get("Status") == STATUS_NOT:
        verdict = "never linked: steward decision Not the same"
    return {"rule": {"uid": spec.uid, "name": spec.rule["name"], "method": METHOD_LABELS[spec.method],
                     "link": spec.link_threshold, "review": spec.review_threshold},
            "a": _rec_ref(spec, ra), "b": _rec_ref(spec, rb), "fields": fields, "exact_keys": exact,
            "explanation": expl, "verdict": verdict, "same_entity_now": bool(ka and ka == kb),
            "decision": {"uid": dec["uid"], "status": STATUS_LABELS.get(dp.get("Status"), "Open"),
                         "by": dp.get("Decided By"), "at": dp.get("Decided At"), "comment": dp.get("Comment")} if dec else None}


def _rec_ref(spec, r):
    p = r["properties"] or {}
    return {"uid": r["uid"], "name": r["name"], "label": record_label(spec, r), "golden": p.get("Golden UID"),
            "score": p.get("Match Score"), "by": p.get("Matched By")}


def entity_members(repo, spec: MatchSpec, golden_uid: str) -> list[dict]:
    return [r for r in source_records(repo, spec) if (r["properties"] or {}).get("Golden UID") == golden_uid]


def decide(repo, rule_uid: str, a: str, b: str, status: str, user: str, comment: str = "",
           score: float | None = None) -> dict:
    """Create or update the Match Pair of (a, b) with a decision (status key: same | not | open)."""
    from .spec import STATUS_BY_KEY
    if status not in STATUS_BY_KEY:
        raise RuleError("status must be same, not or open")
    if a == b:
        raise RuleError("pick two different records")
    spec = load_spec(repo, rule_uid)
    for u in (a, b):
        r = repo.get(u)
        if not r or r["parent_uid"] not in {s["uid"] for s in spec.sources}:
            raise RuleError(f"{u} is not a record of the rule's sources")
    folder = ensure_pairs_folder(repo, spec, user)
    pk = writer.pair_key(a, b)
    node = decisions(repo, spec).get(pk)
    stamp = now_iso()
    st = STATUS_BY_KEY[status]
    if node is None:
        ra, rb = repo.get(min(a, b)), repo.get(max(a, b))
        node = new_node(repo.allocate(1)[0], f"{ra['name']} ↔ {rb['name']}"[:writer.NAME_MAX], [K.NT_MATCH_PAIR],
                        folder, icon="pair", max_child_depth=0,
                        properties={"Record A": ra["uid"], "Record B": rb["uid"], "Score": score, "_pair": pk,
                                    "Reason": "steward"})
    n = clone(node)
    n["properties"].update({"Status": st, "Decided By": None if st == STATUS_OPEN else user,
                            "Decided At": None if st == STATUS_OPEN else stamp, "Comment": comment or ""})
    if score is not None and n["properties"].get("Score") is None:
        n["properties"]["Score"] = score
    repo.upsert([n], user)
    return {"uid": n["uid"], "status": STATUS_LABELS[st]}


def not_this_entity(repo, rule_uid: str, record_uid: str, user: str, comment: str = "") -> dict:
    """Split a record off its entity: Not the same with every other member."""
    spec = load_spec(repo, rule_uid)
    rec = repo.get(record_uid)
    g = ((rec or {}).get("properties") or {}).get("Golden UID")
    others = [r for r in entity_members(repo, spec, g) if r["uid"] != record_uid] if g else []
    if not others:
        raise RuleError("the record is alone in its entity — nothing to split off")
    for o in others:
        decide(repo, rule_uid, record_uid, o["uid"], "not", user, comment)
    return {"decided": len(others)}


def merge_entities(repo, rule_uid: str, golden_a: str, golden_b: str, user: str, comment: str = "") -> dict:
    """Same entity between one record of each entity (the next run joins the whole entities)."""
    spec = load_spec(repo, rule_uid)
    ma, mb = entity_members(repo, spec, golden_a), entity_members(repo, spec, golden_b)
    if not ma or not mb:
        raise RuleError("both golden records need source records")
    if golden_a == golden_b:
        raise RuleError("pick another golden record")
    return decide(repo, rule_uid, min(r["uid"] for r in ma), min(r["uid"] for r in mb), "same", user, comment)


def record_matching(repo, record_uid: str) -> dict:
    """The matching panel of a source record: rule, entity members, pairs that involve it."""
    rec = repo.get(record_uid)
    if not rec:
        raise RuleError(f"{record_uid} not found")
    rule = rule_for_record(repo, rec)
    if not rule:
        return {"rule": None}
    spec = load_spec(repo, rule["uid"])
    p = rec["properties"] or {}
    g = p.get("Golden UID")
    members = entity_members(repo, spec, g) if g else [rec]
    pairs = []
    for n in decisions(repo, spec).values():
        pp = n["properties"] or {}
        if record_uid in (pp.get("Record A"), pp.get("Record B")):
            other = pp["Record B"] if pp.get("Record A") == record_uid else pp["Record A"]
            o = repo.get(other)
            pairs.append({"uid": n["uid"], "other": other, "other_label": record_label(spec, o) if o else other,
                          "status": STATUS_LABELS.get(pp.get("Status"), "Open"), "score": pp.get("Score"),
                          "by": pp.get("Decided By"), "at": pp.get("Decided At"), "comment": pp.get("Comment")})
    return {"rule": {"uid": rule["uid"], "name": rule["name"], "method": METHOD_LABELS[spec.method]},
            "golden": g, "score": p.get("Match Score"), "by": p.get("Matched By"), "at": p.get("Matched At"),
            "members": [_rec_ref(spec, r) for r in members], "pairs": pairs}


def review_queue(repo, rule_uid: str, limit: int = 200) -> dict:
    spec = load_spec(repo, rule_uid)
    rows = []
    nodes = decisions(repo, spec)
    uids = set()
    for n in nodes.values():
        p = n["properties"] or {}
        uids |= {p.get("Record A"), p.get("Record B")}
    recs = repo.get_many([u for u in uids if u])
    for n in nodes.values():
        p = n["properties"] or {}
        ra, rb = recs.get(p.get("Record A")), recs.get(p.get("Record B"))
        rows.append({"uid": n["uid"], "a": _rec_ref(spec, ra) if ra else {"uid": p.get("Record A"), "label": "(deleted)"},
                     "b": _rec_ref(spec, rb) if rb else {"uid": p.get("Record B"), "label": "(deleted)"},
                     "score": _float(p.get("Score")), "status": STATUS_LABELS.get(p.get("Status"), "Open"),
                     "reason": p.get("Reason"), "by": p.get("Decided By"), "at": p.get("Decided At"),
                     "comment": p.get("Comment"), "modified_at": n.get("modified_at")})
    open_ = sorted([r for r in rows if r["status"] == "Open"], key=lambda r: -(r["score"] or 0))
    decided = sorted([r for r in rows if r["status"] != "Open"], key=lambda r: r["at"] or "", reverse=True)
    return {"rule": {"uid": spec.uid, "name": spec.rule["name"], "method": METHOD_LABELS[spec.method],
                     "link": spec.link_threshold, "review": spec.review_threshold,
                     "pairs_folder": (spec.pairs_folder or {}).get("uid")},
            "open": open_[:limit], "open_count": len(open_), "decided": decided[:limit], "decided_count": len(decided)}


def train(repo, rule_uid: str, user: str = "mdm_match_train", activate: bool = False) -> dict:
    """Train the Splink model on all records; saved as a draft (or active right away)."""
    spec = load_spec(repo, rule_uid)
    if spec.method != "splink":
        raise RuleError("only Exact keys + Splink has a model to train")
    errs = [e for e in spec.validate() if "model" not in e]
    if errs:
        raise RuleError("; ".join(errs))
    recs = source_records(repo, spec)
    labels = []
    for n in decisions(repo, spec).values():
        p = n["properties"] or {}
        if p.get("Status") in (STATUS_SAME, STATUS_NOT):
            labels.append((p["Record A"], p["Record B"], p.get("Status") == STATUS_SAME))
    con = _con()
    try:
        candidates.load_raw(con, spec, [{"uid": r["uid"], "src": r["parent_uid"], "values": record_values(spec, r)}
                                        for r in recs])
        candidates.build_recs(con, spec, None)
        model = splink_model.train(spec, con, user, labels)
    finally:
        con.close()
    rule = clone(repo.get(spec.uid))
    rule["properties"]["_model_draft"] = json.dumps(model)
    if activate or not spec.model:
        rule["properties"]["_model"] = json.dumps(model)
    rule["properties"]["Model"] = model_label(model, active=activate or not spec.model)
    repo.upsert([rule], user)
    return {"version": model["_version"], "records": model["_records"], "accuracy": model.get("_accuracy"),
            "notes": model.get("_notes"), "active": activate or not spec.model}


def model_label(model: dict, active: bool = True) -> str:
    acc = model.get("_accuracy") or {}
    s = f"{model.get('_version')} · trained {model.get('_trained_at')} by {model.get('_trained_by')} · {model.get('_records')} records"
    if acc.get("labels"):
        s += f" · precision {acc.get('precision')} recall {acc.get('recall')} on {acc['labels']} decisions"
    return s + ("" if active else " · draft")


def activate_model(repo, rule_uid: str, user: str) -> dict:
    rule = repo.get(rule_uid)
    draft = ((rule or {}).get("properties") or {}).get("_model_draft")
    if not draft:
        raise RuleError("no trained draft model")
    r = clone(rule)
    r["properties"]["_model"] = draft
    r["properties"]["Model"] = model_label(json.loads(draft) if isinstance(draft, str) else draft)
    repo.upsert([r], user)
    return {"activated": True}


def blocking_stats(repo, rule_uid: str, overrides: dict | None = None) -> dict:
    from .filters import passes
    spec = load_spec(repo, rule_uid, overrides)
    every = all_records(repo, spec)
    keep = passes(spec.filters)
    recs = [r for r in every if keep(r)]
    per = [{"uid": s["uid"], "records": sum(1 for r in every if r["parent_uid"] == s["uid"]),
            "passed": sum(1 for r in recs if r["parent_uid"] == s["uid"])} for s in spec.sources]
    con = _con()
    try:
        candidates.load_raw(con, spec, [{"uid": r["uid"], "src": r["parent_uid"], "values": record_values(spec, r)}
                                        for r in recs])
        candidates.build_recs(con, spec, None)
        rules = candidates.blocking_counts(con, spec)
        total = candidates.build_pairs(con, spec) if spec.blocking or len(recs) <= candidates.ALL_PAIRS_LIMIT else None
    finally:
        con.close()
    n = len(recs)
    return {"records": n, "all_pairs": n * (n - 1) // 2, "rules": rules, "total": total, "sources": per}


def needs_full(repo, rule_uid: str) -> bool:
    """The rule changed since the last run (its fingerprint differs) -> the next run is effectively full."""
    spec = load_spec(repo, rule_uid)
    return (spec.rule["properties"] or {}).get("_config_hash") != spec.fingerprint()


__all__ = ["run", "details", "decide", "not_this_entity", "merge_entities", "record_matching", "review_queue",
           "train", "activate_model", "blocking_stats", "needs_full", "rule_for_table", "PAIRS_FOLDER"]
