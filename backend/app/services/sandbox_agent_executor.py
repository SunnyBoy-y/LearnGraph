"""Durable sub-agent executor for the unified sandbox scheduler.

Runs one ``SandboxJob(kind="subagent")`` as a nested, isolated agent loop:
independent DB session + independent model provider, budgeted rounds / wall
time / tool calls / tokens / cost, cooperative cancellation, and a structured
deliverable handoff produced by machine validation instead of model claims.

Lifecycle events are emitted through an injected ``emit_event`` callback so the
scheduler can persist them to ``sandbox_agent_events`` and drive the chat SSE
stream; the executor itself never touches chat state.

Status mapping (job/task):
    SUCCEEDED  — non-empty final answer, deliverables contract satisfied
    PARTIAL    — round/tool/token/cost budget exhausted, or contract missing
    FAILED     — exception / empty result
    TIMED_OUT  — wall-clock deadline expired
    CANCELLED  — cooperative cancellation observed before finishing
    INTERRUPTED— reserved for process/lease loss recovery (not produced here)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], None]

_WRITE_TOOL_PATH_ARG = {
    "sandbox_write_file": "path",
    "sandbox_append_file": "path",
    "sandbox_edit_file": "path",
    "sandbox_delete_file": "path",
}

_AGENT_SESSION_PLACEHOLDERS = frozenset(
    {"", "new", "auto", "none", "null", "default", "create", "latest", "current"}
)


@dataclass(frozen=True, slots=True)
class SubagentRunOutcome:
    status: str  # SUCCEEDED | PARTIAL | FAILED | TIMED_OUT | CANCELLED | INTERRUPTED
    event_type: str
    summary: str
    deliverables: dict[str, Any] | None
    attempt_record: dict[str, Any]
    error_class: str | None = None
    error_message: str | None = None


def _extract_tool_call(call: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
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
    if not session_id:
        return arguments
    existing = arguments.get("sandbox_session_id")
    if isinstance(existing, str) and existing and existing not in _AGENT_SESSION_PLACEHOLDERS:
        return arguments
    return {**arguments, "sandbox_session_id": session_id}


def _write_allowed(write_set: tuple[str, ...] | None, name: str, arguments: dict[str, Any]) -> bool:
    """Static write-set check for path-carrying file tools.

    When ``write_set`` is None the executor falls back to the task lane
    ``work/subagents/<task_id>/``; otherwise only declared prefixes are writable.
    Shell/exec/patch tools are not statically checked (sandbox boundary governs).
    """
    arg_name = _WRITE_TOOL_PATH_ARG.get(name)
    if arg_name is None:
        return True
    path = arguments.get(arg_name)
    if not isinstance(path, str) or not path.strip():
        return False
    cleaned = path.replace("\\", "/").lstrip("./")
    prefixes = tuple(p.replace("\\", "/").strip("/") for p in (write_set or ()))
    return any(cleaned == prefix or cleaned.startswith(prefix + "/") for prefix in prefixes)


def _price_for(provider_key: str, model_id: str) -> dict[str, Any] | None:
    """Best-effort per-million-token USD price lookup from the pricing catalog."""
    try:
        from app.services.pricing_catalog import PRICING_CATALOG
    except Exception:  # noqa: BLE001 - pricing is best-effort
        return None
    for item in PRICING_CATALOG:
        if item.get("provider_key") == provider_key and item.get("model_id") == model_id:
            return item
    return None


def _estimate_tokens(text: str) -> int:
    # Coarse fallback (≈4 chars/token for mixed CJK/ASCII); the executor prefers
    # provider.reported usage when available.
    return max(1, len(text or "") // 4)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Pull the trailing JSON object out of a model's final answer."""
    if not text:
        return None
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def finalize_deliverables(
    result_text: str,
    *,
    default_output_root: str,
    file_exists: Callable[[str], bool] | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Machine-validate the handoff contract.

    Returns ``(deliverables, complete)`` where ``complete`` means the contract
    fields are present and, when a file checker is provided, declared artifacts
    exist. Never trusts "I'm done" — the summary/artifacts/acceptance structure
    must actually be there.
    """
    parsed = _extract_json_block(result_text)
    if parsed is None:
        return (
            {
                "handoff_parse": False,
                "summary": (result_text or "")[:400],
                "artifacts": [],
                "evidence": [],
                "acceptance": [],
                "risks": [],
                "unresolved": ["模型未输出结构化交付说明"],
                "recommended_next_action": "parent_takeover",
                "confidence": 0.0,
            },
            False,
        )
    summary = parsed.get("summary")
    artifacts = parsed.get("artifacts") if isinstance(parsed.get("artifacts"), list) else []
    acceptance = parsed.get("acceptance") if isinstance(parsed.get("acceptance"), list) else []
    evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), list) else []
    normalized: list[dict[str, Any]] = []
    missing: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        path = str(artifact.get("path") or "")
        if not path:
            continue
        entry: dict[str, Any] = {
            "path": path,
            "change": str(artifact.get("change") or ""),
            "sha256": artifact.get("sha256") if isinstance(artifact.get("sha256"), str) else None,
            "file_id": artifact.get("file_id") if isinstance(artifact.get("file_id"), str) else None,
        }
        if file_exists is not None:
            exists = False
            try:
                exists = bool(file_exists(path))
            except Exception:  # noqa: BLE001 - file check is best-effort
                exists = False
            entry["exists"] = exists
            if not exists:
                missing.append(path)
        normalized.append(entry)
    acceptance_statuses = [str(a.get("status")) for a in acceptance if isinstance(a, dict)]
    complete = bool(summary and summary.strip()) and bool(normalized) and bool(
        acceptance_statuses
    ) and all(s == "passed" for s in acceptance_statuses) and not missing
    return (
        {
            "handoff_parse": True,
            "summary": str(summary or "")[:1000],
            "artifacts": normalized,
            "evidence": evidence[:50],
            "acceptance": acceptance[:50],
            "risks": parsed.get("risks") if isinstance(parsed.get("risks"), list) else [],
            "unresolved": parsed.get("unresolved") if isinstance(parsed.get("unresolved"), list) else [],
            "recommended_next_action": str(
                parsed.get("recommended_next_action") or "merge"
            ),
            "confidence": float(parsed.get("confidence") or 0.0),
            "default_output_root": default_output_root,
        },
        complete,
    )


def execute_subagent_job(
    settings: Any,
    job: Any,
    task: Any,
    *,
    emit_event: EventEmitter | None = None,
    provider: Any = None,
    sandbox_service: Any = None,
) -> SubagentRunOutcome:
    """Run one durable sub-agent job to a terminal outcome."""
    from app.core.database import SessionLocal
    from app.providers.factory import model_provider_for_workspace
    from app.providers.ports.model import ProviderChatMessage
    from app.domain.schemas.sandbox import SandboxAgentFileListRequest
    from app.services.sandbox import SandboxAgentWorkspaceService

    spec = job.payload_json or {}
    prompt = str(spec.get("prompt") or "")
    task_id = str(spec.get("task_id") or task.id)
    tools_allowed = spec.get("tools")
    write_set = tuple(spec.get("write_set") or ())
    budget = spec.get("budget") if isinstance(spec.get("budget"), dict) else {}
    max_rounds = int(budget.get("max_rounds") or settings.sandbox_subagent_max_rounds)
    max_seconds = int(budget.get("max_seconds") or settings.sandbox_subagent_max_seconds)
    max_tool_calls = budget.get("max_tool_calls")
    max_tokens = int(budget.get("max_tokens") or settings.sandbox_subagent_default_max_tokens)
    max_cost_usd = float(budget.get("max_cost_usd") or settings.sandbox_subagent_default_max_cost_usd)
    sandbox_session_id = spec.get("sandbox_session_id") or None

    started = time.time()
    deadline = started + max_seconds
    db: Any = None
    emit = emit_event or (lambda _event_type, _payload: None)

    final_text = ""
    used_tokens = 0
    used_cost = 0.0
    tool_calls_total = 0
    rounds_used = 0

    def _attempt_record() -> dict[str, Any]:
        return {
            "attempt": job.attempt,
            "status": "",
            "rounds": rounds_used,
            "tool_calls": tool_calls_total,
            "token_usage": used_tokens,
            "cost_usd": round(used_cost, 6),
            "started_at": started,
            "finished_at": time.time(),
            "error_class": None,
            "error_message": None,
        }

    def _event(event_type: str, payload: dict[str, Any]) -> None:
        payload.setdefault("task_id", task_id)
        emit(event_type, payload)

    final_text = ""
    used_tokens = 0
    used_cost = 0.0
    tool_calls_total = 0
    rounds_used = 0
    try:
        if sandbox_service is None or provider is None:
            db = SessionLocal()
        sandbox = sandbox_service or SandboxAgentWorkspaceService(
            db,
            job.workspace_id,
            job.owner_user_id,
            settings,
        )
        model_provider = provider or model_provider_for_workspace(
            db, job.workspace_id, settings
        )
        allowed = (
            frozenset(tools_allowed)
            if isinstance(tools_allowed, list) and tools_allowed
            else frozenset(
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
        system_content = (
            "你是 LearnGraph 沙箱内的子代理（sub-agent），在一个隔离的离线沙箱工作区中"
            "执行被委派的任务。规则：\n"
            "1. 只能使用下方提供的沙箱工具；不要请求用户授权，不要发起联网操作。\n"
            "2. 每一步尽量收敛：先 sandbox_list_files / sandbox_grep 定位，再 read/edit，"
            "重活交给 sandbox_exec 或 sandbox_bash。\n"
            "3. 完成后输出纯文本最终结果（包含关键文件路径、结论、数据摘要）。\n"
            "4. 不要输出思考过程，只输出任务结果。\n"
            "5. 预算或轮数即将耗尽时，仍输出当前进度与已写入文件路径，不要留空。\n"
            "6. 最终回答末尾附一个 JSON 交付说明块（仅此一个 JSON）："
            '{"summary": "完成了什么", "artifacts": [{"path": "...", "change": "..."}], '
            '"evidence": [{"check": "...", "result": "passed|failed"}], '
            '"acceptance": [{"criterion": "...", "status": "passed|failed"}], '
            '"risks": [], "unresolved": [], '
            '"recommended_next_action": "merge|retry_scoped|parent_takeover", "confidence": 0.9}\n'
            "可用工具：" + "、".join(tool_names)
        )
        messages: list[ProviderChatMessage] = [
            ProviderChatMessage(role="system", content=system_content),
            ProviderChatMessage(role="user", content=prompt),
        ]
        _event("started", {"attempt": job.attempt, "started_at": started})
        for _round in range(1, max_rounds + 1):
            if job.status == "CANCELLED" or getattr(job, "cancel_requested", False):
                _event("cancelled", {"reason": "parent_requested"})
                return SubagentRunOutcome(
                    status="CANCELLED",
                    event_type="cancelled",
                    summary=final_text,
                    deliverables=None,
                    attempt_record=_attempt_record(),
                    error_class="Cancelled",
                    error_message="cancelled by parent",
                )
            if time.time() >= deadline:
                _event("timed_out", {"reason": "wall_clock"})
                return SubagentRunOutcome(
                    status="TIMED_OUT",
                    event_type="timed_out",
                    summary=final_text or "（子代理超时，未能给出最终答案）",
                    deliverables=None,
                    attempt_record=_attempt_record(),
                    error_class="TimeoutError",
                    error_message=f"sub-agent exceeded its {max_seconds}s wall-time budget",
                )
            rounds_used = _round
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for event in model_provider.stream_chat(messages, tools=definitions):
                if event.type == "text_delta" and event.content:
                    text_parts.append(event.content)
                elif event.type == "tool_calls":
                    tool_calls.extend(event.tool_calls or [])
                elif event.type == "completed":
                    break
            # Token accounting: prefer provider-reported usage, fall back to chars.
            usage = getattr(model_provider, "last_usage", None) or {}
            if isinstance(usage, dict):
                used_tokens += int(usage.get("input_tokens") or 0) + int(
                    usage.get("output_tokens") or 0
                )
            else:
                used_tokens += _estimate_tokens("".join(text_parts)) + _estimate_tokens(
                    "".join(str(c) for c in tool_calls)
                )
            price = None
            if not used_cost:
                price = _price_for(
                    getattr(model_provider, "provider_id", ""),
                    getattr(model_provider, "model_id", ""),
                )
            if price:
                in_usd = float(price.get("input_usd_per_million") or 0.0)
                out_usd = float(price.get("output_usd_per_million") or 0.0)
                used_cost += (
                    int(usage.get("input_tokens") or 0) / 1_000_000 * in_usd
                    + int(usage.get("output_tokens") or 0) / 1_000_000 * out_usd
                )
            text = "".join(text_parts)
            if text.strip():
                _event("progress", {"round": _round, "progress_summary": text[:500]})
            if not tool_calls:
                final_text = text
                if not final_text.strip():
                    _event("failed", {"error_class": "EmptyResult"})
                    return SubagentRunOutcome(
                        status="FAILED",
                        event_type="failed",
                        summary="",
                        deliverables=None,
                        attempt_record=_attempt_record(),
                        error_class="EmptyResult",
                        error_message="sub-agent returned an empty final answer",
                    )
                break
            if (
                isinstance(max_tool_calls, int)
                and tool_calls_total + len(tool_calls) > max_tool_calls
            ):
                _event("partial", {"reason": "max_tool_calls"})
                return SubagentRunOutcome(
                    status="PARTIAL",
                    event_type="partial",
                    summary=final_text or "（子代理达到工具调用上限）",
                    deliverables=None,
                    attempt_record=_attempt_record(),
                    error_class="MaxToolCallsExhausted",
                    error_message=f"sub-agent exceeded its {max_tool_calls}-call tool budget",
                )
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
                    arguments = _inject_session_id(arguments, sandbox_session_id)
                    if not _write_allowed(write_set, name, arguments):
                        lane = write_set or (f"work/subagents/{task_id}",)
                        result_text = json.dumps(
                            {
                                "error": "write_not_allowed",
                                "message": (
                                    "write path is outside the declared write_set; "
                                    "only these prefixes are writable: " + ", ".join(lane)
                                ),
                            },
                            ensure_ascii=False,
                        )
                    else:
                        try:
                            outcome = sandbox.execute_agent_tool(
                                name,
                                arguments,
                                chat_session_id=job.chat_session_id,
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
                _event(
                    "tool_call",
                    {"tool_name": name, "ordinal": tool_calls_total, "attempt": job.attempt},
                )
            if used_tokens >= max_tokens or (max_cost_usd > 0 and used_cost >= max_cost_usd):
                _event("partial", {"reason": "budget_exhausted"})
                return SubagentRunOutcome(
                    status="PARTIAL",
                    event_type="partial",
                    summary=final_text or "（子代理达到预算上限）",
                    deliverables=None,
                    attempt_record=_attempt_record(),
                    error_class="BudgetExhausted",
                    error_message=(
                        f"sub-agent exceeded its budget (tokens {used_tokens}/{max_tokens}, "
                        f"cost ${used_cost:.4f}/{max_cost_usd})"
                    ),
                )
        else:
            final_text = final_text or "（子代理达到最大轮数，未能给出最终答案）"
            _event("partial", {"reason": "max_rounds"})
            return SubagentRunOutcome(
                status="PARTIAL",
                event_type="partial",
                summary=final_text,
                deliverables=None,
                attempt_record=_attempt_record(),
                error_class="MaxRoundsExhausted",
                error_message=f"sub-agent reached its {max_rounds}-round cap",
            )
        if job.status == "CANCELLED" or getattr(job, "cancel_requested", False):
            _event("cancelled", {"reason": "parent_requested_after_loop"})
            return SubagentRunOutcome(
                status="CANCELLED",
                event_type="cancelled",
                summary=final_text,
                deliverables=None,
                attempt_record=_attempt_record(),
                error_class="Cancelled",
                error_message="cancelled by parent",
            )
        # ── FINALIZING: machine-validate the handoff ──
        _event("finalizing", {})
        default_output_root = f"work/subagents/{task_id}/outputs"

        def _file_exists(path: str) -> bool:
            try:
                entries = sandbox.list_files(
                    SandboxAgentFileListRequest(
                        chat_session_id=job.chat_session_id,
                        pattern=path,
                        sandbox_session_id=sandbox_session_id,
                    )
                )
                files = entries.get("files") if isinstance(entries, dict) else entries
                return bool(files)
            except Exception:  # noqa: BLE001 - file verification is best-effort
                return False

        deliverables, complete = finalize_deliverables(
            final_text,
            default_output_root=default_output_root,
            file_exists=_file_exists if sandbox_service is not None else None,
        )
        if complete:
            _event("succeeded", {"confidence": deliverables.get("confidence", 0.0)})
            return SubagentRunOutcome(
                status="SUCCEEDED",
                event_type="succeeded",
                summary=final_text,
                deliverables=deliverables,
                attempt_record=_attempt_record(),
            )
        _event("partial", {"reason": "handoff_incomplete"})
        return SubagentRunOutcome(
            status="PARTIAL",
            event_type="partial",
            summary=final_text,
            deliverables=deliverables,
            attempt_record=_attempt_record(),
            error_class="HandoffIncomplete",
            error_message="deliverables contract is incomplete or artifacts missing",
        )
    except Exception as exc:  # noqa: BLE001 - registry reports the failure
        logger.exception("sandbox sub-agent %s failed", task_id)
        _event("failed", {"error_class": type(exc).__name__})
        return SubagentRunOutcome(
            status="FAILED",
            event_type="failed",
            summary=final_text,
            deliverables=None,
            attempt_record=_attempt_record(),
            error_class=type(exc).__name__,
            error_message=str(exc)[:500],
        )
    finally:
        if db is not None:
            db.close()
