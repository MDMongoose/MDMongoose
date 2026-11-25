"""Endpoints of the main screen: hierarchy, node views, editing, search, background runs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from mdm.model import K

from .config import ENVIRONMENT, LOCAL, TABLE
from .deps import me, runner, svc

router = APIRouter()

@router.get("/api/me")
def api_me(request: Request):
    u = me(request)
    return {"name": u.name, "groups": sorted(u.groups), "is_admin": u.is_admin, "root": K.ROOT,
            "local": LOCAL, "table": TABLE, "environment": ENVIRONMENT}


@router.get("/api/prefetch")
def api_prefetch(request: Request, levels: int = 5, per: int = 40, as_group: str | None = None):
    """First 40 children of every node in the top 5 levels, for the UI cache at start."""
    return svc().prefetch(me(request, as_group), max(1, min(levels, 6)), max(1, min(per, 100)))


@router.get("/api/tree/{uid}")
def api_tree(uid: str, request: Request, as_group: str | None = None, k: str | None = None):
    return svc().tree(me(request, as_group), uid, key=k)


@router.get("/api/nodes/{uid}")
def api_node(uid: str, request: Request, as_group: str | None = None, k: str | None = None):
    return svc().node_view(me(request, as_group), uid, key=k)


@router.get("/api/open/{uid}")
def api_open(uid: str, request: Request, as_group: str | None = None, k: str | None = None):
    """One click in the UI: the hierarchy around the node and its property page together.
    k: the node's path_key, when the UI knows it (saves the lookup of the node's location)."""
    return svc().open(me(request, as_group), uid, key=k)


@router.get("/api/search")
def api_search(q: str, request: Request, kind: str = "", under: str = "", limit: int = 50):
    """Name / UID search; `under`: only below that node (the hierarchy's selected node)."""
    return svc().search(me(request), q, kind, max(1, min(limit, 200)), under)


@router.get("/api/meta")
def api_meta(request: Request):
    return svc().meta(me(request))


@router.get("/api/options")
def api_options(ptype: str, request: Request, q: str = "", offset: int = 0, limit: int = 200):
    return svc().options(me(request), ptype, q, offset, limit)


@router.post("/api/node-views")
def api_node_views(request: Request, body: dict[str, Any] = Body(...), as_group: str | None = None):
    """Views of many nodes at once — the UI preloads the children of the opened node."""
    return svc().node_views(me(request, as_group), list(body.get("uids") or []))


@router.get("/api/nodes/{uid}/children")
def api_children_page(uid: str, request: Request, offset: int = 0, limit: int = 40, q: str = "",
                      as_group: str | None = None):
    return svc().children_page(me(request, as_group), uid, offset, limit, q)


@router.patch("/api/nodes/{uid}")
def api_edit(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().edit(me(request), uid, body.get("changes") or {}, body.get("expected"), body.get("user_defined"))


@router.post("/api/nodes/{uid}/lock")
def api_lock(uid: str, request: Request):
    return svc().toggle_lock(me(request), uid)


@router.post("/api/nodes/{uid}/move")
def api_move(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().change_parent(me(request), uid, body["new_parent"])


@router.post("/api/nodes/{uid}/actions/{action_uid}/run")
def api_run_action(uid: str, action_uid: str, request: Request, body: dict[str, Any] = Body(default={})):
    u = me(request)
    _, action = svc().check_action(u, uid, action_uid)
    if action["ui"] == "run_export":  # views: created by the app right away; the rest: mdm_export job
        out = svc().refresh_export(u, uid)
        if out.get("run_id"):
            return out
        rid = f"local-{len(runner().local_runs) + 1}"
        runner().local_runs[rid] = {"run_id": rid, "state": out["state"], "message": out["message"]}
        return {"run_id": rid, "state": out["state"]}
    if action["ui"] == "train_matching":  # fail here, not minutes later in the job
        from mdm.matching.spec import load_spec
        node = svc().repo.get(uid) or {}
        rule = (node.get("properties") or {}).get("Match Rule") if K.NT_MASTER_DATA in node.get("types", []) else uid
        if load_spec(svc().repo, rule).method != "splink":
            return JSONResponse({"error": "only Exact keys + Splink has a model to train — save the rule with that "
                                          "strategy first"}, status_code=400)
    if action["mode"] not in ("job", "pipeline"):
        return JSONResponse({"error": "this action runs in the UI"}, status_code=400)
    return runner().start(action["handler"], uid, u.name, (body or {}).get("options"))


@router.get("/api/nodes/{uid}/valid-parents")
def api_valid_parents(uid: str, request: Request, q: str = "", offset: int = 0, limit: int = 100):
    return svc().valid_parents(me(request), uid, q, offset, limit)


@router.put("/api/nodetypes/{uid}")
def api_save_nodetype(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().save_node_type(me(request), uid, body.get("property_defs") or [], body.get("actions"),
                                body.get("default_icon"), body.get("expected"))


@router.get("/api/log")
def api_log(request: Request, uid: str = "", activity: str = "", limit: int = 50):
    """mdm_log rows (all jobs). Admins see every row; others the rows of nodes they can read."""
    from mdm import runlog
    u = me(request)
    if not uid and not u.is_admin:
        return JSONResponse({"error": "pass uid (only admins list the whole log)"}, status_code=403)
    if uid:
        svc().node_view(u, uid)  # raises when the user cannot read the node
    return runlog.sink_for(svc().repo).read(node_uid=uid or None, activity=activity or None, limit=min(limit, 500))


@router.post("/api/permissions/resolve")
def api_resolve_permissions(request: Request, body: dict[str, Any] = Body(...)):
    """[{group, level}] → Permission node uids (creates missing ones; admins only)."""
    return {"uids": svc().resolve_permissions(me(request), body.get("entries") or [])}


@router.get("/api/runs/{run_id}")
def api_run(run_id: str):
    return runner().status(run_id)


@router.get("/api/health")
def health():
    return {"ok": True}
