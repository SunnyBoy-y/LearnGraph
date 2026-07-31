from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ContextBuildRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    query: str = Field(min_length=1, max_length=50_000)
    token_budget: int = Field(default=16_000, ge=256, le=500_000)
    agent_id: str = Field(default="main_agent", max_length=80)
    provider_id: str = Field(default="", max_length=80)
    model_id: str = Field(default="", max_length=200)
    allowed_sensitivity: list[
        Literal["public", "normal", "private", "sensitive", "restricted"]
    ] = Field(default_factory=lambda: ["public", "normal", "private"])
    debug_manifest: bool = False


class ContextEvidenceView(BaseModel):
    kind: str
    target_id: str
    title: str
    content: str
    source_event_id: str
    scope: str
    confidence: float
    status: str
    retrieval_reason: str
    trust: str
    score: float
    component_scores: dict[str, float] = Field(default_factory=dict)


class ContextBuildView(BaseModel):
    context_build_id: str
    trace_id: str | None = None
    task_state: dict[str, Any] | None = None
    recent_context: list[dict[str, Any]] = Field(default_factory=list)
    memories: list[ContextEvidenceView] = Field(default_factory=list)
    episodes: list[dict[str, Any]] = Field(default_factory=list)
    project_decisions: list[dict[str, Any]] = Field(default_factory=list)
    file_chunks: list[dict[str, Any]] = Field(default_factory=list)
    learning_states: list[dict[str, Any]] = Field(default_factory=list)
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    tool_candidates: list[dict[str, Any]] = Field(default_factory=list)
    provider_messages: list[dict[str, Any]] = Field(default_factory=list)
    context_manifest: list[dict[str, Any]] = Field(default_factory=list)
    section_tokens: dict[str, int] = Field(default_factory=dict)
    total_tokens: int = 0
    package_hash: str
    excluded: dict[str, int] = Field(default_factory=dict)
    degraded_modes: list[str] = Field(default_factory=list)
