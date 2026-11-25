"""Browse: move, clone, link, delete many subtrees — endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ...core.deps import me, svc

router = APIRouter()

@router.post("/api/manage/count")
def api_manage_count(request: Request, body: dict[str, Any] = Body(...)):
    """body: {selected: [uid], left_out: [uid]} -> nodes / child elements of the selection."""
    return svc().manage_count(me(request), body)


@router.post("/api/manage/parents")
def api_manage_parents(request: Request, body: dict[str, Any] = Body(...), q: str = ""):
    return svc().manage_parents(me(request), body, q)


@router.post("/api/manage/{op}/plan")
def api_manage_plan(op: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().manage_plan(me(request), op, body)


@router.post("/api/manage/{op}")
def api_manage_apply(op: str, request: Request, body: dict[str, Any] = Body(...)):
    """op: move | clone | link | delete; body: {selected, left_out, target?, name?}."""
    if op not in ("move", "clone", "link", "delete"):
        return JSONResponse({"error": "unknown action"}, status_code=404)
    return svc().manage_apply(me(request), op, body)
