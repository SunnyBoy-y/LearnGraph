from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TaskStateCreateRequest(BaseModel):
    task_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=240)
    goal: str = Field(default="", max_length=10_000)
    project_id: str | None = Field(default=None, max_length=64)
    goal_id: str | None = Field(default=None, max_length=64)
    parent_task_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=160)


class TaskStateUpdateRequest(BaseModel):
    expected_stream_version: int = Field(ge=0)
    status: Literal[
        "planned", "in_progress", "blocked", "paused", "completed", "cancelled", "superseded"
    ] | None = None
    current_stage: str | None = Field(default=None, max_length=120)
    completed: list[dict[str, Any]] | None = None
    pending: list[dict[str, Any]] | None = None
    constraints: list[dict[str, Any]] | None = None
    blocked_by: list[dict[str, Any]] | None = None
    decisions: list[dict[str, Any]] | None = None
    artifact_refs: list[dict[str, Any]] | None = None
    related_file_refs: list[dict[str, Any]] | None = None
    next_action: str | None = Field(default=None, max_length=10_000)
    idempotency_key: str = Field(min_length=8, max_length=160)


class TaskStateView(BaseModel):
    task_id: str
    stream_version: int
    title: str
    goal: str
    status: str
    current_stage: str
    completed: list[dict[str, Any]]
    pending: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    blocked_by: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    artifact_refs: list[dict[str, Any]]
    related_file_refs: list[dict[str, Any]]
    next_action: str
    updated_at: datetime


class TaskStatePatchCandidate(BaseModel):
    """Incremental patch an LLM/agent may propose for a task state.

    Every field is a delta, never an authoritative snapshot. The server validates
    the proposal against the state machine and the current projection, then
    applies it via deterministic events. A proposer cannot write authoritative
    fields directly or force an illegal status transition.
    """

    proposed_status: Literal[
        "planned", "in_progress", "blocked", "paused", "completed", "cancelled", "superseded"
    ] | None = None
    current_stage: str | None = Field(default=None, max_length=120)
    completed_add: list[dict[str, Any]] = Field(default_factory=list)
    pending_add: list[dict[str, Any]] = Field(default_factory=list)
    pending_remove: list[str] = Field(default_factory=list)
    constraints_add: list[dict[str, Any]] = Field(default_factory=list)
    # ``blocked_by_add`` models a planned step that failed and is now blocked on
    # a dependency. When the proposer leaves ``proposed_status`` blank the server
    # still records the failure observably: it pushes the task to ``blocked``
    # (if the state machine permits) so a ``task.blocked`` event is emitted
    # instead of collapsing the failure into an opaque ``task.stage_changed``.
    blocked_by_add: list[dict[str, Any]] = Field(default_factory=list)
    decisions_add: list[dict[str, Any]] = Field(default_factory=list)
    next_action: str | None = Field(default=None, max_length=10_000)


class TaskStatePatchRequest(BaseModel):
    """Envelope for a proposed patch; carries the CAS expected version."""

    expected_stream_version: int = Field(ge=0)
    candidate: TaskStatePatchCandidate
    idempotency_key: str = Field(min_length=8, max_length=160)


# Plan §5.4: every decision / constraint / open question must cite its source.
# A structured item may name it via any of these keys.
_EPISODE_SOURCE_KEYS = ("source", "source_refs", "source_message_refs", "refs")


def _validate_episode_claim_sources(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every decision / open question / constraint must cite a source.

    Plan §5.4: "每个决定、约束和开放问题必须引用 Source Message/Event".
    A structured item that cannot name where it came from is not allowed to
    enter the episode — the model may only fill structured fields, it must not
    widen the source scope.
    """
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"source reference required: item {idx} must be an object")
        if not any(key in item and item[key] for key in _EPISODE_SOURCE_KEYS):
            raise ValueError(
                "source reference required: each decision/constraint/open_question "
                f"must include one of {_EPISODE_SOURCE_KEYS}"
            )
    return value


class EpisodeGenerateRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=50_000)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    source_message_refs: list[str] = Field(default_factory=list)
    boundary_reason: str = Field(default="explicit", max_length=120)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @field_validator("decisions", "open_questions", "constraints")
    @classmethod
    def _require_source_refs(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _validate_episode_claim_sources(value)


class EpisodeObserveRequest(BaseModel):
    """Trusted deterministic boundary observation.

    Public callers may supply only explicit durable lifecycle signals. Token,
    idle and entity-derived signals are reserved for server-owned workers so a
    client cannot forge automatic episode boundaries.
    """

    conversation_id: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    source_message_refs: list[str] = Field(min_length=1, max_length=200)
    explicit_topic_switch: bool = False
    task_stage_completed: bool = False
    conversation_closed: bool = False
    idempotency_key: str = Field(min_length=8, max_length=160)


class EpisodeCloseRequest(BaseModel):
    expected_stream_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=50_000)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    source_message_refs: list[str] = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @field_validator("decisions", "open_questions", "constraints")
    @classmethod
    def _require_source_refs(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _validate_episode_claim_sources(value)


class EpisodeObservationView(BaseModel):
    boundary_detected: bool
    boundary_reason: str | None = None
    opened_episode: EpisodeView | None = None
    closed_episode: EpisodeView | None = None


class EpisodeSearchRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    query: str = Field(default="", max_length=10_000)
    limit: int = Field(default=10, ge=1, le=50)


class EpisodeView(BaseModel):
    episode_id: str
    stream_version: int
    conversation_id: str
    task_id: str | None
    title: str
    summary: str
    decisions: list[dict[str, Any]]
    open_questions: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    source_message_refs: list[str]
    status: str
    boundary_reason: str
