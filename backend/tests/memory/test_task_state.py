from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-task-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'tasks.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.core.migrations import apply_schema_migrations  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import MemoryScopeContext, MemoryTaskState  # noqa: E402
from app.domain.memory_event_types import MemoryEventType  # noqa: E402
from app.domain.schemas.memory_tasks import (  # noqa: E402
    TaskStateCreateRequest,
    TaskStatePatchCandidate,
    TaskStatePatchRequest,
    TaskStateUpdateRequest,
)
from app.repositories.memory_events import MemoryEventRepository  # noqa: E402
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


# ── LLM patch candidate pipeline ──────────────────────────────────────────────


def _patch(
    svc: MemoryTaskService,
    task_id: str,
    *,
    expected_version: int,
    candidate: dict,
    idem: str,
    scope_override: MemoryScopeContext | None = None,
):
    return svc.apply_patch(
        scope_override or scope(),
        "u1",
        task_id,
        TaskStatePatchRequest(
            expected_stream_version=expected_version,
            candidate=TaskStatePatchCandidate(**candidate),
            idempotency_key=idem,
        ),
    )


def _stream_events(db, task_id: str) -> list:
    state = db.scalar(
        select(MemoryTaskState).where(MemoryTaskState.id == task_id)
    )
    assert state is not None
    return MemoryEventRepository(db).stream_events(scope(), state.stream_id)


def test_patch_merges_incremental_deltas():
    """Patch adds completed/pending/next_action without replacing existing lists."""
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-merge")
        svc = _service(db)
        updated = _patch(
            svc,
            "task-patch-merge",
            expected_version=view.stream_version,
            candidate={
                "proposed_status": "in_progress",
                "current_stage": "implementing",
                "completed_add": [{"step": "design schema"}],
                "pending_add": [{"step": "write code"}, {"step": "run tests"}],
                "next_action": "implement memory_tasks",
            },
            idem="patch-merge-1",
        )
        assert updated.status == "in_progress"
        assert updated.current_stage == "implementing"
        assert [c["step"] for c in updated.completed] == ["design schema"]
        assert [p["step"] for p in updated.pending] == ["write code", "run tests"]
        assert updated.next_action == "implement memory_tasks"

        # Second patch is additive: existing completed/pending are preserved.
        updated2 = _patch(
            svc,
            "task-patch-merge",
            expected_version=updated.stream_version,
            candidate={
                "completed_add": [{"step": "write code"}],
                "pending_add": [{"step": "refactor"}],
            },
            idem="patch-merge-2",
        )
        assert [c["step"] for c in updated2.completed] == ["design schema", "write code"]
        assert [p["step"] for p in updated2.pending] == ["run tests", "refactor"]


def test_patch_pending_remove_by_title():
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-remove")
        svc = _service(db)
        view = _patch(
            svc,
            "task-patch-remove",
            expected_version=view.stream_version,
            candidate={"pending_add": [{"step": "obsolete step"}]},
            idem="patch-remove-1",
        )
        updated = _patch(
            svc,
            "task-patch-remove",
            expected_version=view.stream_version,
            candidate={"pending_remove": ["obsolete step"]},
            idem="patch-remove-2",
        )
        assert updated.pending == []


def test_patch_pending_remove_not_found_raises_422():
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-missing")
        svc = _service(db)
        with pytest.raises(AppError) as exc_info:
            _patch(
                svc,
                "task-patch-missing",
                expected_version=view.stream_version,
                candidate={"pending_remove": ["does-not-exist"]},
                idem="patch-missing-1",
            )
        assert exc_info.value.code == "memory_task_pending_not_found"


def test_patch_illegal_transition_raises_409():
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-legal")
        svc = _service(db)
        _patch(
            svc,
            "task-patch-legal",
            expected_version=view.stream_version,
            candidate={"proposed_status": "completed"},
            idem="patch-legal-1",
        )
        completed = _patch(
            svc,
            "task-patch-legal",
            expected_version=view.stream_version + 1,
            candidate={"proposed_status": "completed"},
            idem="patch-legal-2",
        )
        with pytest.raises(AppError) as exc_info:
            _patch(
                svc,
                "task-patch-legal",
                expected_version=completed.stream_version,
                candidate={"proposed_status": "in_progress"},
                idem="patch-legal-3",
            )
        assert exc_info.value.code == "memory_task_invalid_transition"


def test_patch_cas_conflict_on_stale_version():
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-cas")
        svc = _service(db)
        _patch(
            svc,
            "task-patch-cas",
            expected_version=view.stream_version,
            candidate={"proposed_status": "in_progress"},
            idem="patch-cas-1",
        )
        with pytest.raises(AppError) as exc_info:
            _patch(
                svc,
                "task-patch-cas",
                expected_version=view.stream_version,  # stale
                candidate={"proposed_status": "blocked"},
                idem="patch-cas-2",
            )
        assert exc_info.value.code == "memory_stream_version_conflict"


def test_patch_scope_isolation():
    with SessionLocal() as db:
        _create_task(db, task_id="task-patch-scope", scope_override=scope(workspace="w1"))
        svc = _service(db)
        with pytest.raises(AppError) as exc_info:
            _patch(
                svc,
                "task-patch-scope",
                expected_version=0,
                candidate={"proposed_status": "in_progress"},
                idem="patch-scope-1",
                scope_override=scope(workspace="w2"),
            )
        assert exc_info.value.code == "memory_task_not_found"


def test_patch_idempotent_replay_returns_same():
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-idem")
        svc = _service(db)
        first = _patch(
            svc,
            "task-patch-idem",
            expected_version=view.stream_version,
            candidate={"pending_add": [{"step": "x"}]},
            idem="patch-idem-key",
        )
        second = _patch(
            svc,
            "task-patch-idem",
            expected_version=view.stream_version,
            candidate={"pending_add": [{"step": "x"}]},
            idem="patch-idem-key",
        )
        assert first.stream_version == second.stream_version
        assert first.pending == second.pending


def test_patch_completed_add_emits_step_completed_event():
    """completed_add must emit a dedicated task.step_completed event."""
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-steps")
        svc = _service(db)
        _patch(
            svc,
            "task-patch-steps",
            expected_version=view.stream_version,
            candidate={
                "proposed_status": "in_progress",
                "completed_add": [{"step": "design"}],
            },
            idem="patch-steps-1",
        )
        events = _stream_events(db, "task-patch-steps")
        event_types = [e.event_type for e in events]
        assert MemoryEventType.TASK_STEP_COMPLETED in event_types
        assert MemoryEventType.TASK_RESUMED in event_types  # planned -> in_progress


def test_patch_without_status_change_emits_stage_event():
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-stage")
        svc = _service(db)
        _patch(
            svc,
            "task-patch-stage",
            expected_version=view.stream_version,
            candidate={"current_stage": "planning"},
            idem="patch-stage-1",
        )
        events = _stream_events(db, "task-patch-stage")
        event_types = [e.event_type for e in events]
        # create + stage change; no status event since status stayed "planned"
        assert MemoryEventType.TASK_STAGE_CHANGED in event_types


def test_patch_blocked_by_add_without_status_pushes_to_blocked():
    """A failed planned step (blocked_by_add, no explicit status) must push an
    in_progress task to ``blocked`` and emit a dedicated task.blocked event
    rather than collapsing the failure into an opaque stage change."""
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-block")
        svc = _service(db)
        # Move into in_progress first so blocked is a legal transition.
        view = _patch(
            svc,
            "task-patch-block",
            expected_version=view.stream_version,
            candidate={"proposed_status": "in_progress"},
            idem="patch-block-start",
        )
        updated = _patch(
            svc,
            "task-patch-block",
            expected_version=view.stream_version,
            candidate={"blocked_by_add": [{"task": "missing dependency"}]},
            idem="patch-block-fail",
        )
        assert updated.status == "blocked"
        assert [b["task"] for b in updated.blocked_by] == ["missing dependency"]
        events = _stream_events(db, "task-patch-block")
        event_types = [e.event_type for e in events]
        assert MemoryEventType.TASK_BLOCKED in event_types


def test_patch_blocked_by_add_from_paused_stays_stage_changed():
    """A paused task cannot legally move to blocked, so blockers are recorded
    via stage_changed without an illegal status transition."""
    with SessionLocal() as db:
        view = _create_task(db, task_id="task-patch-pause")
        svc = _service(db)
        view = _patch(
            svc,
            "task-patch-pause",
            expected_version=view.stream_version,
            candidate={"proposed_status": "in_progress"},
            idem="patch-pause-start",
        )
        view = _patch(
            svc,
            "task-patch-pause",
            expected_version=view.stream_version,
            candidate={"proposed_status": "paused"},
            idem="patch-pause-pause",
        )
        updated = _patch(
            svc,
            "task-patch-pause",
            expected_version=view.stream_version,
            candidate={"blocked_by_add": [{"task": "external wait"}]},
            idem="patch-pause-block",
        )
        # paused -> blocked is illegal; status stays paused, no TASK_BLOCKED event
        assert updated.status == "paused"
        assert [b["task"] for b in updated.blocked_by] == ["external wait"]
        events = _stream_events(db, "task-patch-pause")
        event_types = [e.event_type for e in events]
        assert MemoryEventType.TASK_BLOCKED not in event_types
