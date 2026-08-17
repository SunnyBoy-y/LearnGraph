"""Unified sandbox execution scheduler (execution pool).

Design: doc/LearnGraph_沙箱执行池与统一调度设计_v1.0.md

The scheduler owns all capacity decisions. Agents never schedule; they submit
SandboxJob records and follow job state. Capacity shortage keeps a job QUEUED
(HTTP 202 semantics) instead of failing the caller, and the scheduler resumes
QUEUED jobs as capacity frees up.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import (
    SandboxInstance,
    SandboxJob,
    SandboxReservation,
    SandboxSession,
    utc_now,
)
from app.providers.remote.sandbox import SandboxBackendUnavailable

logger = logging.getLogger(__name__)

# AppError codes that mean "capacity shortage → queue, don't fail".
CAPACITY_CODES = {
    "sandbox_user_concurrency_limit",
    "sandbox_host_capacity_exhausted",
    "sandbox_host_memory_budget",
    "sandbox_host_cpu_budget",
    "sandbox_host_disk_reserve",
    "sandbox_session_busy",
}

# Retryable backend failures (queue with backoff, bounded attempts).
RETRYABLE_CODES = {"sandbox_backend_unavailable"}

MAX_JOB_ATTEMPTS = 5
SCHEDULE_BATCH = 8

# Module-level executor so queued jobs run concurrently without blocking the
# scheduler tick; each worker uses its own DB session.
_SCHEDULER_EXECUTOR_LOCK = threading.Lock()
_SCHEDULER_EXECUTOR: ThreadPoolExecutor | None = None


def _get_executor(max_workers: int) -> ThreadPoolExecutor:
    global _SCHEDULER_EXECUTOR
    with _SCHEDULER_EXECUTOR_LOCK:
        if _SCHEDULER_EXECUTOR is None:
            _SCHEDULER_EXECUTOR = ThreadPoolExecutor(
                max_workers=max(1, int(max_workers or 4)),
                thread_name_prefix="sandbox-sched",
            )
        return _SCHEDULER_EXECUTOR


def workload_request_vector(
    settings: Settings, workload_class: str
) -> dict[str, Any]:
    """Server-authoritative resource hint for a workload class.

    Agents never supply raw resource numbers; the deployment maps a workload
    class to a bounded request vector.
    """
    classes = settings.sandbox_workload_classes or {}
    vector = classes.get(workload_class) or classes.get("python") or {
        "cpu": 0.5,
        "memory_bytes": 512 * 1024 * 1024,
        "pids": 64,
    }
    return {
        "cpu": float(vector.get("cpu", 0.5)),
        "memory_bytes": int(vector.get("memory_bytes", 512 * 1024 * 1024)),
        "pids": int(vector.get("pids", 64)),
    }


SANDBOX_SCHEDULING_POLICY_KEY = "sandbox.scheduling_policy"


def workspace_scheduling_policy(db: Session, workspace_id: str) -> dict[str, Any]:
    """Read the workspace-admin sandbox scheduling policy (clamped).

    Returns raw override fields; callers combine them with deployment defaults
    and platform hard caps (``min`` semantics — an override can never raise a
    cap, only lower it).
    """
    from app.domain.models import WorkspaceSetting

    record = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace_id,
            WorkspaceSetting.key == SANDBOX_SCHEDULING_POLICY_KEY,
        )
    )
    if record is None or not isinstance(record.value, dict):
        return {}
    raw = record.value
    result: dict[str, Any] = {}
    for field in (
        "max_instances_per_user",
        "max_parallel_execs_per_instance",
        "queue_depth_per_user",
        "user_weight",
        "profiles",
    ):
        if field in raw and raw[field] is not None:
            result[field] = raw[field]
    return result


def effective_max_instances(
    settings: Settings, policy: dict[str, Any] | None = None
) -> int:
    policy = policy or {}
    override = policy.get("max_instances_per_user")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        override = min(override, settings.sandbox_hard_max_instances_per_user)
    else:
        override = settings.sandbox_default_max_instances_per_user
    return min(
        settings.sandbox_hard_max_instances_per_user,
        override,
        settings.sandbox_active_per_user,
    )


def effective_max_parallel(
    settings: Settings, policy: dict[str, Any] | None = None
) -> int:
    policy = policy or {}
    override = policy.get("max_parallel_execs_per_instance")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        override = min(override, settings.sandbox_hard_max_parallel_execs_per_instance)
    else:
        override = settings.sandbox_default_max_parallel_execs_per_instance
    return min(settings.sandbox_hard_max_parallel_execs_per_instance, override)


def effective_queue_depth(settings: Settings, policy: dict[str, Any] | None = None) -> int:
    policy = policy or {}
    override = policy.get("queue_depth_per_user")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return max(1, override)
    return settings.sandbox_default_queue_depth_per_user


def queue_depth(db: Session, owner_user_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(SandboxJob.id)).where(
                SandboxJob.owner_user_id == owner_user_id,
                SandboxJob.status.in_(("QUEUED", "STARTING")),
            )
        )
        or 0
    )


def evaluate_capacity(
    db: Session,
    settings: Settings,
    actor_id: str,
    *,
    exclude_session_id: str | None = None,
    workspace_id: str | None = None,
) -> tuple[bool, str | None, int]:
    """Evaluate whether a new execution can be admitted for the actor.

    Returns ``(ok, reason, retry_after_seconds)``. This mirrors the old
    ``_enforce_sandbox_capacity`` checks but returns a queueable result instead
    of raising; the unified scheduler uses it for both admission and queue
    reason reporting.
    """
    policy = (
        workspace_scheduling_policy(db, workspace_id)
        if workspace_id
        else {}
    )
    active_states = ("STARTING", "RUNNING", "WARM_IDLE")
    user_active = int(
        db.scalar(
            select(func.count(SandboxSession.id)).where(
                SandboxSession.owner_user_id == actor_id,
                SandboxSession.lifecycle_state.in_(active_states),
                *([] if exclude_session_id is None else [SandboxSession.id != exclude_session_id]),
            )
        )
        or 0
    )
    # New execution-pool instances also count toward the per-user envelope.
    instance_active = int(
        db.scalar(
            select(func.count(SandboxInstance.id)).where(
                SandboxInstance.owner_user_id == actor_id,
                SandboxInstance.state.in_(("PROVISIONING", "READY", "BUSY", "SATURATED")),
            )
        )
        or 0
    )
    effective_max_instances = effective_max_instances(settings, policy)
    total_active = user_active + instance_active
    if total_active >= effective_max_instances:
        return False, "waiting_capacity", 5

    host_active = int(
        db.scalar(
            select(func.count(SandboxSession.id)).where(
                SandboxSession.lifecycle_state.in_(active_states),
                *([] if exclude_session_id is None else [SandboxSession.id != exclude_session_id]),
            )
        )
        or 0
    )
    if host_active >= settings.sandbox_host_max_active:
        return False, "waiting_capacity", 10

    from app.services.sandbox import _sandbox_workspace_root

    host_cpus = getattr(settings, "sandbox_host_cpus", 0) or 0
    host_memory = getattr(settings, "sandbox_host_memory_bytes", 0) or 0
    # The old code probed the backend's host_capacity(); reuse it when available.
    try:
        from app.providers.sandbox_registry import get_sandbox_backend_registry

        backend = get_sandbox_backend_registry().default(settings)
        host_cpus, host_memory = backend.host_capacity()
    except Exception:  # noqa: BLE001 - probe is best-effort
        pass
    requested_count = host_active + instance_active + 1
    if (
        host_memory > 0
        and requested_count * settings.sandbox_memory_bytes
        > host_memory * settings.sandbox_host_max_allocated_memory_ratio
    ):
        return False, "waiting_capacity", 10
    if (
        host_cpus > 0
        and requested_count * settings.sandbox_cpu_count
        > host_cpus * settings.sandbox_host_max_allocated_cpu_ratio
    ):
        return False, "waiting_capacity", 10

    import shutil

    free = shutil.disk_usage(_sandbox_workspace_root(settings)).free
    if free < settings.sandbox_host_minimum_free_disk_bytes:
        return False, "waiting_capacity", 30

    # Real-time pressure probe: high watermark tightens admission. Hard
    # reservations above remain the primary gate; the probe only lowers the
    # effective concurrency (never raises it).
    try:
        from app.providers.sandbox_registry import get_sandbox_backend_registry

        pressure_backend = get_sandbox_backend_registry().default(settings)
        probe = getattr(pressure_backend, "observed_pressure", None)
        if callable(probe):
            pressure = probe()
            host_mem = int(pressure.get("host_memory_bytes") or 0)
            used_mem = int(pressure.get("observed_memory_bytes") or 0)
            if host_mem > 0 and used_mem / host_mem > settings.sandbox_probe_high_watermark:
                return False, "waiting_resource_pressure", 10
    except Exception:  # noqa: BLE001 - probe is best-effort
        pass

    if queue_depth(db, actor_id) >= effective_queue_depth(settings, policy):
        return False, "sandbox_queue_depth_exceeded", 30

    return True, None, 0


class SandboxSchedulerService:
    """Persistent job queue + fair-ish admission for sandbox work."""

    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # ── submission ─────────────────────────────────────────────────────

    def submit_job(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        chat_session_id: str,
        kind: str,
        payload: dict[str, Any],
        workload_class: str = "default",
        idempotency_key: str | None = None,
        deadline_seconds: int | None = None,
    ) -> SandboxJob:
        key_hash = (
            hashlib.sha256(idempotency_key.encode()).hexdigest()
            if idempotency_key
            else None
        )
        if key_hash:
            existing = self.db.scalar(
                select(SandboxJob).where(
                    SandboxJob.workspace_id == workspace_id,
                    SandboxJob.owner_user_id == owner_user_id,
                    SandboxJob.idempotency_key_hash == key_hash,
                )
            )
            if existing is not None:
                return existing
        now = utc_now()
        deadline = deadline_seconds or self.settings.sandbox_queue_deadline_seconds
        job = SandboxJob(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            chat_session_id=chat_session_id,
            kind=kind,
            workload_class=workload_class,
            request_vector=workload_request_vector(self.settings, workload_class),
            payload_json=payload or {},
            status="QUEUED",
            idempotency_key_hash=key_hash,
            queued_at=now,
            available_at=now,
            deadline_at=now + timedelta(seconds=deadline),
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def get_job(self, job_id: str, *, workspace_id: str, owner_user_id: str) -> SandboxJob:
        job = self.db.scalar(
            select(SandboxJob).where(
                SandboxJob.id == job_id,
                SandboxJob.workspace_id == workspace_id,
                SandboxJob.owner_user_id == owner_user_id,
            )
        )
        if job is None:
            raise AppError(404, "sandbox_job_not_found", "Sandbox job was not found")
        return job

    def cancel_job(self, job: SandboxJob) -> SandboxJob:
        now = utc_now()
        if job.status in ("SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"):
            return job
        if job.status in ("QUEUED", "STARTING"):
            claimed = self.db.execute(
                update(SandboxJob)
                .where(
                    SandboxJob.id == job.id,
                    SandboxJob.status.in_(("QUEUED", "STARTING")),
                )
                .values(
                    status="CANCELLED",
                    reason="cancelled_by_user",
                    finished_at=now,
                )
            )
            if claimed.rowcount == 1:
                self.db.commit()
                self.db.refresh(job)
                return job
            self.db.rollback()
        # RUNNING (or races): mark cancel-requested; the execution terminates it.
        job.cancel_requested = True
        job.reason = "cancel_requested"
        self.db.commit()
        self.db.refresh(job)
        return job

    # ── scheduling tick ────────────────────────────────────────────────

    def schedule_once(self) -> dict[str, int]:
        """Run one scheduling round. Returns counters for observability.

        The tick only expires stale jobs, releases expired reservations, and
        CAS-claims candidates; actual execution is dispatched to a thread pool
        (each job gets its own DB session), so one long command never blocks
        the tick or other queued jobs.
        """
        counters = {"expired": 0, "started": 0, "requeued": 0, "failed": 0, "claimed": 0}
        now = utc_now()
        # 1. Expire jobs past their queue deadline.
        expired = self.db.execute(
            update(SandboxJob)
            .where(
                SandboxJob.status == "QUEUED",
                SandboxJob.deadline_at.is_not(None),
                SandboxJob.deadline_at <= now,
            )
            .values(status="EXPIRED", reason="queue_deadline", finished_at=now)
        )
        counters["expired"] = int(expired.rowcount or 0)
        self.db.commit()
        # 2. Release expired reservations.
        released = self.db.execute(
            update(SandboxReservation)
            .where(
                SandboxReservation.status == "HELD",
                SandboxReservation.expires_at <= now,
            )
            .values(status="EXPIRED", released_at=now)
        )
        if released.rowcount:
            self.db.commit()
        # 3. Claim candidate jobs with CAS.
        candidates = list(
            self.db.scalars(
                select(SandboxJob)
                .where(
                    SandboxJob.status == "QUEUED",
                    SandboxJob.available_at <= now,
                )
                .order_by(SandboxJob.priority.desc(), SandboxJob.queued_at.asc())
                .limit(SCHEDULE_BATCH)
            ).all()
        )
        executor = _get_executor(self.settings.sandbox_scheduler_workers)
        for job in candidates:
            claimed = self.db.execute(
                update(SandboxJob)
                .where(SandboxJob.id == job.id, SandboxJob.status == "QUEUED")
                .values(status="STARTING", attempt=SandboxJob.attempt + 1)
            )
            if claimed.rowcount != 1:
                self.db.rollback()
                continue
            self.db.commit()
            counters["claimed"] += 1
            executor.submit(self._run_job_isolated, job.id)
        return counters

    def _run_job_isolated(self, job_id: str) -> None:
        """Worker-thread entry: run one claimed job with its own DB session.

        Uses a fresh ``SandboxSchedulerService`` bound to the worker session so
        no ORM object is shared across threads.
        """
        from app.core.database import SessionLocal

        try:
            with SessionLocal() as db:
                job = db.get(SandboxJob, job_id)
                if job is None:
                    return
                worker = SandboxSchedulerService(db, self.settings)
                worker._run_job(job)
        except Exception:  # noqa: BLE001 - bounded worker failure
            logger.exception("sandbox scheduler worker failed for job %s", job_id)

    # ── execution ──────────────────────────────────────────────────────

    def _run_job(self, job: SandboxJob) -> str:
        """Attempt to run one claimed job.

        Returns the outcome key: started / requeued / failed.
        """
        if job.cancel_requested:
            self._finish(job, "CANCELLED", "cancelled_by_user")
            return "failed"
        if job.attempt > MAX_JOB_ATTEMPTS:
            self._finish(job, "FAILED", "max_attempts_reached")
            return "failed"
        ok, reason, retry_after = evaluate_capacity(
            self.db,
            self.settings,
            job.owner_user_id,
            exclude_session_id=None,
        )
        if not ok:
            if reason == "sandbox_queue_depth_exceeded":
                self._finish(job, "FAILED", reason)
                return "failed"
            job.status = "QUEUED"
            job.reason = reason
            job.available_at = utc_now() + timedelta(seconds=retry_after)
            self.db.commit()
            return "requeued"
        reservation = self._reserve(job)
        if reservation is None:
            job.status = "QUEUED"
            job.reason = "waiting_capacity"
            job.available_at = utc_now() + timedelta(seconds=5)
            self.db.commit()
            return "requeued"
        try:
            self._execute_job(job)
            return "started"
        except AppError as exc:
            if exc.code in CAPACITY_CODES:
                job.status = "QUEUED"
                job.reason = "waiting_capacity"
                job.available_at = utc_now() + timedelta(seconds=5)
                self.db.commit()
                return "requeued"
            if exc.code in RETRYABLE_CODES:
                job.status = "QUEUED"
                job.reason = "backend_unavailable"
                job.available_at = utc_now() + timedelta(seconds=min(30, 5 * job.attempt))
                self.db.commit()
                return "requeued"
            self._finish(job, "FAILED", exc.code, exc.message)
            return "failed"
        except SandboxBackendUnavailable:
            job.status = "QUEUED"
            job.reason = "backend_unavailable"
            job.available_at = utc_now() + timedelta(seconds=min(30, 5 * job.attempt))
            self.db.commit()
            return "requeued"
        except Exception as exc:  # noqa: BLE001 - bounded job failure
            logger.exception("sandbox job %s failed unexpectedly", job.id)
            self._finish(job, "FAILED", "sandbox_job_failed", " ".join(str(exc).split())[:300])
            return "failed"
        finally:
            self._release_reservation(reservation.id)

    def _reserve(self, job: SandboxJob) -> SandboxReservation | None:
        """Create a bounded capacity reservation for a claimed job.

        Returns None when the reservation could not be placed (capacity moved
        between evaluate_capacity and here — the caller requeues).
        """
        now = utc_now()
        reservation = SandboxReservation(
            workspace_id=job.workspace_id,
            job_id=job.id,
            instance_id="",
            resource_vector=dict(job.request_vector or {}),
            token_hash=secrets.token_hex(16),
            generation=0,
            status="HELD",
            expires_at=now + timedelta(seconds=self.settings.sandbox_reservation_ttl_seconds),
        )
        self.db.add(reservation)
        try:
            self.db.commit()
        except Exception:  # noqa: BLE001 - concurrent reservation conflict
            self.db.rollback()
            return None
        self.db.refresh(reservation)
        return reservation

    def _release_reservation(self, reservation_id: str) -> None:
        if not reservation_id:
            return
        try:
            self.db.execute(
                update(SandboxReservation)
                .where(
                    SandboxReservation.id == reservation_id,
                    SandboxReservation.status == "HELD",
                )
                .values(status="RELEASED", released_at=utc_now())
            )
            self.db.commit()
        except Exception:  # noqa: BLE001 - release is best-effort
            self.db.rollback()
            logger.exception("failed to release sandbox reservation %s", reservation_id)

    def _execute_job(self, job: SandboxJob) -> None:
        """Dispatch a claimed job to the matching executor."""
        if job.kind == "agent_command":
            self._execute_agent_command(job)
            return
        if job.kind == "fixed_task":
            # Fixed-task (file parsing) submissions keep their existing
            # synchronous path for now; they are not routed through the queue
            # until the executor is split from create_task. Reject queued
            # fixed_task jobs explicitly instead of silently dropping them.
            self._finish(
                job,
                "FAILED",
                "sandbox_job_kind_unsupported",
                "fixed_task jobs are not queued yet; submit via the synchronous task API",
            )
            return
        raise AppError(422, "sandbox_job_kind_unsupported", f"Unsupported job kind: {job.kind}")

    def _execute_agent_command(self, job: SandboxJob) -> None:
        from app.domain.schemas.sandbox import SandboxAgentCommandRequest
        from app.services.sandbox import SandboxAgentWorkspaceService

        payload = job.payload_json or {}
        try:
            request = SandboxAgentCommandRequest.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            raise AppError(422, "invalid_job", f"Stored job payload is invalid: {exc}") from exc
        svc = SandboxAgentWorkspaceService(
            self.db,
            job.workspace_id,
            job.owner_user_id,
            self.settings,
        )
        idempotency_key = job.idempotency_key_hash or f"job:{job.id}"
        try:
            command = svc.execute_command(request, idempotency_key=idempotency_key)
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(job)
        if command.job_id is None:
            command.job_id = job.id
        job.started_at = utc_now()
        if command.status == "completed":
            self._finish(job, "SUCCEEDED", None)
        elif command.status == "failed":
            self._finish(
                job,
                "FAILED",
                command.error_class or "sandbox_command_failed",
                command.error_message,
            )
        else:
            # Synchronous executor should always reach a terminal command state;
            # treat anything else as still-active (polled by later ticks).
            job.status = "RUNNING"
            job.reason = None
        self.db.commit()

    def _finish(
        self,
        job: SandboxJob,
        status: str,
        error_class: str | None,
        error_message: str | None = None,
    ) -> None:
        job.status = status
        job.reason = error_class
        job.error_class = error_class
        if error_message:
            job.error_message = error_message
        if job.started_at is None:
            job.started_at = utc_now()
        job.finished_at = utc_now()
        self.db.commit()


def run_scheduler_tick() -> dict[str, int]:
    """Entry point for the periodic scheduler loop (see scheduler.py)."""
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        return SandboxSchedulerService(db, get_settings_cached()).schedule_once()


def get_settings_cached():
    from app.core.config import get_settings

    return get_settings()
