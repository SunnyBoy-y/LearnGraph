"""Unit tests for the parallel-safe Agent tool batch scheduler.

Covers the pure scheduler policy in ``app.services.agent_tool_batch``:
execution-class taxonomy, parallel vs serial dispatch, deterministic
provider-order merging, per-future soft timeout, and single-failure
containment. The scheduler is deliberately ChatService-free so these tests
need no database or provider wiring.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.agent_tool_batch import (
    AgentToolOutcome,
    AgentToolRunEntry,
    PARALLEL_SAFE_TOOL_NAMES,
    ToolExecutionClass,
    execute_agent_tool_batch,
    tool_execution_class,
)


def _entry(position: int, name: str = "search_web") -> AgentToolRunEntry:
    return AgentToolRunEntry(
        position=position,
        tool_call={
            "id": f"call_{position}",
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        },
        tool_name=name,
        run_id=f"run_{position}",
    )


def _ok_outcome(entry: AgentToolRunEntry) -> AgentToolOutcome:
    return AgentToolOutcome(
        position=entry.position,
        tool_call_id=str(entry.tool_call.get("id") or ""),
        tool_name=entry.tool_name,
        content=f'{{"position": {entry.position}}}',
        meta={"status": "completed", "position": entry.position},
        sources=(),
    )


class TestToolExecutionClass:
    def test_parallel_safe_allowlist_contains_only_side_effect_free_tools(self):
        assert "get_current_time" in PARALLEL_SAFE_TOOL_NAMES
        assert "search_web" in PARALLEL_SAFE_TOOL_NAMES
        assert "search_images" in PARALLEL_SAFE_TOOL_NAMES
        # A DB-writing tool must never creep into the allowlist.
        assert "fetch_web_page" not in PARALLEL_SAFE_TOOL_NAMES
        assert "generate_image" not in PARALLEL_SAFE_TOOL_NAMES
        assert "parallel_web_research" not in PARALLEL_SAFE_TOOL_NAMES

    def test_classification_taxonomy(self):
        assert tool_execution_class("get_current_time") == ToolExecutionClass.PURE
        assert tool_execution_class("search_web") == ToolExecutionClass.EXTERNAL_READ
        assert tool_execution_class("search_images") == ToolExecutionClass.EXTERNAL_READ
        assert (
            tool_execution_class("search_session_fragments")
            == ToolExecutionClass.DATABASE_READ
        )
        assert (
            tool_execution_class("lg_goal_create") == ToolExecutionClass.DATABASE_WRITE
        )
        assert tool_execution_class("sandbox_exec") == ToolExecutionClass.WORKSPACE_WRITE
        assert tool_execution_class("some_extension_tool") == ToolExecutionClass.UNKNOWN


class TestBatchScheduler:
    def test_serial_mode_runs_in_provider_order(self):
        order: list[int] = []

        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            order.append(entry.position)
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(3)]
        outcomes = execute_agent_tool_batch(
            entries, runner, max_workers=1, timeout_seconds=5, parallel_safe=False
        )
        assert [outcome.position for outcome in outcomes] == [0, 1, 2]
        assert order == [0, 1, 2]

    def test_parallel_mode_overlaps_execution_and_merges_in_order(self):
        """Three runners must overlap (proven by a Barrier) yet outcomes stay
        merged in provider order regardless of completion order."""
        barrier = threading.Barrier(3)

        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            barrier.wait(timeout=5)
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(3)]
        outcomes = execute_agent_tool_batch(
            entries, runner, max_workers=3, timeout_seconds=5, parallel_safe=True
        )
        assert [outcome.position for outcome in outcomes] == [0, 1, 2]
        assert [outcome.content for outcome in outcomes] == [
            '{"position": 0}',
            '{"position": 1}',
            '{"position": 2}',
        ]

    def test_parallel_mode_bounds_workers(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(6)]
        execute_agent_tool_batch(
            entries, runner, max_workers=2, timeout_seconds=5, parallel_safe=True
        )
        assert peak <= 2

    def test_single_failure_does_not_poison_siblings(self):
        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            if entry.position == 1:
                raise RuntimeError("boom")
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(3)]
        outcomes = execute_agent_tool_batch(
            entries, runner, max_workers=3, timeout_seconds=5, parallel_safe=True
        )
        assert [outcome.position for outcome in outcomes] == [0, 1, 2]
        assert outcomes[1].meta["status"] == "failed"
        assert outcomes[1].error == "agent_tool_failed"
        assert outcomes[0].meta["status"] == "completed"
        assert outcomes[2].meta["status"] == "completed"

    def test_timeout_returns_soft_timeout_outcome(self):
        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            time.sleep(0.5)
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(2)]
        outcomes = execute_agent_tool_batch(
            entries, runner, max_workers=2, timeout_seconds=0.05, parallel_safe=True
        )
        assert [outcome.position for outcome in outcomes] == [0, 1]
        assert all(outcome.timed_out for outcome in outcomes)
        assert all(outcome.meta["reason"] == "agent_tool_timeout" for outcome in outcomes)

    def test_is_cancelled_stops_serial_dispatch(self):
        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(4)]
        outcomes = execute_agent_tool_batch(
            entries,
            runner,
            max_workers=1,
            timeout_seconds=5,
            parallel_safe=False,
            is_cancelled=lambda: True,
        )
        # All remaining entries collapse to cancelled failures; none run.
        assert all(outcome.meta["reason"] == "agent_tool_cancelled" for outcome in outcomes)

    def test_single_entry_never_parallelizes(self):
        ran = threading.Event()

        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            ran.set()
            return _ok_outcome(entry)

        outcomes = execute_agent_tool_batch(
            [_entry(0)], runner, max_workers=4, timeout_seconds=5, parallel_safe=True
        )
        assert ran.is_set()
        assert len(outcomes) == 1
