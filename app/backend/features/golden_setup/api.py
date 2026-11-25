"""Golden record setup endpoints. Saving goes through the Match Rule and Survivorship Rule endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.deps import me, svc

router = APIRouter()


@router.get("/api/golden-setup/{uid}")
def api_golden_setup(uid: str, request: Request):
    """uid: a Master Data node, a Match Rule or a Survivorship Rule."""
    return svc().golden_setup(me(request), uid)


@router.post("/api/golden-setup/{uid}/assign")
def api_golden_assign(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().golden_assign(me(request), uid, body.get("master") or "")


@router.post("/api/golden-setup/{uid}/processing")
def api_golden_processing(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    """body.mode: one_machine | distributed — the Master Data node's Processing."""
    return svc().golden_processing(me(request), uid, body.get("mode") or "")
