"""Export a branch as CSV or Excel — endpoints."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...core.deps import me, svc

router = APIRouter()

def _export_options(request: Request) -> dict:
    q = request.query_params
    b = lambda k, d: q.get(k, str(d)).lower() == "true"  # noqa: E731
    return {"with_props": b("with_props", True), "with_uid": b("with_uid", True),
            "mark_balanced": b("mark_balanced", False), "file_name": q.get("file_name", ""),
            "delimiter": {"comma": ",", "semicolon": ";", "tab": "\t"}.get(q.get("delimiter", "comma"), ","),
            "bom": q.get("encoding", "utf8bom") == "utf8bom", "sheets": q.get("sheets", "one"),
            "header": q.get("header", "styled")}


@router.get("/api/nodes/{uid}/export/preview")
def api_export_preview(uid: str, request: Request, format: str = "xlsx", depth: int = 1, balanced: bool = False):
    return svc().export_preview(me(request), uid, {"format": format, "depth": depth, "balanced": balanced,
                                                   **_export_options(request)})


@router.get("/api/nodes/{uid}/export")
def api_export(uid: str, request: Request, format: str = "csv", depth: int = 1, balanced: bool = False):
    name, data, media = svc().export(me(request), uid, format, depth, balanced, _export_options(request))
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
