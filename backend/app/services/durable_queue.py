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

        Used for continuous work such as research polling: the handler is
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


def enqueue_research_poll(research_job_id: str, workspace_id: str, actor_id: str) -> DurableJob:
    """Persist one durable poll job for a remote research task.

    The dedupe key is per research job so exactly one poller exists per task;
    re-enqueueing (e.g. from cancel or restart reconciliation) returns the same
    row instead of stacking duplicate pollers.
    """

    settings = get_settings()
    with SessionLocal() as db:
        return DurableQueue(
            db,
            lease_seconds=settings.durable_queue_lease_seconds,
            max_attempts=settings.durable_queue_max_attempts,
        ).enqueue(
            workspace_id=workspace_id,
            kind="research.poll",
            payload={
                "research_job_id": research_job_id,
                "workspace_id": workspace_id,
                "actor_id": actor_id,
            },
            dedupe_key=f"research.poll:{research_job_id}",
        )


def enqueue_memory_extraction(
    workspace_id: str,
    session_id: str,
    actor_id: str,
    last_message_id: str,
) -> DurableJob:
    """Event-driven memory extraction trigger (replaces polling sweeps).

    Deduplicated per (session, last completed message) so a message is never
    extracted twice even if the completion event fires more than once.
    """

    settings = get_settings()
    with SessionLocal() as db:
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
                "last_message_id": last_message_id,
            },
            dedupe_key=f"memory.extract:{session_id}:{last_message_id}",
        )


def run_research_poll_once(payload: dict[str, Any]) -> bool:
    """Poll one remote research task once with a fresh session.

    Returns True when the research job reached a terminal status or polling
    cannot continue (provider disappeared); False while the task is still
    active and the poll job should be re-armed.
    """

    from app.services.research import poll_research_once

    return poll_research_once(
        str(payload["research_job_id"]),
        str(payload["workspace_id"]),
        str(payload.get("actor_id") or "system:durable-queue"),
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
            if job.kind == "document.parse_index":
                from app.services.document_learning import run_document_job

                run_document_job(
                    str(job.payload["document_job_id"]),
                    str(job.payload["execution_token"]),
                )
                queue.complete(job)
            elif job.kind == "research.poll":
                terminal = run_research_poll_once(job.payload)
                if terminal:
                    queue.complete(job)
                else:
                    # Task still active: re-arm with the configured poll
                    # interval so the worker cycles through tenants instead of
                    # parking on one long research window.
                    queue.rearm(job, delay_seconds=max(0.01, settings.research_poll_seconds))
            elif job.kind == "chat.continue_stream":
                from app.services.chat_durable import run_chat_continue_once

                run_chat_continue_once(job.payload)
                queue.complete(job)
            elif job.kind == "memory.extract":
                from app.domain.models import Workspace
                from app.services.memory_enhancement import extract_session_memories

                workspace = db.get(Workspace, str(job.payload["workspace_id"]))
                if workspace is not None:
                    extract_session_memories(
                        db,
                        workspace,
                        str(job.payload["session_id"]),
                        get_settings(),
                        actor_id=str(
                            job.payload.get("actor_id")
                            or "system:memory-extraction"
                        ),
                    )
                queue.complete(job)
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


def reconcile_research_polling(db: Session | None = None) -> int:
    """Ensure an active durable poll job for every in-flight remote research task.

    Handles research jobs created before the durable queue (no poll row yet)
    and defensive re-arming of terminal poll rows that outlived a still-active
    task. Skipped for terminal and awaiting-approval research jobs (the latter
    has no provider task yet). Runs at startup while the durable worker is
    starting. Returns the number of poll jobs ensured.
    """

    from app.domain.models import ResearchJob
    from app.services.research import ACTIVE_RESEARCH_STATUSES

    settings = get_settings()
    owns_session = db is None
    session = db or SessionLocal()
    ensured = 0
    try:
        research_jobs = session.scalars(
            select(ResearchJob).where(
                ResearchJob.status.in_(ACTIVE_RESEARCH_STATUSES),
                ResearchJob.provider_task_id.is_not(None),
            )
        ).all()
        for research_job in research_jobs:
            dedupe = f"research.poll:{research_job.id}"
            existing = session.scalar(
                select(DurableJob).where(
                    DurableJob.workspace_id == research_job.workspace_id,
                    DurableJob.kind == "research.poll",
                    DurableJob.dedupe_key == dedupe,
                )
            )
            if existing is not None and existing.status in {"queued", "leased"}:
                continue
            if existing is not None:
                # Re-arm a terminal poll row so the still-active task resumes.
                existing.status = "queued"
                existing.attempt_count = 0
                existing.lease_owner = None
                existing.lease_token = None
                existing.lease_expires_at = None
                existing.available_at = utc_now()
            else:
                session.add(
                    DurableJob(
                        workspace_id=research_job.workspace_id,
                        kind="research.poll",
                        payload={
                            "research_job_id": research_job.id,
                            "workspace_id": research_job.workspace_id,
                            "actor_id": "system:durable-queue",
                        },
                        dedupe_key=dedupe,
                        max_attempts=settings.durable_queue_max_attempts,
                    )
                )
            ensured += 1
        session.commit()
    finally:
        if owns_session:
            session.close()
    return ensured


async def durable_queue_worker(stop: asyncio.Event, worker_id: str) -> None:
    """Poll the durable queue without blocking FastAPI's event loop."""

    settings = get_settings()
    while not stop.is_set():
        claimed = await asyncio.to_thread(run_one_durable_job, worker_id)
        if claimed:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.durable_queue_poll_seconds)
        except TimeoutError:
            pass
