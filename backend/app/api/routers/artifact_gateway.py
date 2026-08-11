from __future__ import annotations

import html

from fastapi import APIRouter, Body, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import ArtifactVersion, FileRecord
from app.domain.schemas.artifacts import (
    ArtifactCardPreviewView,
    ArtifactCardPublish,
    ArtifactCardShareTokenCreate,
    ArtifactCardShareTokenCreated,
    ArtifactCardShareTokenView,
    ArtifactCardVersionView,
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


# Public viewer shell for shared card versions. The card preview runs inside a
# sandboxed iframe (scripts allowed, no same-origin, no network escape).
_CARD_SHARE_SHELL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>共享卡片 · LearnGraph</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f4f4f5; color: #18181b; }}
  .frame {{ max-width: 860px; margin: 24px auto; padding: 0 16px; }}
  .head {{ display: flex; align-items: baseline; justify-content: space-between;
    gap: 12px; padding: 10px 4px; }}
  .head strong {{ font-size: 15px; }}
  .head span {{ font-size: 12px; color: #71717a; }}
  iframe {{ width: 100%; height: 72vh; border: 1px solid #e4e4e7; border-radius: 12px;
    background: #fff; display: block; }}
  .info {{ background: #fff; border: 1px solid #e4e4e7; border-radius: 12px;
    padding: 24px; text-align: center; color: #52525b; }}
  .info p {{ margin: 6px 0; }}
  footer {{ text-align: center; color: #a1a1aa; font-size: 12px; padding: 16px; }}
</style>
</head>
<body>
{body}
<footer>由 LearnGraph 生成 · 只读分享 · 不包含原始会话内容</footer>
</body>
</html>"""


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


@router.post(
    "/artifacts/cards/{card_id}/versions",
    response_model=ArtifactCardVersionView,
    status_code=status.HTTP_201_CREATED,
)
def publish_artifact_card_version(
    card_id: str,
    payload: ArtifactCardPublish,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactCardVersionView:
    """Freeze the card's current draft snapshot as the next immutable version."""
    context.require_permission("workspace.write")
    from app.services.artifact_cards import ArtifactCardService

    version = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).publish_version(
        card_id,
        release_notes=payload.release_notes,
        actor_id=context.principal.user_id,
        publish_source="user",
    )
    return ArtifactCardVersionView.model_validate(version)


@router.get(
    "/artifacts/cards/{card_id}/versions",
    response_model=list[ArtifactCardVersionView],
)
def list_artifact_card_versions(
    card_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[ArtifactCardVersionView]:
    """Version history of one card, newest first."""
    context.require_permission("workspace.read")
    from app.services.artifact_cards import ArtifactCardService

    versions = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).list_versions(card_id)
    return [ArtifactCardVersionView.model_validate(version) for version in versions]


@router.get("/artifacts/cards/{card_id}/preview", response_model=ArtifactCardPreviewView)
def get_artifact_card_preview(
    card_id: str,
    db: DB,
    context: CurrentWorkspace,
    version: int | None = None,
) -> ArtifactCardPreviewView:
    """Full render data (preview_html / runtime / viewport) for one card.

    ``version`` selects a frozen published snapshot instead of the live draft.
    """
    context.require_permission("workspace.read")
    from app.services.artifact_cards import ArtifactCardService

    card, snapshot = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).get_preview(card_id, version=version)
    return ArtifactCardPreviewView.model_validate(
        {**card.__dict__, "preview_snapshot": snapshot}
    )


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


@router.delete(
    "/artifacts/cards/versions/{version_id}",
    response_model=ArtifactCardVersionView,
)
def delete_artifact_card_version(
    version_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactCardVersionView:
    """Soft-delete one published card version (snapshot content is untouched)."""
    context.require_permission("workspace.write")
    from app.services.artifact_cards import ArtifactCardService

    version = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).delete_version(version_id)
    return ArtifactCardVersionView.model_validate(version)


@router.get(
    "/artifacts/cards/versions/{version_id}/share-tokens",
    response_model=list[ArtifactCardShareTokenView],
)
def list_artifact_card_share_tokens(
    version_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[ArtifactCardShareTokenView]:
    context.require_permission("workspace.read")
    from app.services.artifact_cards import ArtifactCardService

    tokens = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).list_share_tokens(version_id)
    return [ArtifactCardShareTokenView.model_validate(token) for token in tokens]


@router.post(
    "/artifacts/cards/versions/{version_id}/share-tokens",
    response_model=ArtifactCardShareTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_artifact_card_share_token(
    version_id: str,
    payload: ArtifactCardShareTokenCreate,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactCardShareTokenCreated:
    context.require_permission("workspace.write")
    from app.services.artifact_cards import ArtifactCardService

    raw, token = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).create_share_token(
        version_id,
        label=payload.label,
        expires_at=payload.expires_at,
        max_views=payload.max_views,
    )
    return ArtifactCardShareTokenCreated.model_validate(
        {**token.__dict__, "token": raw}
    )


@router.delete(
    "/artifacts/cards/share-tokens/{token_id}",
    response_model=ArtifactCardShareTokenView,
)
def revoke_artifact_card_share_token(
    token_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> ArtifactCardShareTokenView:
    context.require_permission("workspace.write")
    from app.services.artifact_cards import ArtifactCardService

    token = ArtifactCardService(
        db, context.workspace_id, context.principal.tenant_id
    ).revoke_share_token(token_id)
    return ArtifactCardShareTokenView.model_validate(token)


@router.get("/card-share/{raw_token}")
def card_share_viewer(raw_token: str, db: DB):
    """Public read-only HTML viewer for a shared card version."""
    from app.services.artifact_cards import ArtifactCardService

    version, card = ArtifactCardService(db, "", "").resolve_card_share(raw_token)
    snapshot = version.preview_snapshot or {}
    card_type = card.card_type
    title = card.title or "共享卡片"
    published_at = version.created_at
    release_notes = version.release_notes or ""

    preview_html = snapshot.get("preview_html")
    escaped_preview = (
        html.escape(preview_html, quote=True)
        if isinstance(preview_html, str)
        else ""
    )
    is_magic = card_type == "magic_card" and escaped_preview
    # Component cards need the trusted React renderer and are shown as a
    # read-only info page instead of a sandboxed preview.
    if is_magic:
        body = (
            '<div class="frame">'
            '<div class="head"><strong>{title}</strong>'
            '<span>v{version} · {when} · 只读分享</span></div>'
            '<iframe sandbox="allow-scripts" srcdoc="{preview}"></iframe>'
            "</div>"
        ).format(
            title=html.escape(title),
            version=version.version,
            when=published_at.strftime("%Y-%m-%d %H:%M"),
            preview=escaped_preview,
        )
    else:
        body = (
            '<div class="frame">'
            '<div class="head"><strong>{title}</strong>'
            '<span>v{version} · {when} · 只读分享</span></div>'
            '<div class="info">'
            "<p>该卡片是{kind}，需要 LearnGraph 应用内渲染。</p>"
            "<p>请在原会话或产物页中打开查看。</p>"
            "{notes}"
            "</div></div>"
        ).format(
            title=html.escape(title),
            version=version.version,
            when=published_at.strftime("%Y-%m-%d %H:%M"),
            kind=(
                "交互式学习组件"
                if card_type == "component"
                else "双向交互页面"
            ),
            notes=(
                f"<p>版本说明：{html.escape(release_notes)}</p>"
                if release_notes
                else ""
            ),
        )
    page = _CARD_SHARE_SHELL.format(body=body)
    return HTMLResponse(content=page, media_type="text/html; charset=utf-8")


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
