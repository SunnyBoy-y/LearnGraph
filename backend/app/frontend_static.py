"""Optional same-origin SPA serving for container and self-hosted deploys.

The Vite dev server owns the frontend origin during `npm run dev`. Production
images copy `frontend/dist` into the API process and set
``LEARNGRAPH_FRONTEND_DIST`` so the browser can keep calling ``/api/v1`` on
the same origin. API, OpenAPI, and docs routes stay first-class: this module
only answers paths the API router does not handle.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

_RESERVED_EXACT = frozenset({"docs", "redoc", "openapi.json"})
_RESERVED_PREFIXES = ("api/", "docs/", "redoc/")


class ImmutableAssetFiles(StaticFiles):
    """Long-lived cache only for content-addressed Vite resources."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        if response.status_code < 400 and self._is_hashed(path):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response

    @staticmethod
    def _is_hashed(path: str) -> bool:
        stem = Path(path).stem
        return len(stem.rsplit("-", 1)[-1]) >= 8


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


def web_client_fingerprint(index: Path) -> str:
    """Fingerprint the HTML entry; hashed resource names change with Web builds."""
    digest = hashlib.sha256(index.read_bytes()).hexdigest()
    return digest[:32]


def install_web_client_version(app: FastAPI, dist: Path | None) -> None:
    """Expose a conditional Web build identity without touching the API router."""
    if dist is None or not (dist / "index.html").is_file():
        return
    @app.api_route("/api/v1/web-client/version", methods=["GET", "HEAD"], include_in_schema=False)
    async def web_client_version(request: Request) -> Response:
        # Re-read the file: a deployment may atomically replace the static tree
        # without restarting this process.
        fingerprint = web_client_fingerprint(dist / "index.html")
        etag = f'"{fingerprint}"'
        # Declare the route deliberately, even when HEAD bypasses this handler.
        if request.method == "HEAD":
            return Response(status_code=200, headers={"Cache-Control": "no-cache", "ETag": etag})
        if etag in request.headers.get("if-none-match", ""):
            return Response(status_code=304, headers={"Cache-Control": "no-cache", "ETag": etag})
        return JSONResponse(
            {"version": fingerprint},
            headers={"Cache-Control": "no-cache", "ETag": etag},
        )


def install_frontend_static(app: FastAPI, dist: Path | None) -> None:
    """Mount hashed assets and a SPA fallback when a production build is present."""

    if dist is None:
        return
    if not dist.is_dir() or not (dist / "index.html").is_file():
        logger.warning("LEARNGRAPH_FRONTEND_DIST is set but %s is not a Vite build", dist)
        return

    index = dist / "index.html"
    fingerprint = web_client_fingerprint(index)
    shell_headers = {"Cache-Control": "no-cache", "ETag": f'"{fingerprint}"'}

    if (dist / "assets").is_dir():
        # Vite content-hashed files never change under the same URL. A new build
        # emits new names, while index.html above stays revalidated.
        app.mount(
            "/assets",
            ImmutableAssetFiles(directory=dist / "assets"),
            name="frontend-assets",
        )

    @app.get("/", include_in_schema=False)
    async def frontend_index() -> FileResponse:
        return FileResponse(index, headers=shell_headers)

    install_web_client_version(app, dist)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend_spa(full_path: str) -> FileResponse:
        if _is_reserved_api_path(full_path):
            raise HTTPException(status_code=404, detail="Not Found")
        existing = _safe_dist_file(dist, full_path)
        if existing is not None:
            if existing == index:
                return FileResponse(existing, headers=shell_headers)
            return FileResponse(existing)
        return FileResponse(index, headers=shell_headers)

    logger.info("Serving frontend SPA from %s", dist)
