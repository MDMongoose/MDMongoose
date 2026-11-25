"""One Machine and Distributed processing give the same result.

Every scenario runs twice on identical copies of the demo: One Machine processing (mdm.matching.run,
mdm.golden) on one, Distributed processing (mdm.distributed, local Spark) on the other. Then every node is
compared — source records, golden records, source links, Match Pairs, the UID counter — and the runs'
statistics and rows. Only timestamps may differ.
"""

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app"), str(ROOT / "tests")]
os.environ["MDM_LOCAL"] = "1"

pytest.importorskip("pyspark")
pytest.importorskip("pyarrow")

from mdm import golden, load, processing  # noqa: E402
from mdm.catalog import Catalog  # noqa: E402
from mdm.distributed import refresh_golden as dist_golden, run_match as dist_match  # noqa: E402
from mdm.distributed.io import SparkNodes  # noqa: E402
from mdm.matching import run as mr  # noqa: E402
from mdm.matching.spec import METHOD_EXACT, METHOD_FUZZY, METHOD_SPLINK  # noqa: E402
from mdm.model import K, clone, kind_of, path_key  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import DEMO_SOURCE_ROWS, DEMO_TABLES, all_seed  # noqa: E402
from mdm.uid import to_uid  # noqa: E402

CUSTOMER, SUPPLIER, EMPLOYEE = to_uid(2050), to_uid(2240), to_uid(2340)
CUSTOMER_RULE, SUPPLIER_RULE, EMPLOYEE_RULE = to_uid(2060), to_uid(2260), to_uid(2360)
KNA1, ACCOUNT = to_uid(2021), to_uid(2023)
TIME_PROPS = {"Matched At", "Scored At", "_refreshed_at", "Last Run"}


# ---------------------------------------------------------------------------- local Spark + MDMNodes in memory
@pytest.fixture(scope="module")
def spark():
    import time
    os.environ["TZ"] = "UTC"
    time.tzset()
    from pyspark.sql import SparkSession
    s = (SparkSession.builder.master("local[2]").appName("mdm-distributed-test")
         .config("spark.ui.enabled", "false").config("spark.sql.session.timeZone", "UTC")
         .config("spark.sql.shuffle.partitions", "4").config("spark.default.parallelism", "4")
         .config("spark.sql.execution.arrow.maxRecordsPerBatch", "300")   # several batches per partition
         .getOrCreate())
    yield s
    s.stop()


COLS = ("uid STRING, uid_num BIGINT, name STRING, types ARRAY<STRING>, parent_uid STRING, path ARRAY<STRING>, "
        "depth INT, sort_order DOUBLE, child_links ARRAY<STRUCT<uid: STRING, link_type: STRING, sort_order: DOUBLE>>, "
        "origin STRING, source_table_uid STRING, source_key STRING, record_hash STRING, p STRING, "
        "user_defined MAP<STRING, BOOLEAN>, icon STRING, max_child_depth INT, allowed_child_types ARRAY<STRING>, "
        "owner STRING, steward ARRAY<STRING>, locked BOOLEAN, is_deleted BOOLEAN, modified_at STRING, "
        "modified_by STRING, path_key STRING, kind STRING")


class MemoryNodes(SparkNodes):
    """SparkNodes over a MemoryRepo: Spark reads a snapshot of it; writes go back into it (the MERGE of the
    jobs needs Delta, which local Spark here has not: the rows a MERGE would write are applied instead)."""

    def __init__(self, spark, repo, user="mdm_core"):
        super().__init__(spark, "mdmnodes", repo, user)
        self.dir = tempfile.mkdtemp(prefix="mdm_dist_test_")
        self._df = None
        if hasattr(repo, "listeners"):
            repo.listeners.append(self)
        self.merged, self.deleted = 0, 0

    def nodes(self):
        if self._df is None:
            rows = []
            for n in self.repo.nodes.values():
                rows.append((n["uid"], n["uid_num"], n["name"], n["types"], n["parent_uid"], n["path"], n["depth"],
                             n["sort_order"], n.get("child_links") or [], n["origin"], n.get("source_table_uid"),
                             n.get("source_key"), n.get("record_hash"), json.dumps(n["properties"] or {}, default=str),
                             n.get("user_defined") or {}, n.get("icon"), n.get("max_child_depth"),
                             n.get("allowed_child_types") or [], n.get("owner"), n.get("steward") or [],
                             bool(n["locked"]), bool(n["is_deleted"]), n.get("modified_at"), n.get("modified_by"),
                             path_key(n), kind_of(n)))
            df = self.spark.createDataFrame(rows, COLS)
            df = df.selectExpr("*", "parse_json(p) AS properties").drop("p")
            path = os.path.join(self.dir, f"nodes_{len(os.listdir(self.dir))}")
            df.write.parquet(path)
            self._df = self.spark.read.parquet(path)
            self._df.createOrReplaceTempView("mdmnodes")
        return self._df

    def stage(self, df, name):
        path = os.path.join(self.dir, f"{self.run_id}_{name}")
        df.write.mode("overwrite").parquet(path)
        return self.spark.read.parquet(path)

    def cleanup(self):
        pass

    def merge_rows(self, df):
        nodes = []
        for r in df.select("row_json").collect():
            row = json.loads(r["row_json"])
            n = {k: v for k, v in row.items() if k not in ("properties_json", "expected_modified_at", "path_key", "kind")}
            n["properties"] = json.loads(row["properties_json"])
            n["modified_at"], n["modified_by"] = "now", self.user
            nodes.append(n)
        self.repo.upsert(nodes, self.user)
        self.merged += len(nodes)
        self._df = None
        return len(nodes)

    def delete_rows(self, df):
        uids = [r["uid"] for r in df.select("uid").collect()]
        self.repo.hard_delete(uids)
        self.deleted += len(uids)
        self._df = None
        return len(uids)


class TrackedRepo(MemoryRepo):
    """A MemoryRepo whose writes (rule nodes, the UID counter, decisions) drop the Spark snapshot."""

    def __init__(self, nodes):
        super().__init__(nodes)
        self.listeners = []

    def _changed(self):
        for io in self.listeners:
            io._df = None

    def upsert(self, nodes, user, expected=None):
        super().upsert(nodes, user, expected)
        self._changed()

    def hard_delete(self, uids):
        super().hard_delete(uids)
        self._changed()

    def allocate(self, count):
        out = super().allocate(count)
        self._changed()
        return out


def demo(generated=True):
    """The demo with its sources loaded (the generated companies, suppliers and employees, or the hand-written
    edge cases only)."""
    r = MemoryRepo(all_seed("workspace", "mdm_staging", demo=True))
    for table_uid, src in DEMO_TABLES.items():
        spec = DEMO_SOURCE_ROWS[src]
        rows = []
        for row in spec["rows"] if generated else spec["rows"][:spec["curated"]]:
            d = dict(zip(spec["columns"], row))
            d["_record_hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
            rows.append(d)
        if rows:
            load.merge_records(r, r.get(table_uid), rows)
    return r


class Pair:
    """The same data twice: `one` for One Machine processing, `dist` for Distributed processing."""

    def __init__(self, spark, repo):
        self.one = repo
        self.dist = TrackedRepo(list(repo.nodes.values()))
        self.spark = spark

    def io(self):
        return MemoryNodes(self.spark, self.dist)

    def both(self, fn):
        """Apply the same change (a function of a repo) to both copies."""
        fn(self.one)
        fn(self.dist)

    def match(self, rule, mode="incremental", preview=False):
        a = mr.run(self.one, rule, mode=mode, preview=preview, user="t")
        b = dist_match(self.io(), rule, mode=mode, preview=preview, user="t")
        assert_same_run(a, b)
        self.check()
        return a

    def golden(self, master, retire=True):
        a = golden.refresh(self.one, Catalog(self.one), master, "t", retire=retire)
        b = dist_golden(self.io(), Catalog(self.dist), master, "t", retire=retire)
        assert a == b
        self.check()
        return a

    def check(self):
        assert_same_nodes(self.one, self.dist)


def _num(v):
    """1.0 and 1 are the same value (a whole number read back from VARIANT is an int)."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, dict):
        return {k: _num(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_num(x) for x in v]
    return v


def _norm_node(n):
    n = {k: v for k, v in n.items() if k not in ("modified_at", "modified_by")}
    n["properties"] = {k: v for k, v in (n.get("properties") or {}).items() if k not in TIME_PROPS}
    n["child_links"] = n.get("child_links") or []
    return _num(json.loads(json.dumps(n, sort_keys=True, default=str)))


def assert_same_nodes(a, b):
    ua, ub = set(a.nodes), set(b.nodes)
    assert ua == ub, f"only One Machine: {sorted(ua - ub)[:10]} / only Distributed: {sorted(ub - ua)[:10]}"
    diff = [u for u in sorted(ua) if _norm_node(a.nodes[u]) != _norm_node(b.nodes[u])]
    if diff:
        u = diff[0]
        na, nb = _norm_node(a.nodes[u]), _norm_node(b.nodes[u])
        keys = {k for k in set(na) | set(nb) if na.get(k) != nb.get(k)}
        detail = {k: (na.get(k), nb.get(k)) for k in keys}
        raise AssertionError(f"{len(diff)} node(s) differ, first {u}: {json.dumps(detail, default=str)[:2000]}")


def assert_same_run(a, b):
    sa = {k: v for k, v in a["stats"].items() if k not in ("processing", "processing_note")}
    sb = {k: v for k, v in b["stats"].items() if k not in ("processing", "processing_note")}
    assert sa == sb
    assert a["rows"] == b["rows"]
    assert a.get("pairs") == b.get("pairs")
    assert a["fingerprint"] == b["fingerprint"]


def set_props(repo, uid, **props):
    n = clone(repo.get(uid))
    n["properties"].update(props)
    repo.upsert([n], "t")


# ---------------------------------------------------------------------------- scenarios
@pytest.fixture(scope="module")
def big(spark):
    """The generated demo, matched once in full (the base for the incremental scenarios)."""
    p = Pair(spark, demo(generated=True))
    for rule in (CUSTOMER_RULE, SUPPLIER_RULE, EMPLOYEE_RULE):
        p.match(rule, mode="full")
    for master in (CUSTOMER, SUPPLIER, EMPLOYEE):
        p.golden(master)
    return p


def test_full_runs_and_golden_records_are_equal(big):
    # the fixture compared every node after every step; the demo finds its real entities on both
    c = [n for n in big.dist.children(CUSTOMER) if n["origin"] == "generated"]
    assert len(c) == 1004
    links = [n for n in big.dist.nodes.values() if kind_of(n) == "Link" and not n["is_deleted"]]
    assert len(links) > 1500


def test_nothing_changed_writes_nothing(big):
    a = big.match(CUSTOMER_RULE)
    assert a["stats"]["changed"] == 0 and a["stats"]["records_written"] == 0
    big.golden(CUSTOMER)


def test_incremental_edits_new_and_removed_records(big):
    kna1 = big.one.children(KNA1)
    by_key = {}
    for r in kna1 + big.one.children(ACCOUNT):
        by_key.setdefault(r["properties"].get("Golden UID"), []).append(r["uid"])
    shared = next(r for r in kna1[3:] if len(by_key.get(r["properties"].get("Golden UID"), [])) > 1)
    kna1 = [x for x in kna1 if x["uid"] != shared["uid"]]
    kna1.insert(2, shared)   # the removed record: its entity keeps other records

    def change(repo):
        r = clone(repo.get(kna1[0]["uid"]))
        r["properties"]["NAME1"] = "Totally Different Holding AG"        # leaves its entity
        s = clone(repo.get(kna1[1]["uid"]))
        s["properties"]["NAME1"] = (s["properties"].get("NAME1") or "") + " Kft."   # still the same entity
        t = clone(repo.get(kna1[2]["uid"]))
        t["is_deleted"] = True                                            # its entity loses a record
        new = clone(repo.get(kna1[3]["uid"]))
        new.update(uid=to_uid(900_001), uid_num=900_001, source_key="NEW-1")
        new["properties"] = {k: v for k, v in new["properties"].items()
                             if k not in ("Golden UID", "Match Score", "Matched By", "Matched At", "_match_hash")}
        repo.upsert([r, s, t, new], "t")
    big.both(change)
    a = big.match(CUSTOMER_RULE)
    assert a["stats"]["changed"] >= 3 and a["stats"]["why"]["entity_lost_record"] >= 1
    big.golden(CUSTOMER)


def test_steward_decisions(big):
    recs = big.one.children(KNA1)
    k1, k2 = recs[10]["uid"], recs[11]["uid"]
    acct = big.one.children(ACCOUNT)
    same_ent = next(((a["uid"], b["uid"]) for a in recs for b in acct
                     if a["properties"].get("Golden UID") and
                     a["properties"].get("Golden UID") == b["properties"].get("Golden UID")), None)
    assert same_ent

    def decide(repo):
        mr.decide(repo, CUSTOMER_RULE, k1, k2, "same", "anna", "same company")
        mr.decide(repo, CUSTOMER_RULE, same_ent[0], same_ent[1], "not", "peter", "different")
    big.both(decide)
    a = big.match(CUSTOMER_RULE)
    assert a["stats"]["why"]["decisions"] >= 2 and a["stats"]["blocked_by_decisions"] >= 1
    big.golden(CUSTOMER)
    # Not this entity: a record leaves its whole entity
    big.both(lambda repo: mr.not_this_entity(repo, CUSTOMER_RULE, k1, "peter"))
    big.match(CUSTOMER_RULE)
    big.golden(CUSTOMER)


def test_previews_are_equal_and_write_nothing(big):
    before = copy.deepcopy(big.dist.nodes)
    a = big.match(CUSTOMER_RULE, mode="full", preview=True)
    assert a["stats"]["preview"] and big.dist.nodes == before
    big.match(SUPPLIER_RULE, preview=True)


def test_record_filter_and_rule_change(big):
    def filt(repo):
        set_props(repo, CUSTOMER_RULE, **{"Source Filters": [f"{KNA1}=NAME1 NOT LIKE 'A%'"]})
    big.both(filt)
    pv = big.match(CUSTOMER_RULE, preview=True)
    assert pv["stats"]["left_by_filter"] > 0
    a = big.match(CUSTOMER_RULE)   # the fingerprint changed: all records again
    assert a["stats"]["filtered_out"] > 0
    big.golden(CUSTOMER)
    big.both(lambda repo: set_props(repo, CUSTOMER_RULE, **{"Source Filters": []}))
    big.match(CUSTOMER_RULE)
    big.golden(CUSTOMER)


def test_supplier_and_employee_incremental(big):
    def change(repo):
        kids = repo.children(to_uid(2210))
        for r in (clone(kids[0]), clone(kids[7])):
            r["properties"]["NAME1"] = str(r["properties"].get("NAME1") or "") + " X"
            repo.upsert([r], "t")
        ad = repo.children(to_uid(2313))
        a, b = clone(ad[0]), clone(ad[5])
        a["properties"]["employeeID"] = b["properties"]["employeeID"]   # two accounts of one employee: they merge
        b["properties"]["employeeID"] = "E-NONE"                        # this one leaves its employee
        repo.upsert([a, b], "t")
    big.both(change)
    a = big.match(SUPPLIER_RULE)
    b = big.match(EMPLOYEE_RULE)
    assert a["stats"]["changed"] >= 2 and b["stats"]["changed"] >= 2
    big.golden(SUPPLIER)
    big.golden(EMPLOYEE)


def test_survivorship_strategies_orphans_and_conflicts(big):
    rule = big.one.get(CUSTOMER)["properties"]["Survivorship Rule"]
    prules = [c for c in big.one.children(rule) if K.NT_PROPERTY_RULE in c["types"]]

    def strategies(repo):
        a = clone(repo.get(prules[0]["uid"]))
        a["properties"]["Strategy"] = to_uid(1322)   # Most Recent
        b = clone(repo.get(prules[1]["uid"]))
        b["properties"]["Use Cleaned Value"] = True
        b["properties"]["Eligible If"] = "length(value) > 3"
        repo.upsert([a, b], "t")
        g = [n for n in repo.children(CUSTOMER) if n["origin"] == "generated"]
        lk = clone(g[0])
        lk["locked"] = True
        ud = clone(g[1])
        ud["properties"][prules[0]["properties"]["Property"]] = "Manual Value"
        ud["user_defined"] = {prules[0]["properties"]["Property"]: True}
        repo.upsert([lk, ud], "t")
    big.both(strategies)
    big.golden(CUSTOMER)
    # retire=False keeps orphans; a Golden UID taken by another node is a conflict
    g = [n for n in big.one.children(CUSTOMER) if n["origin"] == "generated"]
    victim = g[5]

    def clash(repo):
        other = clone(repo.get(victim["uid"]))
        other["parent_uid"], other["path"] = to_uid(1400), list(repo.get(to_uid(1400))["path"]) + [to_uid(1400)]
        other["depth"] = len(other["path"])
        repo.upsert([other], "t")
    big.both(clash)
    res = big.golden(CUSTOMER, retire=False)
    assert res["conflicts"]


def test_small_demo_from_scratch(spark):
    """The hand-written edge cases: exact keys, legal forms, a holding, matched from nothing."""
    p = Pair(spark, demo(generated=False))
    p.match(CUSTOMER_RULE)
    p.golden(CUSTOMER)
    recs = p.one.children(KNA1)
    p.both(lambda repo: mr.decide(repo, CUSTOMER_RULE, recs[0]["uid"], recs[-1]["uid"], "same", "anna"))
    p.match(CUSTOMER_RULE)
    p.golden(CUSTOMER)


def test_exact_only_method(spark):
    p = Pair(spark, demo(generated=True))
    p.both(lambda repo: set_props(repo, CUSTOMER_RULE, Method=METHOD_EXACT))
    p.match(CUSTOMER_RULE, mode="full")
    p.golden(CUSTOMER)


# ---------------------------------------------------------------------------- the setting
def test_processing_setting_and_splink_stays_on_one_machine(spark):
    from mdm.processing import DISTRIBUTED, chosen, mode_of
    repo = demo(generated=False)
    master = repo.get(CUSTOMER)
    assert chosen(master) == "one_machine" and mode_of(repo, master) == "one_machine"
    set_props(repo, CUSTOMER, Processing=DISTRIBUTED)
    assert mode_of(repo, repo.get(CUSTOMER)) == "distributed"
    set_props(repo, CUSTOMER_RULE, Method=METHOD_SPLINK)
    assert mode_of(repo, repo.get(CUSTOMER)) == "one_machine"
    set_props(repo, CUSTOMER_RULE, Method=METHOD_FUZZY)
    # without Spark (local mode, the app) a Distributed node runs on One Machine and says so
    res = processing.run_match(repo, repo.get(CUSTOMER), CUSTOMER_RULE, user="t")
    assert res["stats"]["processing"] == "One Machine" and "jobs" in res["stats"]["processing_note"]
    g = processing.refresh_golden(repo, Catalog(repo), CUSTOMER, "t")
    assert g["processing"] == "One Machine"
    with pytest.raises(Exception):
        set_props(repo, CUSTOMER_RULE, Method=METHOD_SPLINK)
        dist_match(MemoryNodes(spark, repo), CUSTOMER_RULE, user="t")


def test_teardown_tmp_dirs():
    for d in Path(tempfile.gettempdir()).glob("mdm_dist_test_*"):
        shutil.rmtree(d, ignore_errors=True)
