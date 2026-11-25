"""One click in the UI (/api/open) against the real SQL of SqlRepo (local Spark): the same result as the
in-memory repo, and only a handful of SQL statements, in few waves."""

import json
import os
import sys
import threading
from pathlib import Path

import pytest

os.environ["MDM_LOCAL"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app"), str(ROOT / "tests")]

pytest.importorskip("pyspark")

from backend.service import MdmService  # noqa: E402
from mdm import links as lk  # noqa: E402
from mdm.catalog import Catalog  # noqa: E402
from mdm.model import K, kind_of, path_key  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import all_seed  # noqa: E402
from mdm.sqlrepo import SparkExecutor, SqlRepo  # noqa: E402
from mdm.uid import to_uid  # noqa: E402
from test_core import ADMIN, load_demo  # noqa: E402

# the statements one click may cost (it was ~35 with separate tree and node requests)
MAX_STATEMENTS_PER_CLICK = 9


@pytest.fixture(scope="module")
def spark():
    import time
    os.environ["TZ"] = "UTC"
    time.tzset()
    from pyspark.sql import SparkSession
    s = (SparkSession.builder.master("local[2]").appName("mdm-open-test")
         .config("spark.ui.enabled", "false").config("spark.sql.session.timeZone", "UTC")
         .config("spark.sql.shuffle.partitions", "1").getOrCreate())
    yield s
    s.stop()


class Counting(SparkExecutor):
    """Counts the statements that reach the engine. Like the app's CachingExecutor, a repeated statement with the
    same parameters is answered from memory (the catalog, the permission folder, ...)."""

    def __init__(self, spark):
        super().__init__(spark)
        self.sql, self._lock, self._memo = [], threading.Lock(), {}

    def query(self, sql, params=None):
        key = (sql, json.dumps(params or {}, sort_keys=True, default=str))
        with self._lock:
            if key in self._memo:
                return [dict(r) for r in self._memo[key]]
            self.sql.append(sql)
        rows = super().query(sql, params)
        with self._lock:
            self._memo[key] = rows
        return [dict(r) for r in rows]


@pytest.fixture(scope="module")
def pair(spark):
    mem = MdmService(MemoryRepo(all_seed("workspace", "mdm_staging", demo=True)))
    load_demo(mem)
    made = mem.add_child_link(ADMIN, K.MASTER_DATA, to_uid(2031))["uid"]   # a Child Link to show its origin
    rows = []
    for n in mem.repo.subtree(K.ROOT) + [mem.repo.get(K.ROOT)]:
        rows.append((n["uid"], n["uid_num"], n["name"], n["types"], n["parent_uid"], n["path"], n["depth"],
                     n["sort_order"], [], n["origin"], n.get("source_table_uid"), n.get("source_key"),
                     n.get("record_hash"), json.dumps(n["properties"] or {}, default=str), n.get("user_defined") or {},
                     n.get("icon"), n.get("max_child_depth"), n.get("allowed_child_types") or [], n.get("owner"),
                     n.get("steward") or [], bool(n["locked"]), False, n.get("modified_by"), path_key(n), kind_of(n)))
    df = spark.createDataFrame(rows, (
        "uid STRING, uid_num BIGINT, name STRING, types ARRAY<STRING>, parent_uid STRING, path ARRAY<STRING>, "
        "depth INT, sort_order DOUBLE, child_links ARRAY<STRUCT<uid: STRING, link_type: STRING, sort_order: DOUBLE>>, "
        "origin STRING, source_table_uid STRING, source_key STRING, record_hash STRING, p STRING, "
        "user_defined MAP<STRING, BOOLEAN>, icon STRING, max_child_depth INT, allowed_child_types ARRAY<STRING>, "
        "owner STRING, steward ARRAY<STRING>, locked BOOLEAN, is_deleted BOOLEAN, modified_by STRING, "
        "path_key STRING, kind STRING"))
    (df.selectExpr("*", "parse_json(p) AS properties", "CAST(NULL AS TIMESTAMP) AS modified_at").drop("p")
       .createOrReplaceTempView("mdmnodes"))
    df.select("uid", "path_key").createOrReplaceTempView("node_index")
    x = Counting(spark)
    repo = SqlRepo(x, "mdmnodes", "node_index")
    sql = MdmService(repo, Catalog(repo))
    return mem, sql, x, made


def _norm(v):
    """Leave out what differs only by the storage (timestamps)."""
    s = json.dumps(v, sort_keys=True, default=str)
    return json.loads(s.replace('"modified_at": null', '"modified_at": ""'))


def _clean(d):
    if isinstance(d, str):   # a whole number read back from VARIANT shows as 1, from memory as 1.0
        return d[:-2] if d.endswith(".0") and d[:-2].lstrip("-").isdigit() else d
    if isinstance(d, float) and d.is_integer():
        return int(d)
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if k not in ("modified_at", "Last Modified")}
    if isinstance(d, list):
        return [_clean(v) for v in d if not (isinstance(v, dict) and v.get("name") == "Last Modified")]
    return d


@pytest.mark.parametrize("pick", ["deep_record", "master", "link"])
def test_open_is_the_same_on_sql_and_few_statements(pair, pick):
    mem, sql, x, made = pair
    if pick == "deep_record":
        uid = next(n["uid"] for n in mem.repo.subtree(K.ROOT) if n["origin"] == "loaded")
    elif pick == "master":
        uid = K.MASTER_DATA
    else:
        uid = made
    want = _clean(_norm(mem.open(ADMIN, uid)))
    sql.open(ADMIN, K.ROOT)     # another node first: the catalog and the permission folder are then cached
    key = path_key(mem.repo.get(uid))
    x.sql.clear()
    got = _clean(_norm(sql.open(ADMIN, uid, key=key)))
    n = len(x.sql)
    assert got == want
    print(f"{pick}: {n} statements")
    # a Child Link also builds its origin's page (its own lookups)
    assert n <= MAX_STATEMENTS_PER_CLICK + (6 if lk.is_link(mem.repo.get(uid)) else 0), "\n".join(q[:160] for q in x.sql)


def test_open_matches_tree_and_node_view(pair):
    mem, _, _, _ = pair
    uid = next(n["uid"] for n in mem.repo.subtree(K.ROOT) if n["origin"] == "loaded")
    both = mem.open(ADMIN, uid)
    assert _clean(both["tree"]) == _clean(mem.tree(ADMIN, uid))
    assert _clean(both["node"]) == _clean(mem.node_view(ADMIN, uid))


def test_keep_warm_window():
    import datetime as dt
    from backend.core.warm import in_window
    mon_9 = dt.datetime(2026, 11, 23, 9, 0)
    assert in_window(mon_9, "08:00-18:00", "Mon-Fri")
    assert not in_window(mon_9.replace(hour=19), "08:00-18:00", "Mon-Fri")
    assert not in_window(dt.datetime(2026, 11, 22, 9, 0), "08:00-18:00", "Mon-Fri")   # Sunday
    assert in_window(dt.datetime(2026, 11, 22, 9, 0), "08:00-18:00", "Sat-Sun")


def test_warm_start_reads_the_top_levels(pair):
    from backend.core.warm import warm_once
    mem, sql, x, _ = pair
    assert warm_once(mem) > 10
    x.sql.clear()
    warm_once(sql)
    first = len(x.sql)
    x.sql.clear()
    warm_once(sql)
    assert first > 0 and len(x.sql) == 0      # the second time everything comes from the cache


def test_server_timing_header_counts_the_statements(pair):
    """The middleware puts the request's statements (from every thread of the request) in Server-Timing."""
    from fastapi.testclient import TestClient

    import backend.core.deps as deps
    from backend.main import app
    _, sql, x, _ = pair
    old = deps._svc
    deps._svc = sql
    try:
        x._memo.clear()
        r = TestClient(app).get(f"/api/open/{to_uid(2031)}")
    finally:
        deps._svc = old
    assert r.status_code == 200
    timing = r.headers["Server-Timing"]
    n = int(timing.split('desc="')[1].split(" ")[0])
    assert "total;dur=" in timing and n >= 5   # a cold read: the statements of all threads are counted


def test_enum_values_on_sql_are_paged_and_looked_up(pair):
    mem, sql, x, _ = pair
    want = [o["name"] for o in mem.options(ADMIN, "Enum(Countries)")]
    assert [o["name"] for o in sql.options(ADMIN, "Enum(Countries)")] == want
    assert [o["name"] for o in sql.options(ADMIN, "Enum(Countries)", "hun")] == ["Hungary"]
    assert [o["name"] for o in sql.options(ADMIN, "Enum(Countries)", "", 2, 2)] == want[2:4]
    resolve = sql.catalog.resolver()
    assert resolve("Enum", "Countries", "hungary") == (to_uid(2031), None)
    assert resolve("Enum", "Countries", "Atlantis")[0] is None
