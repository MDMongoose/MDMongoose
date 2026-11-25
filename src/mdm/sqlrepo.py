"""SQL implementation of NodeRepo for a SQL warehouse (the app) or Spark (jobs).

Complex columns are read as JSON text (to_json) and written through one JSON
parameter that is exploded with from_json, so a batch of nodes is one MERGE —
one transaction — whatever its size.
"""

from __future__ import annotations

import contextvars
import json
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any

from .model import K, kind_of, path_key, path_key_of
from .repo import ConflictError, NodeRepo, as_params, column_sql

ROW_SCHEMA = (
    "ARRAY<STRUCT<uid: STRING, uid_num: BIGINT, name: STRING, types: ARRAY<STRING>, parent_uid: STRING, "
    "path: ARRAY<STRING>, depth: INT, sort_order: DOUBLE, "
    "child_links: ARRAY<STRUCT<uid: STRING, link_type: STRING, sort_order: DOUBLE>>, origin: STRING, "
    "source_table_uid: STRING, source_key: STRING, record_hash: STRING, properties_json: STRING, "
    "user_defined: MAP<STRING, BOOLEAN>, icon: STRING, max_child_depth: INT, allowed_child_types: ARRAY<STRING>, "
    "owner: STRING, steward: ARRAY<STRING>, locked: BOOLEAN, is_deleted: BOOLEAN, expected_modified_at: STRING, "
    "path_key: STRING, kind: STRING>>"
)

SELECT_COLS = """uid, uid_num, name, to_json(types) AS types_json, parent_uid, to_json(path) AS path_json, depth,
  sort_order, to_json(child_links) AS child_links_json, origin, source_table_uid, source_key, record_hash,
  to_json(properties) AS properties_json, to_json(user_defined) AS user_defined_json, icon, max_child_depth,
  to_json(allowed_child_types) AS allowed_json, owner, to_json(steward) AS steward_json, locked, is_deleted,
  CAST(modified_at AS STRING) AS modified_at, modified_by"""

DDL = """CREATE TABLE IF NOT EXISTS {table} (
  uid STRING NOT NULL, uid_num BIGINT NOT NULL, name STRING, types ARRAY<STRING>, parent_uid STRING,
  path ARRAY<STRING>, depth INT, sort_order DOUBLE,
  child_links ARRAY<STRUCT<uid: STRING, link_type: STRING, sort_order: DOUBLE>>,
  origin STRING, source_table_uid STRING, source_key STRING, record_hash STRING,
  properties VARIANT, user_defined MAP<STRING, BOOLEAN>, icon STRING, max_child_depth INT,
  allowed_child_types ARRAY<STRING>, owner STRING, steward ARRAY<STRING>, locked BOOLEAN,
  is_deleted BOOLEAN, modified_at TIMESTAMP, modified_by STRING,
  path_key STRING, kind STRING
)
CLUSTER BY (path_key)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'true',
  'delta.enableRowTracking' = 'true',
  'delta.enableDeletionVectors' = 'true',
  'delta.dataSkippingStringPrefixLength' = '96'
)"""

# Upgrading a table created before path_key/kind existed (run by mdm_init; idempotent).
MIGRATION = [
    "ALTER TABLE {table} ADD COLUMNS (path_key STRING, kind STRING)",
    """UPDATE {table} SET
         path_key = concat('/', array_join(transform(concat(path, array(uid)), u -> substring(u, 5)), '/'), '/'),
         kind = coalesce(try_variant_get(properties, '$.kind', 'string'),
                         CASE WHEN try_variant_get(properties, '$.handler', 'string') IS NOT NULL THEN 'Action' END)
       WHERE path_key IS NULL""",
    "ALTER TABLE {table} SET TBLPROPERTIES ('delta.dataSkippingStringPrefixLength' = '96')",
    "ALTER TABLE {table} CLUSTER BY (path_key)",
]
OPTIMIZE = "OPTIMIZE {table} FULL"


_IDENT = re.compile(r"^[A-Za-z0-9_`.\-]+$")


class SqlExecutor(ABC):
    @abstractmethod
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]: ...

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> dict:
        """A write retries when Delta reports a conflict with a write that ran at the same time (two golden-record
        refreshes, a load and an edit…): the statement is simply run again on the new table version."""
        write = sql.lstrip().split(None, 1)[0].upper() in ("MERGE", "UPDATE", "DELETE", "INSERT")
        for attempt in range(6):
            try:
                rows = self.query(sql, params)
                return rows[0] if rows else {}
            except Exception as e:  # noqa: BLE001
                if not write or attempt == 5 or not is_concurrency_conflict(e):
                    raise
                time.sleep(0.5 * 2 ** attempt + random.random())
        return {}

    def query_fresh(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
        """A read that must never come from a cache (optimistic-concurrency checks)."""
        return self.query(sql, params)


# Per request (the app sets it): every statement that reaches the engine, with its duration — the
# Server-Timing header and the slow-request log show them.
STATS: contextvars.ContextVar = contextvars.ContextVar("mdm_sql_stats", default=None)


def _timed(fn):
    def wrap(self, sql, params=None):
        stats = STATS.get()
        if stats is None:
            return fn(self, sql, params)
        t = time.perf_counter()
        try:
            return fn(self, sql, params)
        finally:
            stats.append((" ".join(sql.split())[:100], (time.perf_counter() - t) * 1000))
    return wrap


_CONFLICTS = ("CONCURRENT", "ConcurrentAppend", "ConcurrentDelete", "ConcurrentTransaction", "ConcurrentWrite",
              "MetadataChanged", "ProtocolChanged")


def is_concurrency_conflict(e: Exception) -> bool:
    """A Delta write conflict (DELTA_CONCURRENT_* / Concurrent*Exception): safe to retry the statement."""
    text = f"{type(e).__name__} {e}"
    return any(c in text for c in _CONFLICTS)


class CachingExecutor(SqlExecutor):
    """Read-through memory cache in front of an executor, for the app.

    Reads (query) of MDMNodes / node_index are cached by (sql, params). Any write the app makes
    (execute) empties the cache at once. Writes made elsewhere (load and golden jobs, other app
    instances) are noticed by checking the table's Delta version at most every `check_seconds`;
    a new version empties the cache. So navigation repeats no warehouse round trips, and data is
    at most `check_seconds` old for changes made outside this app instance.

    background=True (the app): while the app is in use, that version check runs in a background thread, so
    no request waits for it; after a quiet spell (IDLE seconds, the warehouse may have stopped) the first read
    checks it itself, so nothing older than the last check is served."""

    IDLE = 120.0

    def __init__(self, inner: SqlExecutor, table: str, watch: tuple[str, ...] = (), check_seconds: float = 10,
                 max_entries: int = 20000, background: bool = False):
        import threading
        from collections import OrderedDict
        self.inner, self.table, self.watch = inner, table, tuple(w for w in (table, *watch) if w)
        self.check_seconds, self.max_entries = check_seconds, max_entries
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._checked = 0.0
        self._version = None
        self.hits = self.misses = 0
        self.background = background
        self._last_read = 0.0
        self._watcher = None

    def clear(self):
        with self._lock:
            self._data.clear()

    def _watch(self):
        while True:
            time.sleep(max(1.0, self.check_seconds))
            if time.monotonic() - self._last_read < self.IDLE:   # only while the app is in use
                try:
                    self._check_version(force=True)
                except Exception:  # noqa: BLE001
                    pass

    def _before_read(self):
        now = time.monotonic()
        idle = now - self._last_read > self.IDLE
        self._last_read = now
        if not self.background:
            self._check_version()
            return
        if self._watcher is None:
            import threading
            self._watcher = threading.Thread(target=self._watch, name="mdm-cache-version", daemon=True)
            self._watcher.start()
        if idle:
            self._check_version(force=True)

    def _check_version(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._checked < self.check_seconds:
            return
        self._checked = now
        try:
            v = self.inner.query(f"DESCRIBE HISTORY {self.table} LIMIT 1")[0]["version"]
        except Exception:  # noqa: BLE001 - no history access: fall back to time-based expiry
            v = now
        if v != self._version:
            self._version = v
            self.clear()

    def query(self, sql, params=None):
        if not any(w in sql for w in self.watch):
            return self.inner.query(sql, params)
        self._before_read()
        key = (sql, json.dumps(params or {}, sort_keys=True, default=str))
        with self._lock:
            hit = self._data.get(key)
            if hit is not None:
                self._data.move_to_end(key)
                self.hits += 1
                return [dict(r) for r in hit]
        rows = self.inner.query(sql, params)
        with self._lock:
            self.misses += 1
            self._data[key] = rows
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
        return [dict(r) for r in rows]

    def execute(self, sql, params=None):
        try:
            return self.inner.execute(sql, params)
        finally:
            self.clear()

    def query_fresh(self, sql, params=None):
        return self.inner.query(sql, params)


class SparkExecutor(SqlExecutor):
    def __init__(self, spark):
        self.spark = spark

    @_timed
    def query(self, sql, params=None):
        if params and not getattr(self, "_inline", False):
            try:
                df = self.spark.sql(sql, args=params)
            except TypeError:
                # Lakeflow (DLT) pipelines wrap spark.sql without the args= keyword: inline the values instead
                self._inline = True
                df = self.spark.sql(inline_params(sql, params))
        else:
            df = self.spark.sql(inline_params(sql, params) if params else sql)
        return [r.asDict(recursive=True) for r in df.collect()]


def sql_literal(v) -> str:
    """A Spark SQL literal for a parameter value (strings escaped; backslash escapes are on in Spark SQL)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v) if v == v and v not in (float("inf"), float("-inf")) else f"CAST('{v}' AS DOUBLE)"
    if isinstance(v, (list, tuple)):
        return "array(" + ", ".join(sql_literal(x) for x in v) + ")"
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def inline_params(sql: str, params: dict) -> str:
    """Replace :name markers with literals, skipping string literals, quoted identifiers, comments and :: casts."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if sql[j] == "\\" and c != "`":
                    j += 2
                    continue
                if sql[j] == c:
                    break
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
        elif c == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(sql[i:j])
            i = j
        elif c == ":" and sql.startswith("::", i):
            out.append("::")
            i += 2
        elif c == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_"):
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            name = sql[i + 1:j]
            if name not in params:
                raise KeyError(f"SQL parameter :{name} has no value")
            out.append(sql_literal(params[name]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


class WarehouseExecutor(SqlExecutor):
    """Databricks SQL Statement Execution API via databricks-sdk."""

    def __init__(self, workspace_client, warehouse_id: str):
        self.w, self.warehouse_id = workspace_client, warehouse_id

    @_timed
    def query(self, sql, params=None):
        from databricks.sdk.service.sql import StatementParameterListItem, StatementState

        items = []
        for name, value in (params or {}).items():
            if isinstance(value, bool):
                items.append(StatementParameterListItem(name=name, value=str(value).lower(), type="BOOLEAN"))
            elif isinstance(value, int):
                items.append(StatementParameterListItem(name=name, value=str(value), type="BIGINT"))
            elif value is None:
                items.append(StatementParameterListItem(name=name, value=None, type="STRING"))
            else:
                items.append(StatementParameterListItem(name=name, value=str(value), type="STRING"))
        resp = self.w.statement_execution.execute_statement(
            statement=sql, warehouse_id=self.warehouse_id, parameters=items, wait_timeout="50s")
        while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(0.5)
            resp = self.w.statement_execution.get_statement(resp.statement_id)
        if resp.status.state != StatementState.SUCCEEDED:
            msg = resp.status.error.message if resp.status.error else str(resp.status.state)
            raise RuntimeError(f"SQL failed: {msg}")
        if not resp.manifest or not resp.manifest.schema or not resp.manifest.schema.columns:
            return []
        cols = [c.name for c in resp.manifest.schema.columns]
        rows = list((resp.result.data_array if resp.result else None) or [])
        chunk = resp.result.next_chunk_index if resp.result else None
        while chunk is not None:
            part = self.w.statement_execution.get_statement_result_chunk_n(resp.statement_id, chunk)
            rows += part.data_array or []
            chunk = part.next_chunk_index
        return [dict(zip(cols, r)) for r in rows]


def _bool(v):
    return v if isinstance(v, bool) or v is None else str(v).lower() == "true"


def _int(v):
    return None if v is None else int(v)


def _float(v):
    return None if v is None else float(v)


def _json(v, default):
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    return json.loads(v)


def row_to_node(r: dict) -> dict:
    links = _json(r.get("child_links_json"), []) or []
    return {
        "uid": r["uid"], "uid_num": _int(r["uid_num"]), "name": r["name"],
        "types": _json(r.get("types_json"), []) or [], "parent_uid": r.get("parent_uid"),
        "path": _json(r.get("path_json"), []) or [], "depth": _int(r.get("depth")) or 0,
        "sort_order": _float(r.get("sort_order")),
        "child_links": [{"uid": x["uid"], "link_type": x.get("link_type"), "sort_order": x.get("sort_order")} for x in links],
        "origin": r.get("origin"), "source_table_uid": r.get("source_table_uid"),
        "source_key": r.get("source_key"), "record_hash": r.get("record_hash"),
        "properties": _json(r.get("properties_json"), {}) or {},
        "user_defined": _json(r.get("user_defined_json"), {}) or {},
        "icon": r.get("icon"), "max_child_depth": _int(r.get("max_child_depth")),
        "allowed_child_types": _json(r.get("allowed_json"), []) or [], "owner": r.get("owner"),
        "steward": _json(r.get("steward_json"), []) or [], "locked": bool(_bool(r.get("locked"))),
        "is_deleted": bool(_bool(r.get("is_deleted"))), "modified_at": r.get("modified_at"),
        "modified_by": r.get("modified_by"),
    }


def node_to_row(n: dict, expected: str | None = None) -> dict:
    return {
        "uid": n["uid"], "uid_num": n["uid_num"], "name": n["name"], "types": n["types"],
        "parent_uid": n["parent_uid"], "path": n["path"], "depth": n["depth"], "sort_order": n["sort_order"],
        "child_links": n["child_links"], "origin": n["origin"], "source_table_uid": n["source_table_uid"],
        "source_key": n["source_key"], "record_hash": n["record_hash"],
        "properties_json": json.dumps(n["properties"] or {}, default=str),
        "user_defined": n["user_defined"] or {}, "icon": n["icon"], "max_child_depth": n["max_child_depth"],
        "allowed_child_types": n["allowed_child_types"], "owner": n["owner"], "steward": n["steward"],
        "locked": bool(n["locked"]), "is_deleted": bool(n["is_deleted"]), "expected_modified_at": expected,
        "path_key": path_key(n), "kind": kind_of(n),
    }


UPDATE_COLS = ["uid_num", "name", "types", "parent_uid", "path", "depth", "sort_order", "child_links", "origin",
               "source_table_uid", "source_key", "record_hash", "user_defined", "icon", "max_child_depth",
               "allowed_child_types", "owner", "steward", "locked", "is_deleted", "path_key", "kind"]


class SqlRepo(NodeRepo):
    """`index_table` (optional): the node_index materialized view of the export pipeline
    (uid → path_key, name), clustered by uid. It turns a lookup by bare UID or a name search
    into a small indexed read; MDMNodes stays the only source of truth, and anything the index
    does not know yet (new or just-moved nodes) falls back to the main table."""

    def __init__(self, executor: SqlExecutor, table: str, index_table: str | None = None):
        for t in (table, index_table):
            if t and not _IDENT.match(t):
                raise ValueError(f"bad table name {t!r}")
        self.x, self.table, self.index = executor, table, index_table
        self._index_ok = bool(index_table)

    # -- reads ---------------------------------------------------------------
    def _select(self, where: str, params: dict | None = None, order: str = "") -> list[dict]:
        sql = f"SELECT {SELECT_COLS} FROM {self.table} WHERE {where} {order}"
        return [row_to_node(r) for r in self.x.query(sql, params)]

    def _index_keys(self, uids: list[str]) -> dict[str, str]:
        if not self._index_ok:
            return {}
        try:
            rows = self.x.query(f"SELECT uid, path_key FROM {self.index} "
                                "WHERE array_contains(from_json(:uids, 'ARRAY<STRING>'), uid)",
                                {"uids": json.dumps(list(uids))})
            return {r["uid"]: r["path_key"] for r in rows if r.get("path_key")}
        except Exception:  # noqa: BLE001 - index not built yet: use the main table only
            self._index_ok = False
            return {}

    def get_many(self, uids, include_deleted=False):
        uids = list(dict.fromkeys(uids))
        if not uids:
            return {}
        live = "" if include_deleted else " AND NOT is_deleted"
        out: dict[str, dict] = {}
        keys = self._index_keys(uids)
        if keys:  # skips to the files of those path_keys
            for n in self._select("array_contains(from_json(:keys, 'ARRAY<STRING>'), path_key) "
                                  "AND array_contains(from_json(:uids, 'ARRAY<STRING>'), uid)" + live,
                                  {"keys": json.dumps(sorted(set(keys.values()))), "uids": json.dumps(uids)}):
                out[n["uid"]] = n
        missing = [u for u in uids if u not in out]
        if missing:
            for n in self._select("array_contains(from_json(:uids, 'ARRAY<STRING>'), uid)" + live,
                                  {"uids": json.dumps(missing)}):
                out[n["uid"]] = n
        return out

    def ancestors(self, node):
        if not node["path"]:
            return {}
        keys = [path_key_of(node["path"][:i], node["path"][i]) for i in range(len(node["path"]))]
        return {n["uid"]: n for n in self._select(
            "array_contains(from_json(:keys, 'ARRAY<STRING>'), path_key) AND NOT is_deleted",
            {"keys": json.dumps(keys)})}

    def _kids_where(self, parent: dict, q: str, filters) -> tuple[str, dict]:
        where = "path_key LIKE :pfx AND parent_uid = :puid AND NOT is_deleted"
        params = {"pfx": path_key(parent) + "%", "puid": parent["uid"]}
        if q:
            where += " AND (lower(name) LIKE :q OR uid = :qu)"
            params.update(q=f"%{q.lower()}%", qu=q.upper())
        for i, f in enumerate(filters or []):
            expr, is_array = column_sql(f)
            vals = [v for v in (f.get("values") or [])]
            ph, pp = as_params(f"f{i}_", vals)
            params.update(pp)
            empty = "" in vals
            if is_array:
                cond = f"arrays_overlap(coalesce({expr}, array()), array({ph}))" if vals else "false"
                if empty:
                    cond = f"({cond} OR size(coalesce({expr}, array())) = 0)"
            else:
                cond = f"coalesce({expr}, '') IN ({ph})" if vals else "false"
            where += f" AND {cond}"
        return where, params

    def chain(self, uid, key=None):
        """The node and its ancestors in one statement: their path_keys are the prefixes of the node's."""
        if not key:
            key = self._index_keys([uid]).get(uid)
        if key:
            parts = [x for x in key.strip("/").split("/") if x]
            keys = ["/" + "/".join(parts[:i + 1]) + "/" for i in range(len(parts))]
            got = {path_key(n): n for n in self._select(
                "array_contains(from_json(:keys, 'ARRAY<STRING>'), path_key) AND NOT is_deleted",
                {"keys": json.dumps(keys)})}
            out = [got[k] for k in keys if k in got]
            if len(out) == len(keys) and out[-1]["uid"] == uid:
                return out
        return super().chain(uid)   # no or an outdated key: the node first, then its ancestors

    def named(self, text, parent=None, types=None, exact=True, offset=0, limit=40):
        """An Enum's values: its branch only (path_key). A typed Link's targets: through node_index (name and
        types, clustered by UID) when it exists. Exact name (any case) or UID; exact=False: name contains."""
        t = (text or "").strip()
        if exact:
            cond, params = "(lower(name) = :n OR uid = :u)", {"n": t.lower(), "u": t.upper()}
        elif t:
            cond, params = "(lower(name) LIKE :n OR uid = :u)", {"n": f"%{t.lower()}%", "u": t.upper()}
        else:
            cond, params = "true", {}
        page = f"LIMIT {int(limit)} OFFSET {int(offset)}"
        if parent is not None:
            where, p2 = self._kids_where(parent, "", None)
            return self._select(f"{where} AND {cond}", {**params, **p2},
                                f"ORDER BY sort_order IS NULL, sort_order, name, uid_num {page}")
        if not types:
            return []
        params["types"] = json.dumps(list(types))
        typed = "arrays_overlap(types, from_json(:types, 'ARRAY<STRING>'))"
        if self._index_ok:
            try:
                rows = self.x.query(f"SELECT uid FROM {self.index} WHERE {typed} AND {cond} ORDER BY name, uid_num {page}",
                                    params)
                got = self.get_many([r["uid"] for r in rows])
                return [got[r["uid"]] for r in rows if r["uid"] in got]
            except Exception:  # noqa: BLE001 - index not built yet
                self._index_ok = False
        return self._select(f"NOT is_deleted AND {typed} AND {cond}", params, f"ORDER BY name, uid_num {page}")

    def children_with_total(self, parent, offset=0, limit=None, q=""):
        """A page of children with the number of all of them, in one statement."""
        p = self._node(parent)
        if not p:
            return [], 0
        where, params = self._kids_where(p, q, None)
        order = "ORDER BY sort_order IS NULL, sort_order, uid_num"
        if limit is not None:
            order += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        # an uncorrelated scalar subquery: counted once as a plain aggregate (a window count would move every
        # matching row to one partition)
        rows = self.x.query(f"SELECT {SELECT_COLS}, (SELECT count(*) FROM {self.table} WHERE {where}) AS total_rows "
                            f"FROM {self.table} WHERE {where} {order}", params)
        if rows:
            return [row_to_node(r) for r in rows], int(rows[0]["total_rows"])
        return [], (0 if not offset else self.count_children(p, q=q))

    def children(self, parent, offset=0, limit=None, q="", sort=None, filters=None):
        p = self._node(parent)
        if not p:
            return []
        where, params = self._kids_where(p, q, filters)
        if sort:
            expr, is_array = column_sql(sort)
            if is_array:
                expr = f"array_join({expr}, ', ')"
            base = (sort.get("ptype") or "String").split("(")[0]
            if base in ("Number", "Float"):
                expr = f"try_cast({expr} AS DOUBLE)"
            direction = "DESC" if sort.get("dir", 1) < 0 else "ASC"
            order = f"ORDER BY {expr} IS NULL, {expr} {direction}, sort_order IS NULL, sort_order, uid_num"
        else:
            order = "ORDER BY sort_order IS NULL, sort_order, uid_num"
        if limit is not None:
            order += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        return self._select(where, params, order)

    def first_children(self, max_parent_depth, per):
        """One query: the first `per` children of every parent at depth <= max_parent_depth, with totals."""
        rows = self.x.query(
            f"SELECT * FROM (SELECT {SELECT_COLS}, count(*) OVER (PARTITION BY parent_uid) AS _total, "
            "row_number() OVER (PARTITION BY parent_uid ORDER BY sort_order IS NULL, sort_order, uid_num) AS _rn "
            f"FROM {self.table} WHERE NOT is_deleted AND depth BETWEEN 1 AND :d) WHERE _rn <= :per",
            {"d": int(max_parent_depth) + 1, "per": int(per)})
        out, totals = [], {}
        for r in rows:
            n = row_to_node(r)
            totals[n["parent_uid"]] = int(r["_total"])
            out.append(n)
        return out, totals

    def count_children(self, parent, q="", filters=None):
        p = self._node(parent)
        if not p:
            return 0
        where, params = self._kids_where(p, q, filters)
        return int(self.x.execute(f"SELECT count(*) AS c FROM {self.table} WHERE {where}", params).get("c") or 0)

    def distinct_values(self, parent, column, q="", limit=40):
        p = self._node(parent)
        if not p:
            return []
        where, params = self._kids_where(p, "", None)
        expr, is_array = column_sql(column)
        v = f"explode_outer({expr})" if is_array else expr
        sql = (f"SELECT coalesce(v, '') AS v, count(*) AS c FROM (SELECT {v} AS v FROM {self.table} WHERE {where}) "
               + ("WHERE lower(coalesce(v, '')) LIKE :vq " if q else "")
               + f"GROUP BY 1 ORDER BY v = '' DESC, lower(v) LIMIT {int(limit)}")
        if q:
            params["vq"] = f"%{q.lower()}%"
        return [(r["v"], int(r["c"])) for r in self.x.query(sql, params)]

    def subtree(self, uid, limit=None):
        node = self.get(uid)
        if not node:
            return []
        lim = f" LIMIT {int(limit)}" if limit is not None else ""
        return self._select("path_key LIKE :pfx AND uid <> :u AND NOT is_deleted",
                            {"pfx": path_key(node) + "%", "u": uid}, lim)

    def _branch_types(self, node, levels):
        """Types per level; a Child Link counts with its origin's types (one join, links only)."""
        p = {"pfx": path_key(node) + "%", "d": int(node["depth"]), "lv": int(levels)}
        rows = self.x.query(
            f"SELECT depth - :d AS lvl, collect_set(t) AS types FROM {self.table} LATERAL VIEW explode(types) x AS t "
            "WHERE path_key LIKE :pfx AND NOT is_deleted AND kind IS DISTINCT FROM 'Link' AND depth - :d <= :lv "
            "GROUP BY depth - :d", p)
        types = {}
        for r in rows:
            vals = r["types"]
            types[int(r["lvl"])] = set(json.loads(vals) if isinstance(vals, str) else (vals or []))
        links = self.x.query(
            f"SELECT l.lvl, collect_set(t) AS types FROM (SELECT depth - :d AS lvl, "
            f"try_variant_get(properties, '$[\\'Linked Node\\']', 'string') AS target FROM {self.table} "
            "WHERE path_key LIKE :pfx AND NOT is_deleted AND kind = 'Link' AND depth - :d <= :lv) AS l "
            f"JOIN {self.table} AS o ON o.uid = l.target AND NOT o.is_deleted LATERAL VIEW explode(o.types) x AS t "
            "GROUP BY l.lvl", p)
        for r in links:
            vals = r["types"]
            types.setdefault(int(r["lvl"]), set()).update(json.loads(vals) if isinstance(vals, str) else (vals or []))
        return types, bool(links)

    def subtree_stats(self, node):
        r = self.x.execute(
            f"SELECT count(*) AS c, count_if(locked) AS l, count_if(origin = 'loaded') AS ld, "
            f"coalesce(max(depth), :d) - :d AS h FROM {self.table} "
            "WHERE path_key LIKE :pfx AND uid <> :u AND NOT is_deleted",
            {"pfx": path_key(node) + "%", "u": node["uid"], "d": int(node["depth"])})
        return {"count": int(r.get("c") or 0), "locked": int(r.get("l") or 0), "loaded": int(r.get("ld") or 0),
                "height": int(r.get("h") or 0)}

    def by_types(self, type_uids, limit=None):
        lim = f" LIMIT {int(limit)}" if limit is not None else ""
        return self._select("arrays_overlap(types, from_json(:t, 'ARRAY<STRING>')) AND NOT is_deleted",
                            {"t": json.dumps(list(type_uids))},
                            "ORDER BY sort_order IS NULL, sort_order, uid_num" + lim)

    def by_kind(self, kind):
        # `kind` is NULL on every ordinary node, so file statistics skip almost the whole table
        return self._select("kind = :k AND NOT is_deleted", {"k": kind},
                            "ORDER BY sort_order IS NULL, sort_order, uid_num")

    def links_under(self, parent_uids):
        out = []
        ps = list(dict.fromkeys(parent_uids))
        for i in range(0, len(ps), 5000):
            out += self._select("kind = 'Link' AND NOT is_deleted AND array_contains(from_json(:ps, 'ARRAY<STRING>'), parent_uid)",
                                {"ps": json.dumps(ps[i:i + 5000])}, "ORDER BY sort_order IS NULL, sort_order, uid_num")
        return out

    def with_child_links(self):
        return self._select("NOT is_deleted AND size(child_links) > 0", {}, "ORDER BY uid_num")

    def search(self, text, limit=50, under=None):
        if under is not None:  # the branch is a contiguous path_key range: only that range is read
            return self._select("path_key LIKE :pfx AND uid <> :u AND NOT is_deleted AND (lower(name) LIKE :q OR upper(uid) = :uu)",
                                {"pfx": path_key(under) + "%", "u": under["uid"], "q": f"%{text.lower()}%",
                                 "uu": text.upper()}, f"ORDER BY depth, name LIMIT {int(limit)}")
        if self._index_ok:
            try:
                rows = self.x.query(
                    f"SELECT uid FROM {self.index} WHERE lower(name) LIKE :q OR uid = :u "
                    f"ORDER BY depth, name LIMIT {int(limit)}", {"q": f"%{text.lower()}%", "u": text.upper()})
                found = self.get_many([r["uid"] for r in rows])
                return sorted(found.values(), key=lambda n: (n["depth"], n["name"]))
            except Exception:  # noqa: BLE001
                self._index_ok = False
        return self._select("NOT is_deleted AND (lower(name) LIKE :q OR upper(uid) = :u)",
                            {"q": f"%{text.lower()}%", "u": text.upper()}, f"ORDER BY depth, name LIMIT {int(limit)}")

    def child_counts(self, uids, under=None):
        out = {u: 0 for u in uids}
        if not uids:
            return out
        where = "array_contains(from_json(:uids, 'ARRAY<STRING>'), parent_uid) AND NOT is_deleted"
        params = {"uids": json.dumps(list(uids))}
        if under:
            where += " AND path_key LIKE :pfx"
            params["pfx"] = path_key(under) + "%"
        for r in self.x.query(f"SELECT parent_uid, count(*) AS c FROM {self.table} WHERE {where} GROUP BY parent_uid",
                              params):
            out[r["parent_uid"]] = int(r["c"])
        return out

    # -- writes --------------------------------------------------------------
    def upsert(self, nodes, user, expected=None):
        if not nodes:
            return
        expected = expected or {}
        if expected:
            keys = sorted({path_key(n) for n in nodes if n["uid"] in expected})
            cur = self.x.query_fresh(
                f"SELECT uid, CAST(modified_at AS STRING) AS m FROM {self.table} "
                "WHERE array_contains(from_json(:keys, 'ARRAY<STRING>'), path_key) "
                "AND array_contains(from_json(:uids, 'ARRAY<STRING>'), uid)",
                {"keys": json.dumps(keys), "uids": json.dumps(list(expected))})
            seen = {r["uid"]: r["m"] for r in cur}
            for uid, ts in expected.items():
                if seen.get(uid) != ts:
                    raise ConflictError(f"{uid} was changed by someone else — reload and try again")
        for i in range(0, len(nodes), 2000):
            self._merge(nodes[i:i + 2000], user, expected)

    def _merge(self, nodes, user, expected, insert_only=False):
        rows = [node_to_row(n, expected.get(n["uid"])) for n in nodes]
        keys = sorted({r["path_key"] for r in rows})
        sets = ", ".join(f"t.{c} = s.{c}" for c in UPDATE_COLS)
        cols = ", ".join(["uid"] + UPDATE_COLS + ["properties", "modified_at", "modified_by"])
        vals = ", ".join(["s.uid"] + [f"s.{c}" for c in UPDATE_COLS]
                         + ["parse_json(s.properties_json)", "current_timestamp()", ":user"])
        # the path_key predicate lets the MERGE read only the files of the rows it writes
        sql = f"""MERGE INTO {self.table} AS t
USING (SELECT inline(from_json(:rows, '{ROW_SCHEMA}'))) AS s
ON t.uid = s.uid AND array_contains(from_json(:keys, 'ARRAY<STRING>'), t.path_key)
"""
        if not insert_only:
            sql += f"""WHEN MATCHED AND (s.expected_modified_at IS NULL OR CAST(t.modified_at AS STRING) = s.expected_modified_at)
  THEN UPDATE SET {sets}, t.properties = parse_json(s.properties_json), t.modified_at = current_timestamp(),
                  t.modified_by = :user
"""
        sql += f"WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})"
        res = self.x.execute(sql, {"rows": json.dumps(rows, default=str), "user": user, "keys": json.dumps(keys)})
        done = int(res.get("num_updated_rows") or 0) + int(res.get("num_inserted_rows") or 0)
        if insert_only:
            return int(res.get("num_inserted_rows") or 0)
        if done != len(rows):
            raise ConflictError("a node was changed by someone else — reload and try again")
        return done

    def soft_delete_subtree(self, node, include_self, user):
        where = "path_key LIKE :pfx AND NOT is_deleted" + ("" if include_self else " AND uid <> :u")
        res = self.x.execute(f"UPDATE {self.table} SET is_deleted = true, modified_at = current_timestamp(), "
                             f"modified_by = :user WHERE {where}",
                             {"pfx": path_key(node) + "%", "u": node["uid"], "user": user})
        return int(res.get("num_affected_rows") or 0)

    def move_subtree(self, node, new_parent, user):
        old_key, old_len = path_key(node), len(node["path"])
        new_path = list(new_parent["path"]) + [new_parent["uid"]]
        new_key = path_key_of(new_path, node["uid"])
        sql = f"""UPDATE {self.table} SET
  parent_uid = CASE WHEN uid = :u THEN :np ELSE parent_uid END,
  path = concat(from_json(:newpath, 'ARRAY<STRING>'), slice(path, :cut, greatest(size(path) - :oldlen, 0))),
  depth = :newlen + size(path) - :oldlen,
  path_key = concat(:newkey, substring(path_key, :keycut)),
  modified_at = current_timestamp(), modified_by = :user
WHERE path_key LIKE :pfx"""
        res = self.x.execute(sql, {"u": node["uid"], "np": new_parent["uid"], "newpath": json.dumps(new_path),
                                   "cut": old_len + 1, "oldlen": old_len, "newlen": len(new_path),
                                   "newkey": new_key, "keycut": len(old_key) + 1, "pfx": old_key + "%",
                                   "user": user})
        return int(res.get("num_affected_rows") or 0)

    def lift_children(self, node, user):
        key, i = path_key(node), len(node["path"])
        parent_key = key[: key.rstrip("/").rfind("/") + 1]
        res = self.x.execute(f"""UPDATE {self.table} SET
  parent_uid = CASE WHEN parent_uid = :u THEN :pu ELSE parent_uid END,
  path = concat(slice(path, 1, :i), slice(path, :i2, size(path))),
  depth = depth - 1,
  path_key = concat(:pk, substring(path_key, :cut)),
  modified_at = current_timestamp(), modified_by = :user
WHERE path_key LIKE :pfx AND uid <> :u""", {"u": node["uid"], "pu": node["parent_uid"], "i": i, "i2": i + 2,
                                            "pk": parent_key, "cut": len(key) + 1, "pfx": key + "%", "user": user})
        return int(res.get("num_affected_rows") or 0)

    def hard_delete(self, uids):
        if uids:
            self.x.execute(f"DELETE FROM {self.table} WHERE array_contains(from_json(:uids, 'ARRAY<STRING>'), uid)",
                           {"uids": json.dumps(list(uids))})

    def insert_missing(self, nodes, user):
        return sum(self._merge(nodes[i:i + 2000], user, {}, insert_only=True) or 0
                   for i in range(0, len(nodes), 2000))

    def allocate(self, count):
        start = self.allocate_block(count)
        return list(range(start, start + count))

    def allocate_block(self, count):
        ck = "/1/2/7/"  # path_key of System / UID Counter
        for _ in range(20):
            row = self.x.execute(
                f"SELECT try_variant_get(properties, '$.next', 'bigint') AS nxt FROM {self.table} "
                "WHERE path_key = :k AND uid = :c", {"c": K.UID_COUNTER, "k": ck})
            start = int(row["nxt"])
            res = self.x.execute(
                f"UPDATE {self.table} SET properties = parse_json(:p), modified_at = current_timestamp() "
                f"WHERE path_key = :k AND uid = :c AND try_variant_get(properties, '$.next', 'bigint') = :old",
                {"p": json.dumps({"next": start + count}), "c": K.UID_COUNTER, "old": start, "k": ck})
            if int(res.get("num_affected_rows") or 0) == 1:
                return start
            time.sleep(0.2)
        raise ConflictError("could not allocate uids, try again")

    def ensure_table(self, optimize: bool = False) -> list[str]:
        """Create MDMNodes, or upgrade an older one (path_key/kind, clustering). Returns notes."""
        notes = []
        self.x.execute(DDL.format(table=self.table))
        cols = {r.get("col_name") for r in self.x.query(f"DESCRIBE TABLE {self.table}")}
        if "path_key" not in cols:
            for stmt in MIGRATION:
                self.x.execute(stmt.format(table=self.table))
            notes.append("added path_key/kind, re-clustered by path_key")
            optimize = True
        if optimize:
            self.x.execute(OPTIMIZE.format(table=self.table))
            notes.append("OPTIMIZE FULL done")
        return notes
