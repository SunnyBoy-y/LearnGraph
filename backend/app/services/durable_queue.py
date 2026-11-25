from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import DisconnectionError, IntegrityError, OperationalError, TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.domain.models import DurableJob
from app.domain.task_reliability_models import DurableJobAttempt

logger = logging.getLogger(__name__)
_database_faults = (DisconnectionError, OperationalError, PoolTimeout)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


CHAT_GOAL_JOB_KIND = "chat.goal_round"


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
        commit: bool = True,
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
        if not commit:
            self.db.flush()
            return job
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

    def claim_next(
        self, *, now: datetime | None = None, kind: str | None = None,
        exclude_kind: str | None = None,
    ) -> DurableJob | None:
        now = now or utc_now()
        eligible = or_(
            (DurableJob.status == "queued") & (DurableJob.available_at <= now),
            (DurableJob.status == "leased") & (DurableJob.lease_expires_at <= now),
        )
        ready_at = func.coalesce(DurableJob.lease_expires_at, DurableJob.available_at)
        statement = select(
            DurableJob.id, DurableJob.workspace_id, DurableJob.kind, DurableJob.status,
            DurableJob.lease_token, DurableJob.attempt_count, DurableJob.max_attempts,
        ).where(eligible)
        if kind:
            statement = statement.where(DurableJob.kind == kind)
        if exclude_kind:
            statement = statement.where(DurableJob.kind != exclude_kind)
        if self._last_claimed_workspace is not None:
            statement = statement.order_by(DurableJob.workspace_id == self._last_claimed_workspace)
        statement = statement.order_by(DurableJob.priority.desc(), ready_at, DurableJob.created_at)
        for _ in range(16):
            candidate = self.db.execute(statement.limit(1)).first()
            if candidate is None:
                self.db.rollback()
                return None
            exhausted = candidate.attempt_count >= candidate.max_attempts
            token = str(uuid4())
            values = (
                dict(status="failed", completed_at=now, lease_owner=None, lease_token=None,
                     lease_expires_at=None, last_error="attempt_budget_exhausted")
                if exhausted else
                dict(status="leased", lease_owner=self.worker_id, lease_token=token,
                     lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                     attempt_count=DurableJob.attempt_count + 1,
                     started_at=func.coalesce(DurableJob.started_at, now), last_error=None)
            )
            result = self.db.execute(update(DurableJob).where(
                DurableJob.id == candidate.id, eligible, DurableJob.status == candidate.status,
                DurableJob.lease_token == candidate.lease_token,
                DurableJob.attempt_count == candidate.attempt_count,
                (DurableJob.attempt_count >= DurableJob.max_attempts) if exhausted else
                (DurableJob.attempt_count < DurableJob.max_attempts),
            ).values(**values).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                self.db.rollback()
                continue
            if candidate.lease_token:
                self._end_attempt(candidate.lease_token, "lease_expired", now, "lease_expired")
            if not exhausted:
                self.db.add(DurableJobAttempt(
                    lease_token=token, job_id=candidate.id, workspace_id=candidate.workspace_id,
                    kind=candidate.kind, attempt_number=candidate.attempt_count + 1, started_at=now,
                ))
            self.db.commit()
            if not exhausted:
                self._last_claimed_workspace = candidate.workspace_id
                return self.db.scalar(select(DurableJob).where(
                    DurableJob.id == candidate.id, DurableJob.lease_token == token,
                ).execution_options(populate_existing=True))
        return None

    def _end_attempt(self, token: str, outcome: str, now: datetime, error_code: str | None = None) -> None:
        self.db.execute(update(DurableJobAttempt).where(
            DurableJobAttempt.lease_token == token, DurableJobAttempt.finished_at.is_(None),
        ).values(outcome=outcome, finished_at=now, error_code=error_code).execution_options(synchronize_session=False))

    def renew(self, job_id: str, token: str, *, now: datetime | None = None) -> bool:
        now = now or utc_now()
        result = self.db.execute(update(DurableJob).where(
            DurableJob.id == job_id, DurableJob.status == "leased", DurableJob.lease_owner == self.worker_id,
            DurableJob.lease_token == token, DurableJob.lease_expires_at > now,
        ).values(lease_expires_at=now + timedelta(seconds=self.lease_seconds)).execution_options(synchronize_session=False))
        self.db.commit()
        return result.rowcount == 1

    def _settle(self, job: DurableJob, now: datetime, outcome: str, *, values: dict,
                error_code: str | None = None) -> bool:
        token = job.lease_token
        result = self.db.execute(update(DurableJob).where(
            DurableJob.id == job.id, DurableJob.status == "leased",
            DurableJob.lease_owner == self.worker_id, DurableJob.lease_token == token,
            DurableJob.lease_expires_at > now,
        ).values(**values, lease_owner=None, lease_token=None, lease_expires_at=None)
          .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            self.db.rollback()
            return False
        self._end_attempt(token, outcome, now, error_code)
        self.db.commit()
        return True

    def complete(self, job: DurableJob, *, now: datetime | None = None) -> bool:
        now = now or utc_now()
        return self._settle(job, now, "succeeded", values={"status": "completed", "completed_at": now})

    def fail(self, job: DurableJob, error: str, *, now: datetime | None = None,
             error_code: str = "handler_error", retryable: bool = True) -> bool:
        now = now or utc_now()
        terminal = not retryable or job.attempt_count >= job.max_attempts
        values = {"status": "failed" if terminal else "queued", "last_error": error[:500],
                  "completed_at": now if terminal else None}
        if not terminal:
            delay = min(60, 2 ** min(6, max(0, job.attempt_count - 1)))
            values["available_at"] = now + timedelta(seconds=delay * random.uniform(0.8, 1.0))
        return self._settle(job, now, "failed" if terminal else "retryable_failure",
                            values=values, error_code=error_code[:100])

    def cancel_claimed(self, job: DurableJob) -> bool:
        now = utc_now()
        return self._settle(job, now, "cancelled", values={"status": "cancelled", "completed_at": now})

    def cancel(self, job_id: str, workspace_id: str) -> bool:
        now = utc_now()
        result = self.db.execute(update(DurableJob).where(
            DurableJob.id == job_id, DurableJob.workspace_id == workspace_id,
            DurableJob.status.in_(("queued", "leased")),
        ).values(status="cancelled", completed_at=now, lease_owner=None, lease_token=None,
                 lease_expires_at=None).execution_options(synchronize_session=False))
        if result.rowcount == 1:
            self.db.execute(update(DurableJobAttempt).where(
                DurableJobAttempt.job_id == job_id, DurableJobAttempt.finished_at.is_(None),
            ).values(outcome="cancelled", finished_at=now).execution_options(synchronize_session=False))
        self.db.commit()
        return result.rowcount == 1

    def rearm(self, job: DurableJob, *, now: datetime | None = None, delay_seconds: float = 1.0) -> bool:
        """Defer or start a fresh unit of continuous work without charging its retry budget."""
        now = now or utc_now()
        return self._settle(job, now, "deferred", values={
            "status": "queued", "attempt_count": 0,
            "available_at": now + timedelta(seconds=max(0.01, delay_seconds)),
        })


def document_dispatch_key(document_job_id: str, execution_token: str) -> str:
    return f"document.parse_index:{document_job_id}:{execution_token}"


def enqueue_document_job(db: Session, workspace_id: str, document_job_id: str, execution_token: str,
                         *, commit: bool = False) -> DurableJob:
    """Stage the dispatch in the document state transaction."""
    settings = get_settings()
    return DurableQueue(db, max_attempts=settings.durable_queue_max_attempts).enqueue(
        workspace_id=workspace_id,
        kind="document.parse_index",
        payload={"document_job_id": document_job_id, "execution_token": execution_token},
        dedupe_key=document_dispatch_key(document_job_id, execution_token),
        commit=commit,
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




def run_one_durable_job(
    worker_id: str, *, kind: str | None = None, exclude_kind: str | None = None,
    on_poll: Callable[[], None] | None = None, on_heartbeat: Callable[[], None] | None = None,
    fairness: dict[str, str | None] | None = None,
) -> bool:
    """Claim and execute a single closed-registry queue job in a worker thread."""
    settings = get_settings()
    with SessionLocal() as db:
        queue = DurableQueue(db, worker_id=worker_id, lease_seconds=settings.durable_queue_lease_seconds)
        if fairness is not None:
            queue._last_claimed_workspace = fairness.get("workspace")
        job = queue.claim_next(kind=kind, exclude_kind=exclude_kind)
        if on_poll is not None:
            on_poll()
        if job is None:
            return False
        if fairness is not None:
            fairness["workspace"] = job.workspace_id
        db.expunge(job)
        db.rollback()  # Don't leave the claim read transaction open across the handler.
        _execute_claimed_job(queue, job, settings, on_heartbeat)
        return True


@contextmanager
def _lease_heartbeat(queue: DurableQueue, job: DurableJob, on_heartbeat: Callable[[], None] | None):
    stop = threading.Event()
    interval = min(15.0, queue.lease_seconds / 3)
    job_id, token = job.id, job.lease_token

    def renew_until_done():
        while not stop.wait(interval):
            try:
                with SessionLocal() as db:
                    heartbeat = DurableQueue(db, worker_id=queue.worker_id, lease_seconds=queue.lease_seconds)
                    if not heartbeat.renew(job_id, token):
                        return
                if on_heartbeat is not None:
                    on_heartbeat()
            except Exception as error:
                logger.warning("Durable lease renewal failed (%s)", type(error).__name__)

    thread = threading.Thread(target=renew_until_done, name="durable-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=min(1.0, interval))


def _execute_claimed_job(queue, job, settings, on_heartbeat):
    db = queue.db
    with _lease_heartbeat(queue, job, on_heartbeat):
        try:
            if job.kind in {"learning.package.build", "learning.package.grade"}:
                # Retired feature: drain old work without provider calls or deleting history.
                queue.cancel_claimed(job)
            elif job.kind == "graph.cover.ai":
                from app.services.graph_cover_ai import run_cover_ai_job

                run_cover_ai_job(
                    job.id, job.lease_token, str(job.payload["cover_job_id"])
                )
                queue.complete(job)
            elif job.kind == "document.parse_index":
                from app.services.document_learning import run_document_job

                status, code = run_document_job(
                    str(job.payload["document_job_id"]),
                    str(job.payload["execution_token"]),
                    lease=(job.id, job.lease_token),
                )
                if status == "completed":
                    queue.complete(job)
                elif status == "cancelled":
                    queue.cancel_claimed(job)
                else:
                    code = code or "document_execution_incomplete"
                    queue.fail(job, code, error_code=code, retryable=status == "queued")
            elif job.kind == CHAT_GOAL_JOB_KIND:
                from app.services.chat_goals import run_goal_round

                run_goal_round(job, queue.worker_id)
            elif job.kind == "chat.continue_stream":
                from app.services.chat_durable import run_chat_continue_once

                run_chat_continue_once(job.payload)
                queue.complete(job)
            elif job.kind in {"im.process", "im.send"}:
                from app.services.im_channels import deliver, process

                row_id = str(job.payload["id"])
                if job.kind == "im.send":
                    deliver(db, row_id)
                    queue.complete(job)
                elif process(db, settings, row_id):
                    queue.complete(job)
                else:
                    queue.rearm(job, delay_seconds=2)
            elif job.kind == "sticker.semantic":
                from app.services.sticker_pipeline import run_sticker_semantics

                run_sticker_semantics(db, job)
                queue.complete(job)
            elif job.kind == "sticker.index":
                from app.services.stickers import run_sticker_index_batch

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
                queue.fail(job, "Unsupported durable job kind", error_code="unsupported_job_kind", retryable=False)
        except Exception as error:
            db.rollback()
            if isinstance(error, AppError):
                code = error.code
                retryable = error.status_code in {408, 429, 500, 502, 503, 504}
            else:
                code = type(error).__name__
                retryable = not isinstance(error, (TypeError, ValueError, KeyError))
            logger.warning("Durable job %s (%s) failed [%s]", job.id, job.kind, code)
            queue.fail(job, code, error_code=code, retryable=retryable)


async def _wait_for_poll(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass


def _backoff(failures: int, base: float) -> float:
    return min(30, max(0.01, base) * 2 ** min(10, failures - 1)) * random.uniform(0.75, 1)


def _prune_attempt_history() -> None:
    cutoff = utc_now() - timedelta(days=31)
    with SessionLocal() as db:
        old_tokens = select(DurableJobAttempt.lease_token).where(
            DurableJobAttempt.finished_at < cutoff,
        ).order_by(DurableJobAttempt.finished_at).limit(5000)
        db.execute(delete(DurableJobAttempt).where(DurableJobAttempt.lease_token.in_(old_tokens))
                   .execution_options(synchronize_session=False))
        db.commit()


async def durable_queue_worker(stop: asyncio.Event, worker_id: str, *, goal_lane: bool | None = None) -> None:
    """Poll one lane and tolerate transient database outages."""
    from app.services.task_reliability import worker_update

    settings = get_settings()
    lane = "goal" if goal_lane is True else "general"
    next_sticker_sweep = next_document_sweep = next_history_sweep = 0.0
    failures = 0
    fairness: dict[str, str | None] = {}
    filters = ({"kind": CHAT_GOAL_JOB_KIND} if goal_lane is True else
               {"exclude_kind": CHAT_GOAL_JOB_KIND} if goal_lane is False else {})
    while not stop.is_set():
        if goal_lane is not True and time.monotonic() >= next_sticker_sweep:
            try:
                from app.services.stickers import reconcile_sticker_indexes
                await asyncio.to_thread(reconcile_sticker_indexes)
            except Exception:
                logger.exception("Sticker index reconciliation failed")
            next_sticker_sweep = time.monotonic() + 15
        if goal_lane is not True and time.monotonic() >= next_document_sweep:
            try:
                from app.services.document_learning import reconcile_document_dispatches
                await asyncio.to_thread(reconcile_document_dispatches)
            except Exception:
                logger.exception("Document dispatch reconciliation failed")
            next_document_sweep = time.monotonic() + 30
        if goal_lane is not True and time.monotonic() >= next_history_sweep:
            try:
                await asyncio.to_thread(_prune_attempt_history)
            except Exception:
                logger.exception("Durable attempt history cleanup failed")
            next_history_sweep = time.monotonic() + 3600
        try:
            claimed = await asyncio.to_thread(
                run_one_durable_job, worker_id, **filters, fairness=fairness,
                on_poll=lambda: worker_update(lane, polled=True, status="running"),
                on_heartbeat=lambda: worker_update(lane, status="running"),
            )
        except _database_faults as error:
            failures += 1
            worker_update(lane, status="database_error")
            if failures == 1 or failures % 10 == 0:
                logger.warning("Durable queue lane %s poll failed (%s)", lane, type(error).__name__)
            await _wait_for_poll(stop, _backoff(failures, settings.durable_queue_poll_seconds))
            continue
        if failures:
            logger.info("Durable queue lane %s recovered after %s failed polls", lane, failures)
        failures = 0
        if not claimed:
            await _wait_for_poll(stop, settings.durable_queue_poll_seconds)


async def supervise_durable_queue(stop: asyncio.Event, *, goal_lane: bool) -> None:
    from app.services.task_reliability import worker_update

    lane = "goal" if goal_lane else "general"
    failures = 0
    try:
        while not stop.is_set():
            started = time.monotonic()
            worker_update(lane, status="starting")
            try:
                await durable_queue_worker(stop, f"{lane}-{uuid4()}", goal_lane=goal_lane)
                if stop.is_set():
                    break
                raise RuntimeError("Durable queue worker returned before shutdown")
            except Exception as error:
                failures = 1 if time.monotonic() - started >= 60 else failures + 1
                worker_update(lane, status="restarting", restarted=True)
                logger.error("Restarting durable queue lane %s after %s", lane, type(error).__name__)
                await _wait_for_poll(stop, _backoff(failures, 0.25))
    finally:
        worker_update(lane, status="stopped")
