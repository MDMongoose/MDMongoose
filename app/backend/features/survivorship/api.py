"""Survivorship rule editor — endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.deps import me, svc

router = APIRouter()

@router.get("/api/rules/{uid}")
def api_rule(uid: str, request: Request):
    return svc().rule_editor(me(request), uid)


@router.put("/api/rules/{uid}")
def api_rule_save(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().save_rule(me(request), uid, body)


@router.post("/api/rules/{uid}/preview")
def api_rule_preview(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().rule_preview(me(request), uid, body)


@router.post("/api/rules/{uid}/simulate")
def api_rule_simulate(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().rule_simulate(me(request), uid, body)
