"""Export nodes: the generated SQL returns the same table as the Export dialog (runs on local Spark)."""

import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdm import export, exports, golden  # noqa: E402
from mdm.catalog import Catalog  # noqa: E402
from mdm.model import K, kind_of, path_key  # noqa: E402
from mdm.permissions import User  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import all_seed  # noqa: E402
from backend.service import MdmService  # noqa: E402
from mdm.uid import to_uid  # noqa: E402

ADMIN = User("admin@example.com", {"admins", "users"}, is_admin=True)
pyspark = pytest.importorskip("pyspark")
if not shutil.which("java"):
    pytest.skip("no Java for local Spark", allow_module_level=True)


@pytest.fixture(scope="module")
def spark():
    import time
    os.environ["TZ"] = "UTC"  # collect() turns timestamps into local time
    time.tzset()
    from pyspark.sql import SparkSession
    s = (SparkSession.builder.master("local[1]").appName("mdm-exports-test")
         .config("spark.ui.enabled", "false").config("spark.sql.session.timeZone", "UTC").config("spark.sql.shuffle.partitions", "1").getOrCreate())
    yield s
    s.stop()


@pytest.fixture(scope="module")
def svc():
    import hashlib
    from mdm import load
    from mdm.seed import DEMO_SOURCE_ROWS, DEMO_TABLES
    repo = MemoryRepo(all_seed("workspace", "mdm_staging", demo=True))
    for table_uid, src in list(DEMO_TABLES.items())[:3]:
        spec = DEMO_SOURCE_ROWS[src]
        rows = []
        for r in spec["rows"][:120]:
            d = dict(zip(spec["columns"], r))
            d["_record_hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
            rows.append(d)
        load.merge_records(repo, repo.get(table_uid), rows)
    s = MdmService(repo, Catalog(repo))
    from mdm.matching import run as mr
    mr.run(repo, to_uid(2060))
    golden.refresh(repo, s.catalog, to_uid(2050), "test")
    return s


def _table(spark, repo):
    rows = []
    for n in repo.subtree(K.ROOT) + [repo.get(K.ROOT)]:
        rows.append((n["uid"], n["parent_uid"], n["name"], n["types"], json.dumps(n["properties"] or {}, default=str),
                     n["sort_order"], [{"uid": l["uid"], "link_type": l.get("link_type"), "sort_order": l.get("sort_order")}
                                       for l in n.get("child_links") or []], False, path_key(n), kind_of(n)))
    df = spark.createDataFrame(rows, "uid STRING, parent_uid STRING, name STRING, types ARRAY<STRING>, p STRING, "
                                     "sort_order DOUBLE, child_links ARRAY<STRUCT<uid: STRING, link_type: STRING, "
                                     "sort_order: DOUBLE>>, is_deleted BOOLEAN, path_key STRING, kind STRING")
    df.selectExpr("*", "parse_json(p) AS properties").drop("p").createOrReplaceTempView("mdmnodes")


def _cell(v):
    import datetime
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return str(v)


@pytest.mark.parametrize("root,depth,balanced,flag", [
    (to_uid(2050), 1, False, False),     # Customer: golden records with Enum (Country) and Link properties
    (to_uid(3000), 6, False, False),     # Product: 6 levels, ragged
    (to_uid(3000), 6, True, True),       # Product balanced, with the Balanced flag
    (to_uid(2030), 1, False, False),     # Countries
])
def test_sql_matches_export_dialog(spark, svc, root, depth, balanced, flag):
    repo, cat = svc.repo, svc.catalog.refresh(force=True)
    _table(spark, repo)
    spec = {"kind": "view", "name": "t", "root": root, "depth": depth, "balanced": balanced, "with_props": True,
            "with_uid": True, "mark_balanced": flag}
    sql, cols = exports.build_sql(repo, cat, spec, "mdmnodes")
    got = spark.sql(sql).collect()
    _, ui_cols, ui_rows = export.build(repo, cat, repo.get(root), export.ExportOptions(
        depth=depth, balanced=balanced, with_props=True, with_uid=True, mark_balanced=flag))
    assert [c.replace(".", "_").replace(" ", "_") for c in [x["label"] for x in ui_cols]] == cols
    want = sorted(tuple(c["text"] for c in r) for r in ui_rows)
    have = sorted(tuple(_cell(r[c]) for c in cols) for r in got)
    # numbers: the dialog prints stored text, SQL returns typed values — compare loosely
    def norm(rows):
        def one(x):
            try:
                f = float(x)
                return str(int(f)) if f.is_integer() else str(f)
            except ValueError:
                return x
        return sorted(tuple(one(x) for x in r) for r in rows)
    assert len(have) == len(want)
    for h, w in zip(norm(have), norm(want)):
        assert h == w, [(c, a, b) for c, a, b in zip(cols, h, w) if a != b]


def test_export_node_validation(svc):
    from mdm.hierarchy import RuleError
    with pytest.raises(RuleError):  # the demo Export node already uses customer_golden_records
        svc.add_child(ADMIN, K.MODEL_EXPORTS, "Customer golden records", ["Export"],
                      {"Export Type": "Materialized view", "Root Node": to_uid(2050), "Child Depth": 1})
    r = svc.add_child(ADMIN, K.MODEL_EXPORTS, "Customer golden (v2)", ["Export"],
                      {"Export Type": "Materialized view", "Root Node": to_uid(2050), "Child Depth": 1})
    n = svc.repo.get(r["uid"])
    assert n["properties"]["Object Name"] == "customer_golden_v2"
    assert n["properties"]["Location"] == "workspace.mdm_export.customer_golden_v2"
    assert n["properties"]["Include UIDs"] is True and "Last Run" in n["properties"]
    with pytest.raises(RuleError):  # a CSV may reuse a table name, a view may not
        svc.add_child(ADMIN, K.MODEL_EXPORTS, "Again", ["Export"], {"Export Type": "View", "Root Node": to_uid(2050),
                                                                    "Object Name": "customer_golden_v2"})
    with pytest.raises(RuleError):  # no Export Type
        svc.add_child(ADMIN, K.MODEL_EXPORTS, "No type", ["Export"], {"Root Node": to_uid(2050)})
    with pytest.raises(RuleError):  # system object
        svc.add_child(ADMIN, K.MODEL_EXPORTS, "Index", ["Export"], {"Export Type": "View", "Root Node": to_uid(2050),
                                                                    "Object Name": "node_index"})
    f = svc.add_child(ADMIN, K.MODEL_EXPORTS, "Products file", ["Export"],
                      {"Export Type": "CSV", "Root Node": to_uid(3000), "Child Depth": 6, "Object Name": "products"})
    assert svc.repo.get(f["uid"])["properties"]["Location"] == "/Volumes/workspace/mdm_export/exports/products.csv"
    v = svc.node_view(ADMIN, r["uid"])
    assert v["definition"]["kind"] == "Export" and v["definition"]["sql"].startswith("WITH n AS")


def test_sql_with_child_links(spark, svc):
    repo, cat = svc.repo, svc.catalog.refresh(force=True)
    folder = svc.add_child(ADMIN, K.MASTER_DATA, "Featured", ["Folder"])["uid"]
    line = svc.add_child_link(ADMIN, folder, to_uid(3006))["uid"]   # a product line: a Child Link node
    svc.add_child(ADMIN, line, "Own child of the link", ["Folder"])     # the link has its own children
    svc.add_child_link(ADMIN, folder, to_uid(2031))   # Hungary
    _table(spark, repo)
    spec = {"kind": "view", "name": "t", "root": folder, "depth": 3, "balanced": False, "with_props": False,
            "with_uid": True, "mark_balanced": False}
    sql, cols = exports.build_sql(repo, cat, spec, "mdmnodes")
    assert "Linked Node" in sql and "explode(child_links)" not in sql
    got = sorted(tuple(_cell(r[c]) for c in cols) for r in spark.sql(sql).collect())
    _, _, ui_rows = export.build(repo, cat, repo.get(folder), export.ExportOptions(depth=3, with_props=False))
    assert got == sorted(tuple(c["text"] for c in r) for r in ui_rows)
