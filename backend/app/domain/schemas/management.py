from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.domain.schemas.common import ORMModel


class MemoryCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=50_000)
    namespace: Literal["workspace", "session"] = "workspace"
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    scope_type: Literal["workspace", "goal", "node", "session"] | None = None
    scope_id: str | None = Field(default=None, min_length=1, max_length=64)
    goal_id: str | None = Field(default=None, min_length=1, max_length=36)
    node_id: str | None = Field(default=None, min_length=1, max_length=36)
    zone: Literal["hot", "recent", "topics", "archive"] = "recent"
    record_kind: str = Field(default="semantic_memory", min_length=1, max_length=64)
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    resolution_status: Literal[
        "none", "active_misconception", "improving", "resolved", "recurring"
    ] = "none"
    source: str = Field(default="user", max_length=120)
    source_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_scope(self) -> "MemoryCreateRequest":
        if self.namespace == "session" and self.session_id is None:
            raise ValueError("session_id is required for session memory")
        if self.namespace == "workspace" and self.session_id is not None:
            raise ValueError("workspace memory cannot declare session_id")
        return self


class MemoryUpdateRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    zone: Literal["hot", "recent", "topics", "archive"] | None = None
    source_ids: list[str] | None = Field(default=None, max_length=100)
    structured_payload: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    resolution_status: (
        Literal["none", "active_misconception", "improving", "resolved", "recurring"]
        | None
    ) = None
    goal_id: str | None = Field(default=None, min_length=1, max_length=36)
    node_id: str | None = Field(default=None, min_length=1, max_length=36)
    scope_type: Literal["workspace", "goal", "node", "session"] | None = None
    scope_id: str | None = Field(default=None, min_length=1, max_length=64)
    reason: str = Field(default="user_edit", max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> "MemoryUpdateRequest":
        if (
            self.title is None
            and self.content is None
            and self.zone is None
            and self.source_ids is None
            and self.structured_payload is None
            and self.confidence is None
            and self.importance is None
            and self.resolution_status is None
            and self.goal_id is None
            and self.node_id is None
            and self.scope_type is None
            and self.scope_id is None
        ):
            raise ValueError("At least one mutable memory field is required")
        return self


class MemoryView(ORMModel):
    id: str
    lg_memory_id: str
    workspace_id: str
    namespace: str
    session_id: str | None
    scope_type: str = "workspace"
    scope_id: str | None = None
    goal_id: str | None = None
    node_id: str | None = None
    record_kind: str
    merge_strategy: str = "UNION"
    zone: str
    state: str
    title: str
    content_hash: str
    relative_path: str
    revision: int
    source: str
    source_ids: list[str]
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    atom_schema_version: int = 0
    canonical_key: str = ""
    atom_kind: str = "fact"
    ledger_status: str = "active"
    temporal_status: str = "timeless"
    summary_eligibility: str = "legacy_review"
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    event_at: datetime | None = None
    next_review_at: datetime | None = None
    last_verified_at: datetime | None = None
    timezone_name: str = "Asia/Shanghai"
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.7
    importance: float = 0.5
    strength: float = 0.5
    access_count: int = 0
    confirmation_count: int = 0
    successful_use_count: int = 0
    last_accessed_at: datetime | None = None
    resolution_status: str = "none"
    decay_policy: str = "SLOW"
    supersedes_id: str | None = None
    plan_change_text: str = ""
    provider_id: str
    provider_binding_id: str | None
    deleted_at: datetime | None
    recoverable_until: datetime | None
    content_destroyed_at: datetime | None
    tenant_id: str = "local-tenant"
    subject_user_id: str | None = None
    audience_type: str = "workspace"
    task_id: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    file_id: str | None = None
    memory_layer: str = "L4"
    assertion_type: str = "explicit"
    sensitivity: str = "normal"
    lifecycle_status: str = "active"
    superseded_by_id: str | None = None
    head_event_id: str | None = None
    view_source: Literal["record", "event"] = "record"
    projection_version: int = 1
    auto_recall_suppressed: bool = False
    child_agent_denied: bool = False
    restore_available: bool
    created_at: datetime
    updated_at: datetime
    content: str | None = None
    retrieval_score: float | None = None


class MemoryDraftCreateRequest(BaseModel):
    operation: Literal[
        "CREATE",
        "UPDATE",
        "CORRECT",
        "CONFIRM",
        "COMPLETE",
        "CANCEL",
        "RESCHEDULE",
        "MERGE",
        "SUPERSEDE",
        "RETRACT",
        "PROMOTE",
        "DEMOTE",
        "ARCHIVE",
    ] = "CREATE"
    memory_type: str = Field(default="semantic_memory", min_length=1, max_length=64)
    target_memory_id: str | None = Field(default=None, min_length=1, max_length=64)
    proposed_scope_type: Literal["workspace", "goal", "node", "session"] = "workspace"
    proposed_scope_id: str | None = Field(default=None, min_length=1, max_length=64)
    goal_id: str | None = Field(default=None, min_length=1, max_length=36)
    node_id: str | None = Field(default=None, min_length=1, max_length=36)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    branch_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    title: str = Field(default="", max_length=240)
    content: str = Field(default="", max_length=50_000)
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    suggested_decay_policy: str = Field(default="SLOW", max_length=40)
    conflicts_with: list[str] = Field(default_factory=list, max_length=50)
    created_by: str = Field(default="user", max_length=80)
    auto_commit: bool = False


class MemoryDraftDecisionRequest(BaseModel):
    decision: Literal["commit", "reject"]
    reason: str = Field(default="", max_length=500)


class MemoryDraftView(ORMModel):
    id: str
    workspace_id: str
    operation: str
    status: str
    memory_type: str
    target_memory_id: str | None
    proposed_scope_type: str
    proposed_scope_id: str | None
    goal_id: str | None
    node_id: str | None
    session_id: str | None
    branch_session_id: str | None
    title: str
    content: str
    structured_payload: dict[str, Any]
    source_refs: list[dict[str, Any]]
    confidence: float
    importance: float
    suggested_decay_policy: str
    conflicts_with: list[str]
    created_by: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    rejection_reason: str
    result_memory_id: str | None
    result_revision: int | None
    created_at: datetime
    updated_at: datetime


class MemoryTypeDefinitionView(BaseModel):
    memory_type: str
    default_scope: str
    merge_strategy: str
    decay_policy: str
    requires_confirmation: bool
    description: str
    payload_schema: dict[str, str] = Field(default_factory=dict)


class EffectiveMemoryPackageView(BaseModel):
    """Structured context package assembled for a session/task (Phase 2)."""

    session_id: str | None = None
    goal_id: str | None = None
    node_ids: list[str] = Field(default_factory=list)
    effective_memories: list[MemoryView] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    prompt_block: str = ""
    token_estimate: int = 0


class MemoryRevisionView(ORMModel):
    id: str
    memory_id: str
    revision: int
    base_revision: int | None
    operation: str
    title: str
    content: str | None
    content_hash: str
    namespace: str
    session_id: str | None
    record_kind: str
    zone: str
    source: str
    source_ids: list[str]
    actor_id: str
    reason: str
    is_active: bool
    created_at: datetime


class MemoryRevisionRestoreRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(default="user_revision_restore", max_length=500)


class MemoryPolicyUpdateRequest(BaseModel):
    workspace_enabled: bool | None = None
    workspace_recall_enabled: bool | None = None
    workspace_learning_enabled: bool | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    session_enabled: bool | None = None
    session_recall_enabled: bool | None = None
    session_learning_enabled: bool | None = None
    all_sessions_shared: bool | None = None

    @model_validator(mode="after")
    def require_policy_change(self) -> "MemoryPolicyUpdateRequest":
        if all(
            value is None
            for value in (
                self.workspace_enabled,
                self.workspace_recall_enabled,
                self.workspace_learning_enabled,
                self.session_enabled,
                self.session_recall_enabled,
                self.session_learning_enabled,
                self.all_sessions_shared,
            )
        ):
            raise ValueError("A workspace or session policy change is required")
        if (
            any(
                value is not None
                for value in (
                    self.session_enabled,
                    self.session_recall_enabled,
                    self.session_learning_enabled,
                )
            )
            and self.session_id is None
        ):
            raise ValueError("session_id is required for session policy changes")
        return self


class MemoryPolicyView(BaseModel):
    workspace_id: str
    workspace_enabled: bool
    session_id: str | None
    session_enabled: bool | None
    effective_enabled: bool
    workspace_recall_enabled: bool = True
    workspace_learning_enabled: bool = True
    session_recall_enabled: bool | None = None
    session_learning_enabled: bool | None = None
    effective_recall_enabled: bool = False
    effective_learning_enabled: bool = False


class MemoryEvidenceView(ORMModel):
    id: str
    source_kind: str
    source_id: str
    message_id: str | None
    message_part_id: str | None
    file_id: str | None
    tool_call_id: str | None
    authorship: str
    derived_from: list[dict[str, Any]]
    observed_at: datetime
    content_hash: str
    excerpt: str
    profile_eligible: bool
    eligibility_reason: str
    created_at: datetime


class MemoryProfileDimension(BaseModel):
    """Structured profile dimension with a stable key and atom-backed claims."""

    key: str = ""
    title: str = ""
    paragraphs: list[dict[str, Any]] = Field(default_factory=list)


class MemoryProfileView(BaseModel):
    id: str | None = None
    workspace_id: str
    owner_subject_id: str
    version: int = 0
    status: Literal["empty", "atomic_snapshot", "ready", "stale", "building", "failed"] = "empty"
    # M2 contract: a mandatory overview plus structured dimensions. The legacy
    # free-form ``structured_sections`` is kept for backward compatibility but
    # new snapshots are rendered from overview + dimensions.
    overview: str = ""
    dimensions: list[MemoryProfileDimension] = Field(default_factory=list)
    scope: Literal["workspace", "global_user"] = "workspace"
    source_count: int = 0
    markdown: str = ""
    structured_sections: list[dict[str, Any]] = Field(default_factory=list)
    source_atom_ids: list[str] = Field(default_factory=list)
    source_fingerprint: str = ""
    generated_at: datetime | None = None
    updated_at: datetime | None = None
    stale_reason: str = ""


class MemoryProfileIntentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)
    selected_text: str | None = Field(default=None, max_length=4_000)
    selected_atom_ids: list[str] = Field(default_factory=list, max_length=50)
    timezone_name: str = Field(
        default="Asia/Shanghai", min_length=1, max_length=80
    )


class MemoryProfileIntentResult(BaseModel):
    status: str
    drafts_created: int = 0
    auto_committed: int = 0
    affected_memory_ids: list[str] = Field(default_factory=list)
    profile_status: str = "stale"


class ContextSummaryView(ORMModel):
    id: str
    session_id: str
    version: int
    kind: str
    source_message_ids: list[str] = Field(default_factory=list)
    summary: str
    estimated_tokens_before: int
    estimated_tokens_after: int
    created_at: datetime


class MemoryExtractionSettingsView(BaseModel):
    enabled: bool
    provider_id: str
    model_id: str
    follow_conversation: bool = False
    auto_commit: bool


class MemoryEmbeddingSettingsView(BaseModel):
    enabled: bool
    provider_id: str
    model_id: str
    semantic_weight: float


class MemorySummarizationSettingsView(BaseModel):
    enabled: bool
    provider_id: str
    model_id: str
    follow_conversation: bool = False


class MemoryEnhancementView(BaseModel):
    """Workspace-level optional pipelines: extraction, embedding, summaries."""

    workspace_id: str
    extraction: MemoryExtractionSettingsView
    embedding: MemoryEmbeddingSettingsView
    summarization: MemorySummarizationSettingsView
    active_memories: int = 0
    indexed_memories: int = 0
    # Embedding index introspection: which model the cached vectors belong to,
    # and which older-model caches still occupy rows (safe to prune).
    current_model_key: str | None = None
    stale_model_keys: list[dict[str, Any]] = []
    # Filled only by the PUT that actually switched the embedding model, so the
    # frontend can surface an immediate "reindex required" banner.
    cache_invalidated: dict[str, Any] | None = None


class MemoryExtractionSettingsUpdate(BaseModel):
    enabled: bool | None = None
    provider_id: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=200)
    follow_conversation: bool | None = None
    auto_commit: bool | None = None


class MemoryEmbeddingSettingsUpdate(BaseModel):
    enabled: bool | None = None
    provider_id: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=200)
    semantic_weight: float | None = Field(default=None, ge=0.0, le=2.0)


class MemorySummarizationSettingsUpdate(BaseModel):
    enabled: bool | None = None
    provider_id: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=200)
    follow_conversation: bool | None = None


class MemoryEnhancementUpdateRequest(BaseModel):
    extraction: MemoryExtractionSettingsUpdate | None = None
    embedding: MemoryEmbeddingSettingsUpdate | None = None
    summarization: MemorySummarizationSettingsUpdate | None = None


class ContextSummarizationRunResult(BaseModel):
    status: str
    session_id: str | None = None
    version: int | None = None
    covered_messages: int | None = None
    newly_summarized: int | None = None
    summary: ContextSummaryView | None = None


class MemoryJournalView(ORMModel):
    id: str
    memory_id: str
    revision: int
    operation: str
    provider_id: str
    provider_epoch: int
    provider_record_id: str | None
    content_hash: str
    payload: dict[str, Any]
    tombstone: bool
    recoverable_until: datetime | None
    audit_retention_until: datetime | None
    content_scrubbed_at: datetime | None
    created_at: datetime


class MemoryBindingView(ORMModel):
    id: str
    provider_instance_id: str
    memory_id: str
    revision: int
    provider_record_id: str
    provider_entity_kind: str
    provider_entity_value: str
    source_content_hash: str
    target_readback_hash: str
    import_event_id: str | None
    binding_status: str
    verified_at: datetime | None
    last_error: str


class MemoryProviderStatusView(BaseModel):
    provider_id: str
    provider_type: str
    display_name: str
    available: bool
    remote_capability: bool
    status: str
    provider_epoch: int
    # Active memories still bound to a previous provider generation; they are
    # adopted lazily on mutation or in bulk via /memory/maintenance/migrate-provider.
    frozen_memories: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=160)
    provider_type: str = Field(min_length=1, max_length=80)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: SecretStr | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ProviderTypeCatalogView(BaseModel):
    provider_type: str
    role: Literal[
        "model", "image_generation", "vision", "search", "image_search", "fetch", "deep_research", "memory", "transcription", "embedding"
    ]
    label: str
    description: str
    requires_base_url: bool
    requires_secret: bool
    supports_model_discovery: bool
    supports_probe: bool
    create_allowed: bool
    default_base_url: str | None = None
    probe_notice: str | None = None
    brand_id: str | None = None
    brand_icon_url: str | None = None
    documentation_url: str | None = None
    key_management_url: str | None = None
    supports_account_balance: bool = False
    # image_search 角色：能力 tag（text=文搜图，image=图搜图），空表示非图搜角色。
    image_search_modes: tuple[Literal["text", "image"], ...] = ()
    # 免费供应商标记；无需 Key 且无需 Base URL 的免费源创建实例时默认启用。
    is_free: bool = False


class ProviderView(ORMModel):
    id: str
    workspace_id: str
    display_name: str
    provider_type: str
    base_url: str | None
    api_key_masked: str | None
    enabled: bool
    remote_capability: bool
    capabilities: dict[str, Any]
    status: str
    secret_status: str = "missing"
    secret_version: int | None = None
    secret_key_provider: str | None = None
    secret_key_version: int | None = None
    created_at: datetime


class ProviderBalanceInfoView(BaseModel):
    currency: Literal["CNY", "USD"]
    total_balance: str
    granted_balance: str | None = None
    topped_up_balance: str | None = None


class ProviderUsageWindowView(BaseModel):
    """A rolling usage window (e.g. Codex 5h / weekly limits)."""

    label: str
    used_percent: float
    window_minutes: int | None = None
    resets_at: datetime | None = None


class ProviderBalanceView(BaseModel):
    provider_id: str
    vendor: str
    vendor_label: str
    is_available: bool
    balance_infos: list[ProviderBalanceInfoView]
    usage_windows: list[ProviderUsageWindowView] | None = None
    notice: str | None = None
    queried_at: datetime


class ProviderBalanceQueryConfig(BaseModel):
    """cc-switch style custom balance query: a JS config expression evaluated
    in a sandboxed frame on the client; the HTTP request itself runs on the
    server so the plaintext key never reaches the browser."""

    enabled: bool = False
    template_id: str | None = Field(default=None, max_length=40)
    script: str = Field(min_length=1, max_length=20_000)
    timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    auto_query_interval_minutes: int = Field(default=0, ge=0, le=1_440)
    # Extra template variables, e.g. the NewAPI preset's {{accessToken}} /
    # {{userId}}. {{baseUrl}} / {{apiKey}} are reserved and filled server-side.
    variables: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_variables(self) -> "ProviderBalanceQueryConfig":
        if len(self.variables) > 16:
            raise ValueError("At most 16 template variables are allowed")
        for name, value in self.variables.items():
            if not name.strip() or len(name) > 64 or len(str(value)) > 2_048:
                raise ValueError("Template variable names/values exceed the allowed length")
            if name in {"baseUrl", "apiKey"}:
                raise ValueError("baseUrl and apiKey are reserved template variables")
        return self


class ProviderBalanceQueryConfigUpdateRequest(BaseModel):
    # None clears the custom configuration and falls back to the built-in
    # official balance flow.
    config: ProviderBalanceQueryConfig | None = None


class ProviderBalanceQueryConfigView(BaseModel):
    provider_id: str
    config: ProviderBalanceQueryConfig | None = None


class ProviderBalanceQueryHttpRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1_000)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=8_000)

    @model_validator(mode="after")
    def validate_headers(self) -> "ProviderBalanceQueryHttpRequest":
        if len(self.headers) > 32:
            raise ValueError("At most 32 request headers are allowed")
        for name, value in self.headers.items():
            if len(name) > 128 or len(str(value)) > 2_048:
                raise ValueError("Header names/values exceed the allowed length")
        return self


class ProviderBalanceQueryExecuteRequest(BaseModel):
    request: ProviderBalanceQueryHttpRequest
    timeout_seconds: float | None = Field(default=None, ge=1, le=60)
    # Unsaved-variable override used by the config dialog's 测试脚本 flow.
    variables: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_variables(self) -> "ProviderBalanceQueryExecuteRequest":
        if self.variables is None:
            return self
        if len(self.variables) > 16:
            raise ValueError("At most 16 template variables are allowed")
        for name, value in self.variables.items():
            if not name.strip() or len(name) > 64 or len(str(value)) > 2_048:
                raise ValueError(
                    "Template variable names/values exceed the allowed length"
                )
        return self


class ProviderBalanceQueryExecuteView(BaseModel):
    provider_id: str
    status_code: int
    ok: bool
    payload: Any | None = None
    # Raw body when the response was not valid JSON.
    text: str | None = None
    queried_at: datetime


class ProviderBalanceQueryResultRequest(BaseModel):
    """Extractor output cached for the provider list (cc-switch fields)."""

    is_valid: bool | None = None
    invalid_message: str | None = Field(default=None, max_length=500)
    remaining: float | None = None
    unit: str | None = Field(default=None, max_length=20)
    plan_name: str | None = Field(default=None, max_length=120)
    total: float | None = None
    used: float | None = None
    extra: str | None = Field(default=None, max_length=500)


class ProviderBalanceQueryResultView(ProviderBalanceQueryResultRequest):
    provider_id: str
    queried_at: datetime


class GitHubCopilotDeviceLoginStartView(BaseModel):
    device_auth_id: str
    user_code: str
    verification_url: str
    interval_seconds: int


class GitHubCopilotDeviceLoginPollRequest(BaseModel):
    device_auth_id: str = Field(min_length=1, max_length=200)
    user_code: str = Field(min_length=1, max_length=64)


class GitHubCopilotDeviceLoginPollView(BaseModel):
    status: Literal["pending", "authorized"]
    api_key: str | None = None


class CodexDeviceLoginStartView(BaseModel):
    device_auth_id: str
    user_code: str
    verification_url: str
    interval_seconds: int


class CodexDeviceLoginPollRequest(BaseModel):
    device_auth_id: str = Field(min_length=1, max_length=200)
    user_code: str = Field(min_length=1, max_length=64)


class CodexDeviceLoginPollView(BaseModel):
    status: Literal["pending", "authorized"]
    # Returned once on success so the caller can save it through the normal
    # Provider create / secret-rotate path.
    api_key: str | None = None
    account_id: str | None = None
    plan_type: str | None = None


class ProviderUpdateRequest(BaseModel):
    enabled: bool | None = None
    provider_type: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, max_length=500)
    default_model: str | None = Field(default=None, min_length=1, max_length=160)
    default_image_generation_model_id: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    default_transcription_model_id: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    default_realtime_transcription_model_id: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    default_vision_model_id: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    extra_headers: dict[str, str] | None = None
    # Global template switch: on = the group template overrides every model,
    # off = each model uses its own saved snapshot or catalog defaults.
    model_defaults_enabled: bool | None = None
    provider_priority: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def require_change(self) -> "ProviderUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one provider setting is required")
        return self


class ProviderModelCapabilityUpdateRequest(BaseModel):
    # Only meaningful for the group endpoint: removes per-model overrides so
    # every discovered model inherits the group configuration again.
    apply_to_all: bool = False
    reasoning_efforts: list[Literal["low", "medium", "high", "xhigh"]] = Field(
        default_factory=list
    )
    thinking_mapping: dict[
        Literal["off", "low", "medium", "high", "xhigh"],
        str | int | bool | None,
    ] = Field(default_factory=dict)
    default_thinking_mode: Literal["off", "low", "medium", "high", "xhigh"] = "off"
    reasoning_parameter: Literal[
        "reasoning_effort",
        "reasoning.effort",
        "enable_thinking",
        "thinking_budget",
        "thinking",
    ] = "reasoning_effort"
    thinking_required: bool = False
    hosted_web_search: bool = False
    hosted_web_fetch: bool = False
    hosted_image_search: bool = False
    supports_image_input: bool = False
    supports_video_input: bool = False
    supports_structured_output: bool = False
    supports_agent_tools: bool = True
    image_input_mode: Literal["native", "external_vision", "auto"] = "auto"
    default_search_route: Literal[
        "disabled", "model_native", "external", "local", "auto"
    ] = "auto"
    capability_source: Literal[
        "user_declared", "provider_probe", "official_catalog", "runtime_observation"
    ] = "user_declared"
    context_window_tokens: int = Field(default=256_000, ge=8_000, le=10_000_000)
    context_limit_tokens: int = Field(default=204_000, ge=8_000, le=10_000_000)
    context_window_source: Literal[
        "provider", "official_catalog", "user_declared", "conservative_default"
    ] = "user_declared"
    context_window_confidence: Literal["confirmed", "inferred", "unknown"] = "unknown"
    max_output_tokens: int = Field(default=4_096, ge=1, le=1_000_000)
    chat_compaction_ratio: float = Field(default=0.8, ge=0.1, le=1.0)
    agent_compaction_ratio: float = Field(default=1 / 3, ge=0.1, le=1.0)

    @model_validator(mode="after")
    def validate_context_limits(self) -> "ProviderModelCapabilityUpdateRequest":
        if self.context_limit_tokens > self.context_window_tokens:
            raise ValueError("context_limit_tokens cannot exceed context_window_tokens")
        if self.max_output_tokens > 1_000_000:
            raise ValueError("max_output_tokens cannot exceed 1,000,000")
        return self


class ProviderModelCapabilityView(BaseModel):
    provider_id: str
    model_id: str
    capabilities: dict[str, Any]


class ProviderModelCatalogSyncRequest(BaseModel):
    model_ids: list[str] = Field(min_length=1, max_length=2_000)


class ProviderModelCatalogSyncView(BaseModel):
    provider_id: str
    models: list[ProviderModelCapabilityView]


class ProviderModelStateUpdateRequest(BaseModel):
    enabled: bool


class ProviderModelDeleteView(BaseModel):
    provider_id: str
    model_id: str
    default_model: str | None


class ProviderModelStatesUpdateRequest(BaseModel):
    states: dict[str, bool] = Field(min_length=1, max_length=2_000)


class ProviderModelStatesView(BaseModel):
    provider_id: str
    states: dict[str, bool]
    default_model: str | None


class ProviderModelStateView(BaseModel):
    provider_id: str
    model_id: str
    enabled: bool
    is_default: bool


class ProviderSecretRotateRequest(BaseModel):
    api_key: SecretStr


class WorkspaceSecretReferenceUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: SecretStr
    purpose: str = Field(default="provider_api_key", min_length=1, max_length=80)


class WorkspaceSecretReferenceView(BaseModel):
    label: str
    reference: str
    purpose: str
    secret_masked: str
    version: int
    key_provider: str
    key_version: int
    updated_at: datetime


class ProviderSecretLifecycleView(BaseModel):
    provider_id: str
    api_key_masked: str | None
    status: str
    secret_version: int
    key_version: int
    rotated_at: datetime | None
    revoked_at: datetime | None


class SecretStoreStatusView(BaseModel):
    provider: str
    available: bool
    secure_backend: bool
    backend_name: str
    active_key_version: int | None


class HostBridgeStatusView(BaseModel):
    """Frontend guidance for host-service access (whole-app Docker)."""

    deployment_profile: str
    # Effective host-access strategy: "bridge" | "direct" | "off".
    host_access_mode: str
    host_bridge_url: str | None
    auto_derived: bool
    bridge_reachable: bool | None
    # Direct mode: Docker gateway alias resolvable inside the container.
    host_gateway_reachable: bool | None
    bridge_token_ready: bool
    has_local_loopback_providers: bool
    guidance: str


class MasterKeyRotationView(BaseModel):
    provider: str
    previous_key_version: int
    active_key_version: int
    reencrypted_secrets: int


class UsageSummary(BaseModel):
    workspace_id: str
    input_tokens: int
    cached_input_tokens: int
    cache_creation_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    attempts: int
    cost_usd: float
    cost_cny: float
    unpriced_events: int
    remote_usage_recorded: bool


class UsageEventView(ORMModel):
    id: str
    workspace_id: str
    provider_id: str
    model_id: str
    feature: str
    input_tokens: int
    cached_input_tokens: int
    cache_creation_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    attempt: int
    cost_usd: float
    cost_cny: float
    cost_status: str
    price_version_id: str | None
    exchange_rate_version_id: str | None
    input_usd_per_million: float
    cached_input_usd_per_million: float
    cache_write_usd_per_million: float
    price_multiplier: float
    output_usd_per_million: float
    fixed_usd_per_call: float
    usd_cny_rate: float
    latency_ms: int
    created_at: datetime


class ManualPriceUpsertRequest(BaseModel):
    """Workspace-defined list price for a model, overriding catalog defaults."""

    model_id: str = Field(min_length=1, max_length=160)
    provider_id: str = Field(default="*", min_length=1, max_length=80)
    currency: Literal["USD", "CNY"] = "USD"
    input_per_million: float = Field(ge=0)
    cached_input_per_million: float | None = Field(default=None, ge=0)
    output_per_million: float = Field(ge=0)
    fixed_per_call: float = Field(default=0, ge=0)


class ManualPriceView(BaseModel):
    id: str
    model_id: str
    provider_id: str
    currency: str
    input_per_million: float
    cached_input_per_million: float | None
    output_per_million: float
    fixed_per_call: float
    effective_at: datetime


class PriceCatalogItem(BaseModel):
    catalog_id: str
    provider_key: str
    model_id: str
    currency: str
    native_input_per_million: float
    native_cached_input_per_million: float | None
    native_cache_write_per_million: float | None
    native_output_per_million: float
    input_usd_per_million: float
    cached_input_usd_per_million: float | None
    cache_write_usd_per_million: float | None
    output_usd_per_million: float
    conditions: dict[str, Any]
    source_url: str
    as_of: str
    source: str = "builtin"


class ModelsDevSnapshotStatus(BaseModel):
    source: str
    origin: str
    fetched_at: str | None
    provider_count: int
    model_count: int
    priced_model_count: int


class ExchangeRateInfo(BaseModel):
    """The single currently-effective USD/CNY rate used for cost conversion."""

    rate: float
    source: str
    effective_at: datetime


class ExchangeRateSetRequest(BaseModel):
    rate: float = Field(gt=0)


class AlertEmailConfigView(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: Literal["ssl", "starttls", "none"] = "ssl"
    smtp_username: str = ""
    has_password: bool = False
    from_address: str = ""
    to_addresses: list[str] = Field(default_factory=list)


class AlertEmailConfigUpdateRequest(BaseModel):
    enabled: bool = False
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Literal["ssl", "starttls", "none"] = "ssl"
    smtp_username: str = Field(default="", max_length=255)
    # None keeps the previously stored password; empty string clears it.
    smtp_password: str | None = Field(default=None, max_length=500)
    from_address: str = Field(default="", max_length=255)
    to_addresses: list[str] = Field(default_factory=list, max_length=20)


class AlertEmailTestResult(BaseModel):
    ok: bool
    detail: str


class BudgetPolicyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    provider_id: str = Field(default="*", min_length=1, max_length=80)
    model_id: str = Field(default="*", min_length=1, max_length=160)
    feature: str = Field(default="*", min_length=1, max_length=80)
    period: Literal["calendar_day_utc", "calendar_month_utc"] = (
        "calendar_month_utc"
    )
    soft_limit_cny: float | None = Field(default=None, ge=0)
    hard_limit_cny: float | None = Field(default=None, ge=0)
    limit_currency: Literal["CNY", "USD"] = "CNY"
    enabled: bool = True

    @model_validator(mode="after")
    def validate_limits(self) -> "BudgetPolicyCreateRequest":
        if self.soft_limit_cny is None and self.hard_limit_cny is None:
            raise ValueError("At least one budget limit is required")
        if (
            self.soft_limit_cny is not None
            and self.hard_limit_cny is not None
            and self.soft_limit_cny > self.hard_limit_cny
        ):
            raise ValueError("Soft budget limit cannot exceed hard limit")
        return self


class BudgetPolicyUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    soft_limit_cny: float | None = Field(default=None, ge=0)
    hard_limit_cny: float | None = Field(default=None, ge=0)
    limit_currency: Literal["CNY", "USD"] = "CNY"
    enabled: bool = True

    @model_validator(mode="after")
    def validate_limits(self) -> "BudgetPolicyUpdateRequest":
        if self.soft_limit_cny is None and self.hard_limit_cny is None:
            raise ValueError("At least one budget limit is required")
        if (
            self.soft_limit_cny is not None
            and self.hard_limit_cny is not None
            and self.soft_limit_cny > self.hard_limit_cny
        ):
            raise ValueError("Soft budget limit cannot exceed hard limit")
        return self


class BudgetPolicyView(ORMModel):
    id: str
    workspace_id: str
    name: str
    provider_id: str
    model_id: str
    feature: str
    period: str
    soft_limit_cny: float | None
    hard_limit_cny: float | None
    limit_currency: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class BudgetStatusView(BaseModel):
    policy_id: str
    name: str
    provider_id: str
    model_id: str
    feature: str
    period: str
    period_start: datetime
    period_end: datetime
    spent_cny: float
    soft_limit_cny: float | None
    hard_limit_cny: float | None
    soft_limit_cny_effective: float | None
    hard_limit_cny_effective: float | None
    spent_usd: float
    soft_limit_usd: float | None
    hard_limit_usd: float | None
    soft_exceeded: bool
    hard_exceeded: bool
    enabled: bool


class BudgetAlertView(ORMModel):
    id: str
    workspace_id: str
    policy_id: str
    level: str
    status: str
    provider_id: str
    model_id: str
    feature: str
    period_start: datetime
    period_end: datetime
    spent_cny: float
    projected_cost_cny: float
    limit_cny: float
    acknowledged_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PluginView(ORMModel):
    id: str
    workspace_id: str
    plugin_key: str
    name: str
    version: str
    plugin_type: str
    status: str
    enabled: bool
    permissions: list[str]
    capabilities: list[str]


class PluginToggleRequest(BaseModel):
    enabled: bool


class MigrationPreflightRequest(BaseModel):
    source_kind: str = Field(min_length=1, max_length=80)
    target_kind: str = Field(min_length=1, max_length=80)


class MigrationJobView(ORMModel):
    id: str
    workspace_id: str
    source_kind: str
    target_kind: str
    status: str
    report: dict[str, Any]
    created_at: datetime


class WebFetchPolicySettingValue(BaseModel):
    """Workspace-wide approvals for agent and source web fetching."""

    model_config = ConfigDict(extra="forbid")

    allow_without_confirmation: bool = Field(default=False, strict=True)
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allowed_domains")
    @classmethod
    def validate_domains(cls, domains: list[str]) -> list[str]:
        return normalize_allowed_domains(domains)


class ResearchPolicySettingValue(BaseModel):
    """Workspace-wide exact-host allowlist for search and Deep Research."""

    model_config = ConfigDict(extra="forbid")

    allowed_domains: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allowed_domains")
    @classmethod
    def validate_domains(cls, domains: list[str]) -> list[str]:
        return normalize_allowed_domains(domains)


class AccessAllowlistSettingValue(BaseModel):
    """Unified workspace allowlist covering search, web fetch, and outbound egress.

    One list is the single layer of interception control: any exact host in
    ``allowed_domains`` bypasses search/fetch source filtering and the egress
    approval queue. ``allow_all`` (opt-in) disables interception entirely for
    public hosts — private/loopback/metadata targets remain denied by the
    sandbox egress classifier.
    """

    model_config = ConfigDict(extra="forbid")

    allow_all: bool = Field(default=False, strict=True)
    allowed_domains: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("allowed_domains")
    @classmethod
    def validate_domains(cls, domains: list[str]) -> list[str]:
        return normalize_allowed_domains(domains)


def normalize_allowed_domains(domains: list[str]) -> list[str]:
    from app.domain.schemas.components import DOMAIN_PATTERN

    normalized: list[str] = []
    for domain in domains:
        candidate = domain.strip().casefold().rstrip(".")
        if (
            not candidate
            or "://" in candidate
            or "/" in candidate
            or candidate == "*"
            or not DOMAIN_PATTERN.fullmatch(candidate)
        ):
            raise ValueError(
                "allowed_domains must contain exact DNS hostnames without schemes or wildcards"
            )
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


class ChatSuggestedPromptsSettingValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(strict=True)


class ChatDictationCleanupSettingValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(strict=True)


class ChatContextUsageSettingValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(strict=True)


class ChatFeatureModelSettingValue(BaseModel):
    """Optional workspace override for a side-feature model call."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_pair(self) -> "ChatFeatureModelSettingValue":
        if (self.provider_id is None) != (self.model_id is None):
            raise ValueError("provider_id and model_id must both be set or both be null")
        return self


class FunctionalModelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=36)
    model_id: str = Field(min_length=1, max_length=160)


class FunctionalModelDefaultsSettingValue(BaseModel):
    """Workspace routing defaults for capability-specific model invocation."""

    model_config = ConfigDict(extra="forbid")

    chat: FunctionalModelTarget | None = None
    vision: FunctionalModelTarget | None = None
    transcription: FunctionalModelTarget | None = None
    image_generation: FunctionalModelTarget | None = None
    search: FunctionalModelTarget | None = None
    fetch: FunctionalModelTarget | None = None
    deep_research: FunctionalModelTarget | None = None


class ChatResponseStyleSettingValue(BaseModel):
    """Workspace chat response style (base personality + independent traits)."""

    model_config = ConfigDict(extra="forbid")

    base_style: Literal[
        "default",
        "professional",
        "friendly",
        "candid",
        "efficient",
        "exploratory",
        "quirky",
        "cynical",
    ] = "default"
    warmth: Literal[-2, -1, 0, 1, 2] = 0
    enthusiasm: Literal[-2, -1, 0, 1, 2] = 0
    headings_and_lists: Literal[-2, -1, 0, 1, 2] = 0
    emoji: Literal[-2, -1, 0, 1, 2] = 0
    verbosity: Literal[-2, -1, 0, 1, 2] = 0


class ChatDefaultResponseModeSettingValue(BaseModel):
    """Workspace default for the chat composer response mode."""

    model_config = ConfigDict(extra="forbid")

    response_mode: Literal["fast", "thinking", "agentic"] = "agentic"


class SettingUpdateRequest(BaseModel):
    value: Any


class SettingView(ORMModel):
    key: str
    value: Any
    updated_at: datetime
