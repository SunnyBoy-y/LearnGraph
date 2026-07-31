from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.memory_event_models import MemoryScopeContext, MemoryTaskState, new_id, utc_now
from app.domain.memory_event_types import MemoryEventType
from app.domain.schemas.memory_tasks import (
    TaskStateCreateRequest,
    TaskStateUpdateRequest,
    TaskStateView,
)
from app.services.memory_event_store import AppendEvent, MemoryEventStore


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
