from __future__ import annotations

"""HTTP endpoints for the generic Agent egress approval queue (D2.1).

Endpoints are workspace-scoped via ``CurrentWorkspace``. The deployment gate
``settings.sandbox_agent_egress_approvals_enabled`` keeps the channel closed by
default; ordinary workspace members may approve requests they initiated
(``allow_once``/``deny``), while ``allow_always`` requires ``workspace.manage``
and persists the host into the workspace ``agent_egress`` allowlist.

Contracts (design doc md-D2-1 §1.2):
  * A — the only authorization resource is a canonical exact hostname. No
    command / argv / prompt / URL field is accepted by the create schema.
  * B — a decision only adds a host to the allowlist. It never writes an IP,
    CIDR, ``allow_private`` or classifier exception. The runtime
    ``SandboxEgressProxy.authorize_connect`` still resolves + re-classifies
    every CONNECT, so an approved host that later resolves to a private /
    loopback / metadata address is still denied. This channel does NOT bypass
    the proxy classifier.
"""

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import Message, MessagePartRecord, MessageVersion
from app.domain.schemas.egress_authorization import (
    EgressAuthorizationCreateRequest,
    EgressAuthorizationDecisionRequest,
    EgressAuthorizationListResponse,
    EgressAuthorizationRequestView,
)
from app.services.egress_approvals import EgressApprovalService

router = APIRouter(prefix="/egress-approvals", tags=["egress-approvals"])


def _service(db, context: CurrentWorkspace, settings: AppSettings) -> EgressApprovalService:
    if not settings.sandbox_agent_egress_approvals_enabled:
        raise AppError(
            403,
            "egress_authorization_disabled",
            "Generic Agent egress approvals are disabled by the deployment",
        )
    return EgressApprovalService(db, context.workspace_id, settings)


@router.post("", response_model=EgressAuthorizationRequestView, status_code=201)
def create_egress_approval(
    payload: EgressAuthorizationCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> EgressAuthorizationRequestView:
    """Create a pending approval request for a generic Agent egress host.

    Only a canonical hostname is accepted; ``purpose``/``request_context`` are
    display-only. Idempotent per pending (workspace, hostname, source).
    Pending is a suspension — it never blocks or fails the caller.
    """
    service = _service(db, context, settings)
    request = service.create_request(
        hostname=payload.hostname,
        requested_by=context.principal.user_id,
        chat_session_id=payload.chat_session_id,
        purpose=payload.purpose,
        request_context=payload.request_context,
        ttl_seconds=payload.ttl_seconds,
    )
    return EgressAuthorizationRequestView.model_validate(request)


@router.post("/{request_id}/decision", response_model=EgressAuthorizationRequestView)
def decide_egress_approval(
    request_id: str,
    payload: EgressAuthorizationDecisionRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> EgressAuthorizationRequestView:
    """Record a user decision (allow_once / allow_always / deny).

    Only the requesting user, or a workspace manager on their behalf, may
    decide. ``allow_always`` requires ``workspace.manage`` and persists the
    hostname into the workspace ``agent_egress`` allowlist; ``allow_once`` is a
    single-use lease, consumed by the resume path (T4.1).
    """
    service = _service(db, context, settings)
    request = service.decide(
        request_id=request_id,
        decision=payload.decision,
        actor_id=context.principal.user_id,
        is_manager="workspace.manage" in context.permissions,
    )
    # Rewrite the durable ``egress_authorization`` card part on the assistant
    # message that carried it, so a reload or a second browser shows the
    # terminal decision instead of a live pending card (mirrors the
    # fetch_authorization decision path).
    if request.assistant_message_id:
        message = db.scalar(
            select(Message).where(
                Message.id == request.assistant_message_id,
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
                        MessagePartRecord.part_type == "egress_authorization",
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
                    "authorization_status": request.status,
                }
                message.parts = [
                    {
                        **item,
                        "status": "completed",
                        "data": {
                            **(item.get("data") or {}),
                            "decision": payload.decision,
                            "authorization_status": request.status,
                        },
                    }
                    if item.get("type") == "egress_authorization"
                    else item
                    for item in (message.parts or [])
                ]
    return EgressAuthorizationRequestView.model_validate(request)


@router.post("/{request_id}/resume", response_model=dict)
def resume_egress_approval(
    request_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    """Resume the suspended Agent turn after the egress host was approved.

    Only an ``approved`` request with a server-side ``resume_payload`` is
    resumable; the resumed generation runs synchronously and replaces the
    pending card part with the completed answer (D2.1 T4.1).
    """
    service = _service(db, context, settings)
    request = service.get_request(request_id)
    if request.status != "approved" or not request.resume_payload:
        raise AppError(
            409,
            "egress_authorization_not_resumable",
            "该授权不在服务端恢复流程内。",
        )
    from app.api.routers.chat import service as chat_service

    chat = chat_service(db, context, settings)
    return chat.resume_egress_generation(request_id)


@router.get("", response_model=EgressAuthorizationListResponse)
def list_egress_approvals(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    status: str | None = None,
    requested_by: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> EgressAuthorizationListResponse:
    """List workspace-visible approval requests (paged, status-filterable).

    Ordinary members see only their own requests; workspace managers see the
    whole queue. Pending requests past their deadline are expired
    opportunistically so stale cards stop being actionable.
    """
    service = _service(db, context, settings)
    rows, total = service.list_requests(
        actor_id=context.principal.user_id,
        is_manager="workspace.manage" in context.permissions,
        status=status,
        requested_by=requested_by,
        offset=offset,
        limit=limit,
    )
    return EgressAuthorizationListResponse(
        items=[EgressAuthorizationRequestView.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
