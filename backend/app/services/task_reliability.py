"""Task execution metrics and process-local worker signals."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from app.domain.extension_models import ScheduledExtensionRun, ScheduledExtensionTask
from app.domain.models import AuditEvent, DurableJob, utc_now
from app.domain.task_reliability_models import DurableJobAttempt

_worker_lock = threading.Lock()
_workers: dict[str, dict] = {}
_FAULTS = ("retryable_failure", "failed", "lease_expired")


def worker_update(lane: str, *, status: str, polled: bool = False, restarted: bool = False) -> None:
    now = utc_now()
    with _worker_lock:
        state = _workers.setdefault(lane, {"restarts": 0, "last_successful_poll_at": None})
        state.update(status=status, last_progress_at=now)
        if polled:
            state["last_successful_poll_at"] = now
        if restarted:
            state["restarts"] += 1


def _age(now: datetime, then: datetime | None) -> float | None:
    if then is None:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0, (now - then).total_seconds())


def durable_worker_health(db, settings) -> dict:
    if not settings.durable_queue_enabled:
        return {"enabled": False}
    from app.services.durable_queue import CHAT_GOAL_JOB_KIND

    now = utc_now()
    with _worker_lock:
        states = {name: dict(state) for name, state in _workers.items()}
    lanes = {}
    for name, scope in (("general", DurableJob.kind != CHAT_GOAL_JOB_KIND), ("goal", DurableJob.kind == CHAT_GOAL_JOB_KIND)):
        state = states.get(name, {"status": "starting", "restarts": 0, "last_successful_poll_at": None})
        progress_age = _age(now, state.get("last_progress_at"))
        oldest = db.scalar(select(func.min(DurableJob.available_at)).where(
            scope, DurableJob.status == "queued", DurableJob.available_at <= now,
        ))
        queue_age = _age(now, oldest)
        lanes[name] = {**state, "oldest_ready_seconds": queue_age,
                       "degraded": (state["status"] != "running" or progress_age is None
                                    or progress_age > 60 or (queue_age is not None and queue_age > 300))}
    return {"enabled": True, "degraded": any(lane["degraded"] for lane in lanes.values()), "lanes": lanes}


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def task_execution_metrics(db, workspace_id: str, days: int) -> dict:
    now = utc_now()
    since = now - timedelta(days=days)
    attempt = DurableJobAttempt
    run = ScheduledExtensionRun
    task = ScheduledExtensionTask
    buckets = {}
    scope = (attempt.workspace_id == workspace_id, attempt.started_at >= since)
    for kind, outcome, error_code, count in db.execute(select(
        attempt.kind, attempt.outcome, attempt.error_code, func.count(),
    ).where(*scope).group_by(attempt.kind, attempt.outcome, attempt.error_code)):
        bucket = buckets.setdefault(kind, {"outcomes": {}, "error_codes": {}})
        bucket["outcomes"][outcome] = bucket["outcomes"].get(outcome, 0) + count
        if error_code:
            bucket["error_codes"][error_code] = bucket["error_codes"].get(error_code, 0) + count

    def count_if(condition):
        return func.sum(case((condition, 1), else_=0))

    for kind, first, first_failed, recovered, failures in db.execute(select(
        attempt.kind,
        count_if((attempt.attempt_number == 1) & attempt.outcome.in_((*_FAULTS, "succeeded"))),
        count_if((attempt.attempt_number == 1) & attempt.outcome.in_(_FAULTS)),
        count_if((attempt.attempt_number > 1) & (attempt.outcome == "succeeded")),
        count_if(attempt.outcome.in_(_FAULTS)),
    ).where(*scope).group_by(attempt.kind)):
        bucket = buckets[kind]
        bucket["first_attempt_failure_rate"] = _ratio(first_failed, first)
        bucket["attempt_failures"] = failures
        bucket["recovered_attempts"] = recovered

    # Job-level rates use jobs with observed attempts in the reporting window.
    observed = (select(attempt.job_id, func.max(case((attempt.outcome.in_(_FAULTS), 1), else_=0)).label("had_failure"))
                .where(*scope).group_by(attempt.job_id).subquery())
    for kind, completed, failed, affected, recovered in db.execute(select(
        DurableJob.kind, count_if(DurableJob.status == "completed"), count_if(DurableJob.status == "failed"),
        count_if((observed.c.had_failure == 1) & DurableJob.status.in_(("completed", "failed"))),
        count_if((observed.c.had_failure == 1) & (DurableJob.status == "completed")),
    ).join(observed, observed.c.job_id == DurableJob.id).group_by(DurableJob.kind)):
        buckets[kind]["final_failure_rate"] = _ratio(failed, completed + failed)
        buckets[kind]["automatic_recovery_rate"] = _ratio(recovered, affected)

    ready = db.execute(select(func.count(), func.min(DurableJob.available_at)).where(
        DurableJob.workspace_id == workspace_id, DurableJob.status == "queued", DurableJob.available_at <= now,
    )).one()
    expired = db.scalar(select(func.count()).select_from(DurableJob).where(
        DurableJob.workspace_id == workspace_id, DurableJob.status == "leased", DurableJob.lease_expires_at <= now,
    ))
    scheduled = [{"task_type": kind, "trigger": trigger, "status": status, "error_code": code, "count": n}
                 for kind, trigger, status, code, n in db.execute(select(
                     task.task_type, run.trigger, run.status, run.error_code, func.count(),
                 ).select_from(run).join(task, (task.id == run.task_id) & (task.workspace_id == run.workspace_id))
                  .where(run.workspace_id == workspace_id, run.started_at >= since)
                  .group_by(task.task_type, run.trigger, run.status, run.error_code))]
    recovered_dispatches = db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.workspace_id == workspace_id, AuditEvent.created_at >= since,
        AuditEvent.action == "scheduled_task.recovered_dispatch",
    ))
    pending = db.execute(select(func.count(), func.min(run.started_at)).where(
        run.workspace_id == workspace_id, run.status == "running", run.dispatched_at.is_(None),
    )).one()
    return {"since": since, "as_of": now, "durable_jobs": buckets, "scheduled_runs": scheduled,
            "scheduled_dispatch": {"recovered": recovered_dispatches, "pending": pending[0],
                                   "oldest_pending_seconds": _age(now, pending[1])},
            "queue": {"ready": ready[0], "oldest_ready_seconds": _age(now, ready[1]), "expired_leases": expired}}
