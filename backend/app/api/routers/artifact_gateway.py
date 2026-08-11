from __future__ import annotations

from fastapi import APIRouter, Body, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import ArtifactVersion, FileRecord
from app.domain.schemas.artifacts import (
    ArtifactCardPreviewView,
    ArtifactCardView,
    ArtifactCreate,
    ArtifactShareTokenCreated,
    ArtifactShareTokenCreate,
    ArtifactShareTokenView,
    ArtifactSummaryView,
    ArtifactUpdate,
    ArtifactVersionCreate,
    ArtifactVersionUpdate,
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


@router.get("/artifacts", response_model=list[ArtifactSummaryView])
def list_artifacts(
    db: DB,
    context: CurrentWorkspace,
) -> list[ArtifactSummaryView]:
    context.require_permission("workspace.read")
    rows = service(db, context, None).list_artifact_summaries()
    return [
        ArtifactSummaryView.model_validate(
            {**artifact.__dict__, "version_count": version_count}
        )
        for artifact, version_count in rows
    ]


@router.get("/artifacts/cards", response_model=list[ArtifactCardView])
def list_artifact_cards(
    db: DB,
    context: CurrentWorkspace,
    status: str | None = None,
    card_type: str | None = None,
    interactive: bool | None = None,
    sort: str = "updated_at",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> list[ArtifactCardView]:
    """List indexed interactive HTML cards emitted in chat sessions."""
    context.require_permission("workspace.read")
    from app.services.artifact_cards import ArtifactCardService

    cards = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).list_cards(
        status=status,
        card_type=card_type,
        interactive=interactive,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return [ArtifactCardView.model_validate(card) for card in cards]


@router.get("/artifacts/cards/{card_id}/preview", response_model=ArtifactCardPreviewView)
def get_artifact_card_preview(
    card_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactCardPreviewView:
    """Full render data (preview_html / runtime / viewport) for one card."""
    context.require_permission("workspace.read")
    from app.services.artifact_cards import ArtifactCardService

    card = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).get_preview(card_id)
    return ArtifactCardPreviewView.model_validate(card)


@router.delete("/artifacts/cards/{card_id}", response_model=ArtifactCardView)
def delete_artifact_card(
    card_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactCardView:
    """Soft-delete a card so it disappears from the artifacts page."""
    context.require_permission("workspace.write")
    from app.services.artifact_cards import ArtifactCardService

    card = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).delete_card(card_id)
    return ArtifactCardView.model_validate(card)


@router.get(
    "/artifacts/{artifact_id}/versions",
    response_model=list[ArtifactVersionView],
)
def list_artifact_versions(
    artifact_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[ArtifactVersionView]:
    context.require_permission("workspace.read")
    return [
        ArtifactVersionView.model_validate(version)
        for version in service(db, context, None).list_versions(artifact_id)
    ]


@router.get(
    "/artifacts/versions/{version_id}/share-tokens",
    response_model=list[ArtifactShareTokenView],
)
def list_artifact_share_tokens(
    version_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[ArtifactShareTokenView]:
    context.require_permission("workspace.read")
    return [
        ArtifactShareTokenView.model_validate(token)
        for token in service(db, context, None).list_share_tokens(version_id)
    ]


@router.post("/artifacts", response_model=ArtifactView, status_code=status.HTTP_201_CREATED)
def create_artifact(
    payload: ArtifactCreate,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactView:
    context.require_permission("workspace.write")
    return service(db, context, None).create_artifact(payload.name, payload.description)


@router.patch("/artifacts/{artifact_id}", response_model=ArtifactView)
def update_artifact(
    artifact_id: str,
    payload: ArtifactUpdate,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactView:
    context.require_permission("workspace.write")
    return service(db, context, None).update_artifact(
        artifact_id,
        name=payload.name,
        description=payload.description,
    )


@router.delete("/artifacts/{artifact_id}", response_model=ArtifactView)
def delete_artifact(
    artifact_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactView:
    context.require_permission("workspace.write")
    return service(db, context, None).delete_artifact(artifact_id)


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


@router.patch("/artifacts/versions/{version_id}", response_model=ArtifactVersionView)
def update_artifact_version(
    version_id: str,
    payload: ArtifactVersionUpdate,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactVersionView:
    context.require_permission("workspace.write")
    return service(db, context, None).update_version(
        version_id,
        release_notes=payload.release_notes,
    )


@router.delete("/artifacts/versions/{version_id}", response_model=ArtifactVersionView)
def delete_artifact_version(
    version_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactVersionView:
    context.require_permission("workspace.write")
    return service(db, context, None).delete_version(version_id)


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
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="{version.original_name.replace(chr(34), "")}"',
        "Content-Length": str(version.size_bytes),
        "ETag": f'"sha256-{version.sha256}"',
    }
    return StreamingResponse(
        storage.iter_bytes(file.object_key, offset=0, length=version.size_bytes),
        media_type=version.mime_type,
        headers=headers,
    )
