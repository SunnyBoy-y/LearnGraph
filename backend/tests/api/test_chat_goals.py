"""Goal HTTP, durable dispatch, chat persistence, and evaluator lifecycle."""
from datetime import timedelta
import hashlib

import pytest
from sqlalchemy import select

from app.api.deps import get_settings
from app.core.database import SessionLocal
from app.domain.models import ChatGoalRun, DurableJob, Message, MessageSubmission, utc_now
from app.providers.ports.model import ProviderStreamEvent
from app.services import chat_goals
from app.services.chat import ChatService
from app.services.durable_queue import run_one_durable_job


@pytest.fixture
def goal_session(client, register_user, auth_headers, monkeypatch):
    from app.api.routers import chat as router

    settings = get_settings()
    monkeypatch.setattr(settings, "durable_queue_enabled", True)
    calls = {"work": [], "verify": [], "answer": {"verdict": "continue", "summary": "还需一轮", "evidence": "已有草稿"}}

    class Provider:
        available = supports_agent_tools = supports_structured_chat = True
        remote_capability = False
        provider_id = "goal-test-provider"
        model_id = "goal-test-model"
        search_route = "disabled"
        last_usage = {}

        def stream_chat(self, messages, *, tools=None):
            calls["work"].append(messages[-1].content)
            if callback := calls.get("on_work"):
                callback()
            yield ProviderStreamEvent("text_delta", content="已产出示例文档，包含来源和结论。")
            yield ProviderStreamEvent("completed", finish_reason="stop")

        def generate_json(self, prompt, schema_name, schema):
            assert schema_name == "chat_goal_verdict"
            calls["verify"].append(prompt)
            return calls["answer"]

    def factory(db, *, workspace_context, settings, **kwargs):
        if not kwargs.get("agent_mode", True):
            assert kwargs["search_route"] == "disabled"
        service = ChatService(db, workspace_context.workspace_id, workspace_context.principal.user_id, Provider(), settings=settings)
        service._build_fast_preface_provider = lambda: None
        return service

    monkeypatch.setattr(chat_goals, "build_chat_service", factory)
    monkeypatch.setattr(router, "service", lambda db, context, settings, **kwargs: factory(db, workspace_context=context, settings=settings, **kwargs))
    token, workspace, *_ = register_user("goal")
    headers = auth_headers(token, workspace)
    session = client.post("/api/v1/sessions", json={"title": "Goal test", "memory_enabled": False}, headers=headers)
    assert session.status_code == 201, session.text
    sid = session.json()["id"]
    return f"/api/v1/sessions/{sid}/goal", headers, sid, calls


def test_goal_continues_and_stops_at_round_budget(client, goal_session, auth_headers, register_user):
    url, headers, sid, calls = goal_session
    assert client.get(url, headers=headers).json() is None
    payload = {"objective": "制作示例文档，并验证来源", "max_rounds": 2}
    start = client.post(url, headers=headers, json=payload)
    assert start.status_code == 202, start.text
    goal = start.json()
    assert client.post(url, headers=headers, json=payload).json()["id"] == goal["id"]
    assert client.post(url, headers=headers, json={**payload, "objective": "另一任务"}).status_code == 409
    token, workspace, *_ = register_user("goal_other")
    assert client.get(url, headers=auth_headers(token, workspace)).status_code == 404
    assert run_one_durable_job("goal-test")
    status = client.get(url, headers=headers).json()
    assert status["status"] == "queued", status
    assert status["rounds_used"] == 1 and status["last_message_id"]
    with SessionLocal() as db:
        job = db.get(DurableJob, db.get(ChatGoalRun, sid).job_id)
        job.available_at = utc_now() - timedelta(seconds=1)
        db.commit()
    assert run_one_durable_job("different-worker-after-restart")
    status = client.get(url, headers=headers).json()
    assert status["status"] == "budget_exhausted", status
    assert status["rounds_used"] == 2 and len(calls["verify"]) == len(calls["work"]) == 2
    assert "上轮检查点" in calls["work"][1] and "已产出示例文档" in calls["verify"][1]
    assert not run_one_durable_job("goal-test")
    assert client.post(url + "/control", headers=headers, json={"goal_id": goal["id"], "action": "resume"}).status_code == 409


def test_goal_does_not_complete_without_evidence(client, goal_session):
    url, headers, _, calls = goal_session
    assert client.post(url, headers=headers, json={"objective": "验证示例文档"}).status_code == 202
    calls["answer"] = {"verdict": "completed", "summary": "缺少证据的结论", "evidence": " "}
    assert not run_one_durable_job("normal-queue", exclude_kind=chat_goals.CHAT_GOAL_JOB_KIND)
    assert run_one_durable_job("goal-test", kind=chat_goals.CHAT_GOAL_JOB_KIND)
    status = client.get(url, headers=headers).json()
    assert status["status"] == "failed" and status["rounds_used"] == 1, status
    assert len(calls["verify"]) == 1


def test_goal_pause_resume_completion_and_clear_keep_messages(client, goal_session):
    url, headers, sid, calls = goal_session
    goal = client.post(url, headers=headers, json={"objective": "生成并验收示例文档"}).json()
    def control(action):
        response = client.post(url + "/control", headers=headers, json={"goal_id": goal["id"], "action": action})
        assert response.status_code == 200, response.text
        return response.json()

    calls["on_work"] = lambda: control("pause")
    assert run_one_durable_job("goal-test")
    stopped = client.get(url, headers=headers).json()
    assert stopped["status"] == "paused" and stopped["rounds_used"] == 1, stopped
    assert not calls["verify"]
    del calls["on_work"]
    calls["answer"] = {"verdict": "completed", "summary": "文档完成", "evidence": "回复给出来源和结论"}
    assert control("resume")["status"] == "queued"
    assert run_one_durable_job("goal-test")
    status = client.get(url, headers=headers).json()
    assert status["status"] == "completed" and status["rounds_used"] == 2, status
    assert control("clear") is None
    assert client.get(url, headers=headers).json() is None
    with SessionLocal() as db:
        assert len(db.scalars(select(Message).where(Message.session_id == sid)).all()) == 4


@pytest.mark.parametrize("turn_status", ["completed", "interrupted"])
def test_goal_reclaims_lease_without_replaying_a_persisted_turn(client, goal_session, turn_status):
    url, headers, sid, calls = goal_session
    goal = client.post(url, headers=headers, json={"objective": "恢复测试"}).json()
    with SessionLocal() as db:
        row = db.get(ChatGoalRun, sid)
        row.status = "running"
        job = db.get(DurableJob, row.job_id)
        job.status = "leased"
        job.lease_owner = "dead-worker"
        job.lease_token = "dead-lease"
        job.lease_expires_at = utc_now() - timedelta(seconds=1)
        user = Message(workspace_id=row.workspace_id, session_id=sid, role="user", content="恢复测试")
        result = Message(workspace_id=row.workspace_id, session_id=sid, role="assistant", status=turn_status, content="已保存的结果")
        db.add_all([user, result])
        db.flush()
        from app.domain.models import MessageVersion
        version = MessageVersion(workspace_id=row.workspace_id, message_id=result.id, status=turn_status)
        db.add(version)
        db.flush()
        db.add(MessageSubmission(workspace_id=row.workspace_id, session_id=sid, user_message_id=user.id,
            assistant_message_id=result.id, message_version_id=version.id, status=turn_status,
            idempotency_key_hash=hashlib.sha256(f"goal:{goal['id']}:1:{job.id}".encode()).hexdigest(), request_hash="test"))
        db.commit()
    calls["answer"] = {"verdict": "completed", "summary": "文档完成", "evidence": "已保存的结果"}
    assert run_one_durable_job("recovery-worker")
    status = client.get(url, headers=headers).json()
    assert status["status"] == ("completed" if turn_status == "completed" else "failed"), status
    assert status["rounds_used"] == 1 and status["last_message_id"]
    assert not calls["work"]
    assert bool(calls["verify"]) == (turn_status == "completed")
