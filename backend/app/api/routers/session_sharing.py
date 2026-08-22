from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.session_sharing import (
    SessionShareCreate,
    SessionShareDetailView,
    SessionShareMessageView,
    SessionSharePublicView,
    SessionShareTokenCreated,
    SessionShareTokenView,
    SessionShareView,
)
from app.services.session_sharing import SessionSharingService

router = APIRouter(tags=["session-sharing"])


def service(db, context: CurrentWorkspace) -> SessionSharingService:
    return SessionSharingService(
        db,
        context.workspace_id,
        context.principal.user_id,
        context.principal.tenant_id,
    )


@router.post(
    "/sessions/{session_id}/shares",
    response_model=SessionShareTokenCreated,
)
def create_session_share(
    session_id: str,
    payload: SessionShareCreate,
    db: DB,
    context: CurrentWorkspace,
) -> SessionShareTokenCreated:
    """Freeze an immutable snapshot of the session and mint one read-only token."""
    context.require_permission("workspace.write")
    raw, token = service(db, context).create_share(
        session_id,
        scope=payload.scope,
        from_message_id=payload.from_message_id,
        to_message_id=payload.to_message_id,
        answers_only=payload.answers_only,
        label=payload.label,
        expires_at=payload.expires_at,
        max_views=payload.max_views,
    )
    return SessionShareTokenCreated.model_validate({**token.__dict__, "token": raw})


@router.get("/sessions/{session_id}/shares", response_model=list[SessionShareDetailView])
def list_session_shares(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[SessionShareDetailView]:
    """List every share of this session with its tokens (no raw tokens)."""
    context.require_permission("workspace.read")
    svc = service(db, context)
    return [
        SessionShareDetailView.model_validate(
            {**share.__dict__, "tokens": svc.list_tokens(share.id)}
        )
        for share in svc.list_shares(session_id)
    ]


@router.delete("/session-shares/{share_id}", response_model=SessionShareView)
def revoke_session_share(
    share_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> SessionShareView:
    """Revoke the whole snapshot (cascades to every token)."""
    context.require_permission("workspace.write")
    return SessionShareView.model_validate(service(db, context).revoke_share(share_id))


@router.delete(
    "/session-shares/{share_id}/tokens/{token_id}",
    response_model=SessionShareTokenView,
)
def revoke_session_share_token(
    share_id: str,
    token_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> SessionShareTokenView:
    context.require_permission("workspace.write")
    return SessionShareTokenView.model_validate(
        service(db, context).revoke_token(token_id)
    )


@router.get("/share/{raw_token}", response_model=SessionSharePublicView)
def view_shared_session(raw_token: str, request: Request, db: DB) -> SessionSharePublicView:
    """Public read-only view. The token is the complete authorization.

    Only the frozen, display-safe snapshot is returned — no memory, files,
    provider traces, or identity data. The visit increments ``view_count`` and
    records the visitor fingerprint (IP + User-Agent) for audit.
    """
    share, messages = SessionSharingService(db, "", "", "").resolve_share(
        raw_token,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return SessionSharePublicView(
        id=share.id,
        title=share.title,
        scope=share.scope,
        message_count=share.message_count,
        created_at=share.created_at,
        messages=[
            SessionShareMessageView.model_validate(message) for message in messages
        ],
    )
