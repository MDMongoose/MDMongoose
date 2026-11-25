"""What the Spark workers run. Each task calls the same functions One Machine processing uses, so both give the
same values; Spark only decides which records meet on which worker.

mapInPandas tasks:   fn(payload, batches) -> yields pandas DataFrames
applyInPandas tasks: fn(payload, pdf) -> pandas DataFrame
"""

from __future__ import annotations

import json

import pandas as pd

from ..model import K, clone, new_node

try:   # pandas 3 keeps strings in Arrow-backed columns, which Spark's Arrow writer does not take: plain objects
    pd.set_option("future.infer_string", False)
except Exception:  # noqa: BLE001 - older pandas
    pass
from ..sqlrepo import node_to_row, row_to_node


def _rows(pdf: pd.DataFrame) -> list[dict]:
    """pandas rows as plain dicts (missing values as None)."""
    return pdf.astype(object).where(pd.notna(pdf), None).to_dict("records")


def _nodes(pdf: pd.DataFrame) -> list[dict]:
    return [row_to_node(r) for r in _rows(pdf)]


def _row_json(n: dict) -> str:
    return json.dumps(node_to_row(n), default=str)


def _frame(rows: list[dict], cols: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in cols})
    return pd.DataFrame({c: pd.Series([r.get(c) for r in rows], dtype=object) for c in cols})


# ---------------------------------------------------------------------------- matching
PREPARED = ["uid", "src", "name", "source_key", "keep", "has_match", "old_key", "old_score", "old_by", "old_hash",
            "new_hash"]


def prepared_schema(spec) -> str:
    return ("uid string, src string, name string, source_key string, keep boolean, has_match boolean, "
            "old_key string, old_score double, old_by string, old_hash string, new_hash string"
            + "".join(f", `{f.key}` string" for f in spec.fields))


def prepare_match(payload, batches):
    """Source record -> its match values, the record filter, the hash that tells whether it changed."""
    from ..matching.filters import passes
    from ..matching.run import match_hash, record_values
    from ..matching.spec import MATCH_PROPS
    from ..matching.writer import _f
    spec, fp = payload["spec"], payload["fp"]
    keep = passes(spec.filters)
    keys = [f.key for f in spec.fields]
    for pdf in batches:
        out = []
        for n in _nodes(pdf):
            p = n["properties"] or {}
            vals = record_values(spec, n)
            k = p.get("Golden UID") or None
            out.append({"uid": n["uid"], "src": n["parent_uid"], "name": n["name"], "source_key": n["source_key"],
                        "keep": bool(keep(n)), "has_match": any(p.get(x) for x in MATCH_PROPS),
                        "old_key": None if k is None else str(k), "old_score": _f(p.get("Match Score")),
                        "old_by": p.get("Matched By"), "old_hash": p.get("_match_hash"),
                        "new_hash": match_hash(fp, vals), **{key: vals[key] for key in keys}})
        yield _frame(out, PREPARED + keys)


def block_columns(spec) -> list[tuple[int, list[str]]]:
    """[(rule index, [column of each part])] — the blocking key columns of the cleaned records."""
    return [(i, [f"_bk{i}_{j}" for j in range(len(parts))]) for i, parts in enumerate(spec.blocking_parts())]


def cleaned_schema(spec) -> str:
    cols = [f"`{f.key}` string" for f in spec.fields] + [f"{c} string" for _, cs in block_columns(spec) for c in cs]
    return "uid string, src string" + "".join(", " + c for c in cols)


def clean_match(payload, batches):
    """Cleaning steps and blocking keys in DuckDB, the same SQL One Machine processing runs."""
    import duckdb
    import pyarrow as pa
    from ..matching import candidates
    spec = payload["spec"]
    keys = [f.key for f in spec.fields]
    q = candidates._q
    blocks = []
    for i, parts in enumerate(spec.blocking_parts()):
        for j, p in enumerate(parts):
            k = q(spec.field_by_name(p.field).key)
            e = f"left({k}, {int(p.first)})" if p.first else f"right({k}, {int(p.last)})" if p.last else k
            blocks.append(f"{e} AS _bk{i}_{j}")
    for pdf in batches:
        con = duckdb.connect()
        try:
            tbl = pa.Table.from_pydict({c: [None if v is None else str(v) for v in pdf[c].astype(object)
                                            .where(pd.notna(pdf[c]), None)] for c in ["uid", "src"] + keys},
                                       schema=pa.schema([(c, pa.string()) for c in ["uid", "src"] + keys]))
            con.register("raw_in", tbl)
            con.execute("CREATE TABLE raw AS SELECT * FROM raw_in")
            candidates.build_recs(con, spec, None)
            sel = ", ".join(["uid", "src"] + [q(k) for k in keys] + blocks)
            out = con.execute(f"SELECT {sel} FROM recs").fetch_df()
        finally:
            con.close()
        yield out.astype(object).where(pd.notna(out), None)


def score_fuzzy(payload, batches):
    """Fuzzy rules over candidate pairs (a, b, <key>_l, <key>_r ...) in DuckDB: the same SQL."""
    import duckdb
    import pyarrow as pa
    from ..matching import fuzzy_rules
    spec = payload["spec"]
    cols = ["a", "b"] + [f"{f.key}{s}" for f in spec.fields for s in ("_l", "_r")]
    sql = fuzzy_rules.score_sql(spec)
    for pdf in batches:
        con = duckdb.connect()
        try:
            clean = pdf.astype(object).where(pd.notna(pdf), None)
            tbl = pa.Table.from_pydict({c: list(clean[c]) for c in cols}, schema=pa.schema([(c, pa.string()) for c in cols]))
            con.register("cmp", tbl)
            rows = con.execute(sql).fetchall()
        finally:
            con.close()
        out = [{"a": a, "b": b, "score": float(s), "band": band, "rule": f"Fuzzy rule · {rule}"}
               for a, b, s, band, rule in rows if band]
        yield _frame(out, ["a", "b", "score", "band", "rule"])


def cluster(payload, pdf):
    """One connected component: the constrained union-find of One Machine processing."""
    from ..matching import cluster as clustering
    rows = _rows(pdf)
    nodes = sorted({r["a"] for r in rows if r["kind"] == "node"})
    exact = [(r["a"], r["b"], "") for r in rows if r["kind"] == "exact"]
    same = [(r["a"], r["b"], r["score"]) for r in rows if r["kind"] == "same"]
    cannot = [(r["a"], r["b"]) for r in rows if r["kind"] == "cannot"]
    links = [(r["a"], r["b"], r["score"], r["by"]) for r in rows if r["kind"] == "link"]
    groups, ev, blocked = clustering.cluster(nodes, exact, same, cannot, links)
    out = []
    for g in groups:
        gid = min(g)
        for u in g:   # the result of a record, as in mdm.matching.run (step 8)
            score, by = ev.get(u, (None, "Alone"))
            if len(g) == 1:
                score, by = None, "Alone"
            out.append({"kind": "rec", "uid": u, "gid": gid, "size": len(g),
                        "score": None if score is None else round(float(score), 4), "by": by})
    out += [{"kind": "blocked", "uid": a, "gid": b, "size": 0, "score": None, "by": why} for a, b, why in blocked]
    return _frame(out, ["kind", "uid", "gid", "size", "score", "by"])


def keys(payload, pdf):
    """Groups of entities that share previous Golden UIDs: the majority vote of One Machine processing."""
    from ..matching.keys import pick
    rows = _rows(pdf)
    groups: dict[str, set] = {}
    previous = {}
    for r in rows:
        groups.setdefault(r["gid"], set()).add(r["uid"])
        previous[r["uid"]] = r["old_key"]
    picked, used = pick(list(groups.values()), previous)
    out = [{"kind": "group", "gid": min(g), "key": k, "size": len(g)} for k, g in picked]
    out += [{"kind": "released", "gid": None, "key": k, "size": 0}
            for k in sorted({k for k in previous.values() if k} - used)]
    return _frame(out, ["kind", "gid", "key", "size"])


def record_rows(payload, batches):
    """Source records whose matching properties change -> node rows (writer.record_writes)."""
    from ..matching import writer
    from ..matching.spec import MATCH_PROPS
    stamp = payload["stamp"]
    for pdf in batches:
        out = []
        for r in _rows(pdf):
            n = row_to_node(r)
            if r["op"] == "drop":
                c = clone(n)
                for k in MATCH_PROPS:
                    c["properties"].pop(k, None)
                out.append({"uid": n["uid"], "row_json": _row_json(c)})
                continue
            res = {n["uid"]: {"golden": r["new_key"], "score": r["new_score"], "by": r["new_by"], "hash": r["new_hash"]}}
            for m in writer.record_writes({n["uid"]: n}, res, stamp):
                out.append({"uid": m["uid"], "row_json": _row_json(m)})
        yield _frame(out, ["uid", "row_json"])


def new_pair_rows(payload, batches):
    """New Match Pair nodes of the review queue (writer.pair_writes)."""
    from ..matching.spec import STATUS_OPEN
    from ..matching.writer import NAME_MAX
    folder, stamp = payload["folder"], payload["stamp"]
    for pdf in batches:
        out = []
        for r in _rows(pdf):
            name = f"{r['name_a']} ↔ {r['name_b']}"[:NAME_MAX]
            n = new_node(int(r["num"]), name, [K.NT_MATCH_PAIR], folder, icon="pair", max_child_depth=0, properties={
                "Record A": r["a"], "Record B": r["b"], "Score": r["score"], "Status": STATUS_OPEN,
                "Scored At": stamp, "Reason": r["rule"] or "", "_pair": r["pk"]})
            out.append({"uid": n["uid"], "row_json": _row_json(n)})
        yield _frame(out, ["uid", "row_json"])


def updated_pair_rows(payload, batches):
    """Open Match Pairs scored again with another score or reason."""
    from ..matching.writer import _f
    stamp = payload["stamp"]
    for pdf in batches:
        out = []
        for r in _rows(pdf):
            node = row_to_node(r)
            p = node["properties"] or {}
            if _f(p.get("Score")) != _f(r["new_score"]) or p.get("Reason") != (r["new_rule"] or ""):
                m = clone(node)
                m["properties"].update({"Score": r["new_score"], "Scored At": stamp, "Reason": r["new_rule"] or ""})
                out.append({"uid": m["uid"], "row_json": _row_json(m)})
        yield _frame(out, ["uid", "row_json"])


# ---------------------------------------------------------------------------- survivorship
def prepare_golden(payload, batches):
    """Source record -> its Golden UID and its place in the One Machine order (sources, then sort order)."""
    from ..matching.filters import passes
    keep = passes(payload["filters"])
    src_idx = payload["src_idx"]
    for pdf in batches:
        out = []
        for n in _nodes(pdf):
            k = (n["properties"] or {}).get("Golden UID")
            so = n.get("sort_order")
            out.append({"uid": n["uid"], "keep": bool(keep(n)), "golden_key": str(k) if k else None,
                        "o1": src_idx.get(n["parent_uid"], 1 << 30), "o2": so is None, "o3": so if so is not None else 0.0,
                        "o4": n.get("uid_num") or 0, "node_json": json.dumps(n, default=str)})
        yield _frame(out, ["uid", "keep", "golden_key", "o1", "o2", "o3", "o4", "node_json"])


ENTITY = ["target", "kind", "status", "action", "write", "n_members", "conflict", "conflicts_json", "row_json",
          "base", "arg", "text"]


def golden_entity(payload, pdf):
    """One entity: golden.build_golden on its source records (role m), the existing golden record (role old)
    and any other node with the Golden UID (role other). payload["resolved"] None = only collect the values
    the Enum / Link properties look up (they are looked up once, on the driver)."""
    from .. import golden
    rows = _rows(pdf)
    ctx, master, stamp, resolved = payload["ctx"], payload["master"], payload["stamp"], payload["resolved"]
    target = rows[0]["target"]
    members = [json.loads(r["node_json"]) for r in sorted((r for r in rows if r["role"] == "m"),
                                                          key=lambda r: (r["o1"], r["o2"], r["o3"], r["o4"]))]
    old = next((json.loads(r["node_json"]) for r in rows if r["role"] == "old"), None)
    other = next((json.loads(r["node_json"]) for r in rows if r["role"] == "other"), None) if old is None else None
    ctx.use_records(members)
    members = ctx.records
    scores = [x for x in (golden._float(m["properties"].get("Match Score")) for m in members) if x is not None]
    score = round(min(scores), 3) if scores else 1.0
    calls = []

    def resolve(base, arg, text):
        if resolved is None:
            calls.append((base, arg, text))
            return None, "looked up on the driver"
        return tuple(resolved.get(json.dumps([base, arg, text]), (None, f"'{text}' not found")))

    one = golden.build_golden(ctx, master, members, score, [], target, old, other, stamp, resolve, lambda u: u)
    if resolved is None:
        out = [{"target": target, "kind": "call", "base": b, "arg": a, "text": t} for b, a, t in dict.fromkeys(calls)]
        return _frame(out, ENTITY)
    if one["conflict"]:
        return _frame([{"target": target, "kind": "entity", "status": "conflict", "conflict": one["conflict"],
                        "n_members": len(members), "write": False}], ENTITY)
    return _frame([{"target": target, "kind": "entity", "status": "ok", "action": one["action"],
                    "write": bool(one["write"]), "n_members": len(members),
                    "conflicts_json": json.dumps(one["conflicts"]),
                    "row_json": _row_json(one["golden"]) if one["write"] else None}], ENTITY)


def node_json_rows(payload, batches):
    """Node rows -> uid, locked, the node as JSON (golden.build_golden takes node dicts)."""
    for pdf in batches:
        yield _frame([{"uid": n["uid"], "locked": bool(n["locked"]), "node_json": json.dumps(n, default=str)}
                      for n in _nodes(pdf)], ["uid", "locked", "node_json"])


def deleted_rows(payload, batches):
    """Nodes -> the same nodes soft-deleted (retired golden records, source links that are gone)."""
    for pdf in batches:
        yield _frame([{"uid": n["uid"], "row_json": _row_json(dict(n, is_deleted=True))} for n in _nodes(pdf)],
                     ["uid", "row_json"])


def new_link_rows(payload, batches):
    """New Child Link nodes (Link Type source) below golden records (golden.source_links)."""
    from ..links import new_link
    gpath, source = payload["gpath"], payload["source"]
    for pdf in batches:
        out = []
        for r in _rows(pdf):
            n = new_link(int(r["num"]), {"uid": r["target"], "path": gpath}, {"uid": r["uid"], "name": r["name"]},
                         source, node_origin="generated", locked=True, max_child_depth=0)
            n["sort_order"] = float(r["i"] + 1)
            out.append({"uid": n["uid"], "row_json": _row_json(n)})
        yield _frame(out, ["uid", "row_json"])
