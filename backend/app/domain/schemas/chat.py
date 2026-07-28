from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.schemas.common import ORMModel


MessagePartType = Literal[
    "acknowledgement",
    "text",
    "reasoning_summary",
    "reasoning_content",
    "agent_step",
    "tool_call",
    "source_list",
    "attachment",
    "document_selection",
    "selection_quote",
    "image",
    "graph_context",
    "quiz",
    "chart",
    "sandbox",
    "sandbox_artifact",
    "sandbox_status",
    "component",
    "magic_card",
    "user_confirmation",
    "error",
]


class MessagePart(BaseModel):
    id: str
    type: MessagePartType
    status: Literal["pending", "streaming", "completed", "failed"]
    content: str | None = None
    content_delta: str | None = None
    # Stream / storage ordinal used by the client to interleave text with tools.
    sequence: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SessionCreateRequest(BaseModel):
    title: str = Field(default="新学习会话", min_length=1, max_length=240)
    goal_id: str | None = Field(default=None, min_length=1, max_length=36)
    graph_id: str | None = Field(default=None, min_length=1, max_length=36)
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    memory_enabled: bool = False


class SessionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    pinned: bool | None = None
    goal_id: str | None = Field(default=None, min_length=1, max_length=36)
    graph_id: str | None = Field(default=None, min_length=1, max_length=36)


class SessionAutoTitleRequest(BaseModel):
    source_message_id: str = Field(min_length=1, max_length=36)
    expected_title: str = Field(min_length=1, max_length=240)
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)


class ModelSessionTitle(BaseModel):
    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("The generated session title cannot be blank")
        return normalized


class DictationCleanupRequest(BaseModel):
    # A single finalized ASR chunk. The frontend batches finalized speech
    # segments before calling, so the bound covers one flush, not a whole
    # dictation session.
    text: str = Field(min_length=1, max_length=2_000)
    # Read-only tail of the already-cleaned transcript, sent for context so
    # the model can resolve homophones across chunk boundaries.
    context: str = Field(default="", max_length=400)
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)


class ModelDictationCleanup(BaseModel):
    # Empty output is valid: a chunk made purely of filler words cleans to "".
    text: str = Field(max_length=4_000)


class DictationCleanupView(BaseModel):
    text: str


class DictationTranscriptionView(BaseModel):
    # Empty text is valid: a segment can be pure silence or breath noise.
    text: str
    language: str | None = None
    duration_seconds: float | None = None
    request_id: str | None = None


class SessionView(ORMModel):
    id: str
    workspace_id: str
    title: str
    goal_id: str | None
    graph_id: str | None
    project_id: str | None
    parent_session_id: str | None
    source_message_id: str | None
    memory_enabled: bool
    pinned: bool
    model_snapshot: dict[str, Any]
    status: str
    closed_at: datetime | None
    archived_at: datetime | None
    session_kind: str
    writeback_policy: str
    context_capsule: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SessionSearchEntity(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def normalize_values(self) -> "SessionSearchEntity":
        self.type = " ".join(self.type.split())
        self.value = " ".join(self.value.split())
        if not self.type or not self.value:
            raise ValueError("entity type and value cannot contain only whitespace")
        return self


class SessionSearchTimeRange(BaseModel):
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_order(self) -> "SessionSearchTimeRange":
        for label, value in (("from", self.from_), ("to", self.to)):
            if value is not None and value.tzinfo is None:
                raise ValueError(f"timeRange.{label} must include a timezone offset")
        if self.from_ is not None and self.to is not None and self.from_ > self.to:
            raise ValueError("timeRange.from must be earlier than timeRange.to")
        return self


class SessionFragmentSearchRequest(BaseModel):
    """Constrained input shared by the Agent tool and retrieval service."""

    query: str | None = Field(default=None, max_length=500)
    session_ids: list[str] = Field(default_factory=list, max_length=20)
    scope: Literal["linked", "workspace", "all_authorized"] = "linked"
    reason: Literal[
        "resolve_reference",
        "continue_task",
        "recover_decision",
        "verify_memory",
        "find_learning_evidence",
    ] = "resolve_reference"
    keywords: list[str] = Field(default_factory=list, max_length=20)
    phrases: list[str] = Field(default_factory=list, max_length=10)
    entities: list[SessionSearchEntity] = Field(default_factory=list, max_length=20)
    graph_node_ids: list[str] = Field(default_factory=list, max_length=20)
    time_range: SessionSearchTimeRange | None = None
    status: list[
        Literal["current", "confirmed", "possibly_current", "superseded"]
    ] = Field(default_factory=list, max_length=4)
    prefer_recent: bool = True
    top_k: int = Field(default=8, ge=1, le=10)

    @field_validator(
        "session_ids",
        "keywords",
        "phrases",
        "graph_node_ids",
        mode="after",
    )
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        normalized = [" ".join(value.split()) for value in values]
        normalized = [value for value in normalized if value]
        return list(dict.fromkeys(normalized))

    @field_validator("session_ids", "graph_node_ids", mode="after")
    @classmethod
    def validate_identifier_lengths(cls, values: list[str]) -> list[str]:
        if any(len(value) > 36 for value in values):
            raise ValueError("session and graph node IDs cannot exceed 36 characters")
        return values

    @field_validator("keywords", mode="after")
    @classmethod
    def validate_keyword_lengths(cls, values: list[str]) -> list[str]:
        if any(len(value) > 160 for value in values):
            raise ValueError("keywords cannot exceed 160 characters")
        return values

    @field_validator("phrases", mode="after")
    @classmethod
    def validate_phrase_lengths(cls, values: list[str]) -> list[str]:
        if any(len(value) > 240 for value in values):
            raise ValueError("phrases cannot exceed 240 characters")
        return values

    @model_validator(mode="after")
    def require_search_signal(self) -> "SessionFragmentSearchRequest":
        if self.query is not None:
            self.query = " ".join(self.query.split()) or None
        if not (
            self.query
            or self.session_ids
            or self.keywords
            or self.phrases
            or self.entities
            or self.graph_node_ids
        ):
            raise ValueError(
                "Provide session_ids, query, keywords, phrases, entities, or graph_node_ids"
            )
        return self


class SessionFragmentSearchHit(BaseModel):
    result_id: str
    source_session_id: str
    session_title: str
    fragment_type: Literal[
        "conversation",
        "decision",
        "plan",
        "assessment",
        "summary",
    ]
    snippet: str
    matched_terms: list[str] = Field(default_factory=list)
    relation: Literal[
        "current_session",
        "parent",
        "child",
        "same_workspace",
        "same_graph_node",
        "adjacent_graph_node",
    ]
    status: Literal["current", "confirmed", "possibly_current", "superseded"]
    score: float
    created_at: datetime
    message_ids: list[str] = Field(default_factory=list)


class SessionFragmentSearchResponse(BaseModel):
    query: str | None
    scope: Literal["linked", "workspace", "all_authorized"]
    reason: str
    retrieval_strategy: Literal["session_id", "fts5_bm25_rules", "mixed"]
    hits: list[SessionFragmentSearchHit] = Field(default_factory=list)


class SuggestedPromptView(BaseModel):
    id: str
    content: str


class SuggestedPromptGenerateRequest(BaseModel):
    count: int = Field(default=3, ge=2, le=3)
    anchor_message_id: str | None = Field(default=None, min_length=1, max_length=36)
    anchor_message_version_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
    )
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_anchor_version(self) -> "SuggestedPromptGenerateRequest":
        if (self.anchor_message_id is None) != (
            self.anchor_message_version_id is None
        ):
            raise ValueError(
                "anchor_message_id and anchor_message_version_id must be provided together"
            )
        return self


class ModelSuggestedPromptSet(BaseModel):
    questions: list[str] = Field(min_length=2, max_length=3)

    @field_validator("questions")
    @classmethod
    def normalize_questions(cls, values: list[str]) -> list[str]:
        normalized = [" ".join(value.split()) for value in values]
        if any(len(value) < 4 or len(value) > 240 for value in normalized):
            raise ValueError("Each suggested question must contain 4 to 240 characters")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("Suggested questions must be distinct")
        return normalized


class SuggestedPromptBatchView(BaseModel):
    id: str
    session_id: str
    anchor_message_id: str | None
    anchor_message_version_id: str | None
    prompts: list[SuggestedPromptView]
    memory_used: bool
    provider_trace: dict[str, Any]
    generated_at: datetime
    cached: bool


class DocumentSelectionContext(BaseModel):
    file_id: str = Field(min_length=1, max_length=36)
    document_revision_id: str = Field(min_length=1, max_length=36)
    chunk_id: str = Field(min_length=1, max_length=36)
    locator: dict[str, Any] = Field(default_factory=dict)
    selected_text: str = Field(min_length=1, max_length=50_000)
    selected_text_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )

    @model_validator(mode="after")
    def validate_locator_identity(self) -> "DocumentSelectionContext":
        if not self.selected_text.strip():
            raise ValueError("selected_text cannot contain only whitespace")
        locator_chunk_id = self.locator.get("chunk_id")
        if locator_chunk_id is not None and str(locator_chunk_id) != self.chunk_id:
            raise ValueError("locator.chunk_id must match chunk_id")
        locator_revision_id = self.locator.get("document_revision_id")
        if (
            locator_revision_id is not None
            and str(locator_revision_id) != self.document_revision_id
        ):
            raise ValueError(
                "locator.document_revision_id must match document_revision_id"
            )
        return self


class MessageSelectionContext(BaseModel):
    source_message_id: str = Field(min_length=1, max_length=36)
    selected_text: str = Field(min_length=1, max_length=4_000)
    prefix: str = Field(default="", max_length=500)
    suffix: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def normalize_selection(self) -> "MessageSelectionContext":
        self.selected_text = self.selected_text.strip()
        if not self.selected_text:
            raise ValueError("selected_text cannot contain only whitespace")
        return self


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    generation_mode: Literal["text", "image"] = "text"
    parent_message_id: str | None = None
    node_ids: list[str] = Field(default_factory=list, max_length=8)
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    document_selection: DocumentSelectionContext | None = None
    selection_context: MessageSelectionContext | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    thinking_mode: Literal["off", "low", "medium", "high", "xhigh"] | None = None
    search_route: Literal["disabled", "model_native", "external", "local", "auto"] = "disabled"
    web_search: bool = False
    agent_mode: bool = False
    goal_mode: bool = False
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    graph_action: Literal["none", "propose_create", "propose_update"] = "none"
    graph_id: str | None = Field(default=None, min_length=1, max_length=36)

    @model_validator(mode="after")
    def validate_graph_action_target(self):
        if self.goal_mode and not self.agent_mode:
            raise ValueError("goal_mode requires agent_mode")
        if self.graph_action in {"none", "propose_create"} and self.graph_id is not None:
            raise ValueError("graph_id is only valid for propose_update")
        if self.web_search and self.search_route == "disabled":
            self.search_route = "auto"
        if self.search_route != "disabled":
            self.web_search = True
        return self


class MessageRetryRequest(BaseModel):
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    thinking_mode: Literal["off", "low", "medium", "high", "xhigh"] | None = None
    agent_mode: bool | None = None
    search_route: Literal["disabled", "model_native", "external", "local", "auto"] | None = None
    web_search: bool = False
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def normalize_search_options(self) -> "MessageRetryRequest":
        if self.web_search and self.search_route in {None, "disabled"}:
            self.search_route = "auto"
        if self.search_route not in {None, "disabled"}:
            self.web_search = True
        return self


class MessageView(ORMModel):
    id: str
    workspace_id: str
    session_id: str
    parent_message_id: str | None
    role: str
    version: int
    status: str
    content: str
    parts: list[dict[str, Any]]
    provider_trace: dict[str, Any]
    created_at: datetime


class MessageSnapshotView(BaseModel):
    id: str
    workspace_id: str
    session_id: str
    parent_message_id: str | None
    role: str
    message_version_id: str
    version: int
    status: str
    content: str
    parts: list[MessagePart]
    provider_trace: dict[str, Any]
    last_event_id: str | None
    last_sequence: int
    created_at: datetime
    updated_at: datetime


class MessageVersionView(ORMModel):
    id: str
    message_id: str
    version: int
    status: str
    provider_trace: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SSEEventEnvelope(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    event_id: str
    sequence: int
    session_id: str
    message_id: str
    message_version_id: str
    part_id: str | None
    type: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    # Compatibility fields used by the current frontend stream reducer. The
    # canonical protocol event is always carried by ``type``.
    event: str
    part: dict[str, Any] | None = None
    status: str | None = None
    provider_trace: dict[str, Any] | None = None


class SessionContextUsageView(BaseModel):
    """Display-only estimate of how full the session context is.

    ``estimated_tokens`` is a lower bound over the visible timeline; the
    authoritative compaction decision still happens inside the next stream
    request (see ``ChatService.context_usage``).
    """

    session_id: str
    estimated_tokens: int
    input_budget_tokens: int
    compaction_threshold_tokens: int
    remaining_tokens: int
    used_ratio: float
    context_window_tokens: int
    compaction_ratio: float
    message_count: int


class BranchRequest(BaseModel):
    title: str = Field(default="分支会话", min_length=1, max_length=240)


class ConceptBranchCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    source_message_id: str | None = Field(default=None, max_length=36)
    document_selection: DocumentSelectionContext
    selected_sentence: str = Field(default="", max_length=4_000)
    surrounding_text: str = Field(default="", max_length=12_000)
    source_title: str = Field(default="", max_length=500)
    current_node_id: str | None = Field(default=None, max_length=36)
    relevant_parent_message_ids: list[str] = Field(default_factory=list, max_length=4)


class ConceptBranchPromoteRequest(BaseModel):
    action: Literal["merge_summary", "standalone"]
    summary: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def require_summary_for_merge(self):
        if self.action == "merge_summary" and not self.summary.strip():
            raise ValueError("summary is required for merge_summary")
        return self
