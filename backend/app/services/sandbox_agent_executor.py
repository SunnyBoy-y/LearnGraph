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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

from app.domain.schemas.sandbox import AgentTaskResult, AgentTaskArtifact, AgentTaskSource
from app.services.agent_execution_profiles import (
    AgentExecutionProfile,
    AgentProfileError,
    ToolSelectionMode,
    clamp_network_capability,
    fallback_thinking_modes,
    network_capability_allows_tool,
    normalize_thinking_mode,
    resolve_agent_execution_profile,
)

EventEmitter = Callable[[str, dict[str, Any]], None]

_WRITE_TOOL_PATH_ARG = {
    "sandbox_write_file": "path",
    "sandbox_append_file": "path",
    "sandbox_edit_file": "path",
    "sandbox_delete_file": "path",
    "sandbox_download": "destination_path",
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
    require_artifacts: bool = True,
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
    complete = (
        bool(summary and summary.strip())
        and bool(acceptance_statuses)
        and all(s == "passed" for s in acceptance_statuses)
        and not missing
        and (bool(normalized) or not require_artifacts)
    )
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


def _strip_trailing_json_block(text: str) -> str:
    """Remove the machine handoff block from user-facing text when present."""
    if not text:
        return ""
    start = text.rfind("\n{")
    if start < 0 or _extract_json_block(text[start:]) is None:
        return text.strip()
    return text[:start].strip()


def _bounded_string_list(value: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text[:1_000])
        if len(result) >= limit:
            break
    return result


def _normalize_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            source = AgentTaskSource(
                title=str(item.get("title") or "")[:500],
                url=url[:2_000],
                snippet=str(item.get("snippet") or "")[:2_000],
                provider_id=(
                    str(item.get("provider_id"))
                    if item.get("provider_id")
                    else None
                ),
            )
        except ValueError:
            continue
        result.append(source.model_dump(mode="json"))
        if len(result) >= 50:
            break
    return result


def build_agent_task_result(
    *,
    task_id: str,
    profile: AgentExecutionProfile,
    status: str,
    result_text: str,
    deliverables: dict[str, Any] | None,
    sources: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    requirement_version: int,
    thinking_mode_requested: str | None,
    thinking_mode_effective: str | None,
    reasoning_effort_effective: Any,
    error_class: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    parsed = _extract_json_block(result_text) or {}
    handoff = deliverables or {}
    short_answer = str(
        handoff.get("summary")
        or _strip_trailing_json_block(result_text)
    ).strip()[:4_000]
    artifacts: list[dict[str, Any]] = []
    for item in handoff.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        try:
            artifacts.append(
                AgentTaskArtifact.model_validate(item).model_dump(mode="json")
            )
        except Exception:  # noqa: BLE001 - malformed handoff is omitted
            continue
    actual_sources = _normalize_sources(sources)
    parsed_evidence = [
        item for item in (parsed.get("evidence") or []) if isinstance(item, dict)
    ]
    limitations = _bounded_string_list(
        parsed.get("limitations") or parsed.get("risks")
    )
    if profile.requires_search and not actual_sources and status == "succeeded":
        limitations.append("未获得可验证的网页来源")
    error = None
    if error_class:
        error = {
            "class": str(error_class)[:200],
            "message": str(error_message or "")[:500],
        }
    result = AgentTaskResult(
        task_id=task_id,
        task_type=profile.role.value,
        status=status.casefold(),
        short_answer=short_answer,
        findings=_bounded_string_list(parsed.get("findings")),
        evidence=[*evidence[:50], *parsed_evidence[:50]],
        sources=actual_sources,
        artifacts=artifacts[:50],
        limitations=limitations,
        unresolved_questions=_bounded_string_list(
            parsed.get("unresolved") or parsed.get("unresolved_questions")
        ),
        completed_at=datetime.now(timezone.utc),
        requirement_version=max(1, int(requirement_version or 1)),
        thinking_mode_requested=thinking_mode_requested,
        thinking_mode_effective=thinking_mode_effective,
        reasoning_effort_effective=reasoning_effort_effective,
        error=error,
    )
    return result.model_dump(mode="json")


def _with_agent_result(
    deliverables: dict[str, Any] | None, agent_result: dict[str, Any]
) -> dict[str, Any]:
    payload = dict(deliverables or {})
    payload["agent_result"] = agent_result
    return payload




def execute_subagent_job(
    settings: Any,
    job: Any,
    task: Any,
    *,
    emit_event: EventEmitter | None = None,
    provider: Any = None,
    sandbox_service: Any = None,
    tool_runtime: Any = None,
    allowed_domains: list[str] | tuple[str, ...] | None = None,
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
    tools_allowed = spec.get("tools") if "tools" in spec else None
    budget = spec.get("budget") if isinstance(spec.get("budget"), dict) else {}
    max_rounds = int(budget.get("max_rounds") or settings.sandbox_subagent_max_rounds)
    max_seconds = int(budget.get("max_seconds") or settings.sandbox_subagent_max_seconds)
    max_tool_calls = budget.get("max_tool_calls")
    max_tokens = int(budget.get("max_tokens") or settings.sandbox_subagent_default_max_tokens)
    max_cost_usd = float(budget.get("max_cost_usd") or settings.sandbox_subagent_default_max_cost_usd)
    sandbox_session_id = spec.get("sandbox_session_id") or None
    requirement_version = max(
        1,
        int(spec.get("requirement_version") or 1),
    )
    raw_write_set = spec.get("write_set")
    write_set = (
        tuple(str(item) for item in raw_write_set if str(item).strip())
        if isinstance(raw_write_set, list) and raw_write_set
        else None
    )
    effective_write_set = write_set or (f"work/subagents/{task_id}",)
    profile_error: Exception | None = None
    try:
        profile = resolve_agent_execution_profile(
            spec.get("role_key"), tools=tools_allowed
        )
    except AgentProfileError as exc:
        profile = resolve_agent_execution_profile("generic", tools=[])
        profile_error = exc
    raw_network_policy = spec.get("network_policy")
    requested_network_mode = (
        raw_network_policy.get("mode")
        if isinstance(raw_network_policy, dict)
        else None
    )
    try:
        effective_network = clamp_network_capability(
            profile.network_capability,
            requested_network_mode,
        )
    except AgentProfileError as exc:
        profile_error = profile_error or exc
        effective_network = profile.network_capability
    network_policy = {
        **profile.network_policy_payload(),
        "mode": effective_network.value,
        "requested_mode": requested_network_mode,
    }
    try:
        thinking_requested = normalize_thinking_mode(
            spec.get("thinking_mode") or spec.get("thinking_mode_requested"),
            default=profile.default_thinking_mode,
        )
    except AgentProfileError as exc:
        profile_error = profile_error or exc
        thinking_requested = profile.default_thinking_mode
    # normalize_thinking_mode is intentionally strict at the API boundary; a
    # stored legacy job may carry an older value, so the executor degrades to
    # the role default instead of crashing outside the result contract.
    thinking_effective: str | None = None
    reasoning_effort_effective: Any = None
    tool_runtime_error: str | None = None
    actual_sources: list[dict[str, Any]] = []
    actual_evidence: list[dict[str, Any]] = []

    started = time.time()
    deadline = started + max_seconds
    db: Any = None
    emit = emit_event or (lambda _event_type, _payload: None)

    final_text = ""
    used_tokens = 0
    used_cost = 0.0
    tool_calls_total = 0
    rounds_used = 0
    control_check_at = 0.0

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

    def _result_deliverables(
        status: str,
        *,
        result_text: str,
        deliverables: dict[str, Any] | None,
        error_class: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        agent_result = build_agent_task_result(
            task_id=task_id,
            profile=profile,
            status=status,
            result_text=result_text,
            deliverables=deliverables,
            sources=actual_sources,
            evidence=actual_evidence,
            requirement_version=requirement_version,
            thinking_mode_requested=thinking_requested,
            thinking_mode_effective=thinking_effective,
            reasoning_effort_effective=reasoning_effort_effective,
            error_class=error_class,
            error_message=error_message,
        )
        return _with_agent_result(deliverables, agent_result)

    def _control_state() -> tuple[bool, bool]:
        nonlocal control_check_at
        now = time.monotonic()
        if now - control_check_at < 0.5:
            return False, False
        control_check_at = now
        if db is not None:
            try:
                db.expire_all()
                db.refresh(job)
                db.refresh(task)
            except Exception:  # noqa: BLE001 - cancellation polling is best-effort
                try:
                    db.rollback()
                except Exception:
                    pass
        task_spec = getattr(task, "spec_json", None)
        task_spec = task_spec if isinstance(task_spec, dict) else {}
        current_revision = int(
            task_spec.get("current_requirement_version")
            or task_spec.get("requirement_version")
            or requirement_version
        )
        stale = current_revision > requirement_version or bool(
            task_spec.get("superseded_by")
        )
        cancelled = (
            bool(getattr(job, "cancel_requested", False))
            or getattr(job, "status", None) == "CANCELLED"
            or getattr(task, "status", None) == "CANCELLED"
            or bool(task_spec.get("cancel_requested"))
        )
        return cancelled, stale

    def _control_outcome(result_text: str) -> SubagentRunOutcome | None:
        cancelled, stale = _control_state()
        if stale:
            _event("stale", {"reason": "requirement_revised"})
            return SubagentRunOutcome(
                status="STALE",
                event_type="stale",
                summary=result_text,
                deliverables=_result_deliverables(
                    "stale",
                    result_text=result_text,
                    deliverables=None,
                    error_class="RequirementSuperseded",
                    error_message="task requirement was superseded",
                ),
                attempt_record=_attempt_record(),
                error_class="RequirementSuperseded",
                error_message="task requirement was superseded",
            )
        if cancelled:
            _event("cancelled", {"reason": "parent_requested"})
            return SubagentRunOutcome(
                status="CANCELLED",
                event_type="cancelled",
                summary=result_text,
                deliverables=_result_deliverables(
                    "cancelled",
                    result_text=result_text,
                    deliverables=None,
                    error_class="Cancelled",
                    error_message="cancelled by parent",
                ),
                attempt_record=_attempt_record(),
                error_class="Cancelled",
                error_message="cancelled by parent",
            )
        return None

    final_text = ""
    used_tokens = 0
    used_cost = 0.0
    tool_calls_total = 0
    rounds_used = 0
    try:
        if profile_error is not None:
            return SubagentRunOutcome(
                status="FAILED",
                event_type="failed",
                summary="",
                deliverables=_result_deliverables(
                    "failed",
                    result_text="",
                    deliverables=None,
                    error_class="AgentProfileError",
                    error_message=str(profile_error),
                ),
                attempt_record=_attempt_record(),
                error_class="AgentProfileError",
                error_message=str(profile_error)[:500],
            )
        needs_db = provider is None or (
            profile.tool_mode is not ToolSelectionMode.NONE
            and tool_runtime is None
            and sandbox_service is None
        )
        if needs_db:
            db = SessionLocal()
        sandbox = sandbox_service

        if provider is None:
            model_provider = None
            candidates = list(
                fallback_thinking_modes(
                    thinking_requested, default=profile.default_thinking_mode
                )
            )
            for extra in ("low", "medium", "high", "xhigh", "off"):
                if extra not in candidates:
                    candidates.append(extra)
            for candidate in candidates:
                model_provider = model_provider_for_workspace(
                    db,
                    job.workspace_id,
                    settings,
                    thinking_mode=candidate,
                )
                if getattr(model_provider, "available", True):
                    thinking_effective = candidate
                    break
                reason = str(getattr(model_provider, "reason", "")).casefold()
                if not any(
                    token in reason for token in ("thinking", "reasoning")
                ):
                    break
        else:
            model_provider = provider
            thinking_effective = getattr(provider, "thinking_mode", thinking_requested)
        if thinking_effective is None:
            thinking_effective = thinking_requested
        reasoning_effort_effective = getattr(
            model_provider, "actual_reasoning_effort", None
        )

        task_spec = dict(getattr(task, "spec_json", None) or {})
        task_spec.update(
            {
                "thinking_mode_requested": thinking_requested,
                "thinking_mode_effective": thinking_effective,
                "reasoning_effort_effective": reasoning_effort_effective,
                "network_policy": network_policy,
            }
        )
        setattr(task, "spec_json", task_spec)
        if db is not None:
            db.flush()

        requested_tool_names = set(profile.requested_tools())
        if sandbox is None and db is not None and requested_tool_names:
            sandbox = SandboxAgentWorkspaceService(
                db,
                job.workspace_id,
                job.owner_user_id,
                settings,
            )
        available_tool_names: set[str] = set()
        definitions: list[dict[str, Any]] = []
        if tool_runtime is None and db is not None and requested_tool_names:
            try:
                from app.services.chat_service_factory import (
                    build_background_agent_tool_runtime,
                )

                tool_runtime = build_background_agent_tool_runtime(
                    db,
                    workspace_id=job.workspace_id,
                    actor_id=job.owner_user_id,
                    settings=settings,
                )
            except Exception as exc:  # noqa: BLE001 - safe runtime failure
                tool_runtime_error = type(exc).__name__
        if requested_tool_names and tool_runtime is not None:
            all_definitions = tool_runtime.definitions_for_tools(
                tool_names=None,
                web_search_enabled=True,
                memory_enabled=False,
            )
            available_tool_names = {
                str(definition.get("function", {}).get("name"))
                for definition in all_definitions
                if isinstance(definition, dict)
                and isinstance(definition.get("function"), dict)
            }
            definitions = [
                definition
                for definition in all_definitions
                if isinstance(definition, dict)
                and isinstance(definition.get("function"), dict)
                and definition["function"].get("name") in requested_tool_names
            ]
        elif requested_tool_names and sandbox is not None and sandbox_service is not None:
            all_definitions = SandboxAgentWorkspaceService.agent_tool_definitions()
            available_tool_names = {
                str(definition.get("function", {}).get("name"))
                for definition in all_definitions
                if isinstance(definition, dict)
                and isinstance(definition.get("function"), dict)
            }
            definitions = [
                definition
                for definition in all_definitions
                if isinstance(definition, dict)
                and isinstance(definition.get("function"), dict)
                and definition["function"].get("name") in requested_tool_names
            ]
        network_blocked_tools = sorted(
            name
            for name in requested_tool_names
            if not network_capability_allows_tool(effective_network, name)
        )
        if network_blocked_tools:
            blocked = set(network_blocked_tools)
            definitions = [
                definition
                for definition in definitions
                if str(definition.get("function", {}).get("name")) not in blocked
            ]
        missing_tools = sorted(profile.missing_tools(available_tool_names))
        if requested_tool_names and not definitions:
            message = (
                "No authorized runtime tool matched the requested allow-list: "
                + ", ".join(sorted(requested_tool_names))
            )
            if tool_runtime_error:
                message += f" (runtime error: {tool_runtime_error})"
            return SubagentRunOutcome(
                status="FAILED",
                event_type="failed",
                summary="",
                deliverables=_result_deliverables(
                    "failed",
                    result_text="",
                    deliverables=None,
                    error_class="ToolRuntimeUnavailable",
                    error_message=message,
                ),
                attempt_record=_attempt_record(),
                error_class="ToolRuntimeUnavailable",
                error_message=message[:500],
            )
        tool_names = sorted(
            definition["function"]["name"]
            for definition in definitions
            if isinstance(definition.get("function"), dict)
        )
        if profile.role.value == "research":
            system_content = (
                "你是 LearnGraph 的后台 ResearchAgent。只返回结构化研究结果，不直接面向用户说话。"
                "必须使用已提供的搜索/抓取工具获取真实来源；不得编造 URL、搜索结果或引用。"
                "尽可能交叉核对来源；明确区分已核实事实、推断、限制和未知项。"
                "不要输出隐藏思维链，只输出结论、简洁依据、来源和限制。\n"
                "可用工具：" + "、".join(tool_names)
            )
        elif profile.role.value == "reason":
            system_content = (
                "你是 LearnGraph 的后台 ReasonAgent。只进行深度分析并返回结论、简洁理由、"
                "假设、反例、验证步骤和未解决项；不要输出隐藏思维链或逐 token 推理过程。"
                "本任务明确没有工具，不得声称执行了搜索、文件操作或外部动作。\n"
                "可用工具：无"
            )
        elif profile.role.value == "tool":
            system_content = (
                "你是 LearnGraph 的后台 ToolAgent。只可使用下方服务端 allow-list 中的工具，"
                "不得请求或猜测未授权工具，不得绕过现有授权/确认流程。"
                "完成后只输出任务结果，不输出隐藏思维链。\n"
                "可用工具：" + "、".join(tool_names)
            )
        else:
            system_content = (
                "你是 LearnGraph 沙箱内的通用子代理，在隔离的离线沙箱工作区中执行被委派的任务。"
                "只可使用下方工具，不得联网或请求额外权限。\n"
                "可用工具：" + "、".join(tool_names)
            )
        if profile.role.value != "tool":
            system_content += (
                "\n预算或轮数耗尽时仍返回当前可交付内容与明确限制。"
                "最终回答末尾附一个且仅一个 JSON 交付块："
                '{"summary":"...","findings":["..."],"artifacts":[],"evidence":[],'
                '"acceptance":[{"criterion":"...","status":"passed|failed"}],'
                '"risks":[],"limitations":[],"unresolved":[],"confidence":0.0}'
            )
        else:
            system_content += (
                "\n最终回答末尾附一个且仅一个 JSON 交付块："
                '{"summary":"...","artifacts":[{"path":"...","change":"..."}],'
                '"evidence":[],"acceptance":[{"criterion":"...","status":"passed|failed"}],'
                '"risks":[],"limitations":[],"unresolved":[],"confidence":0.0}'
            )
        system_content += (
            "\n网络能力：" + effective_network.value +
            "（仅主机侧 Broker；代码沙箱没有直接网络路由，不支持任意 socket/代理/隧道）。"
        )
        if network_blocked_tools:
            system_content += (
                "\n被网络策略阻止的工具：" + "、".join(network_blocked_tools)
            )
        if missing_tools:
            system_content += "; 未授权/不可用工具：" + "、".join(missing_tools)
        messages: list[ProviderChatMessage] = [
            ProviderChatMessage(role="system", content=system_content),
            ProviderChatMessage(role="user", content=prompt),
        ]
        _event("started", {"attempt": job.attempt, "started_at": started})
        _event(
            "network_policy",
            {"network_policy": network_policy, "blocked_tools": network_blocked_tools},
        )
        for _round in range(1, max_rounds + 1):
            cancelled, stale = _control_state()
            if stale:
                _event("stale", {"reason": "requirement_revised"})
                return SubagentRunOutcome(
                    status="STALE",
                    event_type="stale",
                    summary=final_text,
                    deliverables=_result_deliverables(
                        "stale",
                        result_text=final_text,
                        deliverables=None,
                        error_class="RequirementSuperseded",
                        error_message="task requirement was superseded",
                    ),
                    attempt_record=_attempt_record(),
                    error_class="RequirementSuperseded",
                    error_message="task requirement was superseded",
                )
            if cancelled:
                _event("cancelled", {"reason": "parent_requested"})
                return SubagentRunOutcome(
                    status="CANCELLED",
                    event_type="cancelled",
                    summary=final_text,
                    deliverables=_result_deliverables(
                        "cancelled",
                        result_text=final_text,
                        deliverables=None,
                        error_class="Cancelled",
                        error_message="cancelled by parent",
                    ),
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
                    deliverables=_result_deliverables(
                        "timed_out",
                        result_text=final_text,
                        deliverables=None,
                        error_class="TimeoutError",
                        error_message=f"sub-agent exceeded its {max_seconds}s wall-time budget",
                    ),
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
                control_outcome = _control_outcome("".join(text_parts))
                if control_outcome is not None:
                    return control_outcome
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
                        deliverables=_result_deliverables(
                            "failed",
                            result_text="",
                            deliverables=None,
                            error_class="EmptyResult",
                            error_message="sub-agent returned an empty final answer",
                        ),
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
                    deliverables=_result_deliverables(
                        "partial",
                        result_text=final_text,
                        deliverables=None,
                        error_class="MaxToolCallsExhausted",
                        error_message=f"sub-agent exceeded its {max_tool_calls}-call tool budget",
                    ),
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
                if name not in tool_names:
                    result_text = '{"error": "tool not allowed in sub-agent"}'
                    call_sources: list[dict[str, Any]] = []
                    tool_status = "denied"
                else:
                    arguments = _inject_session_id(arguments, sandbox_session_id)
                    if not _write_allowed(effective_write_set, name, arguments):
                        lane = effective_write_set
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
                        call_sources = []
                        tool_status = "denied"
                    else:
                        call_sources = []
                        tool_status = "completed"
                        try:
                            if tool_runtime is not None and not name.startswith("sandbox_"):
                                runtime_call = {
                                    "id": call_id or f"delegate_{tool_calls_total + 1}",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments, ensure_ascii=False),
                                    },
                                }
                                result_text, result_meta, result_sources = tool_runtime.execute(
                                    runtime_call,
                                    allowed_domains=list(allowed_domains or ()),
                                    chat_session_id=job.chat_session_id,
                                    disclosed_tool_names=set(tool_names),
                                )
                                call_sources = [
                                    dict(item)
                                    for item in (result_sources or [])
                                    if isinstance(item, dict)
                                ]
                                tool_status = str(result_meta.get("status") or "completed")
                            else:
                                if sandbox is None:
                                    raise RuntimeError("sandbox tool runtime is unavailable")
                                outcome = sandbox.execute_agent_tool(
                                    name,
                                    arguments,
                                    chat_session_id=job.chat_session_id,
                                    agent_authorized=True,
                                )
                                result_text = json.dumps(
                                    outcome, ensure_ascii=False, default=str
                                )
                                tool_status = str(outcome.get("status") or "completed")
                            try:
                                parsed_result = json.loads(result_text)
                            except (TypeError, json.JSONDecodeError):
                                parsed_result = {}
                            if isinstance(parsed_result, dict):
                                for item in parsed_result.get("results") or []:
                                    if isinstance(item, dict) and item.get("url"):
                                        call_sources.append(
                                            {
                                                "title": str(item.get("title") or ""),
                                                "url": str(item.get("url") or ""),
                                                "snippet": str(item.get("snippet") or ""),
                                                "provider_id": parsed_result.get("provider_id"),
                                            }
                                        )
                                if name in {"fetch_web_page", "sandbox_fetch"} and parsed_result.get("url"):
                                    call_sources.append(
                                        {
                                            "title": str(parsed_result.get("title") or ""),
                                            "url": str(parsed_result.get("url") or ""),
                                            "snippet": "",
                                            "provider_id": parsed_result.get("provider_id"),
                                        }
                                    )
                            actual_sources.extend(call_sources)
                            actual_evidence.append(
                                {
                                    "tool": name,
                                    "status": tool_status,
                                    "source_count": len(call_sources),
                                }
                            )
                        except Exception as exc:  # noqa: BLE001 - surfaced to the model
                            result_text = json.dumps(
                                {"error": type(exc).__name__, "message": str(exc)[:500]},
                                ensure_ascii=False,
                            )
                            tool_status = "failed"
                            actual_evidence.append(
                                {"tool": name, "status": "failed", "source_count": 0}
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
                control_outcome = _control_outcome(final_text)
                if control_outcome is not None:
                    return control_outcome
            if used_tokens >= max_tokens or (max_cost_usd > 0 and used_cost >= max_cost_usd):
                _event("partial", {"reason": "budget_exhausted"})
                return SubagentRunOutcome(
                    status="PARTIAL",
                    event_type="partial",
                    summary=final_text or "（子代理达到预算上限）",
                    deliverables=_result_deliverables(
                        "partial",
                        result_text=final_text,
                        deliverables=None,
                        error_class="BudgetExhausted",
                        error_message=(
                            f"sub-agent exceeded its budget (tokens {used_tokens}/{max_tokens}, "
                            f"cost ${used_cost:.4f}/{max_cost_usd})"
                        ),
                    ),
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
                deliverables=_result_deliverables(
                    "partial",
                    result_text=final_text,
                    deliverables=None,
                    error_class="MaxRoundsExhausted",
                    error_message=f"sub-agent reached its {max_rounds}-round cap",
                ),
                attempt_record=_attempt_record(),
                error_class="MaxRoundsExhausted",
                error_message=f"sub-agent reached its {max_rounds}-round cap",
            )
        control_outcome = _control_outcome(final_text)
        if control_outcome is not None:
            return control_outcome
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
            file_exists=_file_exists if sandbox is not None else None,
            require_artifacts=(
                bool(requested_tool_names)
                and profile.role.value not in {"research", "reason"}
            ),
        )
        if complete:
            _event(
                "succeeded",
                {
                    "confidence": deliverables.get("confidence", 0.0),
                    "source_count": len(actual_sources),
                    "requirement_version": requirement_version,
                    "thinking_mode_effective": thinking_effective,
                },
            )
            return SubagentRunOutcome(
                status="SUCCEEDED",
                event_type="succeeded",
                summary=final_text,
                deliverables=_result_deliverables(
                    "succeeded",
                    result_text=final_text,
                    deliverables=deliverables,
                ),
                attempt_record=_attempt_record(),
            )
        _event("partial", {"reason": "handoff_incomplete"})
        return SubagentRunOutcome(
            status="PARTIAL",
            event_type="partial",
            summary=final_text,
            deliverables=_result_deliverables(
                "partial",
                result_text=final_text,
                deliverables=deliverables,
                error_class="HandoffIncomplete",
                error_message="deliverables contract is incomplete or artifacts missing",
            ),
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
            deliverables=_result_deliverables(
                "failed",
                result_text=final_text,
                deliverables=None,
                error_class=type(exc).__name__,
                error_message=str(exc)[:500],
            ),
            attempt_record=_attempt_record(),
            error_class=type(exc).__name__,
            error_message=str(exc)[:500],
        )
    finally:
        if db is not None:
            db.close()
