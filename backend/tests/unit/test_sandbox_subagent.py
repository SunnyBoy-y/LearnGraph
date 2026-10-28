"""Unit tests for the sandbox sub-agent runner (nested agent loop).

The loop is tested with injected fakes: a scripted ``stream_chat`` provider
and a recording sandbox service — no database or model provider is touched.

Run with:  uv run --locked --extra test pytest tests/unit/test_sandbox_subagent.py -q
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from app.providers.ports.model import ProviderChatMessage, ProviderStreamEvent
from app.services.sandbox_subagent import (
    SubagentJob,
    SubagentRegistry,
    SubagentSpec,
    _extract_tool_call,
    _run_subagent,
)


def _spec(**overrides: Any) -> SubagentSpec:
    base: dict[str, Any] = {
        "subagent_id": "sa_test_1",
        "workspace_id": "ws_test",
        "actor_id": "u_test",
        "chat_session_id": "ch_test",
        "prompt": "请完成测试任务",
        "tools": None,
        "max_rounds": 6,
        "max_seconds": 300,
        "sandbox_session_id": None,
        "write_set": None,
        "max_tool_calls": None,
    }
    base.update(overrides)
    return SubagentSpec(**base)


class FakeProvider:
    """Yields a scripted event sequence per call."""

    def __init__(self, script: list[list[ProviderStreamEvent]]) -> None:
        self.script = script
        self.calls = 0

    def stream_chat(self, messages: list[ProviderChatMessage], *, tools: list[dict] | None = None):
        assert self.calls < len(self.script), "provider script exhausted"
        events = self.script[self.calls]
        self.calls += 1
        yield from events


class FakeSandbox:
    """Records executed tools and returns canned results."""

    def __init__(self, result: dict[str, Any] | None = None, *, raises: Exception | None = None) -> None:
        self.result = result or {"ok": True, "sandbox_session_id": "sb_test"}
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_agent_tool(self, name: str, arguments: dict[str, Any], *, chat_session_id: str, agent_authorized: bool):
        self.calls.append((name, arguments))
        assert agent_authorized is True
        if self.raises is not None:
            raise self.raises
        return self.result


def _text(text: str) -> ProviderStreamEvent:
    return ProviderStreamEvent(type="text_delta", content=text)


def _done() -> ProviderStreamEvent:
    return ProviderStreamEvent(type="completed", finish_reason="stop")


def _tool_calls(calls: list[dict[str, Any]]) -> ProviderStreamEvent:
    return ProviderStreamEvent(type="tool_calls", tool_calls=calls)


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}


def _run(job: Any, **kwargs: Any) -> Any:
    # Synchronous helper: run the loop body inline instead of a thread.
    _run_subagent(job.spec, job, **kwargs)
    return job


class TestSubagentLoop:
    def test_immediate_answer_no_tools(self) -> None:
        spec = _spec(subagent_id="sa_immediate")
        provider = FakeProvider([[_text("完成，结果：42"), _done()]])
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert job.result == "完成，结果：42"
        assert sandbox.calls == []
        assert job.rounds == 1

    def test_tool_round_then_answer(self) -> None:
        spec = _spec(subagent_id="sa_tool")
        provider = FakeProvider(
            [
                [_text("先看一下"), _tool_calls([_call("c1", "sandbox_list_files", {"pattern": "work/**"})]), _done()],
                [_text("最终结果：文件已确认"), _done()],
            ]
        )
        sandbox = FakeSandbox(result={"ok": True, "sandbox_session_id": "sb", "files": [{"path": "work/a.py", "size_bytes": 1}]})
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert job.result == "最终结果：文件已确认"
        assert len(sandbox.calls) == 1
        assert sandbox.calls[0][0] == "sandbox_list_files"
        assert sandbox.calls[0][1] == {"pattern": "work/**"}
        assert job.rounds == 2

    def test_tool_error_is_surfaced_but_loop_continues(self) -> None:
        spec = _spec(subagent_id="sa_error")
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_delete_file", {"path": "work/x.txt"})]), _done()],
                [_text("删除失败已记录"), _done()],
            ]
        )
        sandbox = FakeSandbox(raises=RuntimeError("boom"))
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert job.result == "删除失败已记录"
        # The tool was attempted (the RuntimeError was caught and serialized
        # back into the tool-result message, so the loop continued).
        assert len(sandbox.calls) == 1
        assert sandbox.calls[0][0] == "sandbox_delete_file"

    def test_disallowed_tool_rejected_without_execution(self) -> None:
        spec = _spec(subagent_id="sa_disallowed", tools=["sandbox_read_file"])
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_fetch", {"url": "https://x.example/"})]), _done()],
                [_text("已跳过联网工具"), _done()],
            ]
        )
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert sandbox.calls == []  # never executed the disallowed tool

    def test_empty_final_answer_fails(self) -> None:
        spec = _spec(subagent_id="sa_empty")
        provider = FakeProvider([[_done()]])  # no text, no tool calls
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "failed"
        assert job.error_class == "EmptyResult"

    def test_whitespace_final_answer_fails(self) -> None:
        spec = _spec(subagent_id="sa_blank")
        provider = FakeProvider([[_text("   \n  "), _done()]])
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "failed"
        assert job.error_class == "EmptyResult"

    def test_historical_round_limit_is_ignored(self) -> None:
        spec = _spec(subagent_id="sa_rounds", max_rounds=3)
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_env_info", {})]), _done()],
                [_tool_calls([_call("c2", "sandbox_env_info", {})]), _done()],
                [_tool_calls([_call("c3", "sandbox_env_info", {})]), _done()],
                [_text("完成"), _done()],
            ]
        )
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert job.rounds == 4

    def test_historical_time_limit_is_ignored(self) -> None:
        spec = _spec(subagent_id="sa_timeout", max_seconds=0)
        provider = FakeProvider([[_text("完成"), _done()]])
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"

    def test_historical_tool_call_limit_is_ignored(self) -> None:
        spec = _spec(subagent_id="sa_toolbudget", max_tool_calls=2)
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_env_info", {}), _call("c2", "sandbox_env_info", {})]), _done()],
                [_tool_calls([_call("c3", "sandbox_env_info", {})]), _done()],
                [_text("完成"), _done()],
            ]
        )
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert job.tool_calls == 3

    def test_write_set_rejects_outside_paths_without_execution(self) -> None:
        spec = _spec(
            subagent_id="sa_writeset",
            write_set=("work/subagents/task_a",),
        )
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_write_file", {"path": "work/shared/other.txt", "content": "x"})]), _done()],
                [_text("越界写入被拒绝"), _done()],
            ]
        )
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert sandbox.calls == []  # the out-of-write-set write never executed

    def test_write_set_allows_declared_prefix(self) -> None:
        spec = _spec(
            subagent_id="sa_writeset_ok",
            write_set=("work/subagents/task_a",),
        )
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_write_file", {"path": "work/subagents/task_a/outputs/x.css", "content": "x"})]), _done()],
                [_text("写入成功"), _done()],
            ]
        )
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert len(sandbox.calls) == 1
        assert sandbox.calls[0][0] == "sandbox_write_file"

    def test_sandbox_session_id_injected_into_tool_calls(self) -> None:
        spec = _spec(subagent_id="sa_session", sandbox_session_id="sb_pinned")
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_write_file", {"path": "work/a.txt", "content": "x"})]), _done()],
                [_text("OK"), _done()],
            ]
        )
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert sandbox.calls[0][1]["sandbox_session_id"] == "sb_pinned"

    def test_sandbox_session_id_keeps_explicit_argument(self) -> None:
        spec = _spec(subagent_id="sa_session_keep", sandbox_session_id="sb_pinned")
        provider = FakeProvider(
            [
                [_tool_calls([_call("c1", "sandbox_env_info", {"sandbox_session_id": "sb_model_chosen"})]), _done()],
                [_text("OK"), _done()],
            ]
        )
        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=provider, sandbox_service=sandbox)
        assert job.status == "completed"
        assert sandbox.calls[0][1]["sandbox_session_id"] == "sb_model_chosen"

    def test_provider_exception_fails_job(self) -> None:
        spec = _spec(subagent_id="sa_provider_err")
        provider = FakeProvider([])

        class _Boom:
            def stream_chat(self, messages, *, tools=None):
                raise RuntimeError("provider exploded")
                yield  # pragma: no cover

        sandbox = FakeSandbox()
        job = _run(SubagentJob(spec=spec), provider=_Boom(), sandbox_service=sandbox)
        assert job.status == "failed"
        assert job.error_class == "RuntimeError"


class TestExtractToolCall:
    def test_openai_shape(self) -> None:
        call_id, name, arguments = _extract_tool_call(
            {"id": "c9", "function": {"name": "sandbox_read_file", "arguments": '{"path": "work/a.py"}'}}
        )
        assert call_id == "c9"
        assert name == "sandbox_read_file"
        assert arguments == {"path": "work/a.py"}

    def test_bad_json_falls_back_to_raw(self) -> None:
        _call_id, _name, arguments = _extract_tool_call(
            {"id": "c1", "function": {"name": "x", "arguments": "{not-json"}}
        )
        assert arguments == {"raw_arguments": "{not-json"}

    def test_flat_shape(self) -> None:
        call_id, name, arguments = _extract_tool_call({"id": "c2", "name": "sandbox_grep", "arguments": {"pattern": "x"}})
        assert call_id == "c2"
        assert name == "sandbox_grep"
        assert arguments == {"pattern": "x"}


class TestRegistry:
    def test_start_and_poll_lifecycle(self) -> None:
        registry = SubagentRegistry()
        provider = FakeProvider([[_text("OK"), _done()]])
        sandbox = FakeSandbox()
        spec = _spec(subagent_id="sa_registry")
        job = registry.start(spec, provider=provider, sandbox_service=sandbox)
        try:
            deadline = time.time() + 10
            while job.status in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.02)
            assert job.status == "completed"
            assert job.result == "OK"
            assert registry.get(spec.subagent_id) is job
        finally:
            registry.cancel(spec.subagent_id)

    def test_cancel_stops_loop(self) -> None:
        import threading as _threading

        registry = SubagentRegistry()
        release = _threading.Event()

        class BlockingProvider:
            def stream_chat(self, messages, *, tools=None):
                release.wait(timeout=10)
                yield _tool_calls([_call("c1", "sandbox_env_info", {})])
                yield _done()

        sandbox = FakeSandbox()
        spec = _spec(subagent_id="sa_cancel")
        job = registry.start(spec, provider=BlockingProvider(), sandbox_service=sandbox)
        try:
            deadline = time.time() + 10
            while job.status != "running" and time.time() < deadline:
                time.sleep(0.02)
            assert job.status == "running"
            registry.cancel(spec.subagent_id)
            release.set()
            deadline = time.time() + 10
            while job.finished_at is None and time.time() < deadline:
                time.sleep(0.02)
            assert job.status == "cancelled"
            assert registry.get(spec.subagent_id).status == "cancelled"
        finally:
            release.set()
            registry.cancel(spec.subagent_id)

    def test_per_chat_concurrency_cap_raises_capacity(self) -> None:
        from app.core.errors import AppError
        from app.core.config import get_settings

        original = get_settings().sandbox_subagent_max_concurrent_chat
        try:
            get_settings().sandbox_subagent_max_concurrent_chat = 1
            registry = SubagentRegistry()
            release = __import__("threading").Event()

            class BlockingProvider:
                def stream_chat(self, messages, *, tools=None):
                    release.wait(timeout=10)
                    yield _text("OK")
                    yield _done()

            first = registry.start(
                _spec(subagent_id="sa_cap_1", chat_session_id="ch_cap"),
                provider=BlockingProvider(),
                sandbox_service=FakeSandbox(),
            )
            try:
                deadline = time.time() + 10
                while first.status != "running" and time.time() < deadline:
                    time.sleep(0.02)
                with pytest.raises(AppError) as exc_info:
                    registry.start(
                        _spec(subagent_id="sa_cap_2", chat_session_id="ch_cap"),
                        provider=BlockingProvider(),
                        sandbox_service=FakeSandbox(),
                    )
                assert exc_info.value.code == "sandbox_subagent_capacity"
            finally:
                release.set()
                registry.cancel(first.spec.subagent_id)
        finally:
            get_settings().sandbox_subagent_max_concurrent_chat = original

    def test_unknown_id_returns_none(self) -> None:
        assert SubagentRegistry.instance().get("sa_does_not_exist") is None
