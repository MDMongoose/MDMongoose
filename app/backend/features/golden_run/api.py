"""Golden records run endpoints. A run is one background flow; GET /api/runs/{run_id} follows it."""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.config import LOCAL
from ...core.deps import me, runner, svc

router = APIRouter()
DONE = ("SUCCESS", "FAILED")


@router.get("/api/golden-run/{uid}")
def api_golden_run(uid: str, request: Request):
    """uid: a Master Data node, a Match Rule or a Survivorship Rule."""
    return svc().golden_run_view(me(request), uid)


@router.post("/api/golden-run/{uid}/run")
def api_golden_run_start(uid: str, request: Request, body: dict[str, Any] = Body(default={})):
    user = me(request)
    _, steps = svc().golden_run_steps(user, uid, body)
    return start_flow(steps, user)


def compile_steps(steps: list[dict]) -> list[dict]:
    """The jobs that carry out the steps, with as few compute start-ups as possible. Each item covers the indexes
    of the steps it does:
      - all loads are one mdm_load run (its For each task loads the Tables in parallel) and export nothing
        (the run refreshes the exports once, at its end);
      - Match and Survivorship run as one mdm_core run (matching, then the golden records, in one task);
      - the file and materialized-view exports run in the last job: its task writes the files, its
        export_pipeline task refreshes the views; View exports stay with the app (no job needed)."""
    plan: list[dict] = []
    jobs: list[dict] = []
    match = next((i for i, s in enumerate(steps) if s["key"] == "match"), None)
    surv = next((i for i, s in enumerate(steps) if s["key"] == "survivorship"), None)
    merge = (match is not None and surv is not None and not steps[match]["options"].get("preview")
             and steps[surv]["options"].get("retire", True) is not False)
    loads = [i for i, s in enumerate(steps) if s["key"] == "load"]
    for i, s in enumerate(steps):
        if s["key"] == "load" and i != loads[0]:
            continue
        if s["key"] == "load":   # one run for all the Tables
            uids = ",".join(steps[j]["uid"] for j in loads)
            label = s["label"] if len(loads) == 1 else f"Load {len(loads)} tables"
            item = {"step": dict(s, uid=uids, label=label, options={**s.get("options", {}), "exports": "none"}),
                    "covers": loads}
        elif s["key"] == "match" and merge:
            item = {"step": dict(s, label="Match + Survivorship", options={**s["options"], "golden": True}), "covers": [i, surv]}
        elif s["key"] == "survivorship" and merge:
            continue
        elif s["key"] == "export" and s.get("export_kind") != "view" and jobs:
            jobs[-1]["exports"].append(s["uid"])
            jobs[-1]["covers"].append(i)
            continue
        else:
            item = {"step": s, "covers": [i]}
        plan.append(item)
        if s["kind"] == "run" and s["key"] != "load":
            item["exports"] = []
            jobs.append(item)
    for j in jobs:
        j["step"] = dict(j["step"], options={**j["step"].get("options", {}), "exports": j.pop("exports") or "none"})
    return plan


def start_flow(steps: list[dict], user) -> dict:
    """Runs the steps one after the other in a background thread; the first failure stops the flow."""
    r = runner()
    fid = f"flow-{len(r.local_runs) + 1}"
    flow = {"run_id": fid, "state": "RUNNING", "message": "", "log": [],
            "steps": [{"key": s["key"], "label": s["label"], "state": "PENDING"} for s in steps]}
    r.local_runs[fid] = flow
    pause = 0.3 if LOCAL else 3.0

    def one(s: dict, view: dict, covered: list[dict] = ()) -> None:
        def upd(**kw) -> None:   # the job's state on every step it does, while it runs
            view.update(kw)
            for v in covered:
                v.update(kw)
        if s["kind"] == "export":
            out = svc().refresh_export(user, s["uid"])
        else:
            out = r.start(s["handler"], s["uid"], user.name, s.get("options"))
        rid = out.get("run_id")
        upd(run_id=rid, state=out.get("state") or "RUNNING", message=out.get("message"))
        while rid and view["state"] not in DONE:
            time.sleep(pause)
            st = r.status(rid)
            upd(state=st.get("state") or "UNKNOWN", message=st.get("message"), url=st.get("url"))
            if view["state"] in DONE:
                flow["log"] += st.get("log") or []
        if view["state"] not in DONE:
            view["state"] = "SUCCESS"

    def work() -> None:
        for item in compile_steps(steps):
            views = [flow["steps"][i] for i in item["covers"]]
            view = {"label": item["step"]["label"], "state": "RUNNING"}
            for v in views:
                v["state"] = "RUNNING"
            try:
                one(item["step"], view, views)
            except Exception as e:  # noqa: BLE001 - shown in the dialog
                view.update(state="FAILED", message=str(e))
            for v in views:   # one job can do several steps (Match + Survivorship, the exports)
                v.update({k: view.get(k) for k in ("state", "message", "url", "run_id")})
            if view["state"] == "FAILED":
                flow.update(state="FAILED", message=f"{view['label']} failed: {view.get('message') or ''}")
                for rest in flow["steps"]:
                    if rest["state"] == "PENDING":
                        rest["state"] = "SKIPPED"
                return
        flow.update(state="SUCCESS", message=f"{len(steps)} step(s) done")

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": fid, "state": "RUNNING", "steps": flow["steps"]}
