from __future__ import annotations

from fastapi import APIRouter, Body, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import ArtifactVersion, FileRecord
from app.domain.schemas.artifacts import (
    ArtifactCreate,
    ArtifactShareTokenCreated,
    ArtifactShareTokenCreate,
    ArtifactShareTokenView,
    ArtifactVersionCreate,
    ArtifactVersionView,
    ArtifactView,
)
from app.providers.storage_factory import object_storage_provider
from app.services.artifact_gateway import ArtifactGatewayService

router = APIRouter(tags=["artifacts"])


def service(db, context, settings):
    return ArtifactGatewayService(
        db,
        context.workspace_id,
        context.principal.user_id,
        context.principal.tenant_id,
    )


@router.post("/artifacts", response_model=ArtifactView, status_code=status.HTTP_201_CREATED)
def create_artifact(
    payload: ArtifactCreate,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactView:
    context.require_permission("workspace.write")
    return service(db, context, None).create_artifact(payload.name, payload.description)


@router.post(
    "/artifacts/{artifact_id}/versions",
    response_model=ArtifactVersionView,
    status_code=status.HTTP_201_CREATED,
)
def publish_artifact_version(
    artifact_id: str,
    payload: ArtifactVersionCreate,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactVersionView:
    context.require_permission("workspace.write")
    return service(db, context, None).publish_version(
        artifact_id,
        payload.file_id,
        source_chat_session_id=payload.source_chat_session_id,
        release_notes=payload.release_notes,
    )


@router.post(
    "/artifacts/versions/{version_id}/share-tokens",
    response_model=ArtifactShareTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_artifact_share_token(
    version_id: str,
    payload: ArtifactShareTokenCreate,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactShareTokenCreated:
    context.require_permission("workspace.write")
    raw, token = service(db, context, None).create_share_token(
        version_id,
        label=payload.label,
        expires_at=payload.expires_at,
        max_downloads=payload.max_downloads,
    )
    return ArtifactShareTokenCreated.model_validate({**token.__dict__, "token": raw})


@router.delete("/artifacts/share-tokens/{token_id}", response_model=ArtifactShareTokenView)
def revoke_artifact_share_token(
    token_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactShareTokenView:
    context.require_permission("workspace.write")
    return service(db, context, None).revoke_share_token(token_id)


@router.get("/artifact-share/{raw_token}")
def download_shared_artifact(raw_token: str, db: DB, settings: AppSettings):
    """Public read-only share endpoint. The token is the complete authorization."""
    gateway = ArtifactGatewayService(db, "", "", "")
    version = gateway.resolve_share_token(raw_token)
    file = db.scalar(select(FileRecord).where(FileRecord.id == version.file_id))
    if file is None or file.sha256 != version.sha256:
        raise AppError(404, "artifact_share_not_found", "Artifact share was not found")
    storage = object_storage_provider(db, version.source_workspace_id, settings)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, immutable, max-age=31536000",
        "Content-Disposition": f'attachment; filename="{version.original_name.replace(chr(34), "")}"',
        "Content-Length": str(version.size_bytes),
        "ETag": f'"sha256-{version.sha256}"',
    }
    return StreamingResponse(
        storage.iter_bytes(file.object_key, offset=0, length=version.size_bytes),
        media_type=version.mime_type,
        headers=headers,
    )
