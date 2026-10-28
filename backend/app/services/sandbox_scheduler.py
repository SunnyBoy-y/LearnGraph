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
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import (
    SandboxAgentEvent,
    SandboxAgentTask,
    SandboxAgentCommand,
    SandboxInstance,
    SandboxJob,
    SandboxReservation,
    SandboxSession,
    utc_now,
)
from app.providers.remote.sandbox import SandboxBackendUnavailable
from app.services.agent_execution_profiles import task_requires_sandbox_capacity

logger = logging.getLogger(__name__)

# AppError codes that mean "capacity shortage → queue, don't fail".
CAPACITY_CODES = {
    "sandbox_user_concurrency_limit",
    "sandbox_host_capacity_exhausted",
    "sandbox_host_memory_budget",
    "sandbox_host_cpu_budget",
    "sandbox_host_disk_reserve",
    "sandbox_session_busy",
    "sandbox_instance_saturated",
}

# Retryable backend failures (queue with backoff, bounded attempts).
RETRYABLE_CODES = {"sandbox_backend_unavailable"}

MAX_JOB_ATTEMPTS = 5
SCHEDULE_BATCH = 8

# Module-level executor so queued jobs run concurrently without blocking the
# scheduler tick; each worker uses its own DB session.
_SCHEDULER_EXECUTOR_LOCK = threading.Lock()
_SCHEDULER_EXECUTOR: ThreadPoolExecutor | None = None
_JOB_CHANGED = threading.Condition()


def notify_job_change() -> None:
    """Wake local waiters; persisted jobs remain authoritative across processes."""
    with _JOB_CHANGED:
        _JOB_CHANGED.notify_all()


def foreground_wait_seconds(settings: Settings) -> float:
    return float(settings.sandbox_queue_deadline_seconds + settings.sandbox_wall_time_seconds + 5)


def append_agent_event(db: Session, task: SandboxAgentTask, event_type: str, payload: dict[str, Any]) -> None:
    """Allocate the sequence under the database write lock, including control races."""
    db.flush()
    seq = db.execute(update(SandboxAgentTask).where(SandboxAgentTask.id == task.id)
        .values(event_seq=func.coalesce(SandboxAgentTask.event_seq, 0) + 1)
        .returning(SandboxAgentTask.event_seq)
        .execution_options(synchronize_session=False)).scalar_one()
    set_committed_value(task, "event_seq", seq)
    db.add(
        SandboxAgentEvent(
            workspace_id=task.workspace_id,
            task_id=task.id,
            seq=seq,
            event_type=event_type,
            payload=payload,
        )
    )


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
    runtime_kind: str | None = None,
    reuse_instance_id: str | None = None,
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
    # Count physical runtimes once. A pooled session is a namespace within an
    # instance, not an additional container. Unallocated failed sessions do not
    # consume capacity merely because they still have a command lease.
    instances = list(db.scalars(select(SandboxInstance).where(
        SandboxInstance.state.in_(("PROVISIONING", "READY", "BUSY", "SATURATED")))))
    refs = {i.backend_resource_ref for i in instances if i.backend_resource_ref}
    sessions = list(db.scalars(select(SandboxSession).where(
        SandboxSession.lifecycle_state.in_(active_states), SandboxSession.backend_session_ref.is_not(None))))
    legacy = [s for s in sessions if s.backend_session_ref not in refs]
    reusable = next((i for i in instances if i.id == reuse_instance_id), None)
    if reusable is None and runtime_kind and workspace_id and settings.sandbox_instance_pooling_enabled:
        reusable = next((i for i in instances if i.owner_user_id == actor_id
            and i.workspace_id == workspace_id and i.runtime_profile == runtime_kind
            and i.backend_resource_ref and i.active_executions < i.max_parallel_execs), None)
    reuse_legacy = any(s.id == exclude_session_id for s in legacy)
    additional = 0 if reusable is not None or reuse_legacy else 1
    if reusable is not None and reusable.active_executions >= reusable.max_parallel_execs:
        return False, "waiting_capacity", 1
    user_active = sum(i.owner_user_id == actor_id for i in instances) + sum(s.owner_user_id == actor_id for s in legacy)
    max_instances = effective_max_instances(settings, policy)
    if user_active + additional > max_instances:
        return False, "waiting_capacity", 1

    host_active = len(instances) + len(legacy)
    if host_active + additional > settings.sandbox_host_max_active:
        return False, "waiting_capacity", 10

    from app.services.sandbox import _sandbox_workspace_root

    host_cpus = getattr(settings, "sandbox_host_cpus", 0) or 0
    host_memory = getattr(settings, "sandbox_host_memory_bytes", 0) or 0
    pressure = None
    combined_probe = False
    # sandboxd returns both totals and pressure. Fetch it once instead of
    # requesting /capacity twice (each request used to resample all runners).
    try:
        from app.providers.sandbox_registry import get_sandbox_backend_registry

        backend = get_sandbox_backend_registry().default(settings)
        snapshot = getattr(backend, "host_capacity_snapshot", None)
        if callable(snapshot):
            combined_probe = True
            pressure = snapshot()
            host_cpus = int(pressure.get("cpu_count") or 0)
            host_memory = int(pressure.get("memory_bytes") or 0)
        else:
            host_cpus, host_memory = backend.host_capacity()
    except Exception:  # noqa: BLE001 - probe is best-effort
        pass
    requested_count = host_active + additional
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

        if not combined_probe:
            pressure_backend = get_sandbox_backend_registry().default(settings)
            probe = getattr(pressure_backend, "observed_pressure", None)
            pressure = probe() if callable(probe) else None
        if pressure is not None:
            host_mem = int(pressure.get("host_memory_bytes") or pressure.get("memory_bytes") or 0)
            used_mem = int(pressure.get("observed_memory_bytes") or 0)
            if host_mem > 0 and used_mem / host_mem > settings.sandbox_probe_high_watermark:
                return False, "waiting_resource_pressure", 10
    except Exception:  # noqa: BLE001 - probe is best-effort
        pass

    return True, None, 0


def wake_capacity_waiters(db: Session, owner_user_id: str) -> None:
    """Make this owner's capacity waiters eligible in the release transaction.

    Admission still rechecks every quota. Backend-error backoff and pressure
    backoff remain intact; a released slot only ends capacity retry delays.
    """
    db.execute(update(SandboxJob).where(
        SandboxJob.owner_user_id == owner_user_id,
        SandboxJob.status == "QUEUED",
        SandboxJob.reason == "waiting_capacity",
    ).values(available_at=utc_now()).execution_options(synchronize_session=False))


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
                if (existing.chat_session_id!=chat_session_id or existing.kind!=kind or
                    {k:v for k,v in (existing.payload_json or {}).items() if not k.startswith('_')} !=
                    {k:v for k,v in (payload or {}).items() if not k.startswith('_')}):
                    raise AppError(409,'idempotency_key_conflict','Execution key has a different payload')
                return existing
        now = utc_now()
        if queue_depth(self.db, owner_user_id) >= effective_queue_depth(
            self.settings, workspace_scheduling_policy(self.db, workspace_id)
        ):
            raise AppError(429, "sandbox_queue_depth_exceeded", "Sandbox job queue is full")
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
            deadline_at=None if kind == "subagent" else now + timedelta(seconds=deadline),
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
        if job.status in ("QUEUED", "STARTING", "PAUSED"):
            claimed = self.db.execute(
                update(SandboxJob)
                .where(
                    SandboxJob.id == job.id,
                    SandboxJob.status.in_(("QUEUED", "STARTING", "PAUSED")),
                )
                .values(
                    status="CANCELLED",
                    cancel_requested=True,
                    reason="cancelled_by_user",
                    finished_at=now,
                )
            )
            if claimed.rowcount == 1:
                self.db.commit()
                self.db.refresh(job)
                notify_job_change()
                return job
            self.db.rollback()
        # RUNNING (or races): mark cancel-requested; the execution terminates it.
        job.cancel_requested = True
        job.reason = "cancel_requested"
        self.db.commit()
        self.db.refresh(job)
        notify_job_change()
        return job

    def wait_job(self, job: SandboxJob, seconds: float, cancel_check: Callable[[], bool] | None = None) -> SandboxJob:
        """Wait on the server, releasing DB transactions between state checks.

        Local completion wakes immediately; the one-second fallback observes
        other processes/restarts and also drives persisted queued jobs when no
        periodic scheduler is running in this process.
        """
        deadline = time.monotonic() + max(0, seconds)
        while job.status in {"QUEUED", "STARTING", "RUNNING"}:
            if cancel_check and cancel_check():
                job = self.cancel_job(job)
                if job.status not in {'QUEUED','STARTING','RUNNING'}:
                    return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if job.status == "QUEUED":
                self.schedule_once()
            self.db.commit()
            with _JOB_CHANGED:
                _JOB_CHANGED.wait(timeout=min(1, remaining))
            self.db.refresh(job)
        return job

    # ── scheduling tick ────────────────────────────────────────────────

    def _recover_stale_starting_jobs(self, now) -> int:
        """Return abandoned CAS claims to the queue.

        A process crash or a worker exception before ``_run_job`` starts can
        leave a job in STARTING with no ``started_at``.  Do not reclaim it
        immediately because the worker may still be between the CAS commit and
        its first database write; thirty seconds is long beyond that handoff.
        """
        cutoff = now - timedelta(seconds=30)
        stale_jobs = list(
            self.db.scalars(
                select(SandboxJob).where(
                    SandboxJob.status == "STARTING",
                    SandboxJob.started_at.is_(None),
                    SandboxJob.available_at <= cutoff,
                )
            ).all()
        )
        if not stale_jobs:
            return 0

        recovered = 0
        terminal_task_statuses = {
            "SUCCEEDED",
            "PARTIAL",
            "FAILED",
            "TIMED_OUT",
            "CANCELLED",
            "STALE",
            "INTERRUPTED",
        }
        for job in stale_jobs:
            job.status = "QUEUED"
            job.reason = "stale_starting_recovered"
            job.available_at = now
            payload = job.payload_json if isinstance(job.payload_json, dict) else {}
            task_id = payload.get("task_id")
            task = self.db.get(SandboxAgentTask, task_id) if isinstance(task_id, str) else None
            if task is not None and task.status not in terminal_task_statuses:
                task.status = "QUEUED"
                task.status_reason = "stale_starting_recovered"
                append_agent_event(
                    self.db,
                    task,
                    "retry_scheduled",
                    {"job_id": job.id, "reason": "stale_starting_recovered"},
                )
            recovered += 1
        self.db.commit()
        return recovered

    def _sync_expired_subagent_tasks(self, now) -> int:
        """Mirror queue expiry onto the durable sub-agent task row."""
        expired_jobs = list(
            self.db.scalars(
                select(SandboxJob).where(
                    SandboxJob.kind == "subagent",
                    SandboxJob.status == "EXPIRED",
                    SandboxJob.reason == "queue_deadline",
                )
            ).all()
        )
        terminal_task_statuses = {
            "SUCCEEDED",
            "PARTIAL",
            "FAILED",
            "TIMED_OUT",
            "CANCELLED",
            "STALE",
            "INTERRUPTED",
        }
        synced = 0
        for job in expired_jobs:
            payload = job.payload_json if isinstance(job.payload_json, dict) else {}
            task_id = payload.get("task_id")
            task = self.db.get(SandboxAgentTask, task_id) if isinstance(task_id, str) else None
            if task is None or task.status in terminal_task_statuses:
                continue
            task.status = "TIMED_OUT"
            task.status_reason = "queue_deadline"
            task.finished_at = now
            append_agent_event(
                self.db,
                task,
                "timed_out",
                {"job_id": job.id, "reason": "queue_deadline"},
            )
            synced += 1
        if synced:
            self.db.commit()
        return synced

    def schedule_once(self) -> dict[str, int]:
        """Run one scheduling round. Returns counters for observability.

        The tick only expires stale jobs, releases expired reservations, and
        CAS-claims candidates; actual execution is dispatched to a thread pool
        (each job gets its own DB session), so one long command never blocks
        the tick or other queued jobs.
        """
        counters = {"expired": 0, "started": 0, "requeued": 0, "failed": 0, "claimed": 0}
        now = utc_now()
        counters['commands_reconciled'] = self.reconcile_commands()
        counters["recovered"] = self._recover_stale_starting_jobs(now)
        # 1. Expire jobs past their queue deadline.
        expired = self.db.execute(
            update(SandboxJob)
            .where(
                SandboxJob.status == "QUEUED",
                SandboxJob.kind != "subagent",
                SandboxJob.deadline_at.is_not(None),
                SandboxJob.deadline_at <= now,
            )
            .values(status="EXPIRED", reason="queue_deadline", finished_at=now)
            .execution_options(synchronize_session=False)
        )
        counters["expired"] = int(expired.rowcount or 0)
        self.db.commit()
        counters["synced_expired_tasks"] = self._sync_expired_subagent_tasks(now)
        # 2. Release expired reservations.
        released = self.db.execute(
            update(SandboxReservation)
            .where(
                SandboxReservation.status == "HELD",
                SandboxReservation.expires_at <= now,
            )
            .values(status="EXPIRED", released_at=now)
            .execution_options(synchronize_session=False)
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
                .values(status="STARTING", attempt=SandboxJob.attempt + 1, available_at=now)
            )
            if claimed.rowcount != 1:
                self.db.rollback()
                continue
            self.db.commit()
            counters["claimed"] += 1
            executor.submit(self._run_job_isolated, job.id)
        return counters

    def reconcile_commands(self) -> int:
        from app.services.sandbox import SandboxAgentWorkspaceService
        stale_cutoff=utc_now()-timedelta(seconds=max(60,self.settings.sandbox_wall_time_seconds+30))
        # Older versions could leave a pre-dispatch command and its namespace
        # lease behind. 'created' is before the native-dispatch commit.
        for abandoned in list(self.db.scalars(select(SandboxAgentCommand).where(
            SandboxAgentCommand.status=='created',SandboxAgentCommand.updated_at<stale_cutoff))):
            abandoned.status='failed'
            abandoned.error_class='execution_not_started'
            abandoned.error_message='Worker stopped before native dispatch'
            abandoned.idempotency_key_hash=None
            self.db.execute(update(SandboxSession).where(SandboxSession.active_command_id==abandoned.id).values(
                active_command_id=None,lease_token_hash=None,lease_expires_at=None,lifecycle_state='COLD'))
            if abandoned.job_id:
                pending=self.db.get(SandboxJob,abandoned.job_id)
                if pending and pending.status in {'STARTING','RUNNING'} and not pending.cancel_requested:
                    pending.status='QUEUED';pending.started_at=None;pending.available_at=utc_now()
        self.db.commit()
        commands = list(self.db.scalars(select(SandboxAgentCommand).where(
            SandboxAgentCommand.status=='running')))
        count = 0
        for command in commands:
            try:
                SandboxAgentWorkspaceService(self.db,command.workspace_id,command.owner_user_id,
                    self.settings).reconcile_command(command)
                count += 1
            except SandboxBackendUnavailable:
                self.db.rollback()
            except Exception:
                self.db.rollback()
                logger.exception('Command reconciliation failed for %s',command.id)
        # A claim can die before creating its command. No native execution can
        # exist in that window: dispatch is after the durable command commit.
        cutoff = utc_now()-timedelta(seconds=30)
        self.db.execute(update(SandboxJob).where(
            SandboxJob.kind=='agent_command',SandboxJob.status.in_(('RUNNING','STARTING')),
            SandboxJob.available_at<cutoff,
            ~select(SandboxAgentCommand.id).where(SandboxAgentCommand.job_id==SandboxJob.id).exists()
        ).values(status='QUEUED',started_at=None,available_at=utc_now(),reason='claim_recovered'))
        self.db.commit()
        # Repair legacy/leaked counters using one correlated SQL statement.
        # A separate read-then-write would race with a concurrent slot borrow.
        active=select(func.count(SandboxAgentCommand.id)).join(SandboxSession,
            SandboxSession.id==SandboxAgentCommand.sandbox_session_id).where(
            SandboxAgentCommand.status=='running',
            (SandboxAgentCommand.instance_id==SandboxInstance.id) | (
                SandboxAgentCommand.instance_id.is_(None) &
                (SandboxSession.backend_session_ref==SandboxInstance.backend_resource_ref))
        ).correlate(SandboxInstance).scalar_subquery()
        from sqlalchemy import case
        self.db.execute(update(SandboxInstance).where(
            SandboxInstance.state.in_(('READY','BUSY','SATURATED','ERROR')),
            SandboxInstance.backend_resource_ref.is_not(None) | (SandboxInstance.state=='ERROR'),
            SandboxInstance.active_executions!=active
        ).values(active_executions=active,state=case((SandboxInstance.state=='ERROR','ERROR'),
            (active>0,'BUSY'),else_='READY')))
        self.db.execute(update(SandboxReservation).where(SandboxReservation.status=='HELD',
            select(SandboxJob.id).where(SandboxJob.id==SandboxReservation.job_id,
                SandboxJob.status.in_(('SUCCEEDED','FAILED','CANCELLED','EXPIRED'))).exists()
        ).values(status='RELEASED',released_at=utc_now()))
        self.db.commit()
        return count

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
                try:
                    worker._run_job(job)
                except Exception as exc:  # noqa: BLE001 - bounded worker failure
                    worker._recover_worker_failure(job, exc)
        except Exception:  # noqa: BLE001 - recovery must not escape the worker
            logger.exception("sandbox scheduler worker failed for job %s", job_id)

    def _recover_worker_failure(self, job: SandboxJob, exc: Exception) -> None:
        """Close the state transition when a worker fails outside ``_run_job``.

        Admission and job dispatch happen before ``_run_job`` enters its normal
        error boundary.  A bug in either phase used to leave the CAS-claimed job
        in ``STARTING`` forever, which also left its durable sub-agent task in
        ``QUEUED``.  Retry boundedly while the job still has attempts left;
        otherwise persist a terminal failure for both the job and its task.
        """
        logger.exception("sandbox scheduler worker failed for job %s", job.id)
        if job.status in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}:
            return

        now = utc_now()
        error_class = type(exc).__name__
        error_message = " ".join(str(exc).split())[:500] or "sandbox scheduler worker failed"
        retry = job.attempt < MAX_JOB_ATTEMPTS and not job.cancel_requested
        task = None
        payload = job.payload_json if isinstance(job.payload_json, dict) else {}
        task_id = payload.get("task_id")
        if isinstance(task_id, str) and task_id:
            task = self.db.get(SandboxAgentTask, task_id)

        if retry:
            job.status = "QUEUED"
            job.reason = "worker_retry"
            job.error_class = error_class
            job.error_message = error_message
            job.available_at = now + timedelta(seconds=min(30, 5 * job.attempt))
            if task is not None and task.status not in {
                "SUCCEEDED",
                "PARTIAL",
                "FAILED",
                "TIMED_OUT",
                "CANCELLED",
                "STALE",
                "INTERRUPTED",
            }:
                task.status = "QUEUED"
                task.status_reason = "worker_retry"
                append_agent_event(
                    self.db,
                    task,
                    "retry_scheduled",
                    {
                        "job_id": job.id,
                        "attempt": job.attempt + 1,
                        "error_class": error_class,
                    },
                )
        else:
            job.status = "FAILED"
            job.reason = "sandbox_scheduler_worker_failed"
            job.error_class = error_class
            job.error_message = error_message
            job.finished_at = now
            if task is not None and task.status not in {
                "SUCCEEDED",
                "PARTIAL",
                "FAILED",
                "TIMED_OUT",
                "CANCELLED",
                "STALE",
                "INTERRUPTED",
            }:
                task.status = "FAILED"
                task.status_reason = error_class
                task.finished_at = now
                append_agent_event(
                    self.db,
                    task,
                    "failed",
                    {
                        "job_id": job.id,
                        "error_class": error_class,
                        "error_message": error_message,
                    },
                )
        self.db.commit()

    # ── execution ──────────────────────────────────────────────────────

    def _run_job(self, job: SandboxJob) -> str:
        """Attempt to run one claimed job.

        Returns the outcome key: started / requeued / failed.
        """
        if isinstance(job, SandboxJob):
            self.db.refresh(job)
        if job.cancel_requested or getattr(job, "status", None) == "CANCELLED":
            self._finish(job, "CANCELLED", "cancelled_by_user")
            return "failed"
        if job.attempt > MAX_JOB_ATTEMPTS:
            self._finish(job, "FAILED", "max_attempts_reached")
            return "failed"
        payload = job.payload_json if isinstance(job.payload_json, dict) else {}
        requires_container = True
        if job.kind == "subagent":
            try:
                requires_container = task_requires_sandbox_capacity(
                    payload.get("role_key"),
                    tools=payload.get("tools"),
                    execution_lane=payload.get("execution_lane"),
                )
            except (TypeError, ValueError):
                requires_container = True
        reservation = None
        if requires_container:
            # A warm sandbox in this conversation is reused by the executor;
            # it must not block its own next task at the per-user limit.
            reusable_session_id = self.db.scalar(select(SandboxSession.id).where(
                SandboxSession.owner_user_id == job.owner_user_id,
                SandboxSession.workspace_id == job.workspace_id,
                SandboxSession.chat_session_id == job.chat_session_id,
                SandboxSession.lifecycle_state == "WARM_IDLE",
                SandboxSession.active_command_id.is_(None),
            ).order_by(SandboxSession.created_at.desc()).limit(1))
            ok, reason, retry_after = evaluate_capacity(
                self.db,
                self.settings,
                job.owner_user_id,
                exclude_session_id=reusable_session_id,
                workspace_id=job.workspace_id,
                runtime_kind=(payload.get("runtime") or "python-node") if job.kind in {"agent_command", "subagent"} else None,
            )
            if not ok:
                if reason == "sandbox_queue_depth_exceeded":
                    self._finish(job, "FAILED", reason)
                    return "failed"
                job.status = "QUEUED"
                job.reason = reason
                job.attempt = max(0, int(job.attempt or 0) - 1)
                job.available_at = utc_now() + timedelta(seconds=retry_after)
                self.db.commit()
                return "requeued"
            reservation = self._reserve(job)
            if reservation is None:
                job.status = "QUEUED"
                job.reason = "waiting_capacity"
                job.attempt = max(0, int(job.attempt or 0) - 1)
                job.available_at = utc_now() + timedelta(seconds=1)
                self.db.commit()
                return "requeued"
        try:
            if isinstance(job, SandboxJob):
                self.db.refresh(job)
                if job.cancel_requested or job.status == "CANCELLED":
                    self._finish(job, "CANCELLED", "cancelled_by_user")
                    return "failed"
            self._execute_job(job)
            return "started"
        except AppError as exc:
            if exc.code in CAPACITY_CODES:
                job.status = "QUEUED"
                job.reason = "waiting_capacity"
                job.attempt = max(0, int(job.attempt or 0) - 1)
                job.available_at = utc_now() + timedelta(seconds=1)
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
            if reservation is not None:
                self._release_reservation(reservation.id)
            notify_job_change()

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
        if job.kind == "subagent":
            self._execute_subagent(job)
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

    def _subagent_active_count(
        self, *, workspace_id: str | None = None, owner_user_id: str | None = None,
        chat_session_id: str | None = None, exclude_job_id: str | None = None,
    ) -> int:
        conditions = [SandboxJob.kind == "subagent", SandboxJob.status.in_(("STARTING", "RUNNING"))]
        if exclude_job_id is not None:
            conditions.append(SandboxJob.id != exclude_job_id)
        if workspace_id is not None:
            conditions.append(SandboxJob.workspace_id == workspace_id)
        if owner_user_id is not None:
            conditions.append(SandboxJob.owner_user_id == owner_user_id)
        if chat_session_id is not None:
            conditions.append(SandboxJob.chat_session_id == chat_session_id)
        return int(self.db.scalar(select(func.count(SandboxJob.id)).where(*conditions)) or 0)

    def _subagent_quota_exceeded(self, job: SandboxJob) -> str | None:
        """Count other executing jobs; queued jobs do not occupy a lane."""
        settings = self.settings
        if (
            settings.sandbox_subagent_max_concurrent_chat > 0
            and self._subagent_active_count(chat_session_id=job.chat_session_id, exclude_job_id=job.id) >= settings.sandbox_subagent_max_concurrent_chat
        ):
            return "subagent_chat_quota"
        if (
            settings.sandbox_subagent_max_concurrent_user > 0
            and self._subagent_active_count(owner_user_id=job.owner_user_id, exclude_job_id=job.id) >= settings.sandbox_subagent_max_concurrent_user
        ):
            return "subagent_user_quota"
        if (
            settings.sandbox_subagent_max_concurrent_workspace > 0
            and self._subagent_active_count(workspace_id=job.workspace_id, exclude_job_id=job.id) >= settings.sandbox_subagent_max_concurrent_workspace
        ):
            return "subagent_workspace_quota"
        return None

    def _execute_subagent(self, job: SandboxJob) -> None:
        """Run one claimed sub-agent job through the durable executor."""
        from app.services.sandbox_agent_executor import execute_subagent_job

        payload = job.payload_json or {}
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            self._finish(job, "FAILED", "invalid_job", "subagent payload missing task_id")
            return
        task = self.db.get(SandboxAgentTask, task_id)
        if (
            task is None
            or task.workspace_id != job.workspace_id
            or task.owner_user_id != job.owner_user_id
            or task.chat_session_id != job.chat_session_id
        ):
            self._finish(job, "FAILED", "invalid_job", "subagent task not found")
            return
        quota_reason = self._subagent_quota_exceeded(job)
        if quota_reason:
            job.status = "QUEUED"
            job.reason = quota_reason
            job.available_at = utc_now() + timedelta(seconds=5)
            self.db.commit()
            return
        job.status = "RUNNING"
        job.started_at = utc_now()
        task.status = "RUNNING"
        task.latest_job_id = job.id
        task.started_at = utc_now()
        self.db.commit()

        def _emit(event_type: str, event_payload: dict[str, Any]) -> None:
            try:
                append_agent_event(self.db, task, event_type, event_payload)
                self.db.commit()
            except Exception:  # noqa: BLE001 - event persistence must not kill the run
                self.db.rollback()
                logger.exception("failed to persist sub-agent event %s", event_type)

        claimed_attempt = job.attempt

        def _save_checkpoint(checkpoint: dict[str, Any]) -> None:
            payload = {**dict(job.payload_json or {}), "_resume_checkpoint": checkpoint}
            changed = self.db.execute(update(SandboxJob).where(
                SandboxJob.id == job.id, SandboxJob.status == "RUNNING",
                SandboxJob.attempt == claimed_attempt,
            ).values(payload_json=payload))
            if changed.rowcount != 1:
                self.db.rollback()
                raise RuntimeError("Sub-agent checkpoint lost execution ownership")
            self.db.commit()

        outcome = execute_subagent_job(
            self.settings, job, task, emit_event=_emit, save_checkpoint=_save_checkpoint
        )
        retry = outcome.status == "RETRYING" and job.attempt < MAX_JOB_ATTEMPTS
        status = "QUEUED" if retry else ("FAILED" if outcome.status == "RETRYING" else outcome.status)
        # Cancellation/revision may have arrived while the model was disconnected.
        self.db.refresh(job)
        self.db.refresh(task)
        if job.cancel_requested or job.status == "CANCELLED" or task.status == "CANCELLED":
            retry, status = False, "CANCELLED"
        task.status = status
        task.status_reason = outcome.error_class
        if status == "PAUSED" and ((task.spec_json or {}).get("takeover_requested") or outcome.error_class == "WaitingApproval"):
            from app.services.managed_agent_session import ensure_child_session
            child = ensure_child_session(self.db, task, job)
            child.context_capsule = {**child.context_capsule, "managed_agent": {
                **child.context_capsule["managed_agent"], "controller": "user"}}
        task.deliverables_json = outcome.deliverables
        task.result_text = (outcome.summary or "")[:16_000]
        record = dict(outcome.attempt_record)
        record.update(
            {
                "status": "RETRYING" if retry else status,
                "error_class": outcome.error_class,
                "error_message": outcome.error_message,
            }
        )
        attempts = list(task.attempts_json or [])
        attempts.append(record)
        task.attempts_json = attempts
        task.finished_at = None if status in {"PAUSED", "QUEUED"} else utc_now()
        if retry:
            delay = min(30, 2 ** max(0, job.attempt - 1))
            job.status, job.reason = "QUEUED", "model_transport_retry"
            job.error_class, job.error_message = outcome.error_class, outcome.error_message
            job.finished_at = None
            job.available_at = utc_now() + timedelta(seconds=delay)
            append_agent_event(self.db, task, "retry_scheduled", {
                "attempt": job.attempt + 1, "max_attempts": MAX_JOB_ATTEMPTS,
                "delay_seconds": delay, "error_class": outcome.error_class,
                "completed_tool_calls": outcome.attempt_record.get("tool_calls", 0),
                "progress_summary": "模型连接暂时中断，将自动重试并从已完成的操作继续。",
            })
            self.db.commit()
            notify_job_change()
            return
        if outcome.status == "RETRYING":
            append_agent_event(self.db, task, status.lower(), {
                "error_class": outcome.error_class, "reason": "model_retry_exhausted" if status == "FAILED" else "cancelled_by_user",
            })
        self.db.commit()
        try:
            from app.voice.coordinator import capture_voice_task_outcome

            capture_voice_task_outcome(self.db, task)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("failed to capture voice result for task %s", task.id)
        self._finish(job, status, outcome.error_class, outcome.error_message)

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
        claimed = self.db.execute(update(SandboxJob).where(
            SandboxJob.id==job.id,SandboxJob.status=='STARTING',SandboxJob.cancel_requested.is_(False)
        ).values(status='RUNNING',reason=None,started_at=utc_now()))
        if claimed.rowcount != 1:
            self.db.rollback()
            return
        self.db.commit()
        notify_job_change()
        try:
            command = svc.execute_command(request, idempotency_key=idempotency_key, job_id=job.id)
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(job)
        if command.job_id is None:
            command.job_id = job.id
        if job.cancel_requested:
            self._finish(job, "CANCELLED", "cancelled_by_user")
        elif command.status == "completed":
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
        job.error_message = error_message
        if job.started_at is None:
            job.started_at = utc_now()
        job.finished_at = utc_now()
        if job.kind == "subagent" and status in {"FAILED", "CANCELLED"}:
            task_id = (job.payload_json or {}).get("task_id")
            task = self.db.get(SandboxAgentTask, task_id) if task_id else None
            if task is not None and task.status in {"QUEUED", "STARTING", "RUNNING", "PAUSED"}:
                task.status, task.status_reason = status, error_class
                task.finished_at = job.finished_at
                append_agent_event(self.db, task, status.lower(), {
                    "job_id": job.id, "reason": error_class,
                })
        self.db.commit()


def run_scheduler_tick() -> dict[str, int]:
    """Entry point for the periodic scheduler loop (see scheduler.py)."""
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        return SandboxSchedulerService(db, get_settings_cached()).schedule_once()


def get_settings_cached():
    from app.core.config import get_settings

    return get_settings()
