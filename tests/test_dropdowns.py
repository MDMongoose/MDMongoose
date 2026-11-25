"""Dropdown backends: paged option and parent lists searched in the database; Edit Children on an empty node."""

import os
import sys
from pathlib import Path

os.environ["MDM_LOCAL"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app"), str(ROOT / "tests")]

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

c = TestClient(app)


def test_options_are_paged_and_searched():
    first = c.get("/api/options", params={"ptype": "Enum(LegalForms)", "limit": 5}).json()
    second = c.get("/api/options", params={"ptype": "Enum(LegalForms)", "offset": 5, "limit": 5}).json()
    assert len(first) == 5 and len(second) == 5 and not {o["uid"] for o in first} & {o["uid"] for o in second}
    hit = c.get("/api/options", params={"ptype": "Enum(LegalForms)", "q": first[0]["uid"]}).json()
    assert [o["uid"] for o in hit][:1] == [first[0]["uid"]]


def test_valid_parents_start_with_the_current_parent_and_page():
    u = c.post("/api/nodes/MDM-12W/children", json={"name": "Movable", "types": ["Folder"], "values": {}}).json()["uid"]
    page = c.get(f"/api/nodes/{u}/valid-parents", params={"limit": 3}).json()
    assert page[0]["uid"] == "MDM-12W" and page[0]["current"]
    rest = c.get(f"/api/nodes/{u}/valid-parents", params={"offset": 3, "limit": 3}).json()
    assert not {o["uid"] for o in page} & {o["uid"] for o in rest}
    found = c.get(f"/api/nodes/{u}/valid-parents", params={"q": "Master"}).json()
    assert any(o["name"] == "Master Data" for o in found)


def test_edit_children_of_an_empty_node_has_columns():
    u = c.post("/api/nodes/MDM-12W/children", json={"name": "Empty", "types": ["Folder"], "values": {}}).json()["uid"]
    t = c.get(f"/api/nodes/{u}/children-table").json()
    assert t["rows"] == [] and "Name" in [col["name"] for col in t["columns"]] and t["can_add"]
    act = next(a for a in c.get(f"/api/nodes/{u}").json()["actions"] if a["ui"] == "edit_children")
    assert act["enabled"]
