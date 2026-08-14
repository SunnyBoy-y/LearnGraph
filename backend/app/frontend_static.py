"""Optional same-origin SPA serving for container and self-hosted deploys.

The Vite dev server owns the frontend origin during `npm run dev`. Production
images copy `frontend/dist` into the API process and set
``LEARNGRAPH_FRONTEND_DIST`` so the browser can keep calling ``/api/v1`` on
the same origin. API, OpenAPI, and docs routes stay first-class: this module
only answers paths the API router does not handle.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

_RESERVED_EXACT = frozenset({"docs", "redoc", "openapi.json"})
_RESERVED_PREFIXES = ("api/", "docs/", "redoc/")


def _is_reserved_api_path(relative: str) -> bool:
    normalized = relative.lstrip("/")
    return normalized in _RESERVED_EXACT or normalized.startswith(_RESERVED_PREFIXES)


def _safe_dist_file(dist: Path, relative: str) -> Path | None:
    if not relative or relative.endswith("/"):
        return None
    candidate = (dist / relative).resolve()
    try:
        candidate.relative_to(dist)
    except ValueError:
        return None
    if not candidate.is_file() or candidate.name.startswith("."):
        return None
    return candidate


def install_frontend_static(app: FastAPI, dist: Path | None) -> None:
    """Mount hashed assets and a SPA fallback when a production build is present."""

    if dist is None:
        return
    if not dist.is_dir() or not (dist / "index.html").is_file():
        logger.warning("LEARNGRAPH_FRONTEND_DIST is set but %s is not a Vite build", dist)
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    index = dist / "index.html"

    @app.get("/", include_in_schema=False)
    async def frontend_index() -> FileResponse:
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend_spa(full_path: str) -> FileResponse:
        if _is_reserved_api_path(full_path):
            raise HTTPException(status_code=404, detail="Not Found")
        existing = _safe_dist_file(dist, full_path)
        if existing is not None:
            return FileResponse(existing)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    logger.info("Serving frontend SPA from %s", dist)
