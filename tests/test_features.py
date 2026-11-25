"""Dialog-level features: rule editor, golden dry run, Configure Load, export options, NodeTypes."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdm import export, golden, load  # noqa: E402
from mdm.hierarchy import RuleError  # noqa: E402
from mdm.model import K  # noqa: E402
from mdm.permissions import User  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import all_seed, upgrade_existing  # noqa: E402
from backend.service import MdmService  # noqa: E402
from mdm.sources import DemoSourceReader  # noqa: E402
from mdm.uid import to_uid  # noqa: E402

from test_core import ADMIN, ALICE, KNA1, ACCOUNT, CUSTOMER, COUNTRIES, load_demo, source_rows  # noqa: E402

RULE = to_uid(2040)


@pytest.fixture
def svc():
    s = MdmService(MemoryRepo(all_seed("workspace", "mdm_staging", demo=True)), sources=DemoSourceReader())
    load_demo(s)
    return s


def test_rule_editor_preview_and_save(svc):
    ed = svc.rule_editor(ALICE, RULE)
    assert [s["label"] for s in ed["sources"]] == ["SAP ECC · kna1", "Salesforce · account", "Webshop · customers"]
    assert {r["property"] for r in ed["rules"]} == {"Legal Name", "VAT ID", "Country", "City", "Email", "Phone"}
    city = next(r for r in ed["rules"] if r["property"] == "City")
    # switch City to Source Priority (kna1 first) in the editor → preview reflects the unsaved config
    city.update(strategy="sp", priority=[KNA1, ACCOUNT])
    pv = svc.rule_preview(ALICE, RULE, {"rules": ed["rules"], "property": "City"})
    assert pv["value"] == "München" and pv["cluster"]["records"] == 2
    assert [r["step"] for r in pv["rows"]][0] == "★ winner"
    sim = svc.rule_simulate(ALICE, RULE, {"rules": ed["rules"]})
    assert sim["records"] == 5 and sim["golden"] == 4
    svc.save_rule(ALICE, RULE, {"rules": ed["rules"]})
    assert golden.rule_cfg(svc.repo.get(to_uid(2044)))["strategy"] == "sp"
    with pytest.raises(RuleError):
        svc.save_rule(ALICE, RULE, {"rules": [dict(city, eligible="1=1; drop table x")]})


def test_eligible_if_source():
    f = golden.compile_condition("source = 'kna1' AND value LIKE '%a%'")
    assert f("abc", "SAP ECC · kna1") and not f("abc", "Salesforce · account")


def test_golden_plan_groups_by_golden_uid_and_links(svc):
    from mdm.matching import run as mr
    p = golden.plan(svc.repo, svc.catalog, CUSTOMER)
    assert p["counts"] == {"Create": 4} and p["unmatched"] == 0
    acme = next(g for g in p["groups"] if g["name"] == "ACME GmbH")
    assert len(acme["records"]) == 2 and acme["values"][0]["prop"] == "Legal Name"
    golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    kids = svc.repo.children(CUSTOMER)
    from mdm.links import target_of
    for g in kids:   # each golden record has a Child Link node per source record
        links = svc.repo.links_under([g["uid"]])
        assert links and sorted(target_of(l) for l in links) == g["properties"]["_members"]
        assert all(l["locked"] and l["origin"] == "generated" for l in links)
    golden.refresh(svc.repo, svc.catalog, CUSTOMER)   # a second refresh keeps the links as they are
    assert sum(len(svc.repo.links_under([g["uid"]])) for g in kids) == sum(len(g["properties"]["_members"]) for g in kids)
    recs = {r["properties"]["NAME1"]: r for r in svc.repo.children(KNA1)}
    # the golden record's UID is the Golden UID matching wrote on its records
    assert svc.repo.get(recs["ACME GmbH"]["properties"]["Golden UID"])["name"] == "ACME GmbH"
    # a steward merges Acme Holding into ACME GmbH: next run -> survivorship updates one, retires the other
    mr.decide(svc.repo, to_uid(2060), recs["Acme Holding"]["uid"], recs["ACME GmbH"]["uid"], "same", "anna")
    mr.run(svc.repo, to_uid(2060))
    p2 = golden.plan(svc.repo, svc.catalog, CUSTOMER)
    assert p2["counts"].get("Update", 0) == 1 and p2["counts"].get("Retire", 0) == 1


def test_load_mapping_name_expression_and_delete_modes(svc):
    t = svc.repo.get(KNA1)
    cfg = svc.load_config(ALICE, KNA1)
    assert cfg["name_expression"].startswith("KUNNR") and cfg["delete_mode"] == "soft"
    body = dict(cfg, mapping=[{"source": "NAME1", "property": "NAME1", "transform": "upper()"},
                              {"source": "LAND1", "property": "LAND1", "transform": "—"}], delete_mode="hard")
    pv = svc.load_preview(ALICE, KNA1, body)
    assert pv["records"][0]["properties"]["NAME1"] == "ACME GMBH"
    assert pv["records"][0]["name"] == "0000100234 · ACME GmbH"
    svc.save_load_config(ALICE, KNA1, body)
    res = load.merge_records(svc.repo, svc.repo.get(KNA1), source_rows("src_sap_kna1")[:2])
    assert res["updated"] == 2 and res["deleted"] == 1  # mapping change re-maps; hard delete
    rec = next(r for r in svc.repo.children(KNA1) if r["source_key"] == "0000100234")
    assert rec["properties"]["NAME1"] == "ACME GMBH" and "ORT01" not in rec["properties"]
    assert len(svc.repo.children(KNA1)) == 2 and len(svc.repo.subtree(KNA1)) == 2
    assert t["uid"] == KNA1


def test_export_options_and_preview(svc):
    node = svc.repo.get(K.REFERENCE_DATA)
    opt = export.ExportOptions(format="csv", depth=3, balanced=True, mark_balanced=True, with_uid=False, delimiter=";")
    pv = export.preview(svc.repo, svc.catalog.refresh(), node, opt)
    assert pv["columns"][0]["label"] == "L0.Name" and pv["columns"][-1]["label"] == "Balanced"
    assert pv["clones"] > 0
    assert not any(c.get("name") == "Origin" for c in pv["columns"])  # base Node props are not exported
    name, data, _ = export.run(svc.repo, svc.catalog, node, opt=opt)
    assert name.endswith(".csv") and b";" in data.splitlines()[0]
    name, data, _ = export.run(svc.repo, svc.catalog, node, opt=export.ExportOptions(format="xlsx", depth=2, sheets="per_level"))
    from io import BytesIO
    from openpyxl import load_workbook
    assert load_workbook(BytesIO(data)).sheetnames == ["L0", "L1", "L2"]


def test_node_type_create_edit_and_grid_property(svc):
    r = svc.create_node_type(ALICE, K.MODEL_NODE_TYPES, "Address", "pin",
                             [{"name": "Street", "base": "String"}, {"name": "Country", "base": "Enum", "param": "Countries"}],
                             [K.A_EDIT], {"name": "Validate", "handler": "mdm_custom.actions.validate"})
    t = svc.repo.get(r["uid"])
    assert [d["ptype"] for d in t["properties"]["property_defs"]] == ["String", "Enum(Countries)"]
    assert len(t["properties"]["actions"]) == 2
    with pytest.raises(RuleError):
        svc.create_node_type(ALICE, K.MODEL_NODE_TYPES, "Bad", "box", [{"name": "X", "base": "Enum", "param": "Nope"}], [])
    view = svc.node_view(ALICE, r["uid"])
    assert view["definition"]["kind"] == "NodeType" and view["definition"]["implemented_by"] == 0
    svc.save_node_type(ALICE, r["uid"], view["definition"]["property_defs"] + [{"name": "City", "base": "String"}])
    folder = svc.add_child(ALICE, K.MASTER_DATA, "Addresses", ["Folder"], {"Allowed Child Type": "Address"})["uid"]
    svc.add_child(ALICE, folder, "HQ", ["Address"], {"Street": "Main 1", "Country": "Germany"})


def test_valid_parents_delete_move_and_permissions(svc):
    vp = svc.valid_parents(ALICE, to_uid(2031))
    assert all(p["uid"] != to_uid(2031) for p in vp)
    assert any(p["uid"] == K.REFERENCE_DATA for p in vp)
    sap = to_uid(2020)
    r = svc.delete(ALICE, sap, mode="move")
    assert r["moved"] >= 4 and svc.repo.get(KNA1)["parent_uid"] == K.REFERENCE_DATA
    perms = svc.node_view(ALICE, CUSTOMER)
    steward = next(p for p in perms["properties"] if p["name"] == "Steward")
    assert steward["perms"][0]["group"] == "admins"
    assert any(x["group"] == "users" and x["level"] == "Manager" for x in steward["perms"])


def test_seed_upgrade_adds_new_definitions():
    old = all_seed("c", "s", True)
    table = next(n for n in old if n["uid"] == K.NT_TABLE)
    table["properties"]["property_defs"] = table["properties"]["property_defs"][:2]
    repo = MemoryRepo(old)
    assert upgrade_existing(repo, all_seed("c", "s", True)) >= 1
    names = [d["name"] for d in repo.get(K.NT_TABLE)["properties"]["property_defs"]]
    assert "Column Mapping" in names and "Delete Mode" in names


def test_children_table_types_and_undefined(svc):
    t = svc.children_table(ADMIN, K.REFERENCE_DATA, 2)
    assert t["level_types"]["1"] and isinstance(t["rows"][0]["undefined"], list)
    assert t["handler"] == "mdm.actions.edit_children"
    res = svc.edit_children(ALICE, COUNTRIES, [{"values": {"Name": "X", "Types": "Country, Ordered", "Order": "abc"}}])
    assert res["errors"][0]["field"] == "Order"


def test_search_filters(svc):
    assert all(h["origin"] == "loaded" for h in svc.search(ALICE, "acme", "loaded"))
    assert User("x", {"users"}).principals == {"x", "users"}
