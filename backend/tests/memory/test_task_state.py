from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-task-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'tasks.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import MemoryScopeContext  # noqa: E402
from app.domain.schemas.memory_tasks import (  # noqa: E402
    TaskStateCreateRequest,
    TaskStateUpdateRequest,
)
from app.services.memory_crypto import MemoryPayloadCipher  # noqa: E402
from app.services.memory_event_store import MemoryEventStore  # noqa: E402
from app.services.memory_tasks import (  # noqa: E402
    MemoryTaskService,
    validate_transition,
)


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        apply_schema_migrations(conn)
    yield


def scope(
    *,
    tenant: str = "t1",
    user: str = "u1",
    workspace: str = "w1",
) -> MemoryScopeContext:
    return MemoryScopeContext(
        tenant_id=tenant,
        principal_user_id=user,
        workspace_id=workspace,
    )


def _service(db) -> MemoryTaskService:
    return MemoryTaskService(db, MemoryEventStore(db, MemoryPayloadCipher("secret")))


def _create_task(
    db,
    *,
    task_id: str = "task-1",
    title: str = "Test Task",
    scope_override: MemoryScopeContext | None = None,
):
    svc = _service(db)
    return svc.create(
        scope_override or scope(),
        "u1",
        TaskStateCreateRequest(
            task_id=task_id,
            title=title,
            goal="test goal",
            idempotency_key=f"create-{task_id}",
        ),
    )


# ── State machine validation ──────────────────────────────────────────────────


def test_valid_transitions():
    """All documented transitions should pass without error."""
    valid = [
        ("planned", "in_progress"),
        ("planned", "blocked"),
        ("planned", "cancelled"),
        ("planned", "superseded"),
        ("in_progress", "blocked"),
        ("in_progress", "paused"),
        ("in_progress", "completed"),
        ("in_progress", "cancelled"),
        ("in_progress", "superseded"),
        ("blocked", "in_progress"),
        ("blocked", "paused"),
        ("blocked", "cancelled"),
        ("blocked", "superseded"),
        ("paused", "in_progress"),
        ("paused", "cancelled"),
        ("paused", "superseded"),
    ]
    for current, target in valid:
        validate_transition(current, target)  # should not raise


def test_invalid_transitions():
    """Terminal states and backward transitions must be rejected."""
    invalid = [
        ("completed", "in_progress"),
        ("completed", "blocked"),
        ("completed", "planned"),
        ("cancelled", "in_progress"),
        ("cancelled", "planned"),
        ("superseded", "in_progress"),
        ("superseded", "planned"),
        ("in_progress", "planned"),
        ("blocked", "planned"),
        ("paused", "planned"),
    ]
    for current, target in invalid:
        with pytest.raises(AppError) as exc_info:
            validate_transition(current, target)
        assert exc_info.value.code == "memory_task_invalid_transition"


def test_terminal_state_cannot_be_updated():
    """Updating a completed task must raise 409."""
    with SessionLocal() as db:
        view = _create_task(db)
        svc = _service(db)
        svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                status="in_progress",
                idempotency_key="start-task-1",
            ),
        )
        completed = svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version + 1,
                status="completed",
                idempotency_key="complete-task-1",
            ),
        )
        assert completed.status == "completed"
        with pytest.raises(AppError) as exc_info:
            svc.update(
                scope(),
                "u1",
                "task-1",
                TaskStateUpdateRequest(
                    expected_stream_version=completed.stream_version,
                    status="in_progress",
                    idempotency_key="resume-task-1",
                ),
            )
        assert exc_info.value.code == "memory_task_invalid_transition"


# ── CAS conflict ──────────────────────────────────────────────────────────────


def test_cas_conflict_on_stale_version():
    """Two concurrent updates with the same expected version; one must fail with 409."""
    with SessionLocal() as db:
        view = _create_task(db)
        svc = _service(db)
        # First update succeeds.
        svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                status="in_progress",
                idempotency_key="start-task-1",
            ),
        )
        db.commit()
        # Second update with stale version must fail.
        with pytest.raises(AppError) as exc_info:
            svc.update(
                scope(),
                "u1",
                "task-1",
                TaskStateUpdateRequest(
                    expected_stream_version=view.stream_version,  # stale!
                    status="blocked",
                    idempotency_key="block-stale",
                ),
            )
        assert exc_info.value.code == "memory_stream_version_conflict"


def test_create_with_custom_task_id():
    with SessionLocal() as db:
        view = _create_task(db, task_id="custom-id")
        assert view.task_id == "custom-id"
        assert view.status == "planned"


def test_create_with_auto_id():
    with SessionLocal() as db:
        svc = _service(db)
        view = svc.create(
            scope(),
            "u1",
            TaskStateCreateRequest(
                title="Auto ID task",
                idempotency_key="auto-id-test",
            ),
        )
        assert view.task_id.startswith("task_")
        assert view.status == "planned"


# ── Full lifecycle ────────────────────────────────────────────────────────────


def test_full_lifecycle_planned_to_completed():
    with SessionLocal() as db:
        view = _create_task(db)
        svc = _service(db)
        assert view.status == "planned"

        view = svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                status="in_progress",
                current_stage="implementing",
                idempotency_key="start-task",
            ),
        )
        assert view.status == "in_progress"
        assert view.current_stage == "implementing"

        view = svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                status="blocked",
                blocked_by=[{"task": "dependency"}],
                idempotency_key="block-task-1",
            ),
        )
        assert view.status == "blocked"
        assert len(view.blocked_by) == 1

        view = svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                status="in_progress",
                idempotency_key="unblock-task-1",
            ),
        )
        assert view.status == "in_progress"

        view = svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                status="completed",
                completed=[{"step": "done"}],
                idempotency_key="complete-task-1-final",
            ),
        )
        assert view.status == "completed"
        assert len(view.completed) == 1


# ── Scope isolation ───────────────────────────────────────────────────────────


def test_cross_workspace_not_found():
    with SessionLocal() as db:
        _create_task(db, scope_override=scope(workspace="w1"))
        svc = _service(db)
        with pytest.raises(AppError) as exc_info:
            svc.require(scope(workspace="w2"), "task-1")
        assert exc_info.value.code == "memory_task_not_found"


def test_cross_tenant_not_found():
    with SessionLocal() as db:
        _create_task(db, scope_override=scope(tenant="t1"))
        svc = _service(db)
        with pytest.raises(AppError) as exc_info:
            svc.require(scope(tenant="t2"), "task-1")
        assert exc_info.value.code == "memory_task_not_found"


def test_cross_user_not_found():
    with SessionLocal() as db:
        _create_task(db, scope_override=scope(user="u1"))
        svc = _service(db)
        with pytest.raises(AppError) as exc_info:
            svc.require(scope(user="u2"), "task-1")
        assert exc_info.value.code == "memory_task_not_found"


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_idempotent_create_returns_same():
    with SessionLocal() as db:
        svc = _service(db)
        first = svc.create(
            scope(),
            "u1",
            TaskStateCreateRequest(
                task_id="task-idem",
                title="Idempotent",
                idempotency_key="idem-create-key",
            ),
        )
        second = svc.create(
            scope(),
            "u1",
            TaskStateCreateRequest(
                task_id="task-idem",
                title="Idempotent",
                idempotency_key="idem-create-key",
            ),
        )
        assert first.task_id == second.task_id
        assert first.stream_version == second.stream_version


def test_idempotent_update_returns_same():
    with SessionLocal() as db:
        view = _create_task(db)
        svc = _service(db)
        first = svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                status="in_progress",
                idempotency_key="idem-update-key",
            ),
        )
        second = svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                status="in_progress",
                idempotency_key="idem-update-key",
            ),
        )
        assert first.stream_version == second.stream_version
        assert first.status == second.status


# ── Non-status updates ────────────────────────────────────────────────────────


def test_stage_update_without_status_change():
    with SessionLocal() as db:
        view = _create_task(db)
        svc = _service(db)
        updated = svc.update(
            scope(),
            "u1",
            "task-1",
            TaskStateUpdateRequest(
                expected_stream_version=view.stream_version,
                current_stage="reviewing",
                next_action="review PR",
                decisions=[{"decision": "use SQLite", "rationale": "local dev"}],
                idempotency_key="stage-update",
            ),
        )
        assert updated.current_stage == "reviewing"
        assert updated.next_action == "review PR"
        assert len(updated.decisions) == 1
        assert updated.status == "planned"  # unchanged
