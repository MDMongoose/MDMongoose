"""Configure Load of a Table node — endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from ...core.deps import me, svc

router = APIRouter()

@router.get("/api/tables/{uid}/load-config")
def api_load_config(uid: str, request: Request):
    return svc().load_config(me(request), uid)


@router.put("/api/tables/{uid}/load-config")
def api_load_config_save(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().save_load_config(me(request), uid, body)


@router.post("/api/tables/{uid}/preview")
def api_load_preview(uid: str, request: Request, body: dict[str, Any] = Body(...)):
    return svc().load_preview(me(request), uid, body)


@router.get("/api/source-catalog")
def api_source_catalog(request: Request, catalog: str = "", schema: str = ""):
    return svc().source_catalog(me(request), catalog, schema)


@router.get("/api/source-cdf")
def api_source_cdf(request: Request, table: str):
    return svc().source_cdf(me(request), table)
