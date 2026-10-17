"""Cooperative task pause/resume, independent of the audio lifecycle."""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.core.errors import AppError
from app.domain.models import SandboxAgentTask, SandboxJob, utc_now
from app.services.sandbox_scheduler import append_agent_event
from app.voice.coordinator import VoiceTaskCoordinator


def control_task(coordinator: VoiceTaskCoordinator, *, voice_session_id: str,
                 task_id: str, operation: str, actor_id: str) -> dict[str, Any]:
    link = coordinator.required_link(voice_session_id, task_id)
    db = coordinator.db
    task = db.scalar(select(SandboxAgentTask).where(
        SandboxAgentTask.workspace_id == coordinator.workspace_id,
        SandboxAgentTask.owner_user_id == actor_id,
        SandboxAgentTask.task_id == task_id,
    ))
    if task is None:
        raise AppError(404, "voice_task_not_found", "Background task was not found")
    job = db.get(SandboxJob, task.latest_job_id) if task.latest_job_id else None
    if (job is None or job.workspace_id != coordinator.workspace_id or job.owner_user_id != actor_id
            or job.chat_session_id != task.chat_session_id
            or job.kind != "subagent" or (job.payload_json or {}).get("task_id") != task.id):
        raise AppError(409, "voice_task_job_missing", "Task has no resumable job")
    if (task.spec_json or {}).get("agent_session_id"):
        from app.services.managed_agent_session import control_child_session
        from app.core.config import get_settings
        result = control_child_session(db, get_settings(), task, operation)
        link.status = "pausing" if task.status_reason == "pause_requested" else result["status"].upper()
        db.commit()
        return {"task_id": task_id, "status": str(link.status).lower()}
    spec = dict(task.spec_json or {})
    if operation == "pause":
        if task.status == "PAUSED":
            return {"task_id": task_id, "status": "paused"}
        if task.status not in {"QUEUED", "RUNNING", "STARTING"} or job.cancel_requested:
            raise AppError(409, "voice_task_not_running", "Only an active task can be paused")
        spec["voice_pause_requested"] = True
        spec.setdefault("voice_pause_requested_at", datetime.now(timezone.utc).isoformat())
        task.spec_json = spec
        # CAS prevents freezing a job that another scheduler already claimed.
        frozen = db.execute(update(SandboxJob).where(
            SandboxJob.id == job.id, SandboxJob.status == "QUEUED",
        ).values(status="PAUSED")).rowcount
        if frozen:
            task.status = "PAUSED"
        else:
            task.status_reason = "pause_requested"
        link.status = "PAUSED" if frozen else "pausing"
        append_agent_event(db, task, "task.paused" if frozen else "task.pausing", {
            "task_id": task_id, "requirement_version": link.requirement_version,
            "status": "paused" if frozen else "pausing",
        })
    elif operation == "resume":
        if task.status != "PAUSED" or job.status != "PAUSED":
            raise AppError(409, "voice_task_not_paused", "Wait for the safe checkpoint before resuming")
        if job.cancel_requested:
            raise AppError(409, "voice_task_cancelled", "A cancelled task cannot resume")
        spec.pop("voice_pause_requested", None)
        paused_at = spec.pop("voice_pause_requested_at", None)
        if paused_at and job.deadline_at:
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(paused_at)
            job.deadline_at += elapsed
        task.spec_json = spec
        checkpoint = (task.deliverables_json or {}).get("_checkpoint")
        payload = dict(job.payload_json or {})
        if checkpoint:
            payload["_resume_checkpoint"] = checkpoint
            # A cooperative pause does not consume a scheduler retry attempt.
            job.attempt = max(0, int(job.attempt or 0) - 1)
        job.payload_json = payload
        job.status, job.available_at, job.finished_at = "QUEUED", utc_now(), None
        task.status, task.finished_at, task.status_reason = "QUEUED", None, None
        link.status = "QUEUED"
        append_agent_event(db, task, "task.resumed", {
            "task_id": task_id, "status": "queued", "requirement_version": link.requirement_version,
        })
    else:
        raise AppError(422, "voice_task_control_invalid", "Unsupported task control operation")
    db.commit()
    return {"task_id": task_id, "status": str(link.status).lower()}
