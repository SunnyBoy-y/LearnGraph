"""LearnGraph independent subapp preview origin.

Serves immutable multi-file teaching bundles through a capability-gated
opaque-origin iframe on its own port, so the preview origin is distinct from
the main API origin. This satisfies the ``script-src 'self'`` invariant in
``doc/LearnGraph_交互式子应用_双向状态通道设计_v1.0.md``: CSP ``'self'`` here
can only ever load resources on this preview origin, never main-API scripts.

Run it as a separate process (``scripts/dev.mjs`` starts it automatically):

    uv run python -m uvicorn app.preview:preview_app --host 127.0.0.1 --port 8001

The preview process reuses the shared database and object storage. It does not
carry user sessions: each resource is authorized by its short-lived capability
token (``capability 即授权``), and responses carry a host-owned CSP that keeps
the previewed application fully offline and unable to reach LearnGraph APIs.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, FastAPI, Path
from fastapi.responses import StreamingResponse

from app.api.deps import AppSettings, DB
from app.core.database import init_database
from app.core.errors import install_error_handlers
from app.providers.storage_factory import object_storage_provider
from app.services.subapp_bundles import SubAppBundleService

router = APIRouter(prefix="/api/v1/subapps", tags=["subapp-preview"])

health_router = APIRouter(prefix="/api/v1", tags=["subapp-preview"])


@health_router.get("/livez")
async def livez() -> dict[str, str]:
    """Process/event-loop liveness probe used by the development supervisor."""
    return {"status": "ok"}


@health_router.get("/health")
def health() -> dict[str, str]:
    """Readiness endpoint retained for diagnostics and compatibility."""
    return {"status": "ok"}


@router.get("/preview/{raw_token}/{bundle_id}/{path:path}")
def serve_bundle_preview(
    raw_token: str,
    bundle_id: str,
    path: str,
    db: DB,
    settings: AppSettings,
):
    """Serve one immutable bundle resource to a capability-authorized viewer.

    The capability token is the complete authorization; the request is validated
    against the immutable manifest and no host/blob/filesystem path is exposed.
    """
    service = SubAppBundleService(db, "", "", settings)
    bundle, item, blob = service.resolve_preview(raw_token, bundle_id, path)
    storage = object_storage_provider(db, bundle.workspace_id, settings)
    headers = {
        "Cache-Control": "private, no-store",
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Content-Security-Policy": (
            "default-src 'none'; base-uri 'none'; connect-src 'none'; form-action 'none'; "
            "frame-src 'none'; object-src 'none'; manifest-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
            "media-src 'self' data: blob:; worker-src blob:"
        ),
    }
    return StreamingResponse(
        storage.iter_bytes(blob.object_key, offset=0, length=item.size_bytes),
        media_type=item.mime_type,
        headers=headers,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The preview process owns its own database initialization (idempotent
    # create_all), so it can be started independently of the main API.
    init_database()
    yield


preview_app = FastAPI(title="LearnGraph subapp preview", lifespan=lifespan)
install_error_handlers(preview_app)
preview_app.include_router(router)
preview_app.include_router(health_router)
