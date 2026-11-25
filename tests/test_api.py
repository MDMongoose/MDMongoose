import os
import sys
from pathlib import Path

os.environ["MDM_LOCAL"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app")]

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from mdm.sqlrepo import node_to_row, row_to_node  # noqa: E402
from mdm.uid import to_uid  # noqa: E402

c = TestClient(app)
CUSTOMER, COUNTRIES = to_uid(2050), to_uid(2030)


def test_me_and_tree():
    me = c.get("/api/me").json()
    assert me["is_admin"] and me["root"] == "MDM-1"
    t = c.get(f"/api/tree/{COUNTRIES}").json()
    assert {x["name"] for x in t["children"]} >= {"Hungary", "Germany"}


def test_edit_add_and_errors():
    r = c.post(f"/api/nodes/{COUNTRIES}/children", json={"name": "Italy", "types": ["Country", "Ordered"]})
    uid = r.json()["uid"]
    n = c.get(f"/api/nodes/{uid}").json()
    r = c.patch(f"/api/nodes/{uid}", json={"changes": {"Alpha2": "IT", "Order": "7,5"}, "expected": n["modified_at"]})
    assert r.status_code == 200
    props = {p["name"]: p for p in r.json()["properties"]}
    assert props["Alpha2"]["value"] == "IT" and props["Order"]["value"] == 7.5
    assert c.patch(f"/api/nodes/{uid}", json={"changes": {"Max Child Depth": "x"}}).status_code == 400
    assert c.patch(f"/api/nodes/{uid}", json={"changes": {"Alpha2": "I"}, "expected": "old"}).status_code == 409
    assert c.post(f"/api/nodes/{COUNTRIES}/children", json={"name": "F", "types": ["Folder"]}).status_code == 400


def test_golden_run_and_export():
    node = c.get(f"/api/nodes/{CUSTOMER}").json()
    act = next(a for a in node["actions"] if a["ui"] == "refresh_golden")
    run = c.post(f"/api/nodes/{CUSTOMER}/actions/{act['uid']}/run").json()
    import time
    for _ in range(30):
        st = c.get(f"/api/runs/{run['run_id']}").json()
        if st["state"] != "RUNNING":
            break
        time.sleep(0.1)
    assert st["state"] == "SUCCESS", st
    x = c.get(f"/api/nodes/{CUSTOMER}/export?format=xlsx&depth=1&balanced=false")
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_sql_row_roundtrip():
    from mdm.seed import all_seed
    n = next(x for x in all_seed("c", "s", True) if x["uid"] == CUSTOMER)
    row = node_to_row(n)
    import json
    back = row_to_node({**row, "types_json": json.dumps(row["types"]), "path_json": json.dumps(row["path"]),
                        "child_links_json": json.dumps(row["child_links"]), "user_defined_json": "{}",
                        "allowed_json": json.dumps(row["allowed_child_types"]), "steward_json": "[]",
                        "modified_at": n["modified_at"], "modified_by": "system"})
    assert back["properties"] == n["properties"] and back["path"] == n["path"]
