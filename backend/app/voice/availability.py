"""Cheap, server-side availability checks for voice Agent capabilities.

The capability endpoint is intentionally a discovery endpoint.  It builds the
same tool graph used by the background Agent and inspects definitions and
provider metadata; it never runs a model, invokes a tool, or performs a
network request.  This keeps reconnect/UI refreshes cheap while preventing the
frontend from advertising capabilities that cannot be admitted.
"""

from __future__ import annotations

from typing import Any

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.providers.factory import model_provider_for_workspace
from app.providers.factory import (
    realtime_asr_provider_for_workspace,
    tts_provider_for_workspace,
)
from app.services.agent_execution_profiles import resolve_agent_execution_profile
from app.services.chat_service_factory import build_agent_tool_runtime
from app.voice.capabilities import VOICE_CAPABILITIES, VoiceCapabilitySpec


def inspect_voice_provider_status(
    db: Any,
    context: WorkspaceContext,
    settings: Settings,
) -> dict[str, object]:
    """Resolve the three providers required before a voice call can speak."""
    checks: list[tuple[str, Any]] = []
    for label, resolver in (
        ("asr", realtime_asr_provider_for_workspace),
        ("tts", tts_provider_for_workspace),
        ("model", model_provider_for_workspace),
    ):
        try:
            provider = resolver(db, context.workspace_id, settings)
            if provider is None or not bool(getattr(provider, "available", False)):
                reason = str(getattr(provider, "reason", "") or "未解析到可用 Provider")
                checks.append((label, reason))
            else:
                checks.append((label, ""))
        except Exception as exc:  # noqa: BLE001 - discovery must stay read-only
            checks.append((label, f"{type(exc).__name__}: {exc}"[:300]))
    blocked = [label for label, reason in checks if reason]
    reasons = [f"{label.upper()}：{reason}" for label, reason in checks if reason]
    return {
        "voice_provider_ready": not blocked,
        "voice_provider_blocked_by": blocked,
        "voice_provider_reason": "；".join(reasons) or None,
        "asr_available": "asr" not in blocked,
        "tts_available": "tts" not in blocked,
        "model_available": "model" not in blocked,
    }


def _permission_allowed(context: WorkspaceContext, required: str) -> bool:
    return required in context.permissions or "workspace.manage" in context.permissions


def inspect_voice_capabilities(
    db: Any,
    context: WorkspaceContext,
    settings: Settings,
) -> list[dict[str, object]]:
    """Return capability availability without model/tool execution."""

    runtime: Any | None = None
    runtime_error: str | None = None
    try:
        # Factory construction only wires providers and tool adapters.  The
        # definitions call below is metadata-only and does not call a model.
        runtime = build_agent_tool_runtime(db, context, settings)
    except Exception as exc:  # an unavailable extension must not break GET
        runtime_error = str(exc) or "Agent runtime could not be assembled"

    model: Any | None = None
    model_error: str | None = None
    try:
        # This resolves the enabled/default model row and decrypts its secret;
        # it does not send a request to the provider.
        model = model_provider_for_workspace(db, context.workspace_id, settings)
    except Exception as exc:
        model_error = str(exc) or "No enabled model provider is available"

    rows: list[dict[str, object]] = []
    for spec in VOICE_CAPABILITIES.values():
        blocked: list[str] = []
        reasons: list[str] = []
        required = spec.required_permission
        if not _permission_allowed(context, required):
            blocked.append("permission")
            reasons.append(f"需要 {required} 权限")

        # A task always needs an enabled model, including no-tool reasoning
        # tasks.  Treat an explicit unavailable provider as unavailable while
        # preserving a graceful response when the deployment has no provider
        # row yet.
        if model_error or model is None or not bool(getattr(model, "available", True)):
            blocked.append("model")
            reasons.append(
                str(getattr(model, "reason", "") or model_error or "没有可用的 Agent 模型")
            )

        # Every delegated voice capability is admitted through the durable
        # SubAgent bridge, including the model-only research/reason lanes.
        if not bool(getattr(settings, "sandbox_subagent_enabled", False)):
            blocked.append("sandbox_subagent")
            reasons.append("后台 Agent 子任务未启用")

        missing_tools: list[str] = []
        available_tools: set[str] = set()
        if runtime is not None:
            try:
                definitions = runtime.definitions_for_tools(
                    tool_names=set(spec.tools),
                    web_search_enabled=True,
                    memory_enabled=True,
                )
                for definition in definitions:
                    function = definition.get("function") if isinstance(definition, dict) else None
                    if isinstance(function, dict) and function.get("name"):
                        available_tools.add(str(function["name"]))
            except Exception as exc:
                runtime_error = str(exc) or runtime_error
        missing_tools = [tool for tool in spec.tools if tool not in available_tools]
        if missing_tools:
            blocked.append("tools")
            reasons.append(f"工具不可用：{', '.join(missing_tools)}")
        elif runtime is None and spec.tools:
            blocked.append("runtime")
            reasons.append(runtime_error or "Agent 工具运行时未就绪")

        try:
            profile = resolve_agent_execution_profile(spec.role_key, tools=list(spec.tools))
            if profile.execution_lane == "sandbox":
                if not bool(getattr(settings, "sandbox_agent_enabled", False)):
                    blocked.append("sandbox_agent")
                    reasons.append("后台 Agent 沙箱未启用")
                if runtime is not None and (runtime.sandbox is None or not runtime.sandbox_authorized):
                    blocked.append("sandbox_permission")
                    reasons.append("当前工作区没有可用的沙箱授权")
        except Exception as exc:
            blocked.append("profile")
            reasons.append(str(exc) or "Agent 执行配置无效")

        rows.append(
            {
                "id": spec.capability_id,
                "description": spec.description,
                "tools": list(spec.tools),
                "requires_confirmation": spec.requires_confirmation,
                "required_permission": required,
                "available": not blocked,
                "blocked_by": sorted(set(blocked)),
                "reason": "；".join(dict.fromkeys(item for item in reasons if item)) or None,
            }
        )
    return rows
