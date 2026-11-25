"""Paging, server-side filters/sort, set-based subtree operations, path_key layout."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mdm.model import K, path_key  # noqa: E402
from mdm.permissions import User  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import all_seed  # noqa: E402
from backend.service import PAGE, MdmService  # noqa: E402
from mdm.uid import to_uid  # noqa: E402

ADMIN = User("admin@example.com", {"admins"}, is_admin=True)
REF, MASTER = to_uid(1400), to_uid(1401)


@pytest.fixture
def svc():
    s = MdmService(MemoryRepo(all_seed("workspace", "mdm_staging", demo=True)))
    s.edit_children(ADMIN, REF, [{"values": {"Name": "Bulk"}, "parent_uid": REF}])
    bulk = next(k for k in s.repo.children(REF, limit=1000) if k["name"] == "Bulk")
    s.edit_children(ADMIN, bulk["uid"], [{"values": {"Name": f"Item {i:03d}", "Icon": "flag" if i % 2 else "box"},
                                          "parent_uid": bulk["uid"]} for i in range(130)])
    s.bulk = bulk["uid"]
    return s


def test_path_key_stored(svc):
    for n in svc.repo.children(svc.bulk, limit=5):
        assert path_key(n) == "/" + "/".join(u[4:] for u in n["path"] + [n["uid"]]) + "/"


def test_tree_pages_children(svc):
    t = svc.tree(ADMIN, svc.bulk)
    assert len(t["children"]) == PAGE and t["children_page"]["total"] == 130 and t["children_page"]["has_more"]
    p = svc.children_page(ADMIN, svc.bulk, offset=120)
    assert len(p["items"]) == 10 and not p["has_more"]
    q = svc.children_page(ADMIN, svc.bulk, q="Item 12")
    assert q["total"] == 10 and all("Item 12" in i["name"] for i in q["items"])


def test_selected_node_pinned_in_siblings(svc):
    last = svc.children_page(ADMIN, svc.bulk, offset=129)["items"][0]
    t = svc.tree(ADMIN, last["uid"])
    assert any(s["uid"] == last["uid"] for s in t["siblings"]) and t["siblings_page"]["total"] == 130


def test_grid_server_filter_sort_and_values(svc):
    col = {"name": "Icon", "ptype": "Icon"}
    vals = svc.children_values(ADMIN, svc.bulk, col)
    assert {v["value"]: v["count"] for v in vals} == {"box": 65, "flag": 65}
    g = svc.children_table(ADMIN, svc.bulk, 1, 0, PAGE, "", {**col, "dir": -1, "name": "Name"}, [{**col, "values": ["flag"]}])
    assert g["total"] == 65 and len(g["rows"]) == PAGE and g["has_more"]
    names = [r["values"]["L1.Name"] for r in g["rows"]]
    assert names == sorted(names, reverse=True) and names[0] == "Item 129"
    assert svc.children_table(ADMIN, svc.bulk, 1, 40, PAGE)["next"] == 80


def test_soft_delete_and_move_subtree(svc):
    kid = svc.children_page(ADMIN, svc.bulk)["items"][0]["uid"]
    svc.add_child(ADMIN, kid, "Grandchild", [], {})
    svc.change_parent(ADMIN, svc.bulk, MASTER)
    moved = svc.repo.get(kid)
    assert moved["path"][-1] == svc.bulk and MASTER in moved["path"]
    gc = svc.repo.children(kid)[0]
    assert path_key(gc).startswith(path_key(moved))
    svc.delete(ADMIN, svc.bulk, "tree")
    assert svc.repo.get(kid) is None or svc.repo.get(kid).get("is_deleted")


def test_caching_executor_serves_repeats_and_clears_on_write():
    from mdm.sqlrepo import CachingExecutor, SqlExecutor

    class Fake(SqlExecutor):
        def __init__(self):
            self.calls, self.version = 0, 1

        def query(self, sql, params=None):
            if sql.startswith("DESCRIBE HISTORY"):
                return [{"version": self.version}]
            self.calls += 1
            return [{"n": self.calls}]

        def execute(self, sql, params=None):
            return {}

    f = Fake()
    c = CachingExecutor(f, "cat.mdm_core.mdmnodes", check_seconds=0)
    q = "SELECT * FROM cat.mdm_core.mdmnodes WHERE uid = :u"
    assert c.query(q, {"u": "A"}) == c.query(q, {"u": "A"}) and f.calls == 1   # second read from memory
    c.execute("UPDATE cat.mdm_core.mdmnodes SET x = 1")                          # own write clears
    c.query(q, {"u": "A"})
    assert f.calls == 2
    f.version = 2                                                                # a job wrote: new version
    c.query(q, {"u": "A"})
    assert f.calls == 3
    assert c.query_fresh(q, {"u": "A"}) and f.calls == 4                         # concurrency checks bypass it


def test_node_views_batch_equals_single_views(svc):
    kids = [k["uid"] for k in svc.repo.children(svc.bulk, limit=40)]
    seen = []
    orig = svc.repo.get_many
    svc.repo.get_many = lambda uids, include_deleted=False: (seen.append(len(uids)), orig(uids, include_deleted))[1]
    views = svc.node_views(ADMIN, kids + ["MDM-NOPE"])
    svc.repo.get_many = orig
    assert list(views) == kids
    for u in kids[:5]:
        assert views[u] == svc.node_view(ADMIN, u)
    assert len(seen) < 12   # a few batched reads, not several per node


def test_steward_editor_data_and_resolve_permissions(svc):
    from mdm.permissions import User
    from backend.service import Forbidden
    md = to_uid(1401)
    row = next(r for r in svc.node_view(ADMIN, md)["properties"] if r["name"] == "Steward")
    assert row["own"] == [] and row["above"] == [{"group": "users", "level": "Manager", "from": "MDM"}] and not row["grants_here"]
    uids = svc.resolve_permissions(ADMIN, [{"group": "finance-readers", "level": "Reader"}, {"group": "users", "level": "Manager"}])
    assert len(uids) == 2 and uids[1] == to_uid(60)                     # the existing one is reused
    assert svc.resolve_permissions(ADMIN, [{"group": "finance-readers", "level": "reader"}]) == uids[:1]
    with pytest.raises(Forbidden):
        svc.resolve_permissions(User("u@x", {"users"}), [{"group": "new-group", "level": "Reader"}])
    svc.edit(ADMIN, md, {"Steward": uids})
    row = next(r for r in svc.node_view(ADMIN, md)["properties"] if r["name"] == "Steward")
    assert [o["group"] for o in row["own"]] == ["finance-readers", "users"]


def test_caching_executor_checks_the_version_off_the_request_path():
    from mdm.sqlrepo import CachingExecutor, SqlExecutor

    class Fake(SqlExecutor):
        def __init__(self):
            self.history = 0

        def query(self, sql, params=None):
            if sql.startswith("DESCRIBE HISTORY"):
                self.history += 1
                return [{"version": 1}]
            return [{"n": 1}]

    f = Fake()
    c = CachingExecutor(f, "cat.mdm_core.mdmnodes", check_seconds=3600, background=True)
    q = "SELECT * FROM cat.mdm_core.mdmnodes WHERE uid = :u"
    c.query(q, {"u": "A"})            # first read after a quiet spell: checks once itself
    assert f.history == 1
    for _ in range(5):
        c.query(q, {"u": "B"})        # while in use: no check on the request path
    assert f.history == 1
