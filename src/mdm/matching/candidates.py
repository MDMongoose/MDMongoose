"""DuckDB tables of one run: cleaned records, exact-key edges, blocking pairs.

    raw(uid, src, <field key>...)         source values of the Match Fields (strings)
    recs(uid, src, scope, <field key>...) cleaned values; scope = records this step looks at
    pairs(a, b)                           candidate pairs, a < b, at least one side in scope
    cmp(a, b, <key>_l, <key>_r ...)       the pairs with both sides' values (Splink's column naming)
"""

from __future__ import annotations

from .spec import MatchSpec
from .standardise import clean_sql

ALL_PAIRS_LIMIT = 3000  # without blocking rules every pair is compared — only for small rules


def _q(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def load_raw(con, spec: MatchSpec, records: list[dict]) -> None:
    """records: {uid, src, values: {field key: raw value}}."""
    keys = [f.key for f in spec.fields]
    rows = [{"uid": r["uid"], "src": r["src"], **{k: (None if r["values"].get(k) in (None, "") else str(r["values"][k]))
                                                  for k in keys}} for r in records]
    cols = ", ".join(["uid VARCHAR", "src VARCHAR"] + [f"{_q(k)} VARCHAR" for k in keys])
    con.execute(f"CREATE OR REPLACE TABLE raw ({cols})")
    if not rows:
        return
    try:
        import pyarrow as pa
        tbl = pa.Table.from_pylist(rows, schema=pa.schema([("uid", pa.string()), ("src", pa.string())]
                                                          + [(k, pa.string()) for k in keys]))
        con.register("raw_arrow", tbl)
        con.execute("INSERT INTO raw SELECT * FROM raw_arrow")
        con.unregister("raw_arrow")
    except ImportError:  # pragma: no cover - pyarrow is in every runtime we deploy to
        con.executemany(f"INSERT INTO raw VALUES ({', '.join('?' for _ in range(len(keys) + 2))})",
                        [[r["uid"], r["src"], *[r[k] for k in keys]] for r in rows])


def build_recs(con, spec: MatchSpec, scope: set[str] | None) -> None:
    """Cleaned values. scope None = every record is in scope (full run)."""
    sel = ", ".join(f"{clean_sql(f.cleaning, _q(f.key), spec.countries, spec.tables)} AS {_q(f.key)}" for f in spec.fields)
    con.execute("CREATE OR REPLACE TABLE scope_ids (uid VARCHAR)")
    if scope is not None and scope:
        con.executemany("INSERT INTO scope_ids VALUES (?)", [(u,) for u in scope])
    in_scope = "TRUE" if scope is None else "uid IN (SELECT uid FROM scope_ids)"
    con.execute(f"CREATE OR REPLACE TABLE recs AS SELECT uid, src, {in_scope} AS scope{', ' + sel if sel else ''} FROM raw")


def set_scope(con, scope: set[str] | None) -> None:
    con.execute("DELETE FROM scope_ids")
    if scope:
        con.executemany("INSERT INTO scope_ids VALUES (?)", [(u,) for u in scope])
    con.execute("UPDATE recs SET scope = " + ("TRUE" if scope is None else "uid IN (SELECT uid FROM scope_ids)"))


def exact_edges(con, spec: MatchSpec, both_in_scope: bool = False) -> list[tuple[str, str, str]]:
    """Records with equal cleaned exact keys: star edges (smallest uid of the group, member, field name).
    Only groups that contain a record in scope."""
    out = []
    for f in spec.exact_fields():
        k = _q(f.key)
        cond = "scope" if not both_in_scope else "scope"
        rows = con.execute(f"""
            WITH g AS (SELECT uid, {k} AS v, min(uid) OVER (PARTITION BY {k}) AS root FROM recs
                       WHERE {k} IS NOT NULL {'AND scope' if both_in_scope else ''}),
                 hit AS (SELECT DISTINCT {k} AS v FROM recs WHERE {cond} AND {k} IS NOT NULL)
            SELECT root, uid FROM g WHERE uid <> root AND v IN (SELECT v FROM hit)""").fetchall()
        out += [(a, b, f.name) for a, b in rows]
    return out


def blocking_condition(spec: MatchSpec, parts) -> str:
    conds = []
    for p in parts:
        f = spec.field_by_name(p.field)
        k = _q(f.key)
        if p.first:
            conds.append(f"left(l.{k}, {int(p.first)}) = left(r.{k}, {int(p.first)})")
        elif p.last:
            conds.append(f"right(l.{k}, {int(p.last)}) = right(r.{k}, {int(p.last)})")
        else:
            conds.append(f"l.{k} = r.{k}")
    return " AND ".join(conds)


def build_pairs(con, spec: MatchSpec, both_in_scope: bool = False) -> int:
    """Candidate pairs of the blocking rules with at least one record in scope (both, for a re-cluster)."""
    need = "l.scope AND r.scope" if both_in_scope else "(l.scope OR r.scope)"
    rules = spec.blocking_parts()
    if not rules:
        n = con.execute("SELECT count(*) FROM recs").fetchall()[0][0]
        if n > ALL_PAIRS_LIMIT:
            raise ValueError(f"{n} records and no blocking rule — add blocking rules (Blocking tab) "
                             f"so that not all {n * (n - 1) // 2:,} pairs are compared")
        sql = f"SELECT l.uid AS a, r.uid AS b FROM recs l JOIN recs r ON l.uid < r.uid WHERE {need}"
    else:
        sql = " UNION ".join(f"SELECT l.uid AS a, r.uid AS b FROM recs l JOIN recs r ON l.uid < r.uid AND "
                             f"{blocking_condition(spec, parts)} WHERE {need}" for parts in rules)
    con.execute(f"CREATE OR REPLACE TABLE pairs AS {sql}")
    return con.execute("SELECT count(*) FROM pairs").fetchall()[0][0]


def build_cmp(con, spec: MatchSpec, fields=None) -> None:
    fields = fields if fields is not None else spec.fields
    cols = ", ".join(f"l.{_q(f.key)} AS {_q(f.key + '_l')}, r.{_q(f.key)} AS {_q(f.key + '_r')}" for f in fields)
    con.execute(f"CREATE OR REPLACE TABLE cmp AS SELECT p.a, p.b{', ' + cols if cols else ''} "
                "FROM pairs p JOIN recs l ON l.uid = p.a JOIN recs r ON r.uid = p.b")


def blocking_counts(con, spec: MatchSpec) -> list[dict]:
    """Pairs per blocking rule over all records (the editor's Blocking tab)."""
    out = []
    for text, parts in zip(spec.blocking, spec.blocking_parts()):
        n = con.execute(f"SELECT count(*) FROM recs l JOIN recs r ON l.uid < r.uid AND "
                        f"{blocking_condition(spec, parts)}").fetchall()[0][0]
        out.append({"rule": text, "pairs": int(n)})
    return out
