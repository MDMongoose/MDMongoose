"""Builds the FastAPI app: error mapping, the core and feature routers, and the built UI."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mdm.hierarchy import RuleError
from mdm.repo import ConflictError
from mdm.sqlrepo import STATS

from .. import features
from .routes import router as core_router
from .service import Forbidden, NotFound

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
SLOW_MS = float(os.getenv("MDM_SLOW_MS", "1500"))
log = logging.getLogger("mdm.timing")
ERRORS = {RuleError: 400, Forbidden: 403, NotFound: 404, ConflictError: 409}


def create_app() -> FastAPI:
    app = FastAPI(title="MDMongoose", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def _timing(request: Request, call_next):
        """Server-Timing on every API response (browser dev tools → Network → Timing): the total time, the SQL
        time and the number of statements. Requests slower than MDM_SLOW_MS (default 1500) are logged with
        their slowest statements."""
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        stats: list = []
        token = STATS.set(stats)
        t = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            STATS.reset(token)
        total = (time.perf_counter() - t) * 1000
        sql = sum(ms for _, ms in stats)
        response.headers["Server-Timing"] = (f'total;dur={total:.0f}, sql;dur={sql:.0f};desc="{len(stats)} statements"')
        if total > SLOW_MS:
            top = sorted(stats, key=lambda x: -x[1])[:5]
            log.warning("slow %s %s: %.0f ms, %d statements (%.0f ms SQL); slowest: %s", request.method,
                        request.url.path, total, len(stats), sql, " | ".join(f"{ms:.0f} ms {q}" for q, ms in top))
        return response
    for exc, status in ERRORS.items():
        app.add_exception_handler(exc, lambda _r, e, status=status: JSONResponse({"error": str(e)}, status_code=status))
    app.include_router(core_router)

    @app.on_event("startup")
    def _warm_start() -> None:   # the top of the hierarchy is read once at start (core/warm.py)
        from . import warm
        from .deps import svc
        warm.start(svc)

    for router in features.routers():
        app.include_router(router)
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        index = DIST / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"error": "frontend not built — run scripts/build.sh"}, status_code=503)

    return app
