from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import func, select

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
    FetchAuthorizationListResponse,
    FetchAuthorizationRequestView,
    UserWebFetchPolicyUpdateRequest,
    UserWebFetchPolicyView,
    WebFetchRuntimeUpdateRequest,
    WebFetchRuntimeView,
)
from app.repositories.audit import AuditRepository
from app.services.web_fetch_runtime import (
    save_web_fetch_runtime,
    web_fetch_runtime_status,
)

router = APIRouter(prefix="/fetch-authorizations", tags=["fetch-authorizations"])


def _to_view(row: FetchAuthorizationRequest) -> FetchAuthorizationRequestView:
    """Map the ORM row to the API view (``actor_id`` surfaces as ``requested_by``)."""
    return FetchAuthorizationRequestView(
        id=row.id,
        workspace_id=row.workspace_id,
        chat_session_id=row.chat_session_id,
        tool_call_id=row.tool_call_id,
        tool_name=row.tool_name,
        requested_url=row.requested_url,
        hostname=row.hostname,
        status=row.status,
        decision=row.decision,
        requested_by=row.actor_id,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=FetchAuthorizationListResponse)
def list_fetch_authorizations(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> FetchAuthorizationListResponse:
    """List persisted web-fetch approval records for the workspace.

    These are the durable ``fetch_authorization_requests`` rows behind the
    聊天内网络审批卡片: every 允许一次 / 以后都允许 / 拒绝 decision is stored in
    PostgreSQL, so the settings page can show the full history after a reload.
    Ordinary members see only their own requests; workspace managers see the
    whole queue. ``allow_always`` rows surface the personal whitelist entry
    that the decision wrote into ``UserWebFetchPolicy``.
    """
    query = select(FetchAuthorizationRequest).where(
        FetchAuthorizationRequest.workspace_id == context.workspace_id
    )
    if "workspace.manage" not in context.permissions:
        query = query.where(
            FetchAuthorizationRequest.actor_id == context.principal.user_id
        )
    if status:
        query = query.where(FetchAuthorizationRequest.status == status)
    total = db.scalar(
        select(func.count()).select_from(query.subquery())
    ) or 0
    rows = db.scalars(
        query.order_by(FetchAuthorizationRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return FetchAuthorizationListResponse(
        items=[_to_view(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/user-policy", response_model=UserWebFetchPolicyView)
def get_fetch_user_policy(
    db: DB,
    context: CurrentWorkspace,
) -> UserWebFetchPolicyView:
    """The current user's personal web-fetch whitelist (聊天内「以后都允许」).

    Stored per (workspace, user) in ``user_web_fetch_policies``; the effective
    fetch domains are the union of this personal list, the workspace
    ``web_fetch.policy`` and the unified ``access.allowlist``.
    """
    policy = db.scalar(
        select(UserWebFetchPolicy).where(
            UserWebFetchPolicy.workspace_id == context.workspace_id,
            UserWebFetchPolicy.user_id == context.principal.user_id,
        )
    )
    return UserWebFetchPolicyView(
        allowed_domains=list(policy.allowed_domains if policy else []),
        allow_without_confirmation=bool(
            policy.allow_without_confirmation if policy else False
        ),
    )


@router.put("/user-policy", response_model=UserWebFetchPolicyView)
def update_fetch_user_policy(
    payload: UserWebFetchPolicyUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> UserWebFetchPolicyView:
    """Replace the current user's personal whitelist from the settings page.

    No ``workspace.manage`` gate — the list is user-scoped, mirroring the
    decision-path write so the settings page can remove a「以后都允许」domain
    without affecting other members.
    """
    policy = db.scalar(
        select(UserWebFetchPolicy).where(
            UserWebFetchPolicy.workspace_id == context.workspace_id,
            UserWebFetchPolicy.user_id == context.principal.user_id,
        )
    )
    domains = [item.strip().casefold() for item in payload.allowed_domains if isinstance(item, str)]
    domains = list(dict.fromkeys(item for item in domains if item))
    if policy is None:
        policy = UserWebFetchPolicy(
            workspace_id=context.workspace_id,
            user_id=context.principal.user_id,
            allowed_domains=domains,
            allow_without_confirmation=payload.allow_without_confirmation,
        )
        db.add(policy)
    else:
        policy.allowed_domains = domains
        policy.allow_without_confirmation = payload.allow_without_confirmation
    AuditRepository(db, context.workspace_id).record(
        actor_id=context.principal.user_id,
        action="web_fetch.user_policy_updated",
        resource_type="user_web_fetch_policy",
        resource_id=context.principal.user_id,
        outcome="updated",
        details={"allowed_domains": domains},
    )
    db.commit()
    db.refresh(policy)
    return UserWebFetchPolicyView(
        allowed_domains=list(policy.allowed_domains),
        allow_without_confirmation=bool(policy.allow_without_confirmation),
    )


@router.get("/settings", response_model=WebFetchRuntimeView)
def get_web_fetch_settings(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> WebFetchRuntimeView:
    """Workspace web fetch preferences plus effective channel status."""
    return web_fetch_runtime_status(db, context.workspace_id, settings)


@router.put("/settings", response_model=WebFetchRuntimeView)
def update_web_fetch_settings(
    payload: WebFetchRuntimeUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> WebFetchRuntimeView:
    """Persist workspace web fetch preferences (sandbox switch + priority)."""
    context.require_permission("workspace.manage")
    save_web_fetch_runtime(
        db, context.workspace_id, context.principal.user_id, payload
    )
    return web_fetch_runtime_status(db, context.workspace_id, settings)


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
        return _to_view(pending)
    # Authority (design doc §6.3): only the requesting user, or a workspace
    # manager deciding on their behalf, may decide a pending request.
    if (
        pending.actor_id != context.principal.user_id
        and "workspace.manage" not in context.permissions
    ):
        raise AppError(
            403,
            "fetch_authorization_not_decider",
            "Only the requesting user or a workspace manager may decide this request",
        )

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
    return _to_view(pending)


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
    # Authority: only the requesting user (or a workspace manager) may resume
    # the paused paid generation bound to this request.
    if (
        pending.actor_id != context.principal.user_id
        and "workspace.manage" not in context.permissions
    ):
        raise AppError(
            403,
            "fetch_authorization_not_decider",
            "Only the requesting user or a workspace manager may resume this request",
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
