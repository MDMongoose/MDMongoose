"""Manage hierarchy: move / clone / link / delete of several subtrees with left-out branches."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdm.hierarchy import RuleError  # noqa: E402
from mdm.model import K, new_node  # noqa: E402
from mdm.permissions import User  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import all_seed  # noqa: E402
from backend.service import Forbidden, MdmService  # noqa: E402

from test_core import ADMIN, ALICE  # noqa: E402


@pytest.fixture
def s():
    """Root › Regions › {DACH › {DE › Berlin, AT}, CEE};  Root › Archive"""
    svc = MdmService(MemoryRepo(all_seed("workspace", "mdm_staging", demo=True)))
    repo = svc.repo
    root = repo.get(K.ROOT)
    nums = iter(repo.allocate(20))
    made = {}

    def add(name, parent):
        n = new_node(next(nums), name, [K.NT_FOLDER], parent)
        repo.upsert([n], "t")
        made[name] = repo.get(n["uid"])
        return made[name]

    regions = add("Regions", root)
    dach = add("DACH", regions)
    de = add("DE", dach)
    add("Berlin", de)
    add("AT", dach)
    add("CEE", regions)
    add("Archive", root)
    svc.u = {k: v["uid"] for k, v in made.items()}
    return svc


def kids(s, name):
    return sorted(c["name"] for c in s.repo.children(s.u[name]))


def test_counts_with_left_out(s):
    u = s.u
    c = s.manage_count(ALICE, {"selected": [u["DACH"]], "left_out": [u["AT"]]})
    assert c == {"roots": 1, "nodes": 3, "child_elements": 2, "left_out_branches": 1, "left_out_nodes": 1}
    # a node ticked again inside a left-out branch is its own root
    c = s.manage_count(ALICE, {"selected": [u["Regions"], u["Berlin"]], "left_out": [u["DE"]]})
    assert c["roots"] == 2 and c["nodes"] == 6 - 2 + 1  # Regions subtree (6) - DE branch (2) + Berlin


def test_move_keeps_left_out_branches(s):
    u = s.u
    plan = s.manage_plan(ALICE, "move", {"selected": [u["DACH"]], "left_out": [u["AT"]], "target": u["Archive"]})
    assert "problem" not in plan and plan["stay_under"] == ["Regions"]
    s.manage_apply(ALICE, "move", {"selected": [u["DACH"]], "left_out": [u["AT"]], "target": u["Archive"]})
    assert kids(s, "Archive") == ["DACH"] and kids(s, "DACH") == ["DE"]
    assert kids(s, "Regions") == ["AT", "CEE"]
    assert s.repo.get(u["Berlin"])["path"][-3:] == [u["Archive"], u["DACH"], u["DE"]]


def test_move_into_own_selection_is_refused(s):
    u = s.u
    plan = s.manage_plan(ALICE, "move", {"selected": [u["Regions"]], "target": u["DE"]})
    assert "inside the selection" in plan["problem"]
    with pytest.raises(RuleError):
        s.manage_apply(ALICE, "move", {"selected": [u["Regions"]], "target": u["DE"]})
    # but a left-out branch moves up first, so it can be the new parent
    s.manage_apply(ALICE, "move", {"selected": [u["DACH"]], "left_out": [u["AT"]], "target": u["AT"]})
    assert kids(s, "AT") == ["DACH"]


def test_clone_copies_only_the_selection(s):
    u = s.u
    out = s.manage_apply(ALICE, "clone", {"selected": [u["DACH"]], "left_out": [u["AT"]], "target": u["Archive"],
                                          "name": "DACH 2027"})
    assert out["cloned"] == 3
    copy = s.repo.get(out["roots"][0])
    assert copy["name"] == "DACH 2027" and copy["uid"] != u["DACH"] and copy["origin"] == "manual"
    de2 = s.repo.children(copy["uid"])
    assert [c["name"] for c in de2] == ["DE"] and [c["name"] for c in s.repo.children(de2[0]["uid"])] == ["Berlin"]
    assert kids(s, "DACH") == ["AT", "DE"]  # the original is untouched


def test_link_mirrors_the_selection_as_child_links(s):
    u = s.u
    from mdm.links import target_of
    sel = {"selected": [u["Regions"]], "left_out": [u["CEE"]], "target": u["Archive"]}
    below = [n for n in s.repo.subtree(u["Regions"]) if u["CEE"] not in n["path"] + [n["uid"]]]
    plan = s.manage_plan(ALICE, "link", sel)
    assert plan["link_count"] == 1 + len(below) and plan["links"][0]["name"] == "Regions"
    s.manage_apply(ALICE, "link", sel)
    top = s.repo.links_under([u["Archive"]])
    assert [target_of(l) for l in top] == [u["Regions"]]          # the selection is mirrored as links
    dach = s.repo.links_under([top[0]["uid"]])
    assert [target_of(l) for l in dach] == [u["DACH"]]             # CEE was left out
    assert [target_of(l) for l in s.repo.links_under([dach[0]["uid"]])] == [c["uid"] for c in s.repo.children(u["DACH"])]
    s.manage_apply(ALICE, "link", {"selected": [u["CEE"]], "target": u["Archive"]})
    assert len(s.repo.links_under([u["Archive"]])) == 2


def test_delete_moves_left_out_up(s):
    u = s.u
    out = s.manage_apply(ALICE, "delete", {"selected": [u["DACH"], u["CEE"]], "left_out": [u["DE"]]})
    assert out["deleted"] == 3 and out["kept"] == 2
    assert kids(s, "Regions") == ["DE"] and kids(s, "DE") == ["Berlin"]


def test_permissions_and_root(s):
    u = s.u
    nobody = User("x@example.com", {"nobody"})
    with pytest.raises(Forbidden):
        s.manage_apply(nobody, "delete", {"selected": [u["CEE"]]})
    with pytest.raises(RuleError, match="root"):
        s.manage_count(ADMIN, {"selected": [K.ROOT]})
    parents = s.manage_parents(ALICE, {"op": "move", "selected": [u["DACH"]]})
    by = {p["name"]: p["problem"] for p in parents}
    assert by["Regions"] == "already their parent" and by.get("DE", "inside").startswith("inside")


def test_clone_refuses_table_nodes(s):
    from test_core import KNA1
    plan = s.manage_plan(ALICE, "clone", {"selected": [KNA1], "target": s.u["Archive"]})
    assert "Table" in plan["problem"]


def test_prefetch_matches_tree(s):
    """The start-up prefetch returns the same children pages as the hierarchy, for the top levels."""
    r = s.prefetch(ADMIN, levels=5, per=40)
    root = r["pages"][K.ROOT]
    assert [i["uid"] for i in root["items"]] == [i["uid"] for i in s.tree(ADMIN, K.ROOT)["children"]]
    for uid, page in list(r["pages"].items())[:15]:
        t = s.tree(ADMIN, uid)
        assert [i["uid"] for i in page["items"]] == [i["uid"] for i in t["children"]]
        assert page["total"] == t["children_page"]["total"]
    assert max(len(p) for p in r["paths"].values()) <= 5
    nobody = User("x@example.com", {"nobody"})
    with pytest.raises(Forbidden):
        s.prefetch(nobody)


def test_search_below_a_node(s):
    u = s.u
    assert {h["name"] for h in s.search(ADMIN, "DE")} >= {"DE"}
    assert [h["name"] for h in s.search(ADMIN, "Berlin", under=u["DACH"])] == ["Berlin"]
    assert s.search(ADMIN, "Berlin", under=u["Archive"]) == []
    hit = s.search(ADMIN, "Berlin", under=u["Regions"])[0]
    assert hit["path_uids"][-3:] == [u["Regions"], u["DACH"], u["DE"]]


def test_search_hits_carry_their_path_nodes(s):
    u = s.u
    hit = s.search(ADMIN, "Berlin", under=u["Regions"])[0]
    assert [n["uid"] for n in hit["path_nodes"]] == hit["path_uids"]
    dach = next(n for n in hit["path_nodes"] if n["uid"] == u["DACH"])
    assert dach["name"] == "DACH" and dach["child_count"] == 2 and hit["child_count"] == 0
