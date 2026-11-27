"""Server-backed per-session queued turns with atomic claim and durable drain."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.admission import acquire_agent_stream_slot
from app.core.config import get_settings
from app.core.database import SessionLocal, enter_active_stream, exit_active_stream
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.models import ChatSession, DurableJob, Message, User, Workspace
from app.domain.queued_turn_models import QueuedChatTurn
from app.domain.schemas.chat import MessageCreateRequest
from app.repositories.audit import AuditRepository
from app.repositories.domain import SessionRepository
from app.services.authorization import AuthorizationService
from app.services.durable_queue import DurableQueue

logger = logging.getLogger(__name__)

QUEUED_CHAT_TURN_JOB_KIND = "queued_chat_turn.dispatch"
MAX_QUEUED_TURNS_PER_SESSION = 20
BACKGROUND_DRAIN_DELAY_SECONDS = 2.5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _has_inflight_assistant_message(db: Session, workspace_id: str, session_id: str) -> bool:
    return (
        db.scalar(
            select(Message.id)
            .where(
                Message.workspace_id == workspace_id,
                Message.session_id == session_id,
                Message.role == "assistant",
                Message.status.in_(("pending", "streaming")),
            )
            .limit(1)
        )
        is not None
    )


class QueuedTurnService:
    """Manage durable queued turns for a chat session."""

    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.sessions = SessionRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def _require_session(self, session_id: str, *, allow_closed: bool = False) -> ChatSession:
        session = self.sessions.require(session_id, "session")
        if not allow_closed and session.status != "active":
            raise AppError(409, "session_closed", "当前会话已关闭，无法排队发送消息。")
        return session

    def list_turns(self, session_id: str) -> list[QueuedChatTurn]:
        self._require_session(session_id, allow_closed=True)
        return list(
            self.db.scalars(
                select(QueuedChatTurn)
                .where(
                    QueuedChatTurn.workspace_id == self.workspace_id,
                    QueuedChatTurn.session_id == session_id,
                    QueuedChatTurn.status.in_(("queued", "dispatching")),
                )
                .order_by(QueuedChatTurn.position.asc(), QueuedChatTurn.created_at.asc())
            ).all()
        )

    def enqueue_turn(
        self,
        session_id: str,
        *,
        client_request_id: str,
        content: str,
        payload: dict[str, Any] | None = None,
        mode: str = "append",
    ) -> QueuedChatTurn:
        self._require_session(session_id)
        normalized_key = client_request_id.strip()
        if not normalized_key:
            raise AppError(422, "invalid_client_request_id", "client_request_id is required")

        existing = self.db.scalar(
            select(QueuedChatTurn).where(
                QueuedChatTurn.workspace_id == self.workspace_id,
                QueuedChatTurn.session_id == session_id,
                QueuedChatTurn.client_request_id == normalized_key,
            )
        )
        if existing is not None:
            return existing

        active_count = (
            self.db.scalar(
                select(func.count())
                .select_from(QueuedChatTurn)
                .where(
                    QueuedChatTurn.workspace_id == self.workspace_id,
                    QueuedChatTurn.session_id == session_id,
                    QueuedChatTurn.status.in_(("queued", "dispatching")),
                )
            )
            or 0
        )
        if active_count >= MAX_QUEUED_TURNS_PER_SESSION:
            raise AppError(
                429,
                "queued_turn_limit_reached",
                f"当前会话最多排队 {MAX_QUEUED_TURNS_PER_SESSION} 条待发消息。",
            )

        if mode == "interrupt":
            min_pos = self.db.scalar(
                select(func.min(QueuedChatTurn.position)).where(
                    QueuedChatTurn.workspace_id == self.workspace_id,
                    QueuedChatTurn.session_id == session_id,
                    QueuedChatTurn.status == "queued",
                )
            )
            position = (min_pos - 1) if min_pos is not None else 0
        else:
            max_pos = self.db.scalar(
                select(func.max(QueuedChatTurn.position)).where(
                    QueuedChatTurn.workspace_id == self.workspace_id,
                    QueuedChatTurn.session_id == session_id,
                )
            )
            position = (max_pos + 1) if max_pos is not None else 0

        now = _utc_now()
        turn = QueuedChatTurn(
            id=str(uuid4()),
            workspace_id=self.workspace_id,
            session_id=session_id,
            created_by=self.actor_id,
            client_request_id=normalized_key,
            position=position,
            status="queued",
            content=content,
            payload_json=dict(payload or {}),
            created_at=now,
            updated_at=now,
        )
        self.db.add(turn)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            replayed = self.db.scalar(
                select(QueuedChatTurn).where(
                    QueuedChatTurn.workspace_id == self.workspace_id,
                    QueuedChatTurn.session_id == session_id,
                    QueuedChatTurn.client_request_id == normalized_key,
                )
            )
            if replayed is not None:
                return replayed
            raise
        self.audit.record(
            actor_id=self.actor_id,
            action="chat.queued_turn.enqueued",
            resource_type="chat_session",
            resource_id=session_id,
            details={"turn_id": turn.id, "mode": mode},
        )
        self.db.commit()
        self.db.refresh(turn)
        if not _has_inflight_assistant_message(self.db, self.workspace_id, session_id):
            schedule_queued_turn_drain(self.db, self.workspace_id, session_id)
        return turn

    def update_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        content: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> QueuedChatTurn:
        self._require_session(session_id)
        turn = self._require_turn(session_id, turn_id)
        if turn.status != "queued":
            raise AppError(409, "queued_turn_not_editable", "该待发消息已在发送中或已结束，无法编辑。")
        if content is not None:
            turn.content = content
        if payload is not None:
            turn.payload_json = dict(payload)
        turn.updated_at = _utc_now()
        self.db.commit()
        self.db.refresh(turn)
        return turn

    def cancel_turn(self, session_id: str, turn_id: str) -> QueuedChatTurn:
        self._require_session(session_id, allow_closed=True)
        turn = self._require_turn(session_id, turn_id)
        if turn.status in {"cancelled", "dispatched"}:
            return turn
        turn.status = "cancelled"
        turn.updated_at = _utc_now()
        self.audit.record(
            actor_id=self.actor_id,
            action="chat.queued_turn.cancelled",
            resource_type="chat_session",
            resource_id=session_id,
            details={"turn_id": turn.id},
        )
        self.db.commit()
        self.db.refresh(turn)
        return turn

    def promote_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        cancel_active_fn: Any | None = None,
    ) -> tuple[QueuedChatTurn, str | None]:
        self._require_session(session_id)
        turn = self._require_turn(session_id, turn_id)
        if turn.status != "queued":
            raise AppError(409, "queued_turn_not_promotable", "该待发消息不在排队状态。")
        min_pos = self.db.scalar(
            select(func.min(QueuedChatTurn.position)).where(
                QueuedChatTurn.workspace_id == self.workspace_id,
                QueuedChatTurn.session_id == session_id,
                QueuedChatTurn.status == "queued",
            )
        )
        turn.position = (min_pos - 1) if min_pos is not None else 0
        turn.updated_at = _utc_now()
        self.db.commit()
        self.db.refresh(turn)

        cancelled_message_id: str | None = None
        if callable(cancel_active_fn):
            active_msg_id = self.db.scalar(
                select(Message.id)
                .where(
                    Message.workspace_id == self.workspace_id,
                    Message.session_id == session_id,
                    Message.role == "assistant",
                    Message.status.in_(("pending", "streaming")),
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            if active_msg_id:
                cancel_active_fn(session_id, active_msg_id)
                cancelled_message_id = active_msg_id
        return turn, cancelled_message_id

    def claim_turn(
        self,
        session_id: str,
        turn_id: str | None = None,
    ) -> QueuedChatTurn | None:
        """Atomically transition one queued turn to ``dispatching`` (CAS)."""
        self._require_session(session_id)
        if turn_id is None:
            candidate = self.db.scalar(
                select(QueuedChatTurn)
                .where(
                    QueuedChatTurn.workspace_id == self.workspace_id,
                    QueuedChatTurn.session_id == session_id,
                    QueuedChatTurn.status == "queued",
                )
                .order_by(QueuedChatTurn.position.asc(), QueuedChatTurn.created_at.asc())
                .limit(1)
            )
            if candidate is None:
                return None
            turn_id = candidate.id

        now = _utc_now()
        updated = self.db.execute(
            update(QueuedChatTurn)
            .where(
                QueuedChatTurn.id == turn_id,
                QueuedChatTurn.workspace_id == self.workspace_id,
                QueuedChatTurn.session_id == session_id,
                QueuedChatTurn.status == "queued",
            )
            .values(status="dispatching", updated_at=now)
        )
        if updated.rowcount != 1:
            self.db.rollback()
            return None
        self.db.commit()
        return self.db.get(QueuedChatTurn, turn_id)

    def mark_dispatched(
        self,
        session_id: str,
        turn_id: str,
        *,
        dispatched_message_id: str | None = None,
    ) -> QueuedChatTurn:
        self._require_session(session_id, allow_closed=True)
        turn = self._require_turn(session_id, turn_id)
        if turn.status != "dispatched":
            turn.status = "dispatched"
            if dispatched_message_id:
                turn.dispatched_message_id = dispatched_message_id
            turn.updated_at = _utc_now()
            self.db.commit()
            self.db.refresh(turn)
        return turn

    def requeue_turn(self, session_id: str, turn_id: str) -> QueuedChatTurn:
        self._require_session(session_id, allow_closed=True)
        turn = self._require_turn(session_id, turn_id)
        if turn.status == "dispatching":
            turn.status = "queued"
            turn.updated_at = _utc_now()
            self.db.commit()
            self.db.refresh(turn)
        return turn

    def _require_turn(self, session_id: str, turn_id: str) -> QueuedChatTurn:
        turn = self.db.scalar(
            select(QueuedChatTurn).where(
                QueuedChatTurn.id == turn_id,
                QueuedChatTurn.workspace_id == self.workspace_id,
                QueuedChatTurn.session_id == session_id,
            )
        )
        if turn is None:
            raise AppError(404, "queued_turn_not_found", "Queued turn was not found")
        return turn


def schedule_queued_turn_drain(
    db: Session,
    workspace_id: str,
    session_id: str,
    *,
    delay_seconds: float = BACKGROUND_DRAIN_DELAY_SECONDS,
) -> bool:
    """Enqueue a deduplicated durable job for the next queued turn on this session."""
    next_turn_id = db.scalar(
        select(QueuedChatTurn.id)
        .where(
            QueuedChatTurn.workspace_id == workspace_id,
            QueuedChatTurn.session_id == session_id,
            QueuedChatTurn.status == "queued",
        )
        .order_by(QueuedChatTurn.position.asc(), QueuedChatTurn.created_at.asc())
        .limit(1)
    )
    if next_turn_id is None:
        return False
    queue = DurableQueue(db, worker_id="chat-queued-turn-scheduler")
    queue.enqueue(
        workspace_id=workspace_id,
        kind=QUEUED_CHAT_TURN_JOB_KIND,
        payload={"session_id": session_id, "turn_id": next_turn_id},
        dedupe_key=f"queued-turn-drain:{next_turn_id}",
        available_at=_utc_now() + timedelta(seconds=max(0.0, delay_seconds)),
        max_attempts=3,
    )
    return True


def _actor_context_for_turn(db: Session, turn: QueuedChatTurn) -> WorkspaceContext:
    user = db.get(User, turn.created_by)
    workspace = db.get(Workspace, turn.workspace_id)
    if user is None or workspace is None:
        raise AppError(404, "workspace_not_found", "Workspace or user not found")
    principal = Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        workspace_id=workspace.id,
    )
    role = AuthorizationService(db, workspace.id).membership_role(user.id) or "member"
    return WorkspaceContext(
        principal=principal,
        workspace=workspace,
        membership_role=role,
    )


def run_queued_turn_dispatch(job: DurableJob, worker_id: str) -> None:
    """Durable worker entry: atomically claim and run the next queued turn if no client did."""
    settings = get_settings()
    session_id = str(job.payload["session_id"])
    with SessionLocal() as db:
        queue = DurableQueue(db, worker_id=worker_id)
        session = db.get(ChatSession, session_id)
        if session is None or session.workspace_id != job.workspace_id or session.status != "active":
            queue.complete(job)
            return
        if _has_inflight_assistant_message(db, job.workspace_id, session_id):
            queue.rearm(job, delay_seconds=3.0)
            return

        svc = QueuedTurnService(db, job.workspace_id, "system:queued-turn-worker")
        turn = svc.claim_turn(session_id)
        if turn is None:
            queue.complete(job)
            return

        ticket = acquire_agent_stream_slot()
        if ticket is None:
            svc.requeue_turn(session_id, turn.id)
            queue.rearm(job, delay_seconds=3.0)
            return

        enter_active_stream()
        try:
            from app.services.chat_service_factory import build_chat_service

            context = _actor_context_for_turn(db, turn)
            raw_payload = dict(turn.payload_json or {})
            request_body = MessageCreateRequest(
                content=turn.content,
                provider_id=raw_payload.get("provider_id"),
                model_id=raw_payload.get("model_id"),
                thinking_mode=raw_payload.get("thinking_mode"),
                agent_mode=bool(raw_payload.get("agent_mode", False)),
                search_route=raw_payload.get("search_route"),
                web_search=bool(raw_payload.get("web_search", False)),
                allowed_domains=list(raw_payload.get("allowed_domains") or []),
                file_ids=list(raw_payload.get("file_ids") or []),
                sticker_ids=list(raw_payload.get("sticker_ids") or []),
                client_message_id=turn.client_request_id,
            )
            chat_svc = build_chat_service(
                db,
                workspace_context=context,
                settings=settings,
                model_id=request_body.model_id,
                provider_id=request_body.provider_id,
                thinking_mode=request_body.thinking_mode,
                search_route=request_body.search_route,
            )
            for _chunk in chat_svc.create_stream(
                session_id,
                request_body,
                idempotency_key=f"queued-turn:{turn.id}",
            ):
                pass
            svc.mark_dispatched(session_id, turn.id)
            # If more turns remain queued, schedule the next drain.
            schedule_queued_turn_drain(db, job.workspace_id, session_id)
            queue.complete(job)
        except Exception as exc:
            logger.warning("Background queued turn dispatch failed for %s: %s", turn.id, exc)
            db.rollback()
            svc.requeue_turn(session_id, turn.id)
            queue.fail(job, type(exc).__name__, error_code="queued_turn_dispatch_failed", retryable=False)
        finally:
            exit_active_stream()
            ticket.release()
