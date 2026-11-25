"""Add / remove child links — endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.deps import me, svc

router = APIRouter()

@router.post("/api/nodes/{uid}/links")
def api_link(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().add_child_link(me(request), uid, body["link_uid"], body.get("link_type"),
                                body.get("position") or "last")


@router.delete("/api/nodes/{uid}/links/{link_uid}")
def api_unlink(uid: str, link_uid: str, request: Request):
    return svc().remove_child_link(me(request), uid, link_uid)
