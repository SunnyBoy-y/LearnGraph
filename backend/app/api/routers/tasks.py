from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.api.memory_deps import event_store, memory_scope
from app.domain.schemas.memory_tasks import (
    TaskStateCreateRequest,
    TaskStatePatchRequest,
    TaskStateUpdateRequest,
    TaskStateView,
)
from app.services.memory_tasks import MemoryTaskService


router = APIRouter(prefix="/tasks", tags=["memory-tasks"])


@router.post("", response_model=TaskStateView)
def create_task(
    payload: TaskStateCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> TaskStateView:
    return MemoryTaskService(db, event_store(db, settings)).create(
        memory_scope(context), context.principal.user_id, payload
    )


@router.get("/{task_id}/state", response_model=TaskStateView)
def get_task_state(
    task_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> TaskStateView:
    service = MemoryTaskService(db, event_store(db, settings))
    return service._view(service.require(memory_scope(context, task_id=task_id), task_id))


@router.put("/{task_id}/state", response_model=TaskStateView)
def update_task_state(
    task_id: str,
    payload: TaskStateUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> TaskStateView:
    return MemoryTaskService(db, event_store(db, settings)).update(
        memory_scope(context, task_id=task_id),
        context.principal.user_id,
        task_id,
        payload,
    )


@router.post("/{task_id}/patch", response_model=TaskStateView)
def patch_task_state(
    task_id: str,
    payload: TaskStatePatchRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> TaskStateView:
    """Apply a validated LLM/agent patch candidate via deterministic events."""
    return MemoryTaskService(db, event_store(db, settings)).apply_patch(
        memory_scope(context, task_id=task_id),
        context.principal.user_id,
        task_id,
        payload,
    )
