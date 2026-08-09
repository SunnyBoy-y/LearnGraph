"""Event-driven Agent task channel for bidirectional sub-applications.

When a sub-application session accepts a ``component.event`` whose event type is
declared in ``interaction_contract.agent_triggers``, this module decides whether
the turn may run automatically (workspace allow / app allowlist / session
consent), creates a pending consent request when it may not, and dispatches the
turn through the durable queue.

The worker reuses :func:`build_chat_service` and ``ChatService.create_stream``
so event-driven turns share the same Provider, Memory, Search, tool and audit
semantics as normal chat turns. The model observes the event with
``subapp_observe`` and writes state with ``subapp_patch_state``; state writes
still go through ``SubAppService.propose_state`` CAS.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.models import (
    MessageSubmission,
    SubAppAgentConsentRequest,
    SubAppAgentRun,
    SubAppBundle,
    SubAppInteractionEvent,
    SubAppSession,
    User,
    Workspace,
    utc_now,
)
from app.domain.schemas.chat import MessageCreateRequest
from app.services.authorization import AuthorizationService
from app.services.chat_service_factory import build_chat_service
from app.services.durable_queue import DurableQueue

JOB_KIND = "subapp.event.process"
RUN_STATUS_SKIPPED = "skipped"
RUN_STATUS_QUEUED = "queued"
RUN_STATUS_PROCESSING = "processing"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"

_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "authorization", "credential")
_PROMPT_STRING_CHARS = 80
_PROMPT_ARRAY_ITEMS = 20
_PROMPT_DEPTH = 2


def _token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _dedupe_key(event_id: str) -> str:
    return f"{JOB_KIND}:{event_id}"


def _session_for_event(db: Session, session_id: str) -> SubAppSession | None:
    return db.scalar(
        select(SubAppSession).where(SubAppSession.id == session_id)
    )


def _event_for_id(db: Session, event_id: str) -> SubAppInteractionEvent | None:
    return db.scalar(
        select(SubAppInteractionEvent).where(SubAppInteractionEvent.id == event_id)
    )


def _bundle_for_session(
    db: Session, session: SubAppSession
) -> SubAppBundle | None:
    if not session.artifact_version_id:
        return None
    return db.scalar(
        select(SubAppBundle).where(
            SubAppBundle.component_manifest_id == session.artifact_version_id,
            SubAppBundle.workspace_id == session.workspace_id,
        )
    )


def _trigger_for_event(
    session: SubAppSession, event_type: str
) -> dict[str, Any] | None:
    for item in session.agent_triggers or []:
        if isinstance(item, dict) and item.get("event_type") == event_type:
            return item
    return None


def _run_for_event(db: Session, event_id: str) -> SubAppAgentRun | None:
    return db.scalar(
        select(SubAppAgentRun).where(SubAppAgentRun.event_id == event_id)
    )


def _ensure_run(
    db: Session,
    *,
    session: SubAppSession,
    event: SubAppInteractionEvent,
    actor_id: str,
    status: str = RUN_STATUS_QUEUED,
    job_id: str | None = None,
) -> SubAppAgentRun:
    run = _run_for_event(db, event.id)
    if run is None:
        run = SubAppAgentRun(
            workspace_id=session.workspace_id,
            session_id=session.id,
            event_id=event.id,
            chat_session_id=session.chat_session_id,
            actor_id=actor_id,
            job_id=job_id,
            status=status,
        )
        db.add(run)
    else:
        run.status = status
        if job_id is not None:
            run.job_id = job_id
    db.flush()
    return run


def _enqueue_job(
    db: Session,
    *,
    session: SubAppSession,
    event: SubAppInteractionEvent,
    actor_id: str,
) -> SubAppAgentRun:
    settings = get_settings()
    queue = DurableQueue(
        db,
        lease_seconds=settings.durable_queue_lease_seconds,
        max_attempts=settings.subapp_event_agent_max_attempts,
    )
    job = queue.enqueue(
        workspace_id=session.workspace_id,
        kind=JOB_KIND,
        payload={
            "event_id": event.id,
            "session_id": session.id,
            "chat_session_id": session.chat_session_id,
            "actor_id": actor_id,
        },
        dedupe_key=_dedupe_key(event.id),
    )
    run = _ensure_run(
        db,
        session=session,
        event=event,
        actor_id=actor_id,
        status=RUN_STATUS_QUEUED,
        job_id=job.id,
    )
    session.agent_status = RUN_STATUS_QUEUED
    session.agent_job_id = job.id
    session.agent_error = None
    session.agent_updated_at = utc_now()
    db.commit()
    db.refresh(run)
    return run


def maybe_enqueue_subapp_event_agent(
    *,
    session_id: str,
    event_id: str,
    workspace_id: str,
    actor_id: str,
    db: Session | None = None,
) -> dict[str, Any]:
    """Decide consent and enqueue one event-driven Agent turn.

    Returns a bounded ``agent`` object for the 202 event response:
    ``triggered``, ``consent_required``, ``pending_consent_id`` and
    ``disabled``. ``db`` is the caller's request/session unit of work when
    available; otherwise a fresh ``SessionLocal`` is opened (used by HTTP
    control endpoints).
    """
    settings = get_settings()
    owns_session = db is None
    session_db = db or SessionLocal()
    try:
        session = _session_for_event(session_db, session_id)
        event = _event_for_id(session_db, event_id)
        if session is None or event is None or session.workspace_id != workspace_id:
            return {
                "triggered": False,
                "consent_required": False,
                "pending_consent_id": None,
                "disabled": False,
            }
        trigger = _trigger_for_event(session, event.event_type)
        if trigger is None:
            return {
                "triggered": False,
                "consent_required": False,
                "pending_consent_id": None,
                "disabled": False,
            }
        if not settings.subapp_event_agent_enabled:
            return {
                "triggered": False,
                "consent_required": False,
                "pending_consent_id": None,
                "disabled": True,
            }

        workspace = session_db.get(Workspace, workspace_id)
        bundle = _bundle_for_session(session_db, session)
        allowed = bool(
            (workspace is not None and workspace.subapp_agent_consent == "allow")
            or (bundle is not None and bundle.agent_consent_allowlisted)
            or session.agent_consent == "allowed_session"
        )
        if allowed:
            _enqueue_job(session_db, session=session, event=event, actor_id=actor_id)
            return {
                "triggered": True,
                "consent_required": False,
                "pending_consent_id": None,
                "disabled": False,
            }

        pending = session_db.scalar(
            select(SubAppAgentConsentRequest).where(
                SubAppAgentConsentRequest.workspace_id == workspace_id,
                SubAppAgentConsentRequest.session_id == session.id,
                SubAppAgentConsentRequest.event_id == event.id,
                SubAppAgentConsentRequest.status == "pending",
            )
        )
        if pending is None:
            pending = SubAppAgentConsentRequest(
                workspace_id=workspace_id,
                session_id=session.id,
                event_id=event.id,
                artifact_version_id=session.artifact_version_id,
                status="pending",
                scope="session",
                expires_at=utc_now() + timedelta(hours=1),
            )
            session_db.add(pending)
            session_db.commit()
            session_db.refresh(pending)
        return {
            "triggered": False,
            "consent_required": True,
            "pending_consent_id": pending.id,
            "disabled": False,
        }
    finally:
        if owns_session:
            session_db.close()


def decide_subapp_agent_consent(
    *,
    session_id: str,
    token: str,
    decision: str,
    actor_id: str,
) -> dict[str, Any]:
    """Apply a consent decision and dispatch the pending event when allowed."""
    with SessionLocal() as db:
        session = _session_for_event(db, session_id)
        if session is None:
            raise AppError(404, "subapp_session_not_found", "Sub-application session not found")
        if session.status != "active":
            raise AppError(
                409,
                "subapp_session_not_active",
                "Sub-application session is not accepting events",
            )
        if not session.current_token_hash or not hmac.compare_digest(
            _token_sha256(token), session.current_token_hash
        ):
            raise AppError(
                401,
                "subapp_token_invalid",
                "Current session capability token is missing, stale, or invalid",
            )
        workspace = db.get(Workspace, session.workspace_id)
        bundle = _bundle_for_session(db, session)
        pending = db.scalar(
            select(SubAppAgentConsentRequest).where(
                SubAppAgentConsentRequest.workspace_id == session.workspace_id,
                SubAppAgentConsentRequest.session_id == session.id,
                SubAppAgentConsentRequest.status == "pending",
            )
            .order_by(SubAppAgentConsentRequest.created_at.desc())
            .limit(1)
        )
        if pending is not None:
            pending.status = "allowed" if decision != "deny" else "denied"
            pending.scope = decision
            pending.decided_by = actor_id
            pending.decided_at = utc_now()

        if decision == "allow_session":
            session.agent_consent = "allowed_session"
        elif decision == "allow_app":
            if bundle is not None:
                bundle.agent_consent_allowlisted = True
        elif decision == "allow_global":
            if workspace is not None:
                workspace.subapp_agent_consent = "allow"
        elif decision != "deny":
            raise AppError(
                422,
                "subapp_agent_consent_invalid",
                "Unknown consent decision",
            )

        event_id = pending.event_id if pending is not None else None
        if decision == "deny":
            event = _event_for_id(db, event_id) if event_id else None
            if event is not None:
                run = _ensure_run(
                    db,
                    session=session,
                    event=event,
                    actor_id=actor_id,
                    status=RUN_STATUS_SKIPPED,
                )
                run.completed_at = utc_now()
            db.commit()
            return {
                "triggered": False,
                "consent_required": False,
                "pending_consent_id": pending.id if pending is not None else None,
                "disabled": False,
            }

        if event_id:
            event = _event_for_id(db, event_id)
            if event is not None:
                _enqueue_job(db, session=session, event=event, actor_id=actor_id)
        else:
            db.commit()
        return {
            "triggered": event_id is not None,
            "consent_required": False,
            "pending_consent_id": pending.id if pending is not None else None,
            "disabled": False,
        }


def _redact_prompt_value(value: Any, depth: int = 0) -> Any:
    if depth > _PROMPT_DEPTH:
        return "[truncated]"
    if isinstance(value, str):
        if any(part in value.casefold() for part in _SENSITIVE_KEY_PARTS):
            return "[redacted]"
        return value[:_PROMPT_STRING_CHARS]
    if isinstance(value, list):
        return [_redact_prompt_value(item, depth + 1) for item in value[:_PROMPT_ARRAY_ITEMS]]
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS)
                else _redact_prompt_value(item, depth + 1)
            )
            for key, item in list(value.items())[:_PROMPT_ARRAY_ITEMS]
        }
    return value


def build_subapp_event_prompt(
    event: SubAppInteractionEvent, session: SubAppSession
) -> str:
    payload = event.payload_json or "{}"
    try:
        import json

        summary = _redact_prompt_value(json.loads(payload))
    except Exception:
        summary = "[unparseable payload]"
    return (
        "你正在处理一个子应用事件。请先用 subapp_observe 读取事件，再根据事件"
        "更新子应用状态；必须通过 subapp_patch_state 写入新状态，不要伪造"
        "事件，不要输出普通聊天文本。\n"
        f"session_id={session.id}\n"
        f"event_id={event.id}\n"
        f"event_type={event.event_type}\n"
        f"payload={summary}"
    )


def run_subapp_event_agent_once(payload: dict[str, Any]) -> bool:
    """Run one event-driven Agent turn in the durable worker.

    Returns True when the job reached a terminal state. Raises on transient
    failures so ``DurableQueue.fail`` applies the bounded retry backoff.
    """
    settings = get_settings()
    with SessionLocal() as db:
        event = _event_for_id(db, str(payload["event_id"]))
        session = _session_for_event(db, str(payload["session_id"]))
        if event is None or session is None:
            return True
        run = _ensure_run(
            db,
            session=session,
            event=event,
            actor_id=str(payload.get("actor_id") or session.actor_id),
        )
        if session.status != "active":
            run.status = RUN_STATUS_SKIPPED
            run.completed_at = utc_now()
            session.agent_status = "idle"
            session.agent_error = None
            session.agent_updated_at = utc_now()
            db.commit()
            return True

        run.status = RUN_STATUS_PROCESSING
        run.started_at = utc_now()
        run.error = None
        session.agent_status = RUN_STATUS_PROCESSING
        session.agent_error = None
        session.agent_updated_at = utc_now()
        db.commit()

        try:
            workspace = db.get(Workspace, session.workspace_id)
            user = db.scalar(
                select(User).where(User.id == session.actor_id)
            )
            if workspace is None or user is None:
                raise AppError(
                    500,
                    "subapp_agent_actor_missing",
                    "Sub-application workspace or actor is unavailable",
                )
            principal = Principal(
                user_id=user.id,
                username=user.username,
                tenant_id=user.tenant_id,
                session_id="system:subapp-agent",
                display_name=user.display_name or user.username,
                is_system_admin=user.is_system_admin,
            )
            permissions = AuthorizationService(db, principal).workspace_permissions(
                workspace
            )
            context = WorkspaceContext(
                principal=principal, workspace=workspace, permissions=permissions
            )
            service = build_chat_service(
                db,
                workspace_context=context,
                settings=settings,
                thinking_mode="off",
                search_route="disabled",
            )
            request = MessageCreateRequest(
                content=build_subapp_event_prompt(event, session),
                agent_mode=True,
                message_kind="subapp_event",
                subapp_event_id=event.id,
            )
            idempotency_key = f"subapp-event:{event.id}"
            service.preflight_create_stream(
                session.chat_session_id or "",
                request,
                idempotency_key=idempotency_key,
                last_event_id=None,
            )
            for _ in service.create_stream(
                session.chat_session_id or "",
                request,
                idempotency_key=idempotency_key,
            ):
                pass

            submission = db.scalar(
                select(MessageSubmission).where(
                    MessageSubmission.workspace_id == session.workspace_id,
                    MessageSubmission.session_id == session.chat_session_id,
                    MessageSubmission.idempotency_key_hash
                    == hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
                )
            )
            if submission is not None:
                run.message_id = submission.assistant_message_id
                run.message_version_id = submission.message_version_id
            run.status = RUN_STATUS_COMPLETED
            run.completed_at = utc_now()
            run.provider_id = getattr(service.model_provider, "provider_id", None)
            run.model_id = getattr(service.model_provider, "model_id", None)
            session.agent_status = "idle"
            session.last_processed_event_id = event.id
            session.agent_error = None
            session.agent_updated_at = utc_now()
            db.commit()
            return True
        except AppError as exc:
            run.status = RUN_STATUS_FAILED
            run.error = f"{exc.code}: {exc.message}"[:500]
            run.completed_at = utc_now()
            session.agent_status = RUN_STATUS_FAILED
            session.agent_error = run.error
            session.agent_updated_at = utc_now()
            db.commit()
            raise
        except Exception as exc:
            message = str(exc)[:500]
            run.status = RUN_STATUS_FAILED
            run.error = message
            run.completed_at = utc_now()
            session.agent_status = RUN_STATUS_FAILED
            session.agent_error = message
            session.agent_updated_at = utc_now()
            db.commit()
            raise


# --------------------------------------------------------------------------- #
# Read / retry / cancel endpoints used by the host UI
# --------------------------------------------------------------------------- #


def _validate_session_token(
    db: Session, session: SubAppSession, token: str
) -> None:
    if session.status != "active":
        raise AppError(
            409,
            "subapp_session_not_active",
            "Sub-application session is not accepting events",
        )
    if not session.current_token_hash or not hmac.compare_digest(
        _token_sha256(token), session.current_token_hash
    ):
        raise AppError(
            401,
            "subapp_token_invalid",
            "Current session capability token is missing, stale, or invalid",
        )


def _run_view(run: SubAppAgentRun | None):
    from app.domain.schemas.subapps import SubAppAgentRunView

    if run is None:
        return None
    return SubAppAgentRunView(
        run_id=run.id,
        session_id=run.session_id,
        event_id=run.event_id,
        status=run.status,
        error=run.error,
        message_id=run.message_id,
        message_version_id=run.message_version_id,
        provider_id=run.provider_id,
        model_id=run.model_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def get_subapp_agent_consent(session_id: str) -> dict[str, Any]:
    from app.domain.schemas.subapps import SubAppAgentConsentView

    with SessionLocal() as db:
        session = _session_for_event(db, session_id)
        if session is None:
            raise AppError(404, "subapp_session_not_found", "Sub-application session not found")
        workspace = db.get(Workspace, session.workspace_id)
        bundle = _bundle_for_session(db, session)
        allowed = bool(
            (workspace is not None and workspace.subapp_agent_consent == "allow")
            or (bundle is not None and bundle.agent_consent_allowlisted)
            or session.agent_consent == "allowed_session"
        )
        pending = db.scalar(
            select(SubAppAgentConsentRequest).where(
                SubAppAgentConsentRequest.workspace_id == session.workspace_id,
                SubAppAgentConsentRequest.session_id == session.id,
                SubAppAgentConsentRequest.status == "pending",
            )
            .order_by(SubAppAgentConsentRequest.created_at.desc())
            .limit(1)
        )
        view = SubAppAgentConsentView(
            mode=workspace.subapp_agent_consent if workspace is not None else "ask",
            allowed=allowed,
            pending_consent_id=pending.id if pending is not None else None,
            triggers=session.agent_triggers or [],
        )
        return view.model_dump()


def get_subapp_agent_task(session_id: str) -> dict[str, Any]:
    from app.domain.schemas.subapps import SubAppAgentTaskStatusView

    with SessionLocal() as db:
        session = _session_for_event(db, session_id)
        if session is None:
            raise AppError(404, "subapp_session_not_found", "Sub-application session not found")
        workspace = db.get(Workspace, session.workspace_id)
        bundle = _bundle_for_session(db, session)
        allowed = bool(
            (workspace is not None and workspace.subapp_agent_consent == "allow")
            or (bundle is not None and bundle.agent_consent_allowlisted)
            or session.agent_consent == "allowed_session"
        )
        pending = db.scalar(
            select(SubAppAgentConsentRequest).where(
                SubAppAgentConsentRequest.workspace_id == session.workspace_id,
                SubAppAgentConsentRequest.session_id == session.id,
                SubAppAgentConsentRequest.status == "pending",
            )
            .order_by(SubAppAgentConsentRequest.created_at.desc())
            .limit(1)
        )
        latest_run = db.scalar(
            select(SubAppAgentRun)
            .where(
                SubAppAgentRun.workspace_id == session.workspace_id,
                SubAppAgentRun.session_id == session.id,
            )
            .order_by(SubAppAgentRun.created_at.desc())
            .limit(1)
        )
        view = SubAppAgentTaskStatusView(
            consent_mode=workspace.subapp_agent_consent if workspace is not None else "ask",
            allowed=allowed,
            pending_consent_id=pending.id if pending is not None else None,
            agent_status=session.agent_status,
            agent_error=session.agent_error,
            latest_run=_run_view(latest_run),
        )
        return view.model_dump()


def retry_subapp_agent_task(
    session_id: str, token: str, actor_id: str
) -> dict[str, Any]:
    from app.domain.schemas.subapps import SubAppAgentTaskRetryView

    with SessionLocal() as db:
        session = _session_for_event(db, session_id)
        if session is None:
            raise AppError(404, "subapp_session_not_found", "Sub-application session not found")
        _validate_session_token(db, session, token)
        run = db.scalar(
            select(SubAppAgentRun)
            .where(
                SubAppAgentRun.workspace_id == session.workspace_id,
                SubAppAgentRun.session_id == session.id,
                SubAppAgentRun.status.in_((RUN_STATUS_FAILED, RUN_STATUS_SKIPPED)),
            )
            .order_by(SubAppAgentRun.created_at.desc())
            .limit(1)
        )
        if run is None:
            return SubAppAgentTaskRetryView(status="idle").model_dump()
        event = _event_for_id(db, run.event_id)
        if event is None:
            raise AppError(404, "subapp_event_not_found", "Sub-application event not found")
        _enqueue_job(db, session=session, event=event, actor_id=actor_id)
        return SubAppAgentTaskRetryView(
            run_id=run.id, status=RUN_STATUS_QUEUED
        ).model_dump()


def cancel_subapp_agent_task(
    session_id: str, token: str, actor_id: str
) -> dict[str, Any]:
    from app.domain.schemas.subapps import SubAppAgentTaskRetryView

    with SessionLocal() as db:
        session = _session_for_event(db, session_id)
        if session is None:
            raise AppError(404, "subapp_session_not_found", "Sub-application session not found")
        _validate_session_token(db, session, token)
        run = db.scalar(
            select(SubAppAgentRun)
            .where(
                SubAppAgentRun.workspace_id == session.workspace_id,
                SubAppAgentRun.session_id == session.id,
                SubAppAgentRun.status == RUN_STATUS_PROCESSING,
            )
            .order_by(SubAppAgentRun.created_at.desc())
            .limit(1)
        )
        if run is not None and run.job_id:
            settings = get_settings()
            DurableQueue(
                db,
                lease_seconds=settings.durable_queue_lease_seconds,
                max_attempts=settings.durable_queue_max_attempts,
            ).cancel(run.job_id, session.workspace_id)
            run.status = RUN_STATUS_SKIPPED
            run.completed_at = utc_now()
        session.agent_status = "idle"
        session.agent_error = None
        session.agent_updated_at = utc_now()
        db.commit()
        return SubAppAgentTaskRetryView(
            run_id=run.id if run is not None else None,
            status="cancelled",
        ).model_dump()
