"""Unit tests for the durable sub-agent executor (v2).

Run with:  uv run --locked --extra test pytest tests/unit/test_sandbox_agent_executor.py -q
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.providers.ports.model import ProviderChatMessage, ProviderStreamEvent
from app.services.sandbox_agent_executor import (
    execute_subagent_job,
    finalize_deliverables,
)


def _settings() -> Any:
    return SimpleNamespace(
        sandbox_subagent_max_rounds=6,
        sandbox_subagent_max_seconds=300,
        sandbox_subagent_default_max_tokens=60_000,
        sandbox_subagent_default_max_cost_usd=0.15,
    )


def _job(payload: dict[str, Any], **overrides: Any) -> Any:
    base = {
        "id": "job_1",
        "workspace_id": "ws_test",
        "owner_user_id": "u_test",
        "chat_session_id": "ch_test",
        "attempt": 1,
        "status": "STARTING",
        "cancel_requested": False,
        "payload_json": payload,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _task(**overrides: Any) -> Any:
    base = {
        "id": "task_pk_1",
        "task_id": "sa_test",
        "workspace_id": "ws_test",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "task_id": "task_pk_1",
        "task_title": "测试任务",
        "role_key": "generic",
        "prompt": "完成测试",
        "tools": None,
        "write_set": None,
        "budget": {"max_rounds": 4, "max_seconds": 60, "max_tokens": 60_000, "max_cost_usd": 0.15},
        "sandbox_session_id": None,
    }
    base.update(overrides)
    return base


class FakeProvider:
    def __init__(self, script: list[list[ProviderStreamEvent]], usage: dict | None = None) -> None:
        self.script = script
        self.calls = 0
        self.last_usage = usage or {}
        self.provider_id = "fake"
        self.model_id = "fake-model"
        self.remote_capability = False  # Scripted unit provider has no remote billing/database.

    def stream_chat(self, messages: list[ProviderChatMessage], *, tools: list[dict] | None = None):
        assert self.calls < len(self.script), "provider script exhausted"
        events = self.script[self.calls]
        self.calls += 1
        yield from events


class FakeSandbox:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {"ok": True}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_agent_tool(self, name: str, arguments: dict[str, Any], *, chat_session_id: str, agent_authorized: bool):
        self.calls.append((name, arguments))
        return self.result

    def list_files(self, request: Any) -> dict[str, Any]:
        return {"files": [{"path": "work/subagents/task_pk_1/outputs/x.css", "size_bytes": 10}]}


def _text(text: str) -> ProviderStreamEvent:
    return ProviderStreamEvent(type="text_delta", content=text)


def _done() -> ProviderStreamEvent:
    return ProviderStreamEvent(type="completed", finish_reason="stop")


def _tool_calls(calls: list[dict[str, Any]]) -> ProviderStreamEvent:
    return ProviderStreamEvent(type="tool_calls", tool_calls=calls)


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}


def _handoff_text() -> str:
    return (
        "完成\n"
        '{"summary": "完成样式", "artifacts": [{"path": "work/subagents/task_pk_1/outputs/x.css", "change": "新增"}], '
        '"evidence": [{"check": "css", "result": "passed"}], '
        '"acceptance": [{"criterion": "样式存在", "status": "passed"}], '
        '"risks": [], "unresolved": [], "recommended_next_action": "merge", "confidence": 0.9}'
    )


class TestExecutor:
    def test_success_with_handoff(self) -> None:
        provider = FakeProvider([[_text(_handoff_text()), _done()]])
        sandbox = FakeSandbox()
        events: list[tuple[str, dict]] = []
        outcome = execute_subagent_job(
            _settings(),
            _job(_payload()),
            _task(),
            emit_event=lambda t, p: events.append((t, p)),
            provider=provider,
            sandbox_service=sandbox,
        )
        assert outcome.status == "SUCCEEDED"
        assert outcome.deliverables is not None
        assert outcome.deliverables["handoff_parse"] is True
        types = [t for t, _ in events]
        assert "started" in types and "succeeded" in types

    def test_historical_round_budget_does_not_stop_execution(self) -> None:
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_env_info", {})]), _done()],
                [_tool_calls([_call("c2", "sandbox_env_info", {})]), _done()],
                [_tool_calls([_call("c3", "sandbox_env_info", {})]), _done()],
                [_text(_handoff_text()), _done()],
            ]
        )
        sandbox = FakeSandbox()
        outcome = execute_subagent_job(
            _settings(),
            _job(_payload(budget={"max_rounds": 2, "max_seconds": 60})),
            _task(),
            provider=provider,
            sandbox_service=sandbox,
        )
        assert outcome.status == "SUCCEEDED"
        assert provider.calls == 4

    def test_empty_answer_fails(self) -> None:
        provider = FakeProvider([[_done()]])
        sandbox = FakeSandbox()
        outcome = execute_subagent_job(
            _settings(), _job(_payload()), _task(), provider=provider, sandbox_service=sandbox
        )
        assert outcome.status == "FAILED"
        assert outcome.error_class == "EmptyResult"

    def test_write_set_rejects_outside_path(self) -> None:
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_write_file", {"path": "work/shared/a.txt", "content": "x"})]), _done()],
                [_text(_handoff_text()), _done()],
            ]
        )
        sandbox = FakeSandbox()
        execute_subagent_job(
            _settings(),
            _job(_payload(write_set=["work/subagents/task_pk_1"])),
            _task(),
            provider=provider,
            sandbox_service=sandbox,
        )
        assert sandbox.calls == []  # out-of-lane write never executed

    def test_session_id_injected(self) -> None:
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_env_info", {})]), _done()],
                [_text(_handoff_text()), _done()],
            ]
        )
        sandbox = FakeSandbox()
        execute_subagent_job(
            _settings(),
            _job(_payload(sandbox_session_id="sb_pinned")),
            _task(),
            provider=provider,
            sandbox_service=sandbox,
        )
        assert sandbox.calls[0][1]["sandbox_session_id"] == "sb_pinned"

    def test_historical_token_budget_does_not_stop_execution(self) -> None:
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_env_info", {})]), _done()],
                [_tool_calls([_call("c2", "sandbox_env_info", {})]), _done()],
                [_text(_handoff_text()), _done()],
            ],
            usage={"input_tokens": 100_000, "output_tokens": 100_000},
        )
        sandbox = FakeSandbox()
        outcome = execute_subagent_job(
            _settings(),
            _job(_payload(budget={"max_rounds": 6, "max_seconds": 60, "max_tokens": 50_000})),
            _task(),
            provider=provider,
            sandbox_service=sandbox,
        )
        assert outcome.status == "SUCCEEDED"
        assert outcome.attempt_record["token_usage"] > 50_000

    def test_cancel_returns_cancelled(self) -> None:
        provider = FakeProvider([[_tool_calls([_call("c1", "sandbox_env_info", {})]), _done()]])
        sandbox = FakeSandbox()
        outcome = execute_subagent_job(
            _settings(),
            _job(_payload(), status="CANCELLED", cancel_requested=True),
            _task(),
            provider=provider,
            sandbox_service=sandbox,
        )
        assert outcome.status == "CANCELLED"

    def test_tool_error_surfaces_but_loop_continues(self) -> None:
        class BoomSandbox(FakeSandbox):
            def execute_agent_tool(self, name, arguments, *, chat_session_id, agent_authorized):
                raise RuntimeError("boom")

        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_env_info", {})]), _done()],
                [_text(_handoff_text()), _done()],
            ]
        )
        outcome = execute_subagent_job(
            _settings(),
            _job(_payload()),
            _task(),
            provider=provider,
            sandbox_service=BoomSandbox(),
        )
        assert outcome.status == "SUCCEEDED"


class TestFinalizeDeliverables:
    def test_complete_contract(self) -> None:
        text = (
            '{"summary": "x", "artifacts": [{"path": "work/o/a.txt"}], '
            '"evidence": [{"check": "c", "result": "passed"}], '
            '"acceptance": [{"criterion": "c", "status": "passed"}]}'
        )
        deliverables, complete = finalize_deliverables(text, default_output_root="work/o", file_exists=lambda p: True)
        assert complete is True
        assert deliverables["handoff_parse"] is True

    def test_missing_summary_incomplete(self) -> None:
        text = '{"artifacts": [{"path": "work/o/a.txt"}], "acceptance": [{"criterion": "c", "status": "passed"}]}'
        _deliverables, complete = finalize_deliverables(text, default_output_root="work/o", file_exists=lambda p: True)
        assert complete is False

    def test_missing_file_incomplete(self) -> None:
        text = (
            '{"summary": "x", "artifacts": [{"path": "work/o/a.txt"}], '
            '"acceptance": [{"criterion": "c", "status": "passed"}]}'
        )
        _deliverables, complete = finalize_deliverables(text, default_output_root="work/o", file_exists=lambda p: False)
        assert complete is False

    def test_no_json_block_incomplete(self) -> None:
        _deliverables, complete = finalize_deliverables("我完成了", default_output_root="work/o")
        assert complete is False
