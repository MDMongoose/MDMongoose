"""Refresh Golden Records — endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...core.deps import me, svc

router = APIRouter()

@router.get("/api/nodes/{uid}/golden/plan")
def api_golden_plan(uid: str, request: Request, threshold: float | None = None, retire: bool = True):
    return svc().golden_plan(me(request), uid, threshold, retire)
