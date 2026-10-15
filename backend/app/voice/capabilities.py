"""Server-owned capability bundles for the full-duplex voice Tutor.

The foreground Tutor must stay small and latency-friendly.  This module gives
it a stable, intent-level vocabulary for handing work to the existing durable
Agent runtime.  Voice code selects a capability id; it never gets to invent an
arbitrary tool allow-list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class VoiceCapabilitySpec:
    """The server-side execution contract for one voice action."""

    capability_id: str
    role_key: str
    tools: tuple[str, ...]
    skills: tuple[str, ...] = ()
    write_set: tuple[str, ...] = ()
    requires_confirmation: bool = False
    required_permission: str = "workspace.manage"
    description: str = ""
    output_contract: Mapping[str, Any] | None = None


# These names are the public Agent function names emitted by AgentToolRuntime
# and MCPAndSkillService.  The executor still performs the final availability,
# permission and network checks before exposing any definition to the model.
VOICE_CAPABILITIES: dict[str, VoiceCapabilitySpec] = {
    "research": VoiceCapabilitySpec(
        capability_id="research",
        role_key="research",
        tools=(
            "search_web",
            "fetch_web_page",
            "sandbox_download",
            "parallel_web_research",
        ),
        skills=("roadmap-planning",),
        required_permission="workspace.write",
        description="检索并整理外部资料，返回来源和限制。",
    ),
    "reasoning": VoiceCapabilitySpec(
        capability_id="reasoning",
        role_key="reason",
        tools=(),
        required_permission="workspace.write",
        description="对问题进行后台深度分析，不调用外部工具。",
    ),
    "learning_read": VoiceCapabilitySpec(
        capability_id="learning_read",
        role_key="tool",
        tools=(
            "lg_graph_read",
            "lg_learning_mastery_read",
            "lg_review_list_due",
            "lg_roadmap_read",
            "lg_schedule_list",
        ),
        skills=("node-learning", "review-coach", "roadmap-planning"),
        required_permission="workspace.write",
        description="读取学习节点、掌握度、复习项和学习行动。",
    ),
    "workspace_read": VoiceCapabilitySpec(
        capability_id="workspace_read",
        role_key="tool",
        tools=(
            "get_current_time",
            "sandbox_list_files",
            "sandbox_grep",
            "sandbox_read_file",
            "sandbox_skill_list",
            "sandbox_skill_read",
        ),
        required_permission="workspace.read",
        description="读取工作区文件和可用技能，不执行写入。",
    ),
    "practice_prepare": VoiceCapabilitySpec(
        capability_id="practice_prepare",
        role_key="tool",
        tools=(
            "lg_graph_read",
            "lg_learning_mastery_read",
            "voice_practice_create",
            "canvas_get_render_contract",
            "canvas_emit_trusted_component",
        ),
        skills=("node-learning",),
        required_permission="workspace.write",
        description="读取学习上下文并生成可交互练习。",
        output_contract={
            "required": ["short_answer", "artifacts"],
            "artifacts": "trusted_components",
        },
    ),
    "learning_evidence": VoiceCapabilitySpec(
        capability_id="learning_evidence",
        role_key="tool",
        tools=("lg_learning_evidence_record",),
        skills=("node-learning", "review-coach"),
        required_permission="workspace.write",
        description="记录有来源的学习证据，结果进入待审核状态。",
        output_contract={"required": ["evidence"]},
    ),
    "graph_proposal": VoiceCapabilitySpec(
        capability_id="graph_proposal",
        role_key="tool",
        tools=(
            "lg_graph_read",
            "lg_graph_create",
            "lg_graph_propose_change",
        ),
        skills=("graph-generation",),
        # Background Agent work is already isolated by the capability allow-list
        # and workspace permission. It starts immediately; the result is
        # surfaced in the conversation canvas for review.
        requires_confirmation=False,
        required_permission="workspace.write",
        description="生成可审核的知识图谱提案。",
        output_contract={"required": ["short_answer", "findings"]},
    ),
    "artifact_create": VoiceCapabilitySpec(
        capability_id="artifact_create",
        role_key="tool",
        tools=(
            "canvas_get_render_contract",
            "canvas_emit_trusted_component",
            "sandbox_publish_file",
            "sandbox_publish_image",
        ),
        requires_confirmation=False,
        description="创建可交互学习产物或发布沙箱文件。",
        output_contract={"required": ["short_answer", "artifacts"]},
    ),
    "workspace_task": VoiceCapabilitySpec(
        capability_id="workspace_task",
        role_key="tool",
        tools=(
            "sandbox_list_files",
            "sandbox_grep",
            "sandbox_read_file",
            "sandbox_write_file",
            "sandbox_append_file",
            "sandbox_edit_file",
            "sandbox_exec",
            "sandbox_todo",
            "sandbox_publish_file",
            "sandbox_publish_image",
            "sandbox_skill_list",
            "sandbox_skill_read",
        ),
        requires_confirmation=False,
        description="在受控 Agent Workspace 中处理文件和构建任务。",
        output_contract={"required": ["short_answer", "artifacts", "limitations"]},
    ),
}

_CAPABILITY_ALIASES = {
    "tool": "workspace_read",
    "workspace": "workspace_read",
    "learning": "learning_read",
    "mastery": "learning_read",
    "practice": "practice_prepare",
    "exercise": "practice_prepare",
    "evidence": "learning_evidence",
    "graph": "graph_proposal",
    "artifact": "artifact_create",
}


def resolve_voice_capability(
    capability_id: str | None,
    *,
    fallback: str = "workspace_task",
) -> VoiceCapabilitySpec:
    """Resolve an intent-level capability using only server-owned entries."""

    normalized = str(capability_id or fallback).strip().casefold()
    normalized = _CAPABILITY_ALIASES.get(normalized, normalized)
    spec = VOICE_CAPABILITIES.get(normalized)
    if spec is None:
        supported = ", ".join(sorted(VOICE_CAPABILITIES))
        raise ValueError(f"Unsupported voice capability {capability_id!r}; supported: {supported}")
    return spec


def capability_id_for_tools(tools: list[str] | tuple[str, ...] | None) -> str | None:
    """Best-effort label for legacy/API-created links that store tools only."""

    normalized = {str(item).strip() for item in (tools or ()) if str(item).strip()}
    if not normalized:
        return None
    matches = [
        spec
        for spec in VOICE_CAPABILITIES.values()
        if normalized == set(spec.tools)
    ]
    if matches:
        return matches[0].capability_id
    return None
