"""Add Child: create a child node, or a new NodeType — endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.deps import me, svc

router = APIRouter()

@router.post("/api/nodes/{uid}/children")
def api_add_child(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().add_child(me(request), uid, body.get("name", ""), body.get("types") or [], body.get("values") or {})


@router.post("/api/nodetypes")
def api_create_nodetype(request: Request, body: dict[str, Any] = Body(...)):
    return svc().create_node_type(me(request), body.get("parent_uid"), body.get("name", ""), body.get("default_icon", "box"),
                                  body.get("property_defs") or [], body.get("actions") or [], body.get("new_action"))
