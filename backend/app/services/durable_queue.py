from __future__ import annotations

import asyncio

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.config import get_settings
from app.domain.models import DurableJob


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DurableQueue:
    """Database-backed queue with lease-token fencing for every state change.

    Handlers remain in a closed application registry; payloads are data only and
    never identify a Python callable or import path supplied by a request.
    """

    def __init__(
        self,
        db: Session,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        max_attempts: int = 3,
    ) -> None:
        self.db = db
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.lease_seconds = max(1, lease_seconds)
        self.max_attempts = max(1, max_attempts)
        # Best-effort per-worker round-robin fairness: the workspace claimed
        # last is de-prioritized on the next claim so one busy tenant cannot
        # monopolize a worker while another tenant has ready work.
        self._last_claimed_workspace: str | None = None

    def enqueue(
        self,
        *,
        workspace_id: str,
        kind: str,
        payload: dict[str, Any],
        dedupe_key: str | None = None,
        priority: int = 0,
        max_attempts: int | None = None,
        available_at: datetime | None = None,
    ) -> DurableJob:
        if dedupe_key:
            existing = self.db.scalar(
                select(DurableJob).where(
                    DurableJob.workspace_id == workspace_id,
                    DurableJob.dedupe_key == dedupe_key,
                )
            )
            if existing is not None:
                if existing.kind != kind or existing.payload != payload:
                    raise ValueError("Durable queue dedupe key was reused with different work")
                return existing
        job = DurableJob(
            workspace_id=workspace_id,
            kind=kind,
            payload=payload,
            dedupe_key=dedupe_key,
            priority=priority,
            max_attempts=max_attempts or self.max_attempts,
            **({"available_at": available_at} if available_at is not None else {}),
        )
        self.db.add(job)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            if not dedupe_key:
                raise
            existing = self.db.scalar(
                select(DurableJob).where(
                    DurableJob.workspace_id == workspace_id,
                    DurableJob.dedupe_key == dedupe_key,
                )
            )
            if existing is None or existing.kind != kind or existing.payload != payload:
                raise
            return existing
        self.db.refresh(job)
        return job

    def claim_next(self, *, now: datetime | None = None, kind: str | None = None) -> DurableJob | None:
        now = now or utc_now()
        eligible = or_(
            (DurableJob.status == "queued") & (DurableJob.available_at <= now),
            (DurableJob.status == "leased") & (DurableJob.lease_expires_at <= now),
        )
        statement = select(DurableJob.id).where(eligible)
        if kind:
            statement = statement.where(DurableJob.kind == kind)
        if self._last_claimed_workspace is not None:
            # Tenant fairness: false (0) sorts before true (1) in SQLite, so a
            # workspace that was just served is moved behind other workspaces.
            statement = statement.order_by(
                (DurableJob.workspace_id == self._last_claimed_workspace).asc(),
                DurableJob.priority.desc(),
                DurableJob.available_at,
                DurableJob.created_at,
            )
        else:
            statement = statement.order_by(
                DurableJob.priority.desc(),
                DurableJob.available_at,
                DurableJob.created_at,
            )
        candidate_id = self.db.scalar(statement.limit(1))
        if candidate_id is None:
            return None
        self._last_claimed_workspace = self.db.scalar(
            select(DurableJob.workspace_id).where(DurableJob.id == candidate_id)
        )
        token = str(uuid4())
        result = self.db.execute(
            update(DurableJob)
            .where(DurableJob.id == candidate_id, eligible)
            .values(
                status="leased",
                lease_owner=self.worker_id,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                attempt_count=DurableJob.attempt_count + 1,
                started_at=func.coalesce(DurableJob.started_at, now),
                last_error=None,
            )
        )
        if result.rowcount != 1:
            self.db.rollback()
            return None
        self.db.commit()
        return self.db.scalar(select(DurableJob).where(DurableJob.id == candidate_id))

    def complete(self, job: DurableJob, *, now: datetime | None = None) -> bool:
        result = self.db.execute(
            update(DurableJob)
            .where(
                DurableJob.id == job.id,
                DurableJob.status == "leased",
                DurableJob.lease_owner == self.worker_id,
                DurableJob.lease_token == job.lease_token,
            )
            .values(status="completed", completed_at=now or utc_now(), lease_expires_at=None)
        )
        self.db.commit()
        return result.rowcount == 1

    def fail(self, job: DurableJob, error: str, *, now: datetime | None = None) -> bool:
        now = now or utc_now()
        terminal = job.attempt_count >= job.max_attempts
        values: dict[str, Any] = {
            "status": "failed" if terminal else "queued",
            "last_error": error[:500],
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "completed_at": now if terminal else None,
        }
        if not terminal:
            values["available_at"] = now + timedelta(seconds=min(60, 2 ** max(0, job.attempt_count - 1)))
        result = self.db.execute(
            update(DurableJob)
            .where(
                DurableJob.id == job.id,
                DurableJob.status == "leased",
                DurableJob.lease_owner == self.worker_id,
                DurableJob.lease_token == job.lease_token,
            )
            .values(**values)
        )
        self.db.commit()
        return result.rowcount == 1

    def cancel(self, job_id: str, workspace_id: str) -> bool:
        result = self.db.execute(
            update(DurableJob)
            .where(
                DurableJob.id == job_id,
                DurableJob.workspace_id == workspace_id,
                DurableJob.status.in_(("queued", "leased")),
            )
            .values(status="cancelled", completed_at=utc_now(), lease_expires_at=None)
        )
        self.db.commit()
        return result.rowcount == 1

    def rearm(
        self,
        job: DurableJob,
        *,
        now: datetime | None = None,
        delay_seconds: float = 1.0,
    ) -> bool:
        """Requeue a leased job without consuming its retry budget.

        Used for continuous work: the handler is
        lease-fenced and re-arms the same row so polling survives process
        restarts (the row is again eligible after ``available_at``) while the
        worker is free to serve other tenants between polls.
        """
        now = now or utc_now()
        result = self.db.execute(
            update(DurableJob)
            .where(
                DurableJob.id == job.id,
                DurableJob.status == "leased",
                DurableJob.lease_owner == self.worker_id,
                DurableJob.lease_token == job.lease_token,
            )
            .values(
                status="queued",
                attempt_count=0,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                available_at=now + timedelta(seconds=max(0.01, delay_seconds)),
            )
        )
        self.db.commit()
        return result.rowcount == 1


def enqueue_document_job(workspace_id: str, document_job_id: str, execution_token: str) -> DurableJob:
    """Persist one dispatch generation for a document job before returning HTTP 202."""

    settings = get_settings()
    with SessionLocal() as db:
        return DurableQueue(
            db,
            lease_seconds=settings.durable_queue_lease_seconds,
            max_attempts=settings.durable_queue_max_attempts,
        ).enqueue(
            workspace_id=workspace_id,
            kind="document.parse_index",
            payload={"document_job_id": document_job_id, "execution_token": execution_token},
            dedupe_key=f"document.parse_index:{document_job_id}:{execution_token}",
        )




def enqueue_memory_extraction(
    workspace_id: str,
    session_id: str,
    actor_id: str,
    last_message_id: str,
) -> DurableJob | None:
    """Debounce all new turns behind one persisted extraction cursor.

    Only queued work is rescheduled. A leased generation is fenced and never
    overwritten; its worker schedules any unseen remainder after completion.
    """

    settings = get_settings()
    with SessionLocal() as db:
        from app.domain.models import ChatSession, MemoryExtractionState, MemoryJobState
        from app.services.memory_job_guard import job_id_for
        from app.services.memory_enhancement import _new_messages_since, _workspace_memory_enabled
        from app.services.memory_batching import extraction_readiness
        session = db.scalar(select(ChatSession).where(ChatSession.id == session_id, ChatSession.workspace_id == workspace_id))
        if session is None or not session.memory_enabled or not session.memory_learning_enabled or not _workspace_memory_enabled(db, workspace_id):
            return None
        state = db.scalar(select(MemoryExtractionState).where(MemoryExtractionState.workspace_id == workspace_id, MemoryExtractionState.session_id == session_id))
        pending = _new_messages_since(db, workspace_id, session_id, state)
        if not pending:
            return None
        readiness = extraction_readiness(pending, session, settings)
        available = utc_now() + timedelta(seconds=readiness.retry_after_seconds)
        key = f"memory.extract.batch:{session_id}:{getattr(state, 'last_message_id', None) or 'start'}"
        progress = db.get(MemoryJobState, job_id_for(workspace_id, "extraction", session_id))
        checkpoint = dict(progress.checkpoint or {}) if progress else {}
        if checkpoint.get("message_id"):
            # A long message can contain several chunks before the full-message
            # cursor changes; a completed old queue key must not swallow them.
            key += f":{checkpoint['message_id']}:{checkpoint.get('content_hash', '')}:{checkpoint.get('offset', 0)}"
            available = utc_now()
        existing = db.scalar(select(DurableJob).where(DurableJob.workspace_id == workspace_id, DurableJob.dedupe_key == key))
        if existing is not None:
            db.execute(update(DurableJob).where(DurableJob.id == existing.id, DurableJob.status == "queued").values(available_at=available))
            db.commit()
            db.refresh(existing)
            return existing
        return DurableQueue(
            db,
            lease_seconds=settings.durable_queue_lease_seconds,
            max_attempts=settings.durable_queue_max_attempts,
        ).enqueue(
            workspace_id=workspace_id,
            kind="memory.extract",
            payload={
                "workspace_id": workspace_id,
                "session_id": session_id,
                "actor_id": actor_id,
                "batch_anchor": getattr(state, "last_message_id", None),
            },
            dedupe_key=key,
            available_at=available,
        )




def run_one_durable_job(worker_id: str) -> bool:
    """Claim and execute a single closed-registry queue job in a worker thread."""

    settings = get_settings()
    with SessionLocal() as db:
        queue = DurableQueue(
            db,
            worker_id=worker_id,
            lease_seconds=settings.durable_queue_lease_seconds,
            max_attempts=settings.durable_queue_max_attempts,
        )
        job = queue.claim_next()
        if job is None:
            return False
        try:
            if job.kind == "learning.package.build":
                from app.services.learning_packages import run_build_stage, learning_lease_heartbeat
                with learning_lease_heartbeat(job.id, job.lease_token):
                    terminal = run_build_stage(job.id, job.lease_token, str(job.payload["build_id"]))
                if terminal:
                    queue.complete(job)
                else:
                    queue.rearm(job, delay_seconds=0.1)
            elif job.kind == "graph.cover.ai":
                from app.services.graph_cover_ai import run_cover_ai_job

                run_cover_ai_job(
                    job.id, job.lease_token, str(job.payload["cover_job_id"])
                )
                queue.complete(job)
            elif job.kind == "learning.package.grade":
                from app.services.learning_packages import grade_attempt, learning_lease_heartbeat
                with learning_lease_heartbeat(job.id, job.lease_token):
                    grade_attempt(job.id, job.lease_token, str(job.payload["attempt_id"]))
                queue.complete(job)
            elif job.kind == "document.parse_index":
                from app.services.document_learning import run_document_job

                run_document_job(
                    str(job.payload["document_job_id"]),
                    str(job.payload["execution_token"]),
                )
                queue.complete(job)
            elif job.kind == "chat.continue_stream":
                from app.services.chat_durable import run_chat_continue_once

                run_chat_continue_once(job.payload)
                queue.complete(job)
            elif job.kind == "sticker.semantic":
                from app.services.sticker_pipeline import run_sticker_semantics

                db.expunge(job)
                run_sticker_semantics(db, job)
                queue.complete(job)
            elif job.kind == "sticker.index":
                from app.services.stickers import run_sticker_index_batch

                db.expunge(job)
                if run_sticker_index_batch(db, job):
                    queue.complete(job)
                else:
                    queue.rearm(job, delay_seconds=0.1)
            elif job.kind == "memory.extract":
                from app.domain.models import Workspace
                from app.services.memory_enhancement import extract_session_memories

                workspace = db.get(Workspace, str(job.payload["workspace_id"]))
                result = {"status": "workspace_deleted"}
                if workspace is not None:
                    result = extract_session_memories(
                        db,
                        workspace,
                        str(job.payload["session_id"]),
                        get_settings(),
                        actor_id=str(
                            job.payload.get("actor_id")
                            or "system:memory-extraction"
                        ),
                    )
                if result.get("status") in {"batch_deferred", "busy", "partial", "retry_backoff"}:
                    queue.rearm(job, delay_seconds=float(result.get("retry_after_seconds") or 30))
                else:
                    queue.complete(job)
                    if result.get("status") == "ok":
                        enqueue_memory_extraction(str(job.payload["workspace_id"]), str(job.payload["session_id"]), str(job.payload.get("actor_id") or "system:memory-extraction"), "")
            elif job.kind == "subapp.event.process":
                from app.services.subapp_event_agent import (
                    run_subapp_event_agent_once,
                )

                terminal = run_subapp_event_agent_once(job.payload)
                if terminal:
                    queue.complete(job)
                else:
                    queue.rearm(
                        job,
                        delay_seconds=settings.subapp_event_agent_idle_seconds,
                    )
            else:
                raise ValueError(f"Unsupported durable job kind: {job.kind}")
        except Exception as error:
            queue.fail(job, str(error))
            return True
        return True




async def durable_queue_worker(stop: asyncio.Event, worker_id: str) -> None:
    """Poll the durable queue without blocking FastAPI's event loop."""

    settings = get_settings()
    import time
    next_learning_sweep = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_learning_sweep:
            from app.services.learning_packages import reconcile_learning_builds
            import logging
            try:
                await asyncio.to_thread(reconcile_learning_builds)
                from app.services.stickers import reconcile_sticker_indexes
                await asyncio.to_thread(reconcile_sticker_indexes)
            except Exception:
                logging.getLogger(__name__).exception("Learning package reconciliation failed")
            next_learning_sweep = time.monotonic() + 15
        claimed = await asyncio.to_thread(run_one_durable_job, worker_id)
        if claimed:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.durable_queue_poll_seconds)
        except TimeoutError:
            pass
