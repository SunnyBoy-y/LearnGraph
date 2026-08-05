from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import (
    FetchAuthorizationRequest,
    Message,
    MessagePartRecord,
    MessageVersion,
    UserWebFetchPolicy,
)
from app.domain.schemas.fetch_authorization import (
    FetchAuthorizationDecisionRequest,
    FetchAuthorizationRequestView,
)
from app.repositories.audit import AuditRepository

router = APIRouter(prefix="/fetch-authorizations", tags=["fetch-authorizations"])


@router.post("/{request_id}/decision", response_model=FetchAuthorizationRequestView)
def decide_fetch_authorization(
    request_id: str,
    payload: FetchAuthorizationDecisionRequest,
    db: DB,
    context: CurrentWorkspace,
) -> FetchAuthorizationRequestView:
    pending = db.scalar(
        select(FetchAuthorizationRequest).where(
            FetchAuthorizationRequest.id == request_id,
            FetchAuthorizationRequest.workspace_id == context.workspace_id,
        )
    )
    if pending is None:
        raise AppError(404, "fetch_authorization_not_found", "Fetch authorization request was not found")
    if pending.status != "pending":
        return FetchAuthorizationRequestView.model_validate(pending)

    pending.decision = payload.decision
    pending.status = "approved" if payload.decision != "deny" else "denied"
    pending.decided_by = context.principal.user_id
    pending.decided_at = datetime.now(timezone.utc)
    # Make the originating card terminal in durable message parts too. Local
    # React state alone is lost after a reload or in another browser.
    if pending.assistant_message_id:
        message = db.scalar(
            select(Message).where(
                Message.id == pending.assistant_message_id,
                Message.workspace_id == context.workspace_id,
            )
        )
        if message is not None:
            latest_version_id = db.scalar(
                select(MessageVersion.id)
                .where(MessageVersion.message_id == message.id)
                .order_by(MessageVersion.version.desc())
                .limit(1)
            )
            part = (
                db.scalar(
                    select(MessagePartRecord)
                    .where(
                        MessagePartRecord.workspace_id == context.workspace_id,
                        MessagePartRecord.message_version_id == latest_version_id,
                        MessagePartRecord.part_type == "fetch_authorization",
                    )
                    .order_by(MessagePartRecord.ordinal.desc())
                )
                if latest_version_id
                else None
            )
            if part is not None:
                part.status = "completed"
                part.data = {
                    **(part.data or {}),
                    "decision": payload.decision,
                    "authorization_status": pending.status,
                }
                message.parts = [
                    {
                        **item,
                        "status": "completed",
                        "data": {
                            **(item.get("data") or {}),
                            "decision": payload.decision,
                            "authorization_status": pending.status,
                        },
                    }
                    if item.get("type") == "fetch_authorization"
                    else item
                    for item in (message.parts or [])
                ]
    if payload.decision == "allow_always":
        # ``allow_always`` writes the *user's own* whitelist so an ordinary
        # member's choice affects only them — no workspace.manage gate. The
        # effective domains for a fetch decision are the union of this
        # user-level list and the workspace-level ``web_fetch.policy``.
        policy = db.scalar(
            select(UserWebFetchPolicy).where(
                UserWebFetchPolicy.workspace_id == context.workspace_id,
                UserWebFetchPolicy.user_id == context.principal.user_id,
            )
        )
        domains = [item for item in (policy.allowed_domains if policy else []) if isinstance(item, str)]
        allow_without_confirmation = bool(
            policy.allow_without_confirmation if policy else False
        )
        hostname = pending.hostname
        if hostname and hostname not in {item.strip().casefold() for item in domains}:
            domains.append(hostname)
        if policy is None:
            policy = UserWebFetchPolicy(
                workspace_id=context.workspace_id,
                user_id=context.principal.user_id,
                allowed_domains=domains,
                allow_without_confirmation=allow_without_confirmation,
            )
            db.add(policy)
        else:
            policy.allowed_domains = domains
            policy.allow_without_confirmation = allow_without_confirmation
    AuditRepository(db, context.workspace_id).record(
        actor_id=context.principal.user_id,
        action="web_fetch.authorization_decided",
        resource_type="fetch_authorization_request",
        resource_id=pending.id,
        outcome=pending.status,
        details={"decision": payload.decision, "hostname": pending.hostname},
    )
    db.commit()
    db.refresh(pending)
    return FetchAuthorizationRequestView.model_validate(pending)


@router.post("/{request_id}/resume")
def resume_fetch_authorization(
    request_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict[str, str]:
    """Resume a paused 极速/思考 fetch turn after the user approved.

    Runs the authorized fetch+search mix synchronously and updates the pending
    assistant message; the frontend refetches session history once this
    returns so the completed answer (with sources) replaces the card.
    """

    pending = db.scalar(
        select(FetchAuthorizationRequest).where(
            FetchAuthorizationRequest.id == request_id,
            FetchAuthorizationRequest.workspace_id == context.workspace_id,
        )
    )
    if pending is None:
        raise AppError(404, "fetch_authorization_not_found", "Fetch authorization request was not found")
    if pending.status != "approved" or not pending.resume_payload:
        raise AppError(
            409,
            "fetch_authorization_not_resumable",
            "This authorization is not awaiting a server-side resume",
        )
    from app.api.routers.chat import service as chat_service

    request_data = pending.resume_payload.get("request") or {}
    service = chat_service(
        db,
        context,
        settings,
        model_id=request_data.get("model_id"),
        provider_id=request_data.get("provider_id"),
        thinking_mode=request_data.get("thinking_mode"),
        search_route=request_data.get("search_route"),
    )
    return service.resume_fetch_generation(request_id)
