"""Edit Children grid — endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.deps import me, svc

router = APIRouter()

@router.post("/api/nodes/{uid}/children-table/query")
def api_children_table_query(uid: str, request: Request, body: dict[str, Any] = Body(...),
                             as_group: str | None = None):
    return svc().children_table(me(request, as_group), uid, body.get("levels", 1), body.get("offset", 0), body.get("limit", 40),
                                body.get("q") or "", body.get("sort"), body.get("filters"))


@router.post("/api/nodes/{uid}/children-table/values")
def api_children_values(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().children_values(me(request), uid, body["column"], body.get("q") or "", body.get("limit", 40))


@router.get("/api/nodes/{uid}/children-table")
def api_children_table(uid: str, request: Request, levels: int = 1):
    return svc().children_table(me(request), uid, levels)


@router.post("/api/nodes/{uid}/children-table")
def api_edit_children(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().edit_children(me(request), uid, body.get("changes") or [], body.get("deletes") or [])
