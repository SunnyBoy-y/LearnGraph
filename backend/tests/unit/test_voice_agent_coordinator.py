from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.voice_agent_coordinator import TutorDelegationAPI, VoiceAgentCoordinator


class FakeDB:
    def __init__(self, task=None):
        self.task = task
        self.commits = 0

    def scalar(self, _query):
        return self.task

    def commit(self):
        self.commits += 1

    def refresh(self, _task):
        return None


class FakeTaskService:
    def __init__(self):
        self.requests = []
        self.status_snapshot = {
            "status": "running",
            "event_seq": 4,
            "events": [{"seq": 4, "event_type": "tool_call", "payload": {}}],
        }

    def toolkit_subagent(self, request):
        self.requests.append(request)
        return {"task_id": "task-1", "subagent_id": "task-1", "status": "queued"}

    def toolkit_subagent_status(self, _request):
        return self.status_snapshot

    def toolkit_subagent_cancel(self, _request):
        return {"status": "running"}


def _task():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        task_id="task-1",
        id="row-1",
        title="Research",
        role_key="research",
        status="RUNNING",
        status_reason=None,
        latest_job_id="job-1",
        result_text=None,
        deliverables_json=None,
        event_seq=0,
        spec_json={"requirement_version": 1, "current_requirement_version": 1},
        chat_session_id="chat-1",
        created_at=now,
        finished_at=None,
        updated_at=now,
    )


def _coordinator(permissions=frozenset({"workspace.manage"})):
    task = _task()
    db = FakeDB(task)
    service = FakeTaskService()
    coordinator = VoiceAgentCoordinator(
        db,
        SimpleNamespace(),
        workspace_id="workspace-1",
        actor_id="user-1",
        permissions=permissions,
        task_service=service,
    )
    return coordinator, db, service, task


def test_research_delegation_returns_fast_accepted_receipt_with_links():
    coordinator, db, service, task = _coordinator()

    receipt = TutorDelegationAPI(
        coordinator,
        chat_session_id="chat-1",
        voice_session_id="voice-1",
        turn_id="turn-1",
    ).delegate_research(
        "compare hash tables",
        purpose="focus on collisions",
        context={"topic": "data structures", "secret_token": "must-not-persist"},
    )

    assert receipt["status"] == "accepted"
    assert receipt["task_id"] == "task-1"
    assert receipt["voice_session_id"] == "voice-1"
    assert receipt["turn_id"] == "turn-1"
    assert receipt["requirement_version"] == 1
    assert service.requests[0].tools is None
    assert service.requests[0].thinking_mode == "medium"
    assert task.spec_json["association"]["voice_session_id"] == "voice-1"
    assert "secret_token" not in task.spec_json["input_snapshot"]["context"]
    assert db.commits >= 1


def test_reasoning_delegation_has_no_tools():
    coordinator, _db, service, _task = _coordinator()

    coordinator.delegate_reasoning(
        question="prove the invariant",
        chat_session_id="chat-1",
        voice_session_id="voice-1",
        turn_id="turn-1",
    )

    assert service.requests[0].role_key == "reason"
    assert service.requests[0].tools == []


def test_tool_delegation_preserves_exact_allowlist():
    coordinator, _db, service, _task = _coordinator()

    coordinator.delegate_tool_task(
        goal="read a file",
        tools=["sandbox_read_file"],
        chat_session_id="chat-1",
        voice_session_id="voice-1",
        turn_id="turn-1",
    )

    assert service.requests[0].role_key == "tool"
    assert service.requests[0].tools == ["sandbox_read_file"]


def test_permission_is_checked_when_permission_set_is_explicitly_empty():
    coordinator, _db, _service, _task = _coordinator(permissions=frozenset())
    with pytest.raises(Exception):
        coordinator.delegate_reasoning(
            question="q",
            chat_session_id="chat-1",
        )


def test_get_task_returns_typed_delivery_view_when_result_ready():
    coordinator, _db, service, task = _coordinator()
    task.status = "SUCCEEDED"
    task.deliverables_json = {
        "agent_result": {
            "task_id": "task-1",
            "task_type": "research",
            "status": "succeeded",
            "short_answer": "answer",
            "findings": [],
            "evidence": [],
            "sources": [{"title": "S", "url": "https://example.com", "snippet": ""}],
            "artifacts": [],
            "limitations": [],
            "unresolved_questions": [],
            "completed_at": "2026-01-01T00:00:00Z",
            "requirement_version": 1,
        }
    }
    task.spec_json.update(
        {
            "delivery_state": "pending",
            "thinking_mode_requested": "medium",
            "thinking_mode_effective": "off",
        }
    )

    view = coordinator.get_background_task("task-1")

    assert view["status"] == "succeeded"
    assert view["delivery_state"] == "ready"
    assert view["agent_result"]["sources"][0]["url"] == "https://example.com"
    assert view["events"][0]["event_type"] == "tool_call"


def test_old_result_revision_is_marked_stale():
    coordinator, _db, _service, task = _coordinator()
    task.status = "SUCCEEDED"
    task.spec_json.update(
        {
            "requirement_version": 1,
            "current_requirement_version": 2,
            "delivery_state": "pending",
        }
    )
    task.deliverables_json = {
        "agent_result": {
            "task_id": "task-1",
            "task_type": "research",
            "status": "succeeded",
            "completed_at": "2026-01-01T00:00:00Z",
            "requirement_version": 1,
        }
    }

    view = coordinator.get_background_task("task-1")

    assert view["delivery_state"] == "stale"
