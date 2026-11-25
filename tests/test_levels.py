"""Seven permission levels, Required Level per action, Governance properties, Enum NodeType, upgrade."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdm.catalog import Catalog  # noqa: E402
from mdm.hierarchy import RuleError  # noqa: E402
from mdm.model import K  # noqa: E402
from mdm.permissions import LEVEL_UIDS, MODIFIER, User  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import all_seed, upgrade_release  # noqa: E402
from backend.service import Forbidden, MdmService  # noqa: E402
from mdm.uid import to_uid  # noqa: E402

ADMIN = User("admin@example.com", {"admins", "users"}, is_admin=True)
PRODUCT = to_uid(3000)
FAMILY = to_uid(3005)  # ProDrill 18V (Product Family) with 5 products


@pytest.fixture()
def svc():
    repo = MemoryRepo(all_seed("workspace", "mdm_staging", demo=True))
    s = MdmService(repo, Catalog(repo))
    levels = ["Reader", "Modifier", "Supervisor", "Manager", "Governor"]
    uids = s.resolve_permissions(ADMIN, [{"group": "users", "level": "Reader"}]
                                 + [{"group": lv.lower() + "s", "level": lv} for lv in levels])
    root = repo.get(K.ROOT)
    repo.upsert([dict(root, steward=uids)], "test")       # users only read; one group per level
    return s


def who(level: str) -> User:
    return User(f"{level}@example.com", {"users", level.lower() + "s"})


def test_levels_and_action_states(svc):
    v = svc.node_view(who("Modifier"), PRODUCT)
    assert v["level"] == "Modifier" and v["can"]["edit"] and not v["can"]["add"] and not v["can"]["lock"]
    acts = {a["ui"]: a for a in v["actions"]}
    assert acts["edit"]["enabled"] and acts["export"]["enabled"]
    assert not acts["add_child"]["enabled"] and acts["add_child"]["why"] == "needs Manager — you have Modifier"
    assert not acts["lock"]["enabled"] and acts["lock"]["required_level"] == "Supervisor"
    r = svc.node_view(who("Reader"), PRODUCT)
    assert [a["ui"] for a in r["actions"] if a["enabled"]] == ["export"]
    assert svc.node_view(ADMIN, PRODUCT)["level"] == "Admin"


def test_modifier_supervisor_manager_governor(svc):
    svc.edit(who("Modifier"), FAMILY, {"Code": "X-1"})
    with pytest.raises(Forbidden):
        svc.add_child(who("Modifier"), FAMILY, "New", ["Product"])
    with pytest.raises(Forbidden):
        svc.toggle_lock(who("Modifier"), FAMILY)
    with pytest.raises(Forbidden):
        svc.edit(who("Modifier"), FAMILY, {"Owner": "someone"})
    mine = svc.add_child(ADMIN, FAMILY, "Lockable", ["Product"])["uid"]   # seeded nodes: only admins unlock
    svc.toggle_lock(who("Supervisor"), mine)
    svc.toggle_lock(who("Supervisor"), mine)
    with pytest.raises(Forbidden):
        svc.change_parent(who("Supervisor"), FAMILY, to_uid(3003))
    kid = svc.add_child(who("Manager"), FAMILY, "New", ["Product"])["uid"]
    svc.delete(who("Manager"), kid)
    with pytest.raises(Forbidden):
        svc.edit(who("Manager"), FAMILY, {"Steward": []})
    svc.edit(who("Governor"), FAMILY, {"Owner": "product-team"})
    assert svc.repo.get(FAMILY)["owner"] == "product-team"


def test_grid_rows_need_manager(svc):
    t = svc.children_table(who("Modifier"), FAMILY)
    assert t["can_edit"] and not t["can_add"] and not t["can_delete"] and not t["can_govern"]
    first = t["rows"][0]["uids"][0]
    assert svc.edit_children(who("Modifier"), FAMILY, [{"uid": first, "values": {"SKU": "S-1"}}])["saved"] == 1
    with pytest.raises(Forbidden):
        svc.edit_children(who("Modifier"), FAMILY, [{"values": {"Name": "Row"}}])
    with pytest.raises(Forbidden):
        svc.edit_children(who("Modifier"), FAMILY, [], deletes=[first])
    with pytest.raises(Forbidden):
        svc.edit_children(who("Manager"), FAMILY, [{"uid": first, "values": {"Owner": "x"}}])


def test_required_level_is_a_property_of_the_action(svc):
    edit = svc.repo.get(K.A_EDIT)
    assert K.NT_ACTION in edit["types"] and edit["properties"]["Required Level"] == LEVEL_UIDS[MODIFIER]
    svc.repo.upsert([dict(edit, properties={**edit["properties"], "Required Level": LEVEL_UIDS[4]})], "test")
    svc.catalog.invalidate()
    with pytest.raises(Forbidden):  # Edit now needs Manager
        svc.edit(who("Modifier"), FAMILY, {"Code": "X-2"})
    a = svc.create_action(ADMIN, "Validate", "mdm_custom.actions.validate", required_level="Supervisor")
    assert svc.repo.get(a["uid"])["properties"]["Required Level"] == LEVEL_UIDS[3]
    with pytest.raises(RuleError):
        svc.resolve_permissions(ADMIN, [{"group": "x", "level": "Admin"}])  # Admin is not grantable


def test_enum_nodetype(svc):
    lst = svc.add_child(ADMIN, K.MODEL_ENUMS, "Regions", ["Folder", "Enum"])["uid"]
    assert svc.repo.get(lst)["properties"]["kind"] == "Enum"
    for name in ("EMEA", "APAC"):
        svc.add_child(ADMIN, lst, name, ["Ordered"])
    t = svc.create_node_type(ADMIN, K.MODEL_NODE_TYPES, "Region Holder", "box",
                             [{"name": "Region", "base": "Enum", "param": "Regions"}], [])["uid"]
    node = svc.add_child(ADMIN, K.MASTER_DATA, "Holder", ["Folder", t], {"Region": "apac"})["uid"]
    row = next(p for p in svc.node_view(ADMIN, node)["properties"] if p["name"] == "Region")
    assert "options" not in row and row["display"] == "APAC"          # the page lists no values …
    assert [o["name"] for o in svc.options(ADMIN, "Enum(Regions)")] == ["EMEA", "APAC"]   # … the dropdown asks
    svc.edit(ADMIN, lst, {"Types": ["Folder"]})          # no longer an enum list
    assert "kind" not in svc.repo.get(lst)["properties"]
    with pytest.raises(RuleError):
        svc.create_node_type(ADMIN, K.MODEL_NODE_TYPES, "Broken", "box",
                             [{"name": "R", "base": "Enum", "param": "Regions"}], [])


def test_upgrade_release():
    repo = MemoryRepo(all_seed("workspace", "mdm_staging", demo=True))
    old = []
    for n in repo.by_kind("Enum"):                         # enum lists before this release: kind only
        old.append(dict(n, types=[t for t in n["types"] if t != K.NT_ENUM]))
    for n in repo.by_kind("Action"):
        p = {k: v for k, v in n["properties"].items() if k != "Required Level"}
        old.append(dict(n, types=[K.NT_FOLDER], properties=p))
    perm = repo.get(to_uid(60))
    old.append(dict(perm, name="users · Modify", properties={"Group": "users", "Level": LEVEL_UIDS[MODIFIER]}))
    for uid, name in ((LEVEL_UIDS[1], "Read"), (LEVEL_UIDS[2], "Modify")):
        old.append(dict(repo.get(uid), name=name))
    repo.upsert(old, "old")
    notes = upgrade_release(repo, [])
    assert any("users · Manager" in x for x in notes)
    assert repo.get(to_uid(60))["properties"]["Level"] == LEVEL_UIDS[4]
    assert repo.get(LEVEL_UIDS[1])["name"] == "Reader"
    assert all(K.NT_ENUM in n["types"] for n in repo.by_kind("Enum"))
    assert all(n["properties"].get("Required Level") and K.NT_ACTION in n["types"] for n in repo.by_kind("Action"))
    assert upgrade_release(repo, []) == []                # idempotent


def test_enum_values_are_looked_up_not_listed(svc):
    """A page names its Enum / Link values with one lookup; typing a value looks up that name only."""
    lst = svc.add_child(ADMIN, K.MODEL_ENUMS, "Sizes", ["Folder", "Enum"])["uid"]
    for name in ("Small", "Medium", "Large"):
        svc.add_child(ADMIN, lst, name, ["Ordered"])
    t = svc.create_node_type(ADMIN, K.MODEL_NODE_TYPES, "Sized", "box",
                             [{"name": "Size", "base": "Enum", "param": "Sizes"},
                              {"name": "Other", "base": "Link", "param": "Node"}], [])["uid"]
    calls = []
    real_children, real_named = svc.repo.children, svc.repo.named
    svc.repo.children = lambda *a, **k: (calls.append(("children", a[0] if a else None)), real_children(*a, **k))[1]
    svc.repo.named = lambda *a, **k: (calls.append(("named", a[0] if a else None)), real_named(*a, **k))[1]
    node = svc.add_child(ADMIN, K.MASTER_DATA, "Box", ["Folder", t], {"Size": "medium", "Other": K.MASTER_DATA})["uid"]
    assert ("named", "medium") in calls and not any(c == ("children", lst) for c in calls)
    calls.clear()
    real_get, real_many = svc.repo.get, svc.repo.get_many
    gets = []
    svc.repo.get = lambda *a, **k: (gets.append(("get", a)), real_get(*a, **k))[1]
    svc.repo.get_many = lambda uids, include_deleted=False: (gets.append(("many", tuple(uids))), real_many(uids, include_deleted))[1]
    v = svc.node_view(ADMIN, node)
    svc.repo.get, svc.repo.get_many, svc.repo.children, svc.repo.named = real_get, real_many, real_children, real_named
    rows = {r["name"]: r for r in v["properties"]}
    assert rows["Size"]["display"] == "Medium" and rows["Size"]["items"][0]["name"] == "Medium"
    assert not any(c[0] == "children" and c[1] == lst for c in calls)          # the Enum's values are not read
    medium = rows["Size"]["value"]
    named = [g for g in gets if medium in g[1]]
    assert len(named) == 1 and named[0][0] == "many"     # the shown values: one batched lookup, nothing per value
