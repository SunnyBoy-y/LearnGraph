"""Exercise the real scoped draft/event pipeline with scripted tool decisions."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.memory_profile import MemoryProfileService


def call(name, arguments, id="call-1"):
    return ProviderStreamEvent(type="tool_calls", tool_calls=[{
        "id": id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }])


def atom(operation="CREATE", target=None, confidence=0.95):
    return {
        "operation": operation, "target_memory_id": target,
        "memory_type": "learning_preference", "atom_kind": "preference",
        "canonical_key": "learning.explanation_style", "title": "偏好简洁讲解",
        "statement": "用户喜欢简洁的讲解", "confidence": confidence, "importance": 0.6,
        "temporal_status": "timeless", "summary_eligibility": "durable",
    }


@pytest.fixture
def model_agent(monkeypatch):
    class Model:
        available = True
        provider_id = "scripted"
        model_id = "scripted-tools"
        remote_capability = False
        last_usage = {"input_tokens": 20, "output_tokens": 10}
        context_window_tokens = 128_000
        max_output_tokens = 4096

        def __init__(self):
            self.rounds = []
            self.decide = lambda index, messages: []

        def stream_chat(self, messages, *, tools):
            self.rounds.append(list(messages))
            assert [t["function"]["name"] for t in tools] == ["read_memories", "update_memories"]
            yield from self.decide(len(self.rounds) - 1, messages)
            yield ProviderStreamEvent(type="completed")

    model = Model()
    monkeypatch.setattr(MemoryProfileService, "_model_selection", lambda self, *args: (model.provider_id, model.model_id))
    monkeypatch.setattr(MemoryProfileService, "_profile_model", lambda self, *args: (model, True))
    monkeypatch.setattr(MemoryProfileService, "refresh_profile", lambda self, **kwargs: SimpleNamespace(status="ready"))
    billing = MagicMock()
    monkeypatch.setattr("app.services.memory_agent.BillingService", lambda *args: billing)
    model.billing = billing
    return model


def stream(client, headers, **payload):
    response = client.post("/api/v1/memory/profile/intents/stream", headers=headers, json={
        "text": "请记住我喜欢简洁讲解", **payload,
    })
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    return events


@pytest.mark.parametrize("operation", ["CREATE", "CORRECT", "RETRACT"])
def test_agent_reads_writes_observes_and_persists(client, register_user, auth_headers, model_agent, operation):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    target = None
    if operation != "CREATE":
        # Legacy/manual records must also be editable and forgettable.
        target = client.post("/api/v1/memory", headers=headers, json={
            "title": "旧偏好", "content": "用户喜欢冗长讲解", "namespace": "workspace",
        }).json()["id"]
    update = atom(operation, target)

    def decide(index, messages):
        if index == 0:
            return [call("read_memories", {})]
        if index == 1:
            observed = json.loads(messages[-1].content)
            if target:
                assert observed["memories"][0]["id"] == target
                assert "冗长" in observed["memories"][0]["content"]
            return [call("update_memories", {"atoms": [update]}, "write-1")]
        observation = json.loads(messages[-1].content)
        assert observation["auto_committed"] == 1
        return [ProviderStreamEvent(type="text_delta", content="已根据要求处理记忆。")]

    model_agent.decide = decide
    events = stream(client, headers)
    assert events[-1][0] == "result", events
    result = events[-1][1]
    assert result["auto_committed"] == 1
    assert result["message"] == "已根据要求处理记忆。"
    assert result["profile_status"] == "ready"
    assert model_agent.billing.record_usage.call_count == 3
    assert [d["status"] for e, d in events if e == "step" and d["id"] == "tool-2"] == ["running", "completed"]
    stored = client.get(f"/api/v1/memory/{result['affected_memory_ids'][0]}", headers=headers).json()
    if operation == "RETRACT":
        assert stored["state"] == "deleted"
    else:
        assert stored["state"] == "active"
        assert "用户喜欢简洁的讲解" in stored["content"]
        assert stored["evidence_ids"]
    assert client.get("/api/v1/memory/drafts?status=PENDING", headers=headers).json() == []


def test_agent_low_confidence_stays_pending(client, register_user, auth_headers, model_agent):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    model_agent.decide = lambda i, _: (
        [call("read_memories", {})] if i == 0 else
        [call("update_memories", {"atoms": [atom(confidence=0.5)]})] if i == 1 else
        [ProviderStreamEvent(type="text_delta", content="已整理为待确认草稿。")]
    )
    events = stream(client, headers)
    assert events[-1][1]["auto_committed"] == 0
    assert events[-1][1]["drafts_created"] == 1
    assert len(client.get("/api/v1/memory/drafts", headers=headers).json()) == 1
    assert client.get("/api/v1/memory", headers=headers).json() == []


def test_agent_rejects_unknown_target_and_can_recover(client, register_user, auth_headers, model_agent):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)

    def decide(i, messages):
        if i == 0:
            return [call("read_memories", {})]
        if i == 1:
            return [call("update_memories", {"atoms": [atom("RETRACT", "foreign-memory")]})]
        assert json.loads(messages[-1].content)["error"] == "invalid_tool_arguments"
        return [ProviderStreamEvent(type="text_delta", content="没有找到要删除的记忆，请说明具体内容。")]

    model_agent.decide = decide
    events = stream(client, headers)
    assert events[-1][1]["status"] == "no_change"
    assert any(e == "step" and d["status"] == "failed" for e, d in events)
    assert client.get("/api/v1/memory", headers=headers).json() == []


def test_agent_deduplicates_repeated_successful_tool(client, register_user, auth_headers, model_agent):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    model_agent.decide = lambda i, _: (
        [call("read_memories", {})] if i == 0 else
        [call("update_memories", {"atoms": [atom()]}, f"write-{i}")] if i < 3 else
        [ProviderStreamEvent(type="text_delta", content="记忆已更新。")]
    )
    events = stream(client, headers)
    assert events[-1][1]["drafts_created"] == 1
    assert len(client.get("/api/v1/memory", headers=headers).json()) == 1


def test_agent_deduplicates_overlapping_batches(client, register_user, auth_headers, model_agent):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    another = {**atom(), "canonical_key": "learning.examples", "title": "偏好例子", "statement": "用户喜欢例子"}
    model_agent.decide = lambda i, _: (
        [call("read_memories", {})] if i == 0 else
        [call("update_memories", {"atoms": [atom()]})] if i == 1 else
        [call("update_memories", {"atoms": [atom(), another]})] if i == 2 else
        [ProviderStreamEvent(type="text_delta", content="已更新两条记忆。")]
    )
    events = stream(client, headers)
    assert events[-1][1]["auto_committed"] == 2
    assert len(client.get("/api/v1/memory", headers=headers).json()) == 2


def test_agent_partial_batch_failure_reports_earlier_commits(client, register_user, auth_headers, model_agent, monkeypatch):
    from app.core.errors import AppError

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    another = {**atom(), "title": "第二条", "statement": "第二条事实"}
    original = MemoryProfileService.apply_atom_operations

    def apply(self, request, payload, *args, **kwargs):
        if payload["atoms"][0]["title"] == "第二条":
            raise AppError(503, "memory_write_failed", "write failed")
        return original(self, request, payload, *args, **kwargs)

    monkeypatch.setattr(MemoryProfileService, "apply_atom_operations", apply)
    model_agent.decide = lambda i, _: (
        [call("read_memories", {})] if i == 0 else
        [call("update_memories", {"atoms": [atom(), another]})]
    )
    events = stream(client, headers)
    assert events[-1][0] == "error"
    assert events[-1][1]["partial_result"]["auto_committed"] == 1
    assert len(client.get("/api/v1/memory", headers=headers).json()) == 1


def test_agent_limit_does_not_report_success(client, register_user, auth_headers, model_agent):
    token, ws, _, _ = register_user()
    model_agent.decide = lambda i, _: [call("read_memories", {}, f"read-{i}")]
    events = stream(client, auth_headers(token, ws))
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "memory_profile_agent_limit"


def test_agent_partial_failure_keeps_actual_committed_count(client, register_user, auth_headers, model_agent):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)

    def decide(i, _):
        if i == 0:
            return [call("read_memories", {})]
        if i == 1:
            return [call("update_memories", {"atoms": [atom()]})]
        raise RuntimeError("provider disconnected")

    model_agent.decide = decide
    events = stream(client, headers)
    assert events[-1][0] == "error"
    assert events[-1][1]["partial_result"]["auto_committed"] == 1
    assert len(client.get("/api/v1/memory", headers=headers).json()) == 1


def test_agent_rejects_cross_workspace_selection(client, register_user, auth_headers, model_agent):
    token, ws, _, _ = register_user("owner")
    headers = auth_headers(token, ws)
    foreign_id = client.post("/api/v1/memory", headers=headers, json={
        "title": "私有记忆", "content": "另一用户的事实", "namespace": "workspace",
    }).json()["id"]
    other_token, other_ws, _, _ = register_user("other")
    events = stream(client, auth_headers(other_token, other_ws), selected_atom_ids=[foreign_id])
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "memory_profile_target_unavailable"
    assert model_agent.rounds == []


def test_agent_unconfigured_never_saves_instruction(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    response = client.post("/api/v1/memory/profile/intents/stream", headers=headers, json={"text": "删除记忆"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "memory_profile_model_unconfigured"
    assert client.get("/api/v1/memory", headers=headers).json() == []
