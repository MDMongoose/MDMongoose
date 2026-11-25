"""Golden record setup and Golden records run: one dialog per flow, opened from the Master Data node or either rule."""

import os
import sys
import time
from pathlib import Path

import pytest

os.environ["MDM_LOCAL"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "app"), str(ROOT / "tests")]

from backend.service import Forbidden, MdmService  # noqa: E402
from mdm.hierarchy import RuleError  # noqa: E402
from mdm.model import K, new_node  # noqa: E402
from mdm.repo import MemoryRepo  # noqa: E402
from mdm.seed import all_seed  # noqa: E402
from mdm.uid import to_uid  # noqa: E402
from mdm.permissions import User  # noqa: E402
from test_core import ADMIN, CUSTOMER, MATCH_RULE, load_demo  # noqa: E402

SURV_RULE = to_uid(2040)
READER_BOB = User("bob@example.com", {"readers"})


@pytest.fixture
def svc():
    s = MdmService(MemoryRepo(all_seed("workspace", "mdm_staging", demo=True)))
    load_demo(s)
    return s


def test_setup_is_the_same_from_every_entry_point(svc):
    out = {u: svc.golden_setup(ADMIN, u) for u in (CUSTOMER, MATCH_RULE, SURV_RULE)}
    assert [out[u]["opened_from"] for u in (CUSTOMER, MATCH_RULE, SURV_RULE)] == ["master", "match", "survivorship"]
    for o in out.values():
        assert o["master"]["uid"] == CUSTOMER and o["match"]["uid"] == MATCH_RULE and o["survivorship"]["uid"] == SURV_RULE
    o = out[CUSTOMER]
    assert {r["property"] for r in o["survivorship"]["rules"]} >= {"Legal Name", "VAT ID", "Country"}
    assert "Legal Name" in [p["name"] for p in o["type_props"][o["survivorship"]["golden_type"]["uid"]]]
    assert all(t["columns"] for t in o["tables"])


def test_unassigned_rule_and_assign(svc):
    rules = svc.repo.get(K.MODEL_RULES)
    num = svc.repo.allocate(1)[0]
    draft = new_node(num, "Draft match", [K.NT_MATCH_RULE], rules, properties={"Sources": []})
    svc.repo.upsert([draft], "t")
    o = svc.golden_setup(ADMIN, draft["uid"])
    assert o["master"] is None and o["match"]["uid"] == draft["uid"] and o["survivorship"] is None and o["can_assign"]
    assert svc.golden_run_view(ADMIN, draft["uid"])["master"] is None
    with pytest.raises(RuleError):
        svc.golden_run_steps(ADMIN, draft["uid"], {"until": "match"})
    with pytest.raises(Forbidden):
        svc.golden_assign(READER_BOB, draft["uid"], CUSTOMER)
    o = svc.golden_assign(ADMIN, draft["uid"], CUSTOMER)
    assert o["master"]["uid"] == CUSTOMER and o["match"]["uid"] == draft["uid"]
    assert svc.repo.get(CUSTOMER)["properties"]["Match Rule"] == draft["uid"]


def test_run_steps_until_and_preview(svc):
    keys = lambda body: [s["key"] for s in svc.golden_run_steps(ADMIN, CUSTOMER, body)[1]]  # noqa: E731
    assert keys({"until": "match"}) == ["load", "load", "load", "match"]
    assert keys({"until": "export"})[-2:] == ["survivorship", "export"]
    assert keys({"until": "export", "preview": True}) == ["match"]
    with pytest.raises(RuleError):
        keys({"until": "load", "preview": True})
    one = svc.golden_run_steps(ADMIN, MATCH_RULE, {"until": "match", "tables": [to_uid(2021)]})[1]
    assert [s["label"] for s in one] == ["Load kna1", "Match"] and one[1]["options"]["golden"] is False
    with pytest.raises(Forbidden):  # a reader may not run matching
        svc.golden_run_steps(READER_BOB, CUSTOMER, {"until": "match", "tables": [to_uid(2021)]})
    # one step only
    assert keys({"only": "match"}) == ["match"]
    assert keys({"only": "survivorship"}) == ["survivorship"]
    assert set(keys({"only": "load"})) == {"load"}
    assert keys({"only": "match", "preview": True}) == ["match"]
    with pytest.raises(RuleError):
        keys({"only": "survivorship", "preview": True})
    v = svc.golden_run_view(ADMIN, SURV_RULE)
    assert v["opened_from"] == "survivorship" and v["survivorship"]["rules"] == 6 and v["exports"]
    assert len(v["tables"]) == 3 and v["tables"][0]["records"] > 0


def test_run_endpoint_runs_the_chain():
    from fastapi.testclient import TestClient

    from backend.main import app
    c = TestClient(app)
    r = c.post(f"/api/golden-run/{CUSTOMER}/run", json={"until": "survivorship", "mode": "full"})
    assert r.status_code == 200, r.text
    for _ in range(200):
        st = c.get(f"/api/runs/{r.json()['run_id']}").json()
        if st["state"] in ("SUCCESS", "FAILED"):
            break
        time.sleep(0.1)
    assert st["state"] == "SUCCESS", st
    assert [s["key"] for s in st["steps"]][-2:] == ["match", "survivorship"]
    assert any(x["activity"] == "match" for x in st["log"])
    assert c.get(f"/api/golden-setup/{MATCH_RULE}").json()["master"]["uid"] == CUSTOMER


def test_train_is_refused_unless_the_rule_is_splink():
    from fastapi.testclient import TestClient

    from backend.main import app
    c = TestClient(app)
    act = next(a for a in c.get(f"/api/nodes/{MATCH_RULE}").json()["actions"] if a["ui"] == "train_matching")
    r = c.post(f"/api/nodes/{MATCH_RULE}/actions/{act['uid']}/run", json={"options": {}})
    assert r.status_code == 400 and "Splink" in r.json()["error"]


def test_run_is_compiled_into_few_jobs(svc):
    from backend.features.golden_run.api import compile_steps
    _, steps = svc.golden_run_steps(ADMIN, CUSTOMER, {"until": "export"})
    plan = compile_steps(steps)
    keys = [p["step"]["key"] for p in plan]
    # the three Tables are one mdm_load run (its For each task loads them in parallel)
    load = [p for p in plan if p["step"]["key"] == "load"]
    tables = [s["uid"] for s in steps if s["key"] == "load"]
    assert len(load) == 1 and len(tables) == 3 and load[0]["step"]["uid"] == ",".join(tables)
    assert load[0]["covers"] == [i for i, s in enumerate(steps) if s["key"] == "load"]
    assert load[0]["step"]["label"] == "Load 3 tables" and load[0]["step"]["options"]["exports"] == "none"
    job = next(p for p in plan if p["step"]["key"] == "match")   # Match + Survivorship in one mdm_core run
    assert job["step"]["options"]["golden"] is True and "survivorship" not in keys
    file_exports = [s["uid"] for s in steps if s["key"] == "export" and s.get("export_kind") != "view"]
    assert job["step"]["options"]["exports"] == (file_exports or "none")   # files + views in the same run
    assert sorted(i for p in plan for i in p["covers"]) == list(range(len(steps)))   # every step is done once
    prev = compile_steps(svc.golden_run_steps(ADMIN, CUSTOMER, {"until": "export", "preview": True})[1])
    assert [p["step"]["options"]["exports"] for p in prev] == ["none"]


def test_unchanged_golden_records_are_not_rewritten(svc):
    from mdm import golden
    golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    seen = []
    real = svc.repo.upsert
    svc.repo.upsert = lambda nodes, user, expected=None: (seen.extend(nodes), real(nodes, user, expected))
    golden.refresh(svc.repo, svc.catalog, CUSTOMER)
    svc.repo.upsert = real
    assert [n for n in seen if n["parent_uid"] == CUSTOMER] == []


def test_load_steps_tables_and_queueing(svc):
    from mdm import load_steps
    repo = svc.repo
    tables = [t["uid"] for t in load_steps.resolve_tables(repo, "all")]
    assert len(tables) >= 3
    two = load_steps.resolve_tables(repo, f"{tables[0].lower()}, {tables[1]}")
    assert [t["uid"] for t in two] == tables[:2]
    assert load_steps.resolve_tables(repo, "none") == []
    with pytest.raises(SystemExit):
        load_steps.resolve_tables(repo, CUSTOMER)   # a Master Data node is not a Table
    # a run waits only for an earlier run that may load one of its Tables
    assert load_steps.overlaps([tables[0]], "all") and load_steps.overlaps([tables[0]], "due")
    assert load_steps.overlaps([tables[0]], f"{tables[1]},{tables[0]}")
    assert not load_steps.overlaps([tables[0]], tables[1])

    class Param:
        def __init__(self, name, value):
            self.name, self.value, self.default = name, value, None

    class Run:
        def __init__(self, run_id, table_uid):
            self.run_id, self.job_parameters = run_id, [Param("table_uid", table_uid)]

    class Jobs:
        def __init__(self, waves):
            self.waves, self.calls = waves, 0

        def list_runs(self, job_id, active_only):
            self.calls += 1
            return self.waves[min(self.calls, len(self.waves)) - 1]

    class Client:
        def __init__(self, waves):
            self.jobs = Jobs(waves)

    other = Client([[Run(5, tables[1]), Run(20, tables[0])]])   # a different Table, and a later run
    load_steps.wait_for_same_tables("1", "10", [tables[0]], poll=0, client=other)
    assert other.jobs.calls == 1   # nothing to wait for
    same = Client([[Run(5, tables[0])], [Run(5, tables[0])], []])   # an earlier run on the same Table, then done
    load_steps.wait_for_same_tables("1", "10", [tables[0]], poll=0, client=same)
    assert same.jobs.calls == 3
