"""The generated demo: 3 customer sources, suppliers, employees, the 6-level product hierarchy."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdm import golden, load  # noqa: E402
from mdm.catalog import Catalog  # noqa: E402
from mdm.model import K  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import DEMO_SOURCE_ROWS, DEMO_TABLES, all_seed, upgrade_existing  # noqa: E402
from mdm.uid import to_uid  # noqa: E402

CUSTOMER, SUPPLIER, EMPLOYEE, PRODUCT = to_uid(2050), to_uid(2240), to_uid(2340), to_uid(3000)


@pytest.fixture(scope="module")
def repo():
    r = MemoryRepo(all_seed("workspace", "mdm_staging", demo=True))
    for table_uid, src in DEMO_TABLES.items():
        spec = DEMO_SOURCE_ROWS[src]
        rows = []
        for row in spec["rows"]:
            d = dict(zip(spec["columns"], row))
            d["_record_hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
            rows.append(d)
        load.merge_records(r, r.get(table_uid), rows)
    return r


def test_source_sizes_and_unique_keys():
    for name, spec in DEMO_SOURCE_ROWS.items():
        keys = [row[0] for row in spec["rows"]]
        assert len(keys) == len(set(keys)), name
        assert all(len(row) == len(spec["columns"]) for row in spec["rows"]), name
    assert len(DEMO_SOURCE_ROWS["src_workday_workers"]["rows"]) == 300
    assert 1600 < sum(len(DEMO_SOURCE_ROWS[t]["rows"]) for t in
                      ("src_sap_kna1", "src_sfdc_account", "src_shop_customers")) < 2000


def test_golden_records_find_the_real_entities(repo):
    from mdm.matching import run as mr
    for rule in (2060, 2260, 2360):
        mr.run(repo, to_uid(rule), mode="full")
    cat = Catalog(repo)
    c = golden.refresh(repo, cat, CUSTOMER, "t")
    assert c["golden"] == 1004 and not c["conflicts"]  # 1 000 generated companies + 4 hand-written ones
    s = golden.refresh(repo, cat, SUPPLIER, "t")
    assert s["golden"] == 200 and not s["conflicts"]
    e = golden.refresh(repo, cat, EMPLOYEE, "t")
    assert e["golden"] == 300 and not e["conflicts"]
    emp = repo.children(EMPLOYEE, limit=1)[0]
    assert emp["properties"]["Employee ID"].startswith("E1") and "@" in emp["properties"].get("Email", "@")


def test_product_hierarchy(repo):
    products = [n for n in repo.subtree(PRODUCT) if to_uid(2401) in n["types"]]
    assert len(products) == 50
    root = repo.get(PRODUCT)
    assert {p["depth"] - root["depth"] for p in products} == {6}
    assert len({p["properties"]["EAN"] for p in products}) == 50


def test_upgrade_adds_new_sources_to_an_existing_rule():
    seeds = all_seed("workspace", "mdm_staging", True)
    old = [dict(n, properties=dict(n["properties"])) for n in seeds]
    rule = next(n for n in old if n["uid"] == to_uid(2040))
    rule["properties"]["Sources"] = rule["properties"]["Sources"][:2]
    pr = next(n for n in old if n["uid"] == to_uid(2041))
    pr["properties"]["Source Mapping"] = "; ".join(pr["properties"]["Source Mapping"].split("; ")[:2])
    r = MemoryRepo(old)
    assert upgrade_existing(r, seeds) >= 2
    assert to_uid(2025) in r.get(to_uid(2040))["properties"]["Sources"]
    assert "=company" in r.get(to_uid(2041))["properties"]["Source Mapping"]


def test_node_fields_in_node_group_and_upgrade_moves_them():
    seeds = all_seed("workspace", "mdm_staging", True)
    node_t = next(n for n in seeds if n["uid"] == to_uid(50))
    groups = {d["name"]: (d["group"], d["priority"]) for d in node_t["properties"]["property_defs"]}
    assert groups["UID"] == groups["Name"] == ("General", 0) and groups["Parent"] == ("Node", 98)
    old = [dict(n, properties=dict(n["properties"])) for n in seeds]
    o = next(n for n in old if n["uid"] == to_uid(50))
    o["properties"]["property_defs"] = [dict(d, group="General", priority=0) if d["group"] == "Node" else d
                                        for d in o["properties"]["property_defs"]]
    r = MemoryRepo(old)
    assert upgrade_existing(r, seeds) >= 1
    defs = {d["name"]: d["group"] for d in r.get(to_uid(50))["properties"]["property_defs"]}
    assert defs["Types"] == "Node" and defs["UID"] == "General"
