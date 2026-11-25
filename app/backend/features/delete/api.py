"""Delete a node or all children of a node — endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...core.deps import me, svc

router = APIRouter()

@router.get("/api/nodes/{uid}/delete-preview")
def api_delete_preview(uid: str, request: Request):
    s = svc()
    view = s.node_view(me(request), uid)
    st = s.repo.subtree_stats(s.repo.get(uid))
    return {"uid": uid, "name": view["name"], "descendants": st["count"], "locked": st["locked"], "loaded": st["loaded"]}


@router.delete("/api/nodes/{uid}")
def api_delete(uid: str, request: Request, mode: str = "tree"):
    return svc().delete(me(request), uid, mode)


@router.post("/api/nodes/{uid}/delete-children")
def api_delete_children(uid: str, request: Request):
    return svc().delete_all_children(me(request), uid)
