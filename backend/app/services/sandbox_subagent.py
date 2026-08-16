"""Sandbox sub-agent runner: nested, isolated agent loops over a restricted
sandbox tool subset.

Each sub-agent runs in its own daemon thread with its own database session and
its own model provider; it only shares the workspace's durable sandbox files
(which is the point of a sandbox sub-agent).  Results are stored in an
in-process registry and polled with ``sandbox_subagent_status``.

v1 scope: tools are a statically filtered subset of the sandbox tool set
(no host-side authorization cards), rounds and wall time are capped, and the
final answer is returned as plain text.
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

_SUBAGENT_SYSTEM_PROMPT = (
    "你是 LearnGraph 沙箱内的子代理（sub-agent），在一个隔离的离线沙箱工作区中"
    "执行被委派的任务。规则：\n"
    "1. 只能使用下方提供的沙箱工具；不要请求用户授权，不要发起联网操作。\n"
    "2. 每一步尽量收敛：先 sandbox_list_files / sandbox_grep 定位，再 read/edit，"
    "重活交给 sandbox_exec 或 sandbox_bash。\n"
    "3. 完成后用纯文本输出最终结果（包含关键文件路径、结论、数据摘要）。\n"
    "4. 不要输出思考过程，只输出任务结果。\n"
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


@dataclass(slots=True)
class SubagentJob:
    spec: SubagentSpec
    status: str = "queued"  # queued | running | completed | failed | cancelled
    error_class: str | None = None
    error_message: str | None = None
    rounds: int = 0
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


def _run_subagent(spec: SubagentSpec, job: SubagentJob) -> None:
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.providers.factory import model_provider_for_workspace
    from app.providers.ports.model import ProviderChatMessage
    from app.services.sandbox import SandboxAgentWorkspaceService

    settings = get_settings()
    job.status = "running"
    job.started_at = time.time()
    deadline = job.started_at + spec.max_seconds
    db = SessionLocal()
    try:
        sandbox = SandboxAgentWorkspaceService(
            db,
            spec.workspace_id,
            spec.actor_id,
            settings,
        )
        provider = model_provider_for_workspace(db, spec.workspace_id, settings)
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
        final_text = ""
        for _round in range(1, spec.max_rounds + 1):
            if time.time() > deadline:
                raise TimeoutError(f"sub-agent exceeded its {spec.max_seconds}s wall-time budget")
            job.rounds = _round
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for event in provider.stream_chat(messages, tools=definitions):
                if event.type == "text_delta" and event.content:
                    text_parts.append(event.content)
                elif event.type == "tool_calls":
                    tool_calls.extend(event.tool_calls or [])
                elif event.type == "completed":
                    break
            text = "".join(text_parts)
            if not tool_calls:
                final_text = text
                break
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
        else:
            final_text = final_text or (
                "（子代理达到最大轮数，未能给出最终答案）"
            )
        job.result = (final_text or "").strip()[:16_000]
        job.status = "completed"
    except Exception as exc:  # noqa: BLE001 - registry reports the failure
        logger.exception("sandbox sub-agent %s failed", spec.subagent_id)
        job.status = "failed"
        job.error_class = type(exc).__name__
        job.error_message = str(exc)[:500]
    finally:
        job.finished_at = time.time()
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

    def start(self, spec: SubagentSpec) -> SubagentJob:
        with self._jobs_lock:
            if len(self._jobs) >= self._MAX_JOBS:
                # Evict the oldest finished job to keep the registry bounded.
                finished = sorted(
                    (
                        (job.finished_at or 0, job_id)
                        for job_id, job in self._jobs.items()
                        if job.status in {"completed", "failed", "cancelled"}
                    ),
                    reverse=True,
                )
                for _finished_at, job_id in finished[: len(finished) - (self._MAX_JOBS - 1)]:
                    self._jobs.pop(job_id, None)
            job = SubagentJob(spec=spec)
            self._jobs[spec.subagent_id] = job
        thread = threading.Thread(
            target=_run_subagent,
            args=(spec, job),
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
