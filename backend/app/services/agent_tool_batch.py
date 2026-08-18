from __future__ import annotations

"""Agent tool batch execution policy and concurrency-safe scheduler.

Concurrency safety contract
---------------------------
* ``PARALLEL_SAFE_TOOL_NAMES`` lists tools proven free of any local side
  effect: no DB writes, no filesystem mutation, no sandbox mutation, and no
  provider-instance mutable state shared with sibling calls.  These are the
  only tools eligible for concurrent execution, and they must run on an
  isolated SQLAlchemy Session (see ``build_agent_tool_worker_runtime``).
* Every other tool keeps the legacy single-worker shared-executor path
  (serialized), preserving the current transaction/audit semantics exactly.
* Outcomes are always merged back in provider call order (position), never in
  completion order, so transcript replay matches the live SSE stream and
  tests are deterministic regardless of thread scheduling.
* A timed-out future is soft-cancelled only (Python threads cannot be killed);
  late work is discarded by the caller's attempt token, never replayed into
  the transcript.

This module deliberately contains no ChatService import: the scheduler is a
pure policy over ``(position, runner)`` pairs so it stays unit-testable.
"""

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol, Sequence

__all__ = [
    "AgentToolOutcome",
    "AgentToolRunEntry",
    "AgentToolRunner",
    "PARALLEL_SAFE_TOOL_NAMES",
    "ToolExecutionClass",
    "execute_agent_tool_batch",
    "tool_execution_class",
]


class ToolExecutionClass(StrEnum):
    """Coarse concurrency class used for policy bookkeeping and auditing.

    The scheduler itself only parallelizes ``PARALLEL_SAFE_TOOL_NAMES``; the
    class taxonomy exists so future stages (resource-key scheduling, DB-read
    parallelism) have an explicit, auditable classification to extend.
    """

    PURE = "pure"  # host-local, side-effect-free computation
    EXTERNAL_READ = "external_read"  # outbound read-only I/O (search/fetch)
    DATABASE_READ = "database_read"  # DB reads on an isolated session
    DATABASE_WRITE = "database_write"  # DB writes, serialized by resource key
    WORKSPACE_WRITE = "workspace_write"  # sandbox/workspace/path mutations
    UNKNOWN = "unknown"  # extensions without a declared policy


# Tools proven free of any local side effect and safe to run concurrently on
# isolated sessions.  Keep this list small and review it on every addition:
# a tool becomes eligible only when a code audit confirms it performs no
# ``extensions.db`` write, no audit commit, no sandbox mutation, and no
# provider-instance state mutation shared across calls.
PARALLEL_SAFE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_current_time",
        "search_web",
        "search_images",
    }
)

# Deterministic classification for every built-in Agent tool.  Extensions
# (MCP/Skill) default to UNKNOWN and are never parallelized by this module.
_DATABASE_READ_TOOLS: frozenset[str] = frozenset(
    {
        "search_session_fragments",
        "search_conversation_history",
        "read_conversation_segment",
        "get_memory_evidence",
        "subapp_observe",
        "list_providers",
        "list_provider_models",
        "get_model_capabilities",
        "get_secret_store_status",
        "list_settings",
        "get_setting",
        "get_provider_balance",
        "get_provider_balance_query_config",
        "get_alert_email_config",
        "get_functional_model_defaults",
        "list_secret_labels",
        "get_budget_status",
        "list_budget_alerts",
        "get_exchange_rate",
        "list_manual_prices",
        "get_usage_summary",
        "list_usage_events",
        "get_memory_policy",
        "list_plugins",
        "get_local_probe_policy",
        "get_deep_research",
        "read_chart",
        "lg_goal_read",
        "component_list",
        "list_session_files",
    }
)

_PURE_TOOLS: frozenset[str] = frozenset(
    {
        "get_current_time",
        "canvas_get_render_contract",
        "create_chart",
    }
)

_EXTERNAL_READ_TOOLS: frozenset[str] = frozenset(
    {
        "search_web",
        "search_images",
        # fetch_web_page may consume a one-time authorization grant (a DB
        # write) or create an authorization challenge, so it is *not*
        # parallel-safe even though its hot path is read-only I/O.
        "parallel_web_research",
    }
)

# Everything not listed above and not sandbox_*/unknown falls into
# DATABASE_WRITE (management writes, Goal/Graph writes, memory writes,
# provider writes, canvas/artifact writes, image/file persistence, etc.).
_DATABASE_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "propose_memory_draft",
        "canvas_emit_trusted_component",
        "canvas_emit_magic_card",
        "artifact_publish_card",
        "component_register_manifest",
        "component_authorize",
        "lg_graph_create",
        "lg_graph_propose_change",
        "lg_goal_create",
        "lg_goal_confirm",
        "lg_goal_ask",
        "lg_goal_ask_batch",
        "lg_goal_edit_draft",
        "subapp_patch_state",
        "create_provider",
        "update_provider",
        "rotate_provider_secret",
        "delete_provider",
        "put_model_capabilities",
        "refresh_provider_models",
        "probe_provider",
        "verify_provider_declaration",
        "validate_provider_default_models",
        "configure_dashscope_balance",
        "update_setting",
        "rename_conversation",
        "update_provider_balance_query_config",
        "set_model_enabled",
        "set_models_enabled",
        "update_alert_email_config",
        "test_alert_email",
        "set_functional_model_default",
        "list_budget_policies",
        "delete_budget_policy",
        "acknowledge_budget_alert",
        "set_exchange_rate",
        "refresh_exchange_rate",
        "upsert_manual_price",
        "remove_manual_price",
        "refresh_models_dev_snapshot",
        "update_memory_policy",
        "reindex_memory_embeddings",
        "toggle_plugin",
        "update_local_probe_policy",
        "refresh_mcp_server",
        "update_skill_manifest",
        "transcribe_audio",
        "analyze_image",
        "start_deep_research",
        "download_external_image",
        "download_github_source",
        "fetch_web_page",
        "read_session_file",
        "generate_image",
    }
)


def tool_execution_class(name: str) -> ToolExecutionClass:
    """Return the auditable concurrency class for a built-in tool name."""
    if name in _PURE_TOOLS:
        return ToolExecutionClass.PURE
    if name in _EXTERNAL_READ_TOOLS:
        return ToolExecutionClass.EXTERNAL_READ
    if name in _DATABASE_READ_TOOLS:
        return ToolExecutionClass.DATABASE_READ
    if name in _DATABASE_WRITE_TOOLS:
        return ToolExecutionClass.DATABASE_WRITE
    if name.startswith("sandbox_"):
        return ToolExecutionClass.WORKSPACE_WRITE
    return ToolExecutionClass.UNKNOWN


@dataclass(frozen=True, slots=True)
class AgentToolRunEntry:
    """One tool call scheduled in a batch.

    ``position`` is the provider call index (0-based, stable order); outcomes
    are merged back into the transcript in this order, never in completion
    order.  ``run_id`` is the per-tool audit run id (may be shared by multiple
    tools of one assistant turn, mirroring the legacy call site).
    """

    position: int
    tool_call: dict[str, Any]
    tool_name: str
    run_id: str


@dataclass(frozen=True, slots=True)
class AgentToolOutcome:
    """Immutable result of one tool execution, safe to hand across threads."""

    position: int
    tool_call_id: str
    tool_name: str
    content: str
    meta: dict[str, Any]
    sources: tuple[dict[str, Any], ...]
    timed_out: bool = False
    elapsed_ms: int = 0
    error: str | None = None


class AgentToolRunner(Protocol):
    """A runner executes one entry and returns its immutable outcome.

    Implementations must not touch the caller's SQLAlchemy Session, SSE
    sequence, MessagePart ordinal, or transcript state from a worker thread.
    """

    def __call__(self, entry: AgentToolRunEntry) -> AgentToolOutcome: ...


def execute_agent_tool_batch(
    entries: Sequence[AgentToolRunEntry],
    runner: AgentToolRunner,
    *,
    max_workers: int,
    timeout_seconds: float | None,
    parallel_safe: bool,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[AgentToolOutcome]:
    """Run a batch of tool calls and return outcomes in provider order.

    * ``parallel_safe=True`` and more than one entry: execute concurrently on
      ``ThreadPoolExecutor`` with ``max_workers`` bounded; every future gets an
      independent deadline (``timeout_seconds``); a timed-out future is
      soft-cancelled and replaced with a timeout outcome.
    * Otherwise: run serially in provider order via the same runner, so the
      legacy path keeps its exact semantics.
    * ``is_cancelled`` (when provided) is honored between serial executions
      only; concurrent batches start all workers up front and rely on the
      caller's attempt token to discard late results.
    """
    outcomes: list[AgentToolOutcome | None] = [None] * len(entries)
    if parallel_safe and len(entries) > 1 and max_workers > 1:
        worker_count = min(len(entries), max(1, int(max_workers)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_position = {
                executor.submit(runner, entry): entry.position for entry in entries
            }
            pending = set(future_to_position)
            completed_positions: set[int] = set()
            try:
                iterator = (
                    as_completed(future_to_position)
                    if timeout_seconds is None
                    else as_completed(future_to_position, timeout=timeout_seconds)
                )
                for future in iterator:
                    pending.discard(future)
                    position = future_to_position[future]
                    completed_positions.add(position)
                    try:
                        outcomes[position] = future.result()
                    except Exception:
                        entry = _entry_at(entries, position)
                        outcomes[position] = _failure_outcome(
                            entry, "agent_tool_failed", "The authorized tool failed"
                        )
            except FutureTimeout:
                # Every still-pending worker shares the batch deadline (soft
                # only; Python threads cannot be killed). The caller's attempt
                # token discards any late result.
                for future in pending:
                    future.cancel()
                    position = future_to_position[future]
                    entry = _entry_at(entries, position)
                    outcomes[position] = AgentToolOutcome(
                        position=position,
                        tool_call_id=str(entry.tool_call.get("id") or ""),
                        tool_name=entry.tool_name,
                        content=_timeout_content(timeout_seconds, entry.tool_name),
                        meta={
                            "status": "failed",
                            "reason": "agent_tool_timeout",
                            "timeout_seconds": timeout_seconds,
                        },
                        sources=(),
                        timed_out=True,
                    )
    else:
        for entry in entries:
            if is_cancelled is not None and is_cancelled():
                outcomes[entry.position] = _failure_outcome(
                    entry, "agent_tool_cancelled", "Tool batch was cancelled"
                )
                continue
            try:
                outcomes[entry.position] = runner(entry)
            except Exception:
                outcomes[entry.position] = _failure_outcome(
                    entry, "agent_tool_failed", "The authorized tool failed"
                )
    assert all(item is not None for item in outcomes)
    return [item for item in outcomes if item is not None]  # type: ignore[misc]


def _entry_at(entries: Sequence[AgentToolRunEntry], position: int) -> AgentToolRunEntry:
    for entry in entries:
        if entry.position == position:
            return entry
    raise LookupError(f"No batch entry at position {position}")


def _failure_outcome(entry: AgentToolRunEntry, reason: str, message: str) -> AgentToolOutcome:
    import json as _json

    return AgentToolOutcome(
        position=entry.position,
        tool_call_id=str(entry.tool_call.get("id") or ""),
        tool_name=entry.tool_name,
        content=_json.dumps({"error": reason, "message": message}, ensure_ascii=False),
        meta={"status": "failed", "reason": reason, "error_code": reason},
        sources=(),
        error=reason,
    )


def _timeout_content(timeout_seconds: float | None, tool_name: str) -> str:
    import json as _json

    return _json.dumps(
        {
            "error": "agent_tool_timeout",
            "tool": tool_name,
            "timeout_seconds": timeout_seconds,
        },
        ensure_ascii=False,
    )


@dataclass(frozen=True, slots=True)
class _BatchContext:
    """Reserved for a future resource-key scheduler stage (DAG + locks)."""

    resource_keys: tuple[str, ...] = field(default_factory=tuple)
