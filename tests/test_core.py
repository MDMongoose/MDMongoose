import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdm import golden, load  # noqa: E402
from mdm.catalog import Catalog  # noqa: E402
from mdm.formats import parse_timestamp, parse_value, split_list  # noqa: E402
from mdm.hierarchy import RuleError  # noqa: E402
from mdm.model import K  # noqa: E402
from mdm.permissions import User  # noqa: E402
from mdm.repo import ConflictError, MemoryRepo  # noqa: E402
from mdm.seed import DEMO_SOURCE_ROWS, all_seed  # noqa: E402
from backend.service import Forbidden, MdmService  # noqa: E402
from mdm.uid import from_uid, to_uid  # noqa: E402

ALICE = User("alice@example.com", {"users"})
ADMIN = User("admin@example.com", {"admins"}, is_admin=True)
KNA1, ACCOUNT, CUSTOMER, COUNTRIES = to_uid(2021), to_uid(2023), to_uid(2050), to_uid(2030)


@pytest.fixture
def svc():
    return MdmService(MemoryRepo(all_seed("workspace", "mdm_staging", demo=True)))


def source_rows(table):
    spec = DEMO_SOURCE_ROWS[table]
    out = []
    for r in spec["rows"][:spec["curated"]]:  # the hand-written edge cases only
        d = dict(zip(spec["columns"], r))
        d["_record_hash"] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
        out.append(d)
    return out


MATCH_RULE = to_uid(2060)


def load_demo(s, match=True):
    """The hand-written kna1 + account records, matched (Golden UIDs written) like after a load."""
    repo = s.repo
    load.merge_records(repo, repo.get(KNA1), source_rows("src_sap_kna1"))
    load.merge_records(repo, repo.get(ACCOUNT), source_rows("src_sfdc_account"))
    if match:
        from mdm.matching import run as mr
        mr.run(repo, MATCH_RULE)


# ---------------------------------------------------------------- basics
def test_uid_roundtrip():
    assert to_uid(1) == "MDM-1"
    assert to_uid(100_001) == "MDM-255T"
    assert from_uid("MDM-255T") == 100_001


def test_formats():
    assert parse_timestamp("15.11.2026 09:30").value == "2026-11-15 09:30"
    assert parse_timestamp("11/15/2026").value == "2026-11-15 00:00"
    assert parse_timestamp("1794731400").ok
    assert split_list('a, "b, c"; d') == ["a", "b, c", "d"]
    assert parse_value("Boolean", "").value is False
    assert parse_value("Number", "1,234").value == 1234
    assert not parse_value("Number", "1.5").ok


def test_seed_unique_and_reserved():
    nodes = all_seed("c", "s", True)
    assert len({n["uid"] for n in nodes}) == len(nodes)
    assert all(n["uid_num"] <= 100_000 for n in nodes)


# ---------------------------------------------------------------- views
def test_node_view_union_and_groups(svc):
    v = svc.node_view(ALICE, to_uid(2031))  # Hungary: Country + Ordered
    names = [p["name"] for p in v["properties"]]
    assert names[0] == "UID" and "Alpha2" in names and "Order" in names
    assert names.index("Owner") > names.index("Alpha2")  # Governance (99) last
    groups = [p["group"] for p in v["properties"]]
    assert groups[-1] == "Governance"


def test_allowed_child_type_inherited_display(svc):
    v = svc.node_view(ALICE, KNA1)
    act = {a["ui"]: a for a in v["actions"]}
    assert act["load_children"]["enabled"]
    assert [a["group"] for a in v["actions"]][0] == "Self"


def test_tree_three_levels(svc):
    t = svc.tree(ALICE, to_uid(2031))
    assert [a["name"] for a in t["ancestors"]] == ["MDM", "Reference Data"]
    assert t["parent"]["name"] == "Countries"
    assert any(s["uid"] == to_uid(2031) for s in t["siblings"])


# ---------------------------------------------------------------- rules
def test_allowed_child_types_enforced(svc):
    with pytest.raises(RuleError):
        svc.add_child(ALICE, COUNTRIES, "Not a country", ["Folder"])
    r = svc.add_child(ALICE, COUNTRIES, "France", ["Country", "Ordered"], {"Alpha2": "FR", "Order": "5"})
    assert from_uid(r["uid"]) > 100_000
    n = svc.repo.get(r["uid"])
    assert n["properties"]["Alpha2"] == "FR" and n["sort_order"] == 5.0


def test_max_child_depth(svc):
    with pytest.raises(RuleError):  # Countries allows 1 level; Hungary has max 0
        svc.add_child(ALICE, to_uid(2031), "Budapest", ["Country"])


def test_locked_and_permissions(svc):
    with pytest.raises(RuleError):
        svc.edit(ALICE, K.SYSTEM, {"Name": "x"})  # locked
    reader = User("bob@example.com", {"readers"})
    with pytest.raises(Forbidden):
        svc.node_view(reader, CUSTOMER)  # no grant for 'readers'
    with pytest.raises(Forbidden):
        svc.toggle_lock(ALICE, K.SYSTEM)  # seeded: only admins unlock
    svc.toggle_lock(ADMIN, K.SYSTEM)


def test_narrowing_permission(svc):
    repo = svc.repo
    perm = svc.add_child(ADMIN, K.SYS_PERMISSIONS, "users · Reader", ["Permission"],
                         {"Group": "users", "Level": "Reader"}) if not repo.get(K.SYS_PERMISSIONS)["locked"] else None
    # Permissions folder is locked → create the node directly as admin after unlocking
    if perm is None:
        svc.toggle_lock(ADMIN, K.SYS_PERMISSIONS)
        perm = svc.add_child(ADMIN, K.SYS_PERMISSIONS, "users · Reader", ["Permission"],
                             {"Group": "users", "Level": "Reader"})
    svc.edit(ADMIN, CUSTOMER, {"Steward": [perm["uid"]]})
    v = svc.node_view(ALICE, CUSTOMER)
    assert v["level"] == "Reader"
    assert all(not a["enabled"] for a in v["actions"] if a["ui"] != "export")
    assert next(a for a in v["actions"] if a["ui"] == "export")["enabled"]  # Export needs only Reader
    with pytest.raises(Forbidden):
        svc.edit(ALICE, CUSTOMER, {"Name": "x"})


def test_change_parent_rewrites_paths(svc):
    a = svc.add_child(ALICE, K.MASTER_DATA, "A", ["Folder"])["uid"]
    b = svc.add_child(ALICE, a, "B", ["Folder"])["uid"]
    c = svc.add_child(ALICE, K.REFERENCE_DATA, "C", ["Folder"])["uid"]
    svc.change_parent(ALICE, a, c)
    assert svc.repo.get(b)["path"][-2:] == [c, a]
    with pytest.raises(RuleError):
        svc.change_parent(ALICE, c, b)  # cycle


def test_conflict(svc):
    n = svc.repo.get(to_uid(2031))
    svc.edit(ALICE, n["uid"], {"Alpha3": "HUX"})
    with pytest.raises(ConflictError):
        svc.edit(ALICE, n["uid"], {"Alpha3": "HUN"}, expected="1999-01-01")


def test_child_link_shown_in_place(svc):
    made = svc.add_child_link(ALICE, K.MASTER_DATA, to_uid(2031))
    t = svc.tree(ALICE, K.MASTER_DATA)
    link = [c for c in t["children"] if c["is_link"]]
    assert link and link[0]["uid"] == made["uid"] and link[0]["target"] == to_uid(2031) and link[0]["name"] == "Hungary"
    assert "child_links" not in svc.node_view(ALICE, K.MASTER_DATA)   # the parent does not list them


def test_child_link_node_own_and_origin_values(svc):
    from mdm.hierarchy import RuleError as RE
    made = svc.add_child_link(ALICE, K.MASTER_DATA, to_uid(2031))["uid"]
    v = svc.node_view(ALICE, made)
    assert v["name"] == "Hungary" and v["link"]["target"] == to_uid(2031) and v["link"]["readable"]
    rows = {r["name"]: r for r in v["properties"]}
    assert rows["Alpha2"]["from_origin"] and rows["Alpha2"]["read_only"] and rows["Name"]["read_only"]
    assert not rows["Max Child Depth"]["read_only"] and not rows.get("Owner", {}).get("from_origin")
    with pytest.raises(RE, match="origin"):
        svc.edit(ALICE, made, {"Alpha2": "XX"})
    svc.edit(ALICE, made, {"Max Child Depth": "2"})
    assert svc.repo.get(made)["max_child_depth"] == 2 and svc.repo.get(to_uid(2031))["max_child_depth"] != 2
    kid = svc.add_child(ALICE, made, "Under the link", ["Folder"])["uid"]   # its own children
    assert [c["uid"] for c in svc.tree(ALICE, made)["children"]] == [kid]
    with pytest.raises(RE, match="already linked"):
        svc.add_child_link(ALICE, K.MASTER_DATA, to_uid(2031))


# ---------------------------------------------------------------- grid / export
def test_children_table_and_batch_edit(svc):
    t = svc.children_table(ALICE, COUNTRIES)
    keys = [c["key"] for c in t["columns"]]
    assert keys[:2] == ["L1.UID", "L1.Name"] and "L1.Alpha2" in keys
    hu = next(r for r in t["rows"] if r["values"]["L1.Name"] == "Hungary")
    res = svc.edit_children(ALICE, COUNTRIES, [
        {"uid": hu["uids"][0], "values": {"Currency": "HUF2"}, "expected": hu["modified"][0]},
        {"values": {"Name": "Poland", "Alpha2": "PL", "Types": "Country, Ordered"}},
        {"uid": "MDM-ZZZZZ", "values": {"Name": "Czechia"}},  # unknown uid → new row with a new uid
    ])
    assert res == {"saved": 3, "errors": []}
    names = [c["name"] for c in svc.repo.children(COUNTRIES)]
    assert "Poland" in names and "Czechia" in names
    assert svc.repo.get(hu["uids"][0])["properties"]["Currency"] == "HUF2"


def test_batch_edit_reports_errors_atomically(svc):
    res = svc.edit_children(ALICE, COUNTRIES, [{"values": {"Name": "Ok", "Types": "Country"}},
                                               {"values": {"Name": "Bad", "Types": "Folder"}}])
    assert res["saved"] == 0 and res["errors"][0]["row"] == 1
    assert "Ok" not in [c["name"] for c in svc.repo.children(COUNTRIES)]


def test_export_balanced(svc):
    name, data, _ = svc.export(ALICE, K.REFERENCE_DATA, "csv", depth=2, balanced=True)
    text = data.decode("utf-8-sig").splitlines()
    assert text[0].startswith("L0.UID,L0.Name")
    assert "L2.Name" in text[0]
    name, data, mt = svc.export(ALICE, COUNTRIES, "xlsx", depth=1)
    assert name.endswith(".xlsx") and data[:2] == b"PK"


# ---------------------------------------------------------------- load + golden
def test_load_merge_and_user_defined(svc):
    load_demo(svc)
    recs = svc.repo.children(KNA1)
    assert len(recs) == 3 and all(r["origin"] == "loaded" for r in recs)
    r = next(x for x in recs if x["source_key"] == "0000100871")
    svc.edit(ALICE, r["uid"], {"NAME1": "Acme Holding (fixed)"})
    assert svc.repo.get(r["uid"])["user_defined"]["NAME1"] is True
    rows = source_rows("src_sap_kna1")
    for row in rows:
        row["TELF1"] = "+49 1"
        row["_record_hash"] += "x"
    res = load.merge_records(svc.repo, svc.repo.get(KNA1), rows[:2])
    assert res["updated"] == 2 and res["deleted"] == 1
    r2 = svc.repo.get(r["uid"])
    assert r2["properties"]["NAME1"] == "Acme Holding (fixed)" and r2["properties"]["TELF1"] == "+49 1"


def test_golden_refresh(svc):
    load_demo(svc)
    res = golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    assert res["golden"] == 4 and res["created"] == 4, res
    kids = {g["name"]: g for g in svc.repo.children(CUSTOMER)}
    acme = kids["ACME GmbH"]
    assert len(acme["properties"]["_members"]) == 2
    assert acme["properties"]["VAT ID"] == "DE123456789"
    assert acme["properties"]["Country"] == to_uid(2032)  # Enum(Countries) → Germany
    assert acme["properties"]["Email"] == "info@acme.example"
    # user-defined value survives the next refresh, uids stay stable
    svc.edit(ALICE, acme["uid"], {"City": "München (HQ)"})
    res2 = golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    assert res2["created"] == 0 and res2["updated"] == 4
    assert svc.repo.get(acme["uid"])["properties"]["City"] == "München (HQ)"


def test_eligible_if():
    f = golden.compile_condition("value RLIKE '^[A-Z]{2,3}[0-9]+$' AND length(value) >= 5")
    assert f("DE123456789") and not f("123") and not f(None)
    assert golden.compile_condition("value LIKE '%@%'")("a@b.c")
    with pytest.raises(RuleError):
        golden.compile_condition("1=1; DROP TABLE x")


def test_old_child_links_become_link_nodes(svc):
    from mdm.links import target_of
    from mdm.seed import upgrade_child_links
    p = dict(svc.repo.get(K.MASTER_DATA), child_links=[{"uid": to_uid(2031), "link_type": "same_as", "sort_order": 1.0}])
    svc.repo.upsert([p], "test")
    assert upgrade_child_links(svc.repo) == ["1 child link(s) converted to Child Link nodes"]
    links = svc.repo.links_under([K.MASTER_DATA])
    assert [target_of(l) for l in links] == [to_uid(2031)] and links[0]["properties"]["Link Type"] == to_uid(1331)
    assert svc.repo.get(K.MASTER_DATA)["child_links"] == [] and upgrade_child_links(svc.repo) == []
