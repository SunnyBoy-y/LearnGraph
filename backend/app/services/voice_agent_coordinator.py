"""Deterministic voice-to-agent coordination over durable sandbox tasks.

This module is the only bridge the foreground Tutor needs for delegation.  It
does not call a model or write audio; it validates intent, creates a durable
``SandboxAgentTask`` through the existing toolkit/scheduler, and returns a fast
receipt.  Results are read back from the same task row and its typed
``agent_result`` deliverable.

Voice sessions, turns and revisions are deliberately stored in existing task
JSON fields.  That keeps the bridge additive and avoids a second scheduler,
result bus, or schema migration in this phase.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select

from app.core.errors import AppError
from app.domain.models import SandboxAgentTask, utc_now
from app.domain.schemas.sandbox import (
    AgentTaskResult,
    SandboxAgentSubagentCancelRequest,
    SandboxAgentSubagentRequest,
    SandboxAgentSubagentStatusRequest,
)
from app.services.agent_execution_profiles import (
    AgentProfileError,
    AgentRole,
    ToolSelectionMode,
    resolve_agent_execution_profile,
)
from app.services.sandbox_scheduler import append_agent_event


CONTEXT_MAX_CHARS = 24_000
TERMINAL_TASK_STATUSES = {
    "SUCCEEDED",
    "PARTIAL",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
    "STALE",
    "INTERRUPTED",
}
_RESULT_READY_STATUSES = {"SUCCEEDED", "PARTIAL", "FAILED", "TIMED_OUT", "STALE", "INTERRUPTED"}
_SECRET_KEY_FRAGMENTS = (
    "secret",
    "token",
    "password",
    "credential",
    "api_key",
    "authorization",
    "cookie",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_digest(payload: Mapping[str, Any], *, length: int = 20) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _bounded_context(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 5,
    max_items: int = 80,
    max_string: int = 4_000,
) -> Any:
    """Redact likely secrets and bound an untrusted context snapshot."""
    if depth >= max_depth:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_string]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= max_items:
                break
            key = str(raw_key)
            if any(fragment in key.casefold() for fragment in _SECRET_KEY_FRAGMENTS):
                continue
            result[key] = _bounded_context(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _bounded_context(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
            )
            for item in value[:max_items]
        ]
    return str(value)[:max_string]


def _tools_value(profile: Any) -> list[str] | None:
    if profile.role is AgentRole.REASON:
        return []
    if profile.tool_mode is ToolSelectionMode.DEFAULT:
        return None
    if profile.tool_mode is ToolSelectionMode.NONE:
        return []
    return list(profile.tools)


class VoiceAgentCoordinator:
    """Create, observe and revise durable Tutor-owned background tasks."""

    def __init__(
        self,
        db: Any,
        settings: Any,
        *,
        workspace_id: str,
        actor_id: str,
        permissions: frozenset[str] | set[str] | None = None,
        task_service: Any = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.permissions = None if permissions is None else frozenset(permissions)
        self._task_service = task_service

    def _service(self) -> Any:
        if self._task_service is None:
            from app.services.sandbox import SandboxAgentWorkspaceService

            self._task_service = SandboxAgentWorkspaceService(
                self.db,
                self.workspace_id,
                self.actor_id,
                self.settings,
            )
        return self._task_service

    def _require_workspace_manage(self) -> None:
        if self.permissions is None:
            raise AppError(
                403,
                "voice_agent_permission_unverified",
                "A server-verified permission set is required to delegate a background Agent task",
            )
        if "workspace.manage" not in self.permissions:
            raise AppError(
                403,
                "voice_agent_permission_denied",
                "Workspace management permission is required to delegate a background Agent task",
            )

    def _load_task(self, task_id: str) -> SandboxAgentTask:
        task = self.db.scalar(
            select(SandboxAgentTask).where(
                SandboxAgentTask.workspace_id == self.workspace_id,
                SandboxAgentTask.owner_user_id == self.actor_id,
                SandboxAgentTask.task_id == task_id,
            )
        )
        if task is None:
            raise AppError(404, "voice_agent_task_not_found", "Background Agent task was not found")
        return task

    def _linked_tasks(
        self,
        *,
        voice_session_id: str,
        requirement_group_id: str | None = None,
        limit: int = 200,
    ) -> list[SandboxAgentTask]:
        candidates = self.db.scalars(
            select(SandboxAgentTask)
            .where(
                SandboxAgentTask.workspace_id == self.workspace_id,
                SandboxAgentTask.owner_user_id == self.actor_id,
            )
            .order_by(SandboxAgentTask.updated_at.desc())
            .limit(max(1, min(int(limit), 500)))
        ).all()
        linked: list[SandboxAgentTask] = []
        for task in candidates:
            spec = task.spec_json if isinstance(task.spec_json, dict) else {}
            association = spec.get("association")
            association_session = (
                association.get("voice_session_id")
                if isinstance(association, dict)
                else None
            )
            task_voice_session = (
                association_session or spec.get("voice_session_id")
            )
            if task_voice_session != voice_session_id:
                continue
            if requirement_group_id and spec.get("requirement_group_id") != requirement_group_id:
                continue
            linked.append(task)
        return linked

    def _result_view(self, task: SandboxAgentTask) -> dict[str, Any]:
        spec = dict(task.spec_json or {})
        deliverables = task.deliverables_json if isinstance(task.deliverables_json, dict) else {}
        raw_result = deliverables.get("agent_result")
        result: dict[str, Any] | None = None
        if isinstance(raw_result, dict):
            try:
                result = AgentTaskResult.model_validate(raw_result).model_dump(mode="json")
            except Exception:
                result = raw_result
        current_revision = int(
            spec.get("current_requirement_version")
            or spec.get("requirement_version")
            or 1
        )
        result_revision = (
            int(result.get("requirement_version") or 0) if isinstance(result, dict) else 0
        )
        is_stale = (
            task.status == "STALE"
            or bool(spec.get("superseded_by"))
            or (bool(result_revision) and result_revision < current_revision)
        )
        if is_stale:
            delivery_state = "stale"
        elif task.status not in TERMINAL_TASK_STATUSES:
            delivery_state = "pending"
        else:
            persisted_delivery_state = str(spec.get("delivery_state") or "")
            delivery_state = (
                persisted_delivery_state
                if persisted_delivery_state in {"delivered", "dismissed"}
                else "ready"
            )
        return {
            "task_id": task.task_id,
            "subagent_id": task.task_id,
            "job_id": task.latest_job_id,
            "title": task.title,
            "role_key": task.role_key,
            "status": task.status.casefold(),
            "status_reason": task.status_reason,
            "result_text": task.result_text,
            "agent_result": result,
            "deliverables": task.deliverables_json,
            "event_seq": task.event_seq,
            "requirement_group_id": spec.get("requirement_group_id"),
            "requirement_version": spec.get("requirement_version"),
            "current_requirement_version": current_revision,
            "thinking_mode_requested": spec.get("thinking_mode_requested"),
            "thinking_mode_effective": spec.get("thinking_mode_effective"),
            "execution_lane": spec.get("execution_lane"),
            "delivery_state": delivery_state,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        }

    def delegate(
        self,
        *,
        role_key: str,
        prompt: str | None = None,
        objective: str | None = None,
        chat_session_id: str,
        title: str = "",
        thinking_mode: str | None = None,
        tools: list[str] | tuple[str, ...] | None = None,
        skills: list[str] | None = None,
        write_set: list[str] | None = None,
        voice_session_id: str | None = None,
        turn_id: str | None = None,
        session_epoch: int | None = None,
        context_version: str | None = None,
        context: Mapping[str, Any] | None = None,
        requirement_group_id: str | None = None,
        requirement_version: int = 1,
    ) -> dict[str, Any]:
        """Accept a task quickly and return a durable receipt.

        This method only performs bounded JSON/database work.  It never waits
        for the background Agent to finish and never invokes TTS.
        """
        self._require_workspace_manage()
        request_text = str(objective or prompt or "").strip()
        if not request_text:
            raise AppError(422, "voice_agent_prompt_required", "Delegation requires a non-empty objective")
        profile = resolve_agent_execution_profile(role_key, tools=list(tools) if tools is not None else None)
        requested_thinking = str(thinking_mode or profile.default_thinking_mode).strip().casefold()
        normalized_tools = _tools_value(profile)
        group_digest_payload = {
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "voice_session_id": voice_session_id,
            "turn_id": turn_id,
            "chat_session_id": chat_session_id,
            "role": profile.role.value,
            "prompt": request_text,
            "requirement_version": int(requirement_version),
        }
        digest = _stable_digest(group_digest_payload)
        requirement_group_id = requirement_group_id or f"rg_{digest}"
        idempotency_key = (
            f"voice-agent:{digest}:v{int(requirement_version)}"
        )

        supplied_context = _bounded_context(dict(context or {}))
        context_snapshot = {
            "context": supplied_context,
            "context_version": context_version,
            "session_epoch": session_epoch,
        }
        if len(json.dumps(context_snapshot, ensure_ascii=False, default=str)) > CONTEXT_MAX_CHARS:
            context_snapshot["context"] = {
                "preview": json.dumps(
                    supplied_context, ensure_ascii=False, default=str
                )[:CONTEXT_MAX_CHARS]
            }

        request = SandboxAgentSubagentRequest(
            chat_session_id=chat_session_id,
            prompt=request_text,
            title=title or request_text[:80],
            role_key=profile.role.value,
            tools=normalized_tools,
            skills=skills,
            write_set=write_set,
            thinking_mode=requested_thinking,
            voice_session_id=voice_session_id,
            turn_id=turn_id,
            requirement_group_id=requirement_group_id,
            requirement_version=int(requirement_version),
            context_snapshot=context_snapshot,
            idempotency_key=idempotency_key,
        )
        created = self._service().toolkit_subagent(request)
        task_id = str(created.get("task_id") or created.get("subagent_id") or "")
        if not task_id:
            raise AppError(502, "voice_agent_task_not_created", "Task bridge returned no task id")
        task = self._load_task(task_id)
        spec = dict(task.spec_json or {})
        spec.update(
            {
                "input_snapshot": request.context_snapshot,
                "association": {
                    "voice_session_id": voice_session_id,
                    "turn_id": turn_id,
                    "chat_session_id": chat_session_id,
                    "session_epoch": session_epoch,
                    "context_version": context_version,
                },
                "requirement_group_id": requirement_group_id,
                "requirement_version": int(requirement_version),
                "current_requirement_version": int(requirement_version),
                "delivery_state": "pending",
            }
        )
        task.spec_json = spec
        self.db.commit()
        return {
            "task_id": task_id,
            "subagent_id": task_id,
            "job_id": task.latest_job_id,
            "status": "accepted",
            "role_key": profile.role.value,
            "title": task.title,
            "chat_session_id": chat_session_id,
            "voice_session_id": voice_session_id,
            "turn_id": turn_id,
            "requirement_group_id": requirement_group_id,
            "requirement_version": int(requirement_version),
            "thinking_mode_requested": requested_thinking,
            "thinking_mode_effective": None,
            "execution_lane": profile.execution_lane,
            "accepted_at": _utc_iso(),
            "idempotent_replay": bool(created.get("idempotent_replay")),
        }

    def get_task(self, task_id: str, *, after_event_seq: int | None = None) -> dict[str, Any]:
        task = self._load_task(task_id)
        snapshot = self._service().toolkit_subagent_status(
            SandboxAgentSubagentStatusRequest(
                chat_session_id=task.chat_session_id,
                subagent_id=task_id,
                after_event_seq=after_event_seq,
            )
        )
        view = self._result_view(task)
        view["events"] = list(snapshot.get("events") or [])
        view["event_seq"] = int(snapshot.get("event_seq") or task.event_seq or 0)
        return view

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self._load_task(task_id)
        self._service().toolkit_subagent_cancel(
            SandboxAgentSubagentCancelRequest(
                chat_session_id=task.chat_session_id,
                subagent_id=task_id,
            )
        )
        self.db.refresh(task)
        return self._result_view(task)

    def revise_task(
        self,
        task_id: str,
        *,
        revised_request: str,
        context: Mapping[str, Any] | None = None,
        turn_id: str | None = None,
        session_epoch: int | None = None,
        context_version: str | None = None,
    ) -> dict[str, Any]:
        """Supersede the current requirement and start revision N+1."""
        current = self._load_task(task_id)
        spec = dict(current.spec_json or {})
        group_id = str(spec.get("requirement_group_id") or "")
        if not group_id:
            group_id = f"rg_{_stable_digest({'task_id': task_id})}"
        linked = self._linked_tasks(
            voice_session_id=str(
                (spec.get("association") or {}).get("voice_session_id")
                if isinstance(spec.get("association"), dict)
                else spec.get("voice_session_id")
            ),
            requirement_group_id=group_id,
        )
        current_version = max(
            [
                int((item.spec_json or {}).get("requirement_version") or 1)
                for item in linked
            ]
            or [int(spec.get("requirement_version") or 1)]
        )
        next_version = current_version + 1
        for item in linked:
            if item.status in TERMINAL_TASK_STATUSES:
                continue
            item_spec = dict(item.spec_json or {})
            item_spec["current_requirement_version"] = next_version
            item_spec["superseded_by"] = group_id
            item_spec["superseded_at"] = _utc_iso()
            item.spec_json = item_spec
            append_agent_event(
                self.db,
                item,
                "requirement_superseded",
                {
                    "requirement_group_id": group_id,
                    "requirement_version": item_spec.get("requirement_version"),
                    "current_requirement_version": next_version,
                    "turn_id": turn_id,
                },
            )
        self.db.commit()
        new_task = self.delegate(
            role_key=str(spec.get("role_key") or "generic"),
            objective=revised_request,
            chat_session_id=current.chat_session_id,
            title=current.title,
            thinking_mode=str(spec.get("thinking_mode_requested") or ""),
            tools=spec.get("tool_profile"),
            skills=spec.get("skill_profile"),
            write_set=spec.get("write_set"),
            voice_session_id=(
                (spec.get("association") or {}).get("voice_session_id")
                if isinstance(spec.get("association"), dict)
                else spec.get("voice_session_id")
            ),
            turn_id=turn_id,
            session_epoch=session_epoch,
            context_version=context_version,
            context=context,
            requirement_group_id=group_id,
            requirement_version=next_version,
        )
        return {
            "superseded_task_id": task_id,
            "requirement_group_id": group_id,
            "requirement_version": next_version,
            "new_task": new_task,
        }

    def list_ready_results(
        self,
        *,
        voice_session_id: str,
        include_stale: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for task in self._linked_tasks(voice_session_id=voice_session_id):
            view = self._result_view(task)
            if view["delivery_state"] == "stale" and not include_stale:
                continue
            if view["delivery_state"] not in {"ready", "failed", "stale"}:
                continue
            results.append(view)
            if len(results) >= max(1, min(int(limit), 100)):
                break
        return results

    def acknowledge_result(self, task_id: str, *, delivery_state: str = "delivered") -> dict[str, Any]:
        if delivery_state not in {"delivered", "dismissed"}:
            raise AppError(422, "invalid_delivery_state", "delivery_state must be delivered or dismissed")
        task = self._load_task(task_id)
        spec = dict(task.spec_json or {})
        spec["delivery_state"] = delivery_state
        spec["delivery_updated_at"] = _utc_iso()
        task.spec_json = spec
        append_agent_event(
            self.db,
            task,
            "result_delivery",
            {"delivery_state": delivery_state, "delivered_at": spec["delivery_updated_at"]},
        )
        self.db.commit()
        self.db.refresh(task)
        return self._result_view(task)

    # Tutor-facing narrow API: do not expose arbitrary tool names or DB access.
    def delegate_research(
        self,
        *,
        query: str,
        chat_session_id: str,
        purpose: str = "",
        context: Mapping[str, Any] | None = None,
        voice_session_id: str | None = None,
        turn_id: str | None = None,
        thinking_mode: str | None = None,
        tools: list[str] | None = None,
        requirement_group_id: str | None = None,
        requirement_version: int = 1,
    ) -> dict[str, Any]:
        objective = query.strip()
        if purpose.strip():
            objective = f"{objective}\n\nPurpose: {purpose.strip()}"
        return self.delegate(
            role_key=AgentRole.RESEARCH.value,
            objective=objective,
            chat_session_id=chat_session_id,
            title="Research",
            thinking_mode=thinking_mode,
            tools=tools,
            voice_session_id=voice_session_id,
            turn_id=turn_id,
            context=context,
            requirement_group_id=requirement_group_id,
            requirement_version=requirement_version,
        )

    def delegate_reasoning(
        self,
        *,
        question: str,
        chat_session_id: str,
        context: Mapping[str, Any] | None = None,
        voice_session_id: str | None = None,
        turn_id: str | None = None,
        thinking_mode: str | None = None,
    ) -> dict[str, Any]:
        return self.delegate(
            role_key=AgentRole.REASON.value,
            objective=question,
            chat_session_id=chat_session_id,
            title="Reasoning",
            thinking_mode=thinking_mode,
            tools=None,
            voice_session_id=voice_session_id,
            turn_id=turn_id,
            context=context,
        )

    def delegate_tool_task(
        self,
        *,
        goal: str,
        tools: list[str],
        chat_session_id: str,
        context: Mapping[str, Any] | None = None,
        voice_session_id: str | None = None,
        turn_id: str | None = None,
        thinking_mode: str | None = None,
        write_set: list[str] | None = None,
    ) -> dict[str, Any]:
        if not tools:
            raise AgentProfileError("ToolAgent requires a non-empty server-approved tool allow-list")
        return self.delegate(
            role_key=AgentRole.TOOL.value,
            objective=goal,
            chat_session_id=chat_session_id,
            title="Tool task",
            thinking_mode=thinking_mode,
            tools=tools,
            write_set=write_set,
            voice_session_id=voice_session_id,
            turn_id=turn_id,
            context=context,
        )

    def get_background_task(self, task_id: str, *, after_event_seq: int | None = None) -> dict[str, Any]:
        return self.get_task(task_id, after_event_seq=after_event_seq)

    def cancel_background_task(self, task_id: str) -> dict[str, Any]:
        return self.cancel_task(task_id)

    def revise_background_task(self, task_id: str, *, revised_request: str, **scope: Any) -> dict[str, Any]:
        return self.revise_task(task_id, revised_request=revised_request, **scope)


class TutorDelegationAPI:
    """Small tool-facing adapter with no wait or direct persistence methods."""

    def __init__(
        self,
        coordinator: VoiceAgentCoordinator,
        *,
        chat_session_id: str,
        voice_session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.chat_session_id = chat_session_id
        self.voice_session_id = voice_session_id
        self.turn_id = turn_id

    def delegate_research(
        self,
        query: str,
        purpose: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.coordinator.delegate_research(
            query=query,
            purpose=purpose,
            context=context,
            chat_session_id=self.chat_session_id,
            voice_session_id=self.voice_session_id,
            turn_id=self.turn_id,
        )

    def delegate_reasoning(
        self,
        question: str,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.coordinator.delegate_reasoning(
            question=question,
            context=context,
            chat_session_id=self.chat_session_id,
            voice_session_id=self.voice_session_id,
            turn_id=self.turn_id,
        )

    def delegate_tool_task(
        self,
        goal: str,
        tools: list[str],
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.coordinator.delegate_tool_task(
            goal=goal,
            tools=tools,
            context=context,
            chat_session_id=self.chat_session_id,
            voice_session_id=self.voice_session_id,
            turn_id=self.turn_id,
        )

    def get_background_task(self, task_id: str) -> dict[str, Any]:
        return self.coordinator.get_background_task(task_id)

    def cancel_background_task(self, task_id: str) -> dict[str, Any]:
        return self.coordinator.cancel_background_task(task_id)

    def revise_background_task(self, task_id: str, revised_request: str) -> dict[str, Any]:
        return self.coordinator.revise_background_task(
            task_id,
            revised_request=revised_request,
            turn_id=self.turn_id,
        )
