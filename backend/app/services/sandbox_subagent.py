"""Sandbox sub-agent runner: nested, isolated agent loops over a restricted
sandbox tool subset.

Each sub-agent runs in its own daemon thread with its own database session and
its own model provider; it only shares the workspace's durable sandbox files
(which is the point of a sandbox sub-agent).  Results are stored in an
in-process registry and polled with ``sandbox_subagent_status``.

v1.1 (P0 semantics): the status machine now distinguishes real success from
budget-exhausted / empty / timed-out / cancelled outcomes:

- ``completed`` — a non-empty final answer was produced without exhausting the
  round budget (the only status that means "delivered").
- ``partial``   — the round or tool-call budget ran out but the sub-agent may
  have left usable files/text behind; the parent should inspect the workspace.
- ``failed``    — an unexpected exception, or an empty final answer.
- ``timed_out`` — the wall-clock deadline expired.
- ``cancelled`` — a cancellation request was honoured.

``write_set`` (optional) restricts file mutations to declared path prefixes;
``max_tool_calls`` (optional) caps tool execution; both keep a sub-agent from
silently writing outside its lane.  ``sandbox_session_id`` is now injected into
every tool call so the sub-agent is pinned to the parent's declared session
instead of letting ``_resolve_session`` pick another one.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Sub-agents get the offline-capable sandbox tool subset.  Host-side
# authorization-card tools (fetch/search/git clone) and nested sub-agents are
# excluded so a sub-agent can never raise an interactive approval.
DEFAULT_SUBAGENT_TOOLS = frozenset(
    {
        "sandbox_env_info",
        "sandbox_list_files",
        "sandbox_grep",
        "sandbox_read_file",
        "sandbox_write_file",
        "sandbox_append_file",
        "sandbox_edit_file",
        "sandbox_delete_file",
        "sandbox_exec",
        "sandbox_bash",
        "sandbox_todo",
        "sandbox_apply_patch",
        "sandbox_git",
        "sandbox_skill_list",
        "sandbox_skill_read",
    }
)

# File-mutating tools whose argument carries a workspace-relative path.  Only
# these are statically checked against ``write_set``; shell/exec/patch stay
# governed by the sandbox boundary itself.
_WRITE_TOOL_PATH_ARG = {
    "sandbox_write_file": "path",
    "sandbox_append_file": "path",
    "sandbox_edit_file": "path",
    "sandbox_delete_file": "path",
}

# Terminal statuses kept by the bounded registry.
_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "partial", "timed_out"}
)

_SUBAGENT_SYSTEM_PROMPT = (
    "你是 LearnGraph 沙箱内的子代理（sub-agent），在一个隔离的离线沙箱工作区中"
    "执行被委派的任务。规则：\n"
    "1. 只能使用下方提供的沙箱工具；不要请求用户授权，不要发起联网操作。\n"
    "2. 每一步尽量收敛：先 sandbox_list_files / sandbox_grep 定位，再 read/edit，"
    "重活交给 sandbox_exec 或 sandbox_bash。\n"
    "3. 完成后用纯文本输出最终结果（包含关键文件路径、结论、数据摘要）。\n"
    "4. 不要输出思考过程，只输出任务结果。\n"
    "5. 如果预算或轮数即将耗尽，仍然输出当前进度与已写入文件路径，不要留空。\n"
)


@dataclass(frozen=True, slots=True)
class SubagentSpec:
    subagent_id: str
    workspace_id: str
    actor_id: str
    chat_session_id: str
    prompt: str
    tools: list[str] | None
    max_rounds: int
    max_seconds: int
    sandbox_session_id: str | None = None
    write_set: tuple[str, ...] | None = None
    max_tool_calls: int | None = None


@dataclass(slots=True)
class SubagentJob:
    spec: SubagentSpec
    status: str = "queued"  # queued | running | completed | failed | cancelled | partial | timed_out
    error_class: str | None = None
    error_message: str | None = None
    rounds: int = 0
    tool_calls: int = 0
    duration_ms: int = 0
    result: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


def _extract_tool_call(call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Normalize a provider tool_call into (id, name, parsed arguments)."""
    call_id = str(call.get("id") or "")
    function = call.get("function")
    if isinstance(function, dict):
        name = str(function.get("name") or "unknown")
        raw_input = function.get("arguments") or ""
    else:
        name = str(call.get("name") or "unknown")
        raw_input = call.get("arguments") or ""
    if isinstance(raw_input, str):
        try:
            arguments = json.loads(raw_input) if raw_input.strip() else {}
        except json.JSONDecodeError:
            arguments = {"raw_arguments": raw_input}
    elif isinstance(raw_input, dict):
        arguments = raw_input
    else:
        arguments = {}
    return call_id, name, arguments


def _inject_session_id(arguments: dict[str, Any], session_id: str | None) -> dict[str, Any]:
    """Pin a sub-agent tool call to the declared sandbox session."""
    if not session_id:
        return arguments
    existing = arguments.get("sandbox_session_id")
    if isinstance(existing, str) and existing and existing not in {
        "", "new", "auto", "none", "null", "default", "create", "latest", "current",
    }:
        return arguments
    return {**arguments, "sandbox_session_id": session_id}


def _normalize_prefix(prefix: str) -> str:
    return prefix.replace("\\", "/").strip("/")


def _write_allowed(spec: SubagentSpec, name: str, arguments: dict[str, Any]) -> bool:
    """Static write-set check for path-carrying file tools.

    Returns True for non-file tools (shell/exec/patch remain governed by the
    sandbox boundary).  When ``write_set`` is unset nothing is restricted
    (backwards-compatible; the v2 executor defaults to the task directory).
    """
    if not spec.write_set:
        return True
    arg_name = _WRITE_TOOL_PATH_ARG.get(name)
    if arg_name is None:
        return True
    path = arguments.get(arg_name)
    if not isinstance(path, str) or not path.strip():
        return False
    cleaned = path.replace("\\", "/").lstrip("./")
    prefixes = tuple(_normalize_prefix(p) for p in spec.write_set)
    return any(
        cleaned == prefix or cleaned.startswith(prefix + "/")
        for prefix in prefixes
    )


def _finish_subagent(
    job: SubagentJob,
    status: str,
    final_text: str,
    *,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    job.status = status
    job.error_class = error_class
    job.error_message = error_message
    job.result = (final_text or "").strip()[:16_000]
    if job.finished_at is None:
        job.finished_at = time.time()
    if job.started_at is not None:
        job.duration_ms = int((job.finished_at - job.started_at) * 1000)


def _run_subagent(
    spec: SubagentSpec,
    job: SubagentJob,
    *,
    provider: Any = None,
    sandbox_service: Any = None,
) -> None:
    """Run the nested loop. ``provider``/``sandbox_service`` may be injected
    for tests; production constructs them from the workspace settings."""
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.providers.factory import model_provider_for_workspace
    from app.providers.ports.model import ProviderChatMessage
    from app.services.sandbox import SandboxAgentWorkspaceService

    settings = get_settings()
    job.status = "running"
    job.started_at = time.time()
    deadline = job.started_at + spec.max_seconds
    db: Any = None
    final_text = ""
    try:
        if sandbox_service is None or provider is None:
            db = SessionLocal()
        sandbox = sandbox_service or SandboxAgentWorkspaceService(
            db,
            spec.workspace_id,
            spec.actor_id,
            settings,
        )
        model_provider = provider or model_provider_for_workspace(db, spec.workspace_id, settings)
        allowed = (
            frozenset(spec.tools)
            if spec.tools
            else DEFAULT_SUBAGENT_TOOLS
        )
        definitions = [
            definition
            for definition in SandboxAgentWorkspaceService.agent_tool_definitions()
            if isinstance(definition, dict)
            and isinstance(definition.get("function"), dict)
            and definition["function"].get("name") in allowed
        ]
        tool_names = sorted(
            definition["function"]["name"]
            for definition in definitions
            if isinstance(definition.get("function"), dict)
        )
        system_content = _SUBAGENT_SYSTEM_PROMPT + "\n可用工具：" + "、".join(tool_names)
        messages: list[ProviderChatMessage] = [
            ProviderChatMessage(role="system", content=system_content),
            ProviderChatMessage(role="user", content=spec.prompt),
        ]
        tool_calls_total = 0
        for _round in range(1, spec.max_rounds + 1):
            if job.status == "cancelled":
                _finish_subagent(job, "cancelled", final_text)
                return
            if time.time() > deadline:
                _finish_subagent(
                    job,
                    "timed_out",
                    final_text or "（子代理超时，未能给出最终答案）",
                    error_class="TimeoutError",
                    error_message=f"sub-agent exceeded its {spec.max_seconds}s wall-time budget",
                )
                return
            job.rounds = _round
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for event in model_provider.stream_chat(messages, tools=definitions):
                if event.type == "text_delta" and event.content:
                    text_parts.append(event.content)
                elif event.type == "tool_calls":
                    tool_calls.extend(event.tool_calls or [])
                elif event.type == "completed":
                    break
            text = "".join(text_parts)
            if not tool_calls:
                final_text = text
                if not final_text.strip():
                    _finish_subagent(
                        job,
                        "failed",
                        "",
                        error_class="EmptyResult",
                        error_message="sub-agent returned an empty final answer",
                    )
                    return
                break
            if (
                spec.max_tool_calls is not None
                and tool_calls_total + len(tool_calls) > spec.max_tool_calls
            ):
                _finish_subagent(
                    job,
                    "partial",
                    final_text or "（子代理达到工具调用上限，未能给出最终答案）",
                    error_class="MaxToolCallsExhausted",
                    error_message=f"sub-agent exceeded its {spec.max_tool_calls}-call tool budget",
                )
                return
            messages.append(
                ProviderChatMessage(
                    role="assistant",
                    content=text,
                    tool_calls=tool_calls,
                )
            )
            for call in tool_calls:
                call_id, name, arguments = _extract_tool_call(call)
                if name not in allowed:
                    result_text = '{"error": "tool not allowed in sub-agent"}'
                else:
                    arguments = _inject_session_id(arguments, spec.sandbox_session_id)
                    if not _write_allowed(spec, name, arguments):
                        result_text = json.dumps(
                            {
                                "error": "write_not_allowed",
                                "message": (
                                    "write path is outside the declared write_set; "
                                    "only these prefixes are writable: "
                                    + (", ".join(spec.write_set) if spec.write_set else "")
                                ),
                            },
                            ensure_ascii=False,
                        )
                    else:
                        try:
                            outcome = sandbox.execute_agent_tool(
                                name,
                                arguments,
                                chat_session_id=spec.chat_session_id,
                                agent_authorized=True,
                            )
                            result_text = json.dumps(
                                outcome, ensure_ascii=False, default=str
                            )
                        except Exception as exc:  # noqa: BLE001 - surfaced to the model
                            result_text = json.dumps(
                                {"error": type(exc).__name__, "message": str(exc)[:500]},
                                ensure_ascii=False,
                            )
                if len(result_text) > 8_000:
                    result_text = result_text[:8_000] + "\n...[truncated]"
                messages.append(
                    ProviderChatMessage(
                        role="tool",
                        tool_call_id=call_id or None,
                        content=result_text,
                    )
                )
                tool_calls_total += 1
                job.tool_calls = tool_calls_total
        else:
            # Round budget exhausted: never report success.
            _finish_subagent(
                job,
                "partial",
                final_text or "（子代理达到最大轮数，未能给出最终答案）",
                error_class="MaxRoundsExhausted",
                error_message=f"sub-agent reached its {spec.max_rounds}-round cap",
            )
            return
        if job.status == "cancelled":
            _finish_subagent(job, "cancelled", final_text)
            return
        _finish_subagent(job, "completed", final_text)
    except Exception as exc:  # noqa: BLE001 - registry reports the failure
        logger.exception("sandbox sub-agent %s failed", spec.subagent_id)
        _finish_subagent(
            job,
            "failed",
            final_text,
            error_class=type(exc).__name__,
            error_message=str(exc)[:500],
        )
    finally:
        job.finished_at = time.time()
        if db is not None:
            db.close()


class SubagentRegistry:
    """Process-local registry of running/finished sub-agent jobs."""

    _instance: "SubagentRegistry | None" = None
    _lock = threading.Lock()
    _MAX_JOBS = 64

    @classmethod
    def instance(cls) -> "SubagentRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = SubagentRegistry()
        return cls._instance

    def __init__(self) -> None:
        self._jobs: dict[str, SubagentJob] = {}
        self._jobs_lock = threading.RLock()

    def _active_for_chat(self, chat_session_id: str) -> int:
        with self._jobs_lock:
            return sum(
                1
                for job in self._jobs.values()
                if job.spec.chat_session_id == chat_session_id
                and job.status in {"queued", "running"}
            )

    def start(
        self,
        spec: SubagentSpec,
        *,
        provider: Any = None,
        sandbox_service: Any = None,
    ) -> SubagentJob:
        from app.core.config import get_settings

        limit = get_settings().sandbox_subagent_max_concurrent_chat
        if limit and limit > 0:
            active = self._active_for_chat(spec.chat_session_id)
            if active >= limit:
                from app.core.errors import AppError

                raise AppError(
                    503,
                    "sandbox_subagent_capacity",
                    f"Chat already has {active} active sub-agents (limit {limit})",
                )
        with self._jobs_lock:
            if len(self._jobs) >= self._MAX_JOBS:
                # Evict the OLDEST finished jobs first to keep the registry
                # bounded (ascending order = oldest finished_at first).
                finished = sorted(
                    (
                        (job.finished_at or 0, job_id)
                        for job_id, job in self._jobs.items()
                        if job.status in _TERMINAL_STATUSES
                    ),
                    reverse=False,
                )
                excess = len(finished) - (self._MAX_JOBS - 1)
                for _finished_at, job_id in finished[: excess]:
                    self._jobs.pop(job_id, None)
            job = SubagentJob(spec=spec)
            self._jobs[spec.subagent_id] = job
        thread = threading.Thread(
            target=_run_subagent,
            args=(spec, job),
            kwargs={"provider": provider, "sandbox_service": sandbox_service},
            name=f"sandbox-subagent-{spec.subagent_id}",
            daemon=True,
        )
        thread.start()
        return job

    def get(self, subagent_id: str) -> SubagentJob | None:
        with self._jobs_lock:
            return self._jobs.get(subagent_id)

    def cancel(self, subagent_id: str) -> SubagentJob | None:
        with self._jobs_lock:
            job = self._jobs.get(subagent_id)
            if job is not None and job.status in {"queued", "running"}:
                job.status = "cancelled"
            return job
