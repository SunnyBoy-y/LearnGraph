from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.memory_event_models import (
    MemoryScopeContext,
    MemoryTaskState,
    new_id,
    utc_now,
)
from app.domain.memory_event_types import MemoryEventType
from app.domain.schemas.memory_tasks import (
    TaskStateCreateRequest,
    TaskStatePatchRequest,
    TaskStateUpdateRequest,
    TaskStateView,
)
from app.services.memory_event_store import AppendEvent, MemoryEventStore

# ── State machine ─────────────────────────────────────────────────────────────
# Each key maps to the set of statuses the task may transition TO.
_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    # A planned task may be completed directly (single-step task that is done
    # on creation) without needing to transit through in_progress first.
    "planned": frozenset({"in_progress", "blocked", "completed", "cancelled", "superseded"}),
    "in_progress": frozenset({"blocked", "paused", "completed", "cancelled", "superseded"}),
    "blocked": frozenset({"in_progress", "paused", "cancelled", "superseded"}),
    "paused": frozenset({"in_progress", "cancelled", "superseded"}),
    "completed": frozenset(),  # terminal
    "cancelled": frozenset(),  # terminal
    "superseded": frozenset(),  # terminal
}


def validate_transition(current: str, target: str) -> None:
    """Raise 409 if the transition is not allowed."""

    if current == target:
        # Same-status updates are allowed (idempotent); event store deduplicates.
        return
    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise AppError(
            409,
            "memory_task_invalid_transition",
            f"Task status cannot transition from '{current}' to '{target}'",
            {"current_status": current, "target_status": target},
        )


_REF_FIELDS = ("id", "title", "name", "task", "step")


def _item_key(item: dict[str, Any]) -> str:
    """Stable dedupe key for a structured list item.

    An explicit ``id``/``title``/``name``/``task``/``step`` field wins; otherwise
    the canonical JSON of the whole item is used so repeated identical dicts
    collapse to the same key.
    """
    for field in _REF_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value:
            return f"{field}:{value}"
    return "json:" + json.dumps(item, sort_keys=True, ensure_ascii=False)


def _contains_item(items: list[dict[str, Any]], item: dict[str, Any]) -> bool:
    key = _item_key(item)
    return any(_item_key(existing) == key for existing in items)


def _index_of(items: list[dict[str, Any]], ref: str) -> int | None:
    """Find a structured list item by any reference field or by its canonical key."""
    for idx, existing in enumerate(items):
        if _item_key(existing) == ref:
            return idx
        if any(existing.get(field) == ref for field in _REF_FIELDS):
            return idx
    return None


class MemoryTaskService:
    def __init__(self, db: Session, store: MemoryEventStore) -> None:
        self.db = db
        self.store = store

    def create(
        self, scope: MemoryScopeContext, actor_id: str, request: TaskStateCreateRequest
    ) -> TaskStateView:
        task_id = request.task_id or f"task_{new_id()}"
        result = self.store.append(
            scope,
            aggregate_type="task",
            aggregate_id=task_id,
            expected_version=0,
            event=AppendEvent(
                event_type=MemoryEventType.TASK_CREATED,
                payload=request.model_dump(),
                idempotency_key=request.idempotency_key,
                actor_id=actor_id,
            ),
            outbox_kinds=("index",),
        )
        existing = self._get(scope, task_id)
        if existing is None:
            existing = MemoryTaskState(
                id=task_id,
                stream_id=result.event.stream_id,
                stream_version=result.event.stream_version,
                tenant_id=scope.tenant_id,
                subject_user_id=scope.principal_user_id,
                workspace_id=scope.workspace_id,
                project_id=request.project_id,
                goal_id=request.goal_id,
                parent_task_id=request.parent_task_id,
                title=request.title,
                goal=request.goal,
                status="planned",
                started_at=utc_now(),
                head_event_id=result.event.event_id,
            )
            self.db.add(existing)
            self.db.flush()
        self.db.commit()
        return self._view(existing)

    def update(
        self,
        scope: MemoryScopeContext,
        actor_id: str,
        task_id: str,
        request: TaskStateUpdateRequest,
    ) -> TaskStateView:
        state = self.require(scope, task_id)
        if request.status is not None:
            validate_transition(state.status, request.status)
        event_type = self._event_type(request.status)
        result = self.store.append(
            scope,
            aggregate_type="task",
            aggregate_id=task_id,
            expected_version=request.expected_stream_version,
            event=AppendEvent(
                event_type=event_type,
                payload=request.model_dump(exclude_none=True),
                idempotency_key=request.idempotency_key,
                actor_id=actor_id,
            ),
            outbox_kinds=("index",),
        )
        if result.idempotent_replay:
            return self._view(state)
        for source, target in (
            ("status", "status"),
            ("current_stage", "current_stage"),
            ("completed", "completed_json"),
            ("pending", "pending_json"),
            ("constraints", "constraints_json"),
            ("blocked_by", "blocked_by_json"),
            ("decisions", "decisions_json"),
            ("artifact_refs", "artifact_refs_json"),
            ("related_file_refs", "related_file_refs_json"),
            ("next_action", "next_action"),
        ):
            value = getattr(request, source)
            if value is not None:
                setattr(state, target, value)
        state.stream_version = result.event.stream_version
        state.head_event_id = result.event.event_id
        if state.status == "paused":
            state.paused_at = utc_now()
        if state.status == "completed":
            state.completed_at = utc_now()
        self.db.commit()
        return self._view(state)

    def apply_patch(
        self,
        scope: MemoryScopeContext,
        actor_id: str,
        task_id: str,
        request: TaskStatePatchRequest,
    ) -> TaskStateView:
        """Apply a validated LLM/agent patch as deterministic events.

        The candidate is a delta, never an authoritative snapshot: status
        transitions are validated against the state machine, structured lists
        are merged by stable key, and the applied changes are recorded as
        ``task.step_completed`` / status / stage events. ``completed_add``
        emits a dedicated ``task.step_completed`` event so step completion is
        observable independently of any status change. All events append in one
        transaction under the same CAS expected version.
        """
        state = self.require(scope, task_id)
        candidate = request.candidate

        target_status = state.status
        if candidate.proposed_status is not None:
            validate_transition(state.status, candidate.proposed_status)
            target_status = candidate.proposed_status
        elif candidate.blocked_by_add and target_status != "blocked":
            # A planned step failed and is now blocked on a dependency. If the
            # proposer did not set an explicit status, push the task to
            # ``blocked`` so the failure is observable as a dedicated
            # ``task.blocked`` event rather than an opaque stage change. Only
            # attempt the transition when the state machine permits it; a
            # ``paused`` task, for example, must be resumed first, so we leave
            # its status untouched and record the blockers via stage_changed.
            _BLOCKED_TRANSITIONS = _VALID_TRANSITIONS.get(state.status, frozenset())
            if "blocked" in _BLOCKED_TRANSITIONS:
                target_status = "blocked"

        completed = list(state.completed_json or [])
        pending = list(state.pending_json or [])
        constraints = list(state.constraints_json or [])
        blocked_by = list(state.blocked_by_json or [])
        decisions = list(state.decisions_json or [])

        for item in candidate.completed_add:
            if _contains_item(pending, item):
                # Completion graduates a step out of the todo set: a finished
                # step cannot stay both done and pending. Unlike
                # ``pending_remove`` (which is an explicit deletion and errors
                # when the ref is unknown), this is an implicit migration so a
                # completed step that was never tracked as pending is simply
                # recorded as done.
                idx = _index_of(pending, _item_key(item))
                if idx is not None:
                    pending.pop(idx)
            if not _contains_item(completed, item):
                completed.append(item)
        for item in candidate.pending_add:
            if not _contains_item(pending, item):
                pending.append(item)
        for ref in candidate.pending_remove:
            idx = _index_of(pending, ref)
            if idx is None:
                raise AppError(
                    422,
                    "memory_task_pending_not_found",
                    f"No pending item matches '{ref}'",
                    {"ref": ref},
                )
            pending.pop(idx)
        for item in candidate.constraints_add:
            if not _contains_item(constraints, item):
                constraints.append(item)
        for item in candidate.blocked_by_add:
            if not _contains_item(blocked_by, item):
                blocked_by.append(item)
        for item in candidate.decisions_add:
            if not _contains_item(decisions, item):
                decisions.append(item)

        merged: dict[str, Any] = {
            "current_stage": candidate.current_stage,
            "completed": completed,
            "pending": pending,
            "constraints": constraints,
            "blocked_by": blocked_by,
            "decisions": decisions,
            "next_action": candidate.next_action,
        }

        events: list[tuple[str, dict[str, Any], str]] = []
        if target_status != state.status:
            events.append(
                (
                    self._event_type(target_status),
                    {**merged, "status": target_status},
                    f"{request.idempotency_key}:status",
                )
            )
        if candidate.completed_add:
            events.append(
                (
                    MemoryEventType.TASK_STEP_COMPLETED,
                    {
                        "task_id": task_id,
                        "status": target_status,
                        "completed": candidate.completed_add,
                    },
                    f"{request.idempotency_key}:steps",
                )
            )
        if not events:
            events.append(
                (MemoryEventType.TASK_STAGE_CHANGED, merged, f"{request.idempotency_key}:stage")
            )

        expected = request.expected_stream_version
        last_event_id = state.head_event_id
        for event_type, payload, idem_key in events:
            result = self.store.append(
                scope,
                aggregate_type="task",
                aggregate_id=task_id,
                expected_version=expected,
                event=AppendEvent(
                    event_type=event_type,
                    payload={k: v for k, v in payload.items() if v is not None},
                    idempotency_key=idem_key,
                    actor_id=actor_id,
                ),
                outbox_kinds=("index",),
            )
            if result.idempotent_replay:
                # The whole patch was already committed in one transaction; the
                # projection already reflects it. Do not re-apply deltas.
                return self._view(state)
            expected = result.event.stream_version
            last_event_id = result.event.event_id

        state.status = target_status
        if candidate.current_stage is not None:
            state.current_stage = candidate.current_stage
        state.completed_json = completed
        state.pending_json = pending
        state.constraints_json = constraints
        state.blocked_by_json = blocked_by
        state.decisions_json = decisions
        if candidate.next_action is not None:
            state.next_action = candidate.next_action
        state.stream_version = expected
        state.head_event_id = last_event_id
        if target_status == "paused":
            state.paused_at = utc_now()
        if target_status == "completed":
            state.completed_at = utc_now()
        self.db.commit()
        return self._view(state)

    def require(self, scope: MemoryScopeContext, task_id: str) -> MemoryTaskState:
        state = self._get(scope, task_id)
        if state is None:
            raise AppError(404, "memory_task_not_found", "Task state was not found")
        return state

    def _get(self, scope: MemoryScopeContext, task_id: str) -> MemoryTaskState | None:
        return self.db.scalar(
            select(MemoryTaskState).where(
                MemoryTaskState.id == task_id,
                MemoryTaskState.tenant_id == scope.tenant_id,
                MemoryTaskState.workspace_id == scope.workspace_id,
                (MemoryTaskState.subject_user_id == scope.principal_user_id)
                | (MemoryTaskState.subject_user_id.is_(None)),
            )
        )

    @staticmethod
    def _event_type(status: str | None) -> str:
        return {
            "blocked": MemoryEventType.TASK_BLOCKED,
            "completed": MemoryEventType.TASK_COMPLETED,
            "cancelled": MemoryEventType.TASK_CANCELLED,
            "in_progress": MemoryEventType.TASK_RESUMED,
        }.get(status or "", MemoryEventType.TASK_STAGE_CHANGED)

    @staticmethod
    def _view(state: MemoryTaskState) -> TaskStateView:
        return TaskStateView(
            task_id=state.id,
            stream_version=state.stream_version,
            title=state.title,
            goal=state.goal,
            status=state.status,
            current_stage=state.current_stage,
            completed=state.completed_json,
            pending=state.pending_json,
            constraints=state.constraints_json,
            blocked_by=state.blocked_by_json,
            decisions=state.decisions_json,
            artifact_refs=state.artifact_refs_json,
            related_file_refs=state.related_file_refs_json,
            next_action=state.next_action,
            updated_at=state.updated_at,
        )
