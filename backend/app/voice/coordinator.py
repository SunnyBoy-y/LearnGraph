"""Deterministic coordination for durable voice task results.

This module deliberately contains no model calls and no audio code. It owns the
small state machine that sits between an existing ``SandboxAgentTask`` and the
foreground tutor:

* task links freeze the originating voice session, turn, requirement and policy;
* terminal task states are projected into a durable result inbox once;
* requirement revisions invalidate older ready results;
* delivery is idempotent and is never inferred from reconnects.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    SandboxAgentTask,
    VoiceResultInboxRecord,
    VoiceSpeechDeliveryRecord,
    VoiceTaskLinkRecord,
    utc_now,
)
from app.repositories.domain import (
    VoiceResultInboxRepository,
    VoiceSpeechDeliveryRepository,
    VoiceTaskLinkRepository,
)

TERMINAL_TASK_STATUSES = frozenset(
    {"SUCCEEDED", "PARTIAL", "FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"}
)
SUCCESS_TASK_STATUSES = frozenset({"SUCCEEDED", "PARTIAL"})

VOICE_TASK_ROLES = frozenset({"generic", "research", "reason", "tool"})
VOICE_ROLE_TOOL_POLICY: dict[str, tuple[str, ...]] = {
    "generic": (),
    # Research is server-selected, never client-selected. The task executor
    # resolves availability; an unavailable name fails closed there.
    "research": (
        "search_web",
        "fetch_web_page",
        "sandbox_download",
        "parallel_web_research",
    ),
    # A pure reasoning task must not inherit sandbox/file tools.
    "reason": (),
    "tool": (),
}
KNOWN_VOICE_TOOLS = frozenset(
    {
        "search_web",
        "fetch_web_page",
        "parallel_web_research",
        "sandbox_download",
        "sandbox_env_info",
        "sandbox_list_files",
        "sandbox_grep",
        "sandbox_read_file",
        "sandbox_write_file",
        "sandbox_append_file",
        "sandbox_edit_file",
        "sandbox_delete_file",
        "sandbox_exec",
        "sandbox_bash",
        "sandbox_todo",
        "sandbox_apply_patch",
        "sandbox_git",
        "sandbox_skill_list",
        "sandbox_skill_read",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_voice_role(role_key: str | None) -> str:
    normalized = str(role_key or "generic").strip().casefold()
    return normalized if normalized in VOICE_TASK_ROLES else "generic"


def resolve_voice_tool_policy(
    role_key: str | None, requested_tools: list[str] | None
) -> list[str]:
    """Return the server-owned tool allow-list for a voice task role."""
    role = normalize_voice_role(role_key)
    if role == "tool":
        requested = [str(item).strip() for item in (requested_tools or ()) if item]
        return sorted({item for item in requested if item in KNOWN_VOICE_TOOLS})
    return list(VOICE_ROLE_TOOL_POLICY[role])


def resolve_voice_write_policy(
    role_key: str | None, requested_write_set: list[str] | None
) -> list[str]:
    """Tool role may write only to explicitly requested relative work paths."""
    role = normalize_voice_role(role_key)
    if role != "tool":
        return []
    normalized: list[str] = []
    for item in requested_write_set or ():
        value = str(item or "").strip().replace("\\", "/").lstrip("/")
        if not value or value.startswith("../") or "/../" in value:
            continue
        if value.startswith("work/") and value not in normalized:
            normalized.append(value[:240])
    return normalized


def _task_fingerprint(task: SandboxAgentTask) -> str:
    payload = {
        "task_id": task.task_id,
        "status": task.status,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "result_text": task.result_text or "",
        "deliverables": task.deliverables_json or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "voice-result:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _result_payload(task: SandboxAgentTask) -> tuple[dict[str, Any], str, int, str]:
    deliverables = dict(task.deliverables_json or {})
    agent_result = deliverables.get("agent_result")
    if not isinstance(agent_result, dict):
        agent_result = {}
    summary = str(
        agent_result.get("short_answer")
        or deliverables.get("short_answer")
        or deliverables.get("summary")
        or task.result_text
        or ""
    ).strip()
    if summary and "summary" not in deliverables:
        deliverables["summary"] = summary[:4_000]
    sources = (
        agent_result.get("sources")
        or deliverables.get("sources")
        or deliverables.get("evidence")
        or []
    )
    if not isinstance(sources, list):
        sources = []
    if agent_result:
        deliverables["agent_result"] = agent_result
    safe_error = ""
    if task.status not in SUCCESS_TASK_STATUSES:
        safe_error = str(task.status_reason or task.status or "agent_task_failed")[:160]
    return deliverables, summary[:4_000], len(sources), safe_error


def _find_link_for_task(
    db: Session, task: SandboxAgentTask
) -> VoiceTaskLinkRecord | None:
    return db.scalar(
        select(VoiceTaskLinkRecord).where(
            VoiceTaskLinkRecord.workspace_id == task.workspace_id,
            VoiceTaskLinkRecord.subagent_id == task.task_id,
        )
    )


def capture_voice_task_outcome(
    db: Session, task: SandboxAgentTask
) -> VoiceResultInboxRecord | None:
    """Project one terminal Agent task into the durable voice result inbox.

    Idempotent on the task's content fingerprint. The scheduler calls this in
    the same transaction as the task's terminal fields, so a disconnected client
    cannot lose a completed result.
    """

    link = _find_link_for_task(db, task)
    if link is None:
        return None
    if task.status not in TERMINAL_TASK_STATUSES:
        link.status = task.status
        link.last_observed_event_seq = max(
            int(link.last_observed_event_seq or 0), int(task.event_seq or 0)
        )
        return None

    repo = VoiceResultInboxRepository(db, task.workspace_id)
    fingerprint = _task_fingerprint(task)
    existing = repo.get_by_dedupe_key(fingerprint, tenant_id=link.tenant_id)
    if existing is not None:
        link.status = task.status
        link.last_observed_event_seq = max(
            int(link.last_observed_event_seq or 0), int(task.event_seq or 0)
        )
        db.commit()
        return existing

    payload, summary, source_count, safe_error = _result_payload(task)
    status = "ready" if task.status in SUCCESS_TASK_STATUSES else "failed"
    result_version = max(0, int(link.latest_result_version or 0)) + 1
    result = VoiceResultInboxRecord(
        workspace_id=task.workspace_id,
        tenant_id=link.tenant_id,
        voice_session_id=link.voice_session_id,
        task_link_id=link.id,
        subagent_id=link.subagent_id,
        requirement_version=max(1, int(link.requirement_version or 1)),
        result_version=result_version,
        dedupe_key=fingerprint,
        status=status,
        result_type=(
            "agent_result"
            if task.status in SUCCESS_TASK_STATUSES
            else "cancelled"
            if task.status == "CANCELLED"
            else "agent_failure"
        ),
        summary=summary,
        payload_json=payload,
        source_count=source_count,
        safe_error=safe_error,
        captured_at=utc_now(),
        available_at=utc_now(),
    )
    link.latest_result_version = result_version
    link.last_observed_event_seq = max(
        int(link.last_observed_event_seq or 0), int(task.event_seq or 0)
    )
    link.status = task.status
    if task.status == "CANCELLED" and link.cancel_requested_at is not None:
        link.cancel_acknowledged_at = utc_now()
        link.cancel_checkpoint_json = {
            **dict(link.cancel_checkpoint_json or {}),
            "observed_status": task.status,
            "observed_event_seq": int(task.event_seq or 0),
            "acknowledged_at": link.cancel_acknowledged_at.isoformat(),
        }
    elif link.cancel_requested_at is not None:
        link.cancel_checkpoint_json = {
            **dict(link.cancel_checkpoint_json or {}),
            "observed_status": task.status,
            "observed_event_seq": int(task.event_seq or 0),
        }
    savepoint = db.begin_nested()
    try:
        db.add(result)
        db.flush()
    except IntegrityError:
        savepoint.rollback()
        return repo.get_by_dedupe_key(fingerprint, tenant_id=link.tenant_id)
    savepoint.commit()
    return result


class VoiceTaskCoordinator:
    """Narrow repository/state-machine interface used by the voice service."""

    def __init__(self, db: Session, *, workspace_id: str, tenant_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.tenant_id = tenant_id
        self.links = VoiceTaskLinkRepository(db, workspace_id)
        self.results = VoiceResultInboxRepository(db, workspace_id)
        self.deliveries = VoiceSpeechDeliveryRepository(db, workspace_id)

    def reserve_link(
        self,
        *,
        voice_session_id: str,
        chat_session_id: str,
        prompt: str,
        title: str,
        role_key: str,
        thinking_mode: str,
        trigger_turn_id: str | None,
        idempotency_key: str | None,
        requirement_version: int = 1,
    ) -> tuple[VoiceTaskLinkRecord, bool]:
        existing = self.links.get_by_idempotency_key(
            idempotency_key or "", tenant_id=self.tenant_id
        )
        if existing is not None:
            return existing, False
        link = VoiceTaskLinkRecord(
            id=f"vtl_{uuid4().hex[:24]}",
            workspace_id=self.workspace_id,
            tenant_id=self.tenant_id,
            voice_session_id=voice_session_id,
            chat_session_id=chat_session_id,
            trigger_turn_id=trigger_turn_id,
            subagent_id=f"pending_{uuid4().hex[:20]}",
            idempotency_key=idempotency_key,
            status="creating",
            thinking_mode=thinking_mode,
            title=title,
            original_prompt=prompt,
            current_prompt=prompt,
            requirement_version=max(1, int(requirement_version)),
            latest_result_version=0,
            allowed_tools=resolve_voice_tool_policy(role_key, None),
            auto_delivery=True,
        )
        self.db.add(link)
        try:
            self.db.commit()
            self.db.refresh(link)
        except IntegrityError:
            self.db.rollback()
            existing = self.links.get_by_idempotency_key(
                idempotency_key or "", tenant_id=self.tenant_id
            )
            if existing is None:
                raise
            return existing, False
        return link, True

    def complete_link(
        self,
        link: VoiceTaskLinkRecord,
        *,
        subagent_id: str,
        job_id: str | None,
        status: str,
    ) -> VoiceTaskLinkRecord:
        link.subagent_id = subagent_id
        link.latest_job_id = job_id
        link.status = status
        self.db.commit()
        self.db.refresh(link)
        return link

    def find_link(
        self, voice_session_id: str, subagent_id: str
    ) -> VoiceTaskLinkRecord | None:
        return self.links.get_by_subagent(
            voice_session_id, subagent_id, tenant_id=self.tenant_id
        )

    def required_link(
        self, voice_session_id: str, subagent_id: str
    ) -> VoiceTaskLinkRecord:
        link = self.find_link(voice_session_id, subagent_id)
        if link is None:
            raise AppError(
                404, "voice_task_not_found", "Task is not linked to this voice session"
            )
        return link

    def list_links(self, voice_session_id: str) -> list[VoiceTaskLinkRecord]:
        return self.links.list_for_session(
            voice_session_id, tenant_id=self.tenant_id
        )

    def reconcile_link(
        self, link: VoiceTaskLinkRecord, snapshot: dict[str, Any]
    ) -> VoiceResultInboxRecord | None:
        """Apply a toolkit task snapshot and capture a terminal result."""
        task = self.db.scalar(
            select(SandboxAgentTask).where(
                SandboxAgentTask.workspace_id == self.workspace_id,
                SandboxAgentTask.task_id == link.subagent_id,
            )
        )
        if task is not None:
            return capture_voice_task_outcome(self.db, task)
        status = str(snapshot.get("status") or link.status)
        link.status = status
        link.last_observed_event_seq = max(
            int(link.last_observed_event_seq or 0),
            int(snapshot.get("event_seq") or 0),
        )
        self.db.commit()
        return None

    def request_cancel(self, link: VoiceTaskLinkRecord, *, reason: str) -> None:
        if link.status in TERMINAL_TASK_STATUSES:
            return
        now = utc_now()
        link.cancel_requested_at = link.cancel_requested_at or now
        link.cancel_checkpoint_json = {
            **dict(link.cancel_checkpoint_json or {}),
            "phase": "requested",
            "reason": reason[:160],
            "requested_at": link.cancel_requested_at.isoformat(),
            "job_id": link.latest_job_id,
        }
        self.db.commit()

    def acknowledge_cancel(
        self, link: VoiceTaskLinkRecord, snapshot: dict[str, Any]
    ) -> None:
        status = str(snapshot.get("status") or link.status)
        link.status = status
        link.last_observed_event_seq = max(
            int(link.last_observed_event_seq or 0),
            int(snapshot.get("event_seq") or 0),
        )
        if status in TERMINAL_TASK_STATUSES:
            link.cancel_acknowledged_at = utc_now()
        link.cancel_checkpoint_json = {
            **dict(link.cancel_checkpoint_json or {}),
            "phase": "acknowledged"
            if link.cancel_acknowledged_at is not None
            else "pending",
            "observed_status": status,
            "observed_event_seq": int(link.last_observed_event_seq or 0),
            "job_id": snapshot.get("latest_job_id") or link.latest_job_id,
            "acknowledged_at": (
                link.cancel_acknowledged_at.isoformat()
                if link.cancel_acknowledged_at is not None
                else None
            ),
        }
        self.db.commit()

    def revise_requirement(
        self,
        link: VoiceTaskLinkRecord,
        *,
        prompt: str,
        title: str | None = None,
        note: str = "",
    ) -> VoiceTaskLinkRecord:
        now = utc_now()
        link.requirement_version = max(1, int(link.requirement_version or 1)) + 1
        link.current_prompt = prompt
        if title is not None:
            link.title = title
        link.status = "revision_pending"
        link.cancel_requested_at = now
        link.cancel_acknowledged_at = None
        link.cancel_checkpoint_json = {
            **dict(link.cancel_checkpoint_json or {}),
            "phase": "superseded",
            "note": note[:500],
            "requested_at": now.isoformat(),
            "previous_requirement_version": link.requirement_version - 1,
        }
        rows = list(
            self.db.scalars(
                select(VoiceResultInboxRecord).where(
                    VoiceResultInboxRecord.workspace_id == self.workspace_id,
                    VoiceResultInboxRecord.tenant_id == self.tenant_id,
                    VoiceResultInboxRecord.task_link_id == link.id,
                    VoiceResultInboxRecord.requirement_version
                    < link.requirement_version,
                    VoiceResultInboxRecord.status.in_(("pending", "ready")),
                )
            ).all()
        )
        for result in rows:
            result.status = "stale"
            result.stale_at = now
        self.db.commit()
        self.db.refresh(link)
        return link

    def mark_cancel_superseded(
        self, link: VoiceTaskLinkRecord, *, status: str
    ) -> None:
        link.cancel_acknowledged_at = utc_now()
        link.status = status
        link.cancel_checkpoint_json = {
            **dict(link.cancel_checkpoint_json or {}),
            "phase": "superseded",
            "observed_status": status,
            "acknowledged_at": link.cancel_acknowledged_at.isoformat(),
        }
        self.db.commit()

    def complete_revision(
        self, link: VoiceTaskLinkRecord, snapshot: dict[str, Any]
    ) -> None:
        """Acknowledge that the replacement execution for a revision was accepted."""
        status = str(snapshot.get("status") or "queued")
        link.status = status
        link.latest_job_id = str(
            snapshot.get("latest_job_id") or snapshot.get("job_id") or ""
        ) or link.latest_job_id
        link.cancel_acknowledged_at = utc_now()
        link.cancel_checkpoint_json = {
            **dict(link.cancel_checkpoint_json or {}),
            "phase": "superseded",
            "observed_status": status,
            "replacement_job_id": link.latest_job_id,
            "acknowledged_at": link.cancel_acknowledged_at.isoformat(),
        }
        self.db.commit()
        self.db.refresh(link)

    def list_results(
        self,
        voice_session_id: str,
        *,
        include_terminal: bool = True,
        limit: int = 50,
    ) -> list[VoiceResultInboxRecord]:
        return self.results.list_for_session(
            voice_session_id,
            tenant_id=self.tenant_id,
            include_terminal=include_terminal,
            limit=limit,
        )

    def get_result(
        self, voice_session_id: str, result_id: str
    ) -> VoiceResultInboxRecord:
        result = self.db.scalar(
            select(VoiceResultInboxRecord).where(
                VoiceResultInboxRecord.id == result_id,
                VoiceResultInboxRecord.workspace_id == self.workspace_id,
                VoiceResultInboxRecord.tenant_id == self.tenant_id,
                VoiceResultInboxRecord.voice_session_id == voice_session_id,
            )
        )
        if result is None:
            raise AppError(404, "voice_result_not_found", "Voice result was not found")
        return result

    def deliver_result(
        self,
        *,
        voice_session_id: str,
        result_id: str,
        request_id: str,
    ) -> tuple[VoiceResultInboxRecord, VoiceSpeechDeliveryRecord, bool]:
        result = self.get_result(voice_session_id, result_id)
        existing = self.deliveries.get_by_idempotency_key(
            request_id, tenant_id=self.tenant_id
        )
        if existing is not None:
            if existing.result_id != result.id:
                raise AppError(
                    409,
                    "voice_delivery_idempotency_conflict",
                    "Delivery idempotency key was already used for another result",
                )
            return result, existing, False
        if result.status == "stale":
            raise AppError(409, "voice_result_stale", "Voice result is stale")
        if result.status == "dismissed":
            raise AppError(409, "voice_result_dismissed", "Voice result was dismissed")
        delivered = self.deliveries.get_by_result(
            result.id, tenant_id=self.tenant_id
        )
        if delivered is not None:
            return result, delivered, False
        link = self.db.get(VoiceTaskLinkRecord, result.task_link_id)
        if link is not None and int(result.requirement_version) < int(
            link.requirement_version or 1
        ):
            result.status = "stale"
            result.stale_at = utc_now()
            self.db.commit()
            raise AppError(409, "voice_result_stale", "Voice result is stale")
        now = utc_now()
        delivery = VoiceSpeechDeliveryRecord(
            workspace_id=self.workspace_id,
            tenant_id=self.tenant_id,
            voice_session_id=voice_session_id,
            result_id=result.id,
            subagent_id=result.subagent_id,
            requirement_version=result.requirement_version,
            result_version=result.result_version,
            idempotency_key=request_id,
            speech_id=f"speech_{uuid4().hex[:24]}",
            status="delivered",
            claimed_at=now,
            completed_at=now,
            snapshot={
                "summary": result.summary[:1_000],
                "source_count": result.source_count,
                "result_status_before_delivery": result.status,
            },
        )
        if result.status != "failed":
            result.status = "delivered"
        result.delivered_at = now
        self.db.add(delivery)
        self.db.commit()
        self.db.refresh(result)
        self.db.refresh(delivery)
        return result, delivery, True

    def release_delivery(
        self,
        *,
        voice_session_id: str,
        result_id: str,
        request_id: str,
    ) -> VoiceResultInboxRecord:
        """Undo a claimed handoff when the Tutor frame was not enqueued."""

        result = self.get_result(voice_session_id, result_id)
        delivery = self.deliveries.get_by_idempotency_key(
            request_id, tenant_id=self.tenant_id
        )
        if delivery is None or delivery.result_id != result.id:
            return result
        previous_status = str(
            (delivery.snapshot or {}).get("result_status_before_delivery") or "ready"
        )
        if result.status in {"delivered", "failed"}:
            result.status = previous_status if previous_status in {
                "ready",
                "failed",
                "stale",
            } else "ready"
            result.delivered_at = None
        self.db.delete(delivery)
        self.db.commit()
        self.db.refresh(result)
        return result
    def dismiss_result(
        self, *, voice_session_id: str, result_id: str
    ) -> VoiceResultInboxRecord:
        result = self.get_result(voice_session_id, result_id)
        if result.status in {"ready", "stale"}:
            result.status = "dismissed"
            result.dismissed_at = utc_now()
            self.db.commit()
            self.db.refresh(result)
        return result
