from types import SimpleNamespace

from app.services import sandbox_scheduler as scheduler


def test_waiting_for_capacity_does_not_consume_execution_attempt(monkeypatch):
    monkeypatch.setattr(scheduler, "evaluate_capacity", lambda *a, **k: (False, "waiting_capacity", 5))
    service = scheduler.SandboxSchedulerService(SimpleNamespace(commit=lambda: None, scalar=lambda q: None), SimpleNamespace())
    job = SimpleNamespace(cancel_requested=False, attempt=5, kind="command", owner_user_id="u",
                          payload_json={}, status="STARTING", reason=None, workspace_id="w", chat_session_id="c")
    assert service._run_job(job) == "requeued"
    assert job.status == "QUEUED"
    assert job.attempt == 4


def test_preexecution_failure_finishes_the_visible_task(monkeypatch):
    task = SimpleNamespace(status="QUEUED", event_seq=2, id="task", workspace_id="w")
    events = []
    # Sequence allocation is covered with real Sessions in managed-session API
    # tests. This unit isolates pre-execution status propagation.
    monkeypatch.setattr(scheduler, "append_agent_event", lambda db, task, event_type, payload:
        events.append(SimpleNamespace(event_type=event_type, payload=payload)))
    db = SimpleNamespace(get=lambda model, key: task, add=events.append, commit=lambda: None)
    service = scheduler.SandboxSchedulerService(db, SimpleNamespace())
    job = SimpleNamespace(id="job", kind="subagent", payload_json={"task_id": "task"}, started_at=None)
    service._finish(job, "FAILED", "max_attempts_reached")
    assert task.status == "FAILED"
    assert task.status_reason == "max_attempts_reached"
    assert task.finished_at == job.finished_at
    assert events[0].event_type == "failed"


def test_own_idle_sandbox_is_reused_at_capacity(monkeypatch):
    seen = {}
    def capacity(*args, **kwargs):
        seen.update(kwargs)
        return True, None, 0
    monkeypatch.setattr(scheduler, "evaluate_capacity", capacity)
    db = SimpleNamespace(commit=lambda: None, scalar=lambda q: "idle-session")
    service = scheduler.SandboxSchedulerService(db, SimpleNamespace())
    monkeypatch.setattr(service, "_reserve", lambda job: SimpleNamespace(id="reservation"))
    monkeypatch.setattr(service, "_release_reservation", lambda key: None)
    monkeypatch.setattr(service, "_execute_job", lambda job: None)
    job = SimpleNamespace(cancel_requested=False, attempt=1, kind="command", owner_user_id="u",
                          payload_json={}, workspace_id="w", chat_session_id="c")
    assert service._run_job(job) == "started"
    assert seen == {"exclude_session_id": "idle-session", "workspace_id": "w", "runtime_kind": None}
