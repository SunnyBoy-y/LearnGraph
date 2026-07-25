from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

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
    zone: Literal["hot", "recent", "topics", "archive"] = "topics"
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
    provider_id: str
    provider_binding_id: str | None
    deleted_at: datetime | None
    recoverable_until: datetime | None
    content_destroyed_at: datetime | None
    restore_available: bool
    created_at: datetime
    updated_at: datetime
    content: str | None = None
    retrieval_score: float | None = None


class MemoryDraftCreateRequest(BaseModel):
    operation: Literal[
        "CREATE",
        "UPDATE",
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
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    session_enabled: bool | None = None

    @model_validator(mode="after")
    def require_policy_change(self) -> "MemoryPolicyUpdateRequest":
        if self.workspace_enabled is None and self.session_enabled is None:
            raise ValueError("A workspace or session policy change is required")
        if self.session_enabled is not None and self.session_id is None:
            raise ValueError("session_id is required for session policy changes")
        return self


class MemoryPolicyView(BaseModel):
    workspace_id: str
    workspace_enabled: bool
    session_id: str | None
    session_enabled: bool | None
    effective_enabled: bool


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
        "model", "image_generation", "vision", "search", "fetch", "deep_research", "memory", "transcription"
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
    granted_balance: str
    topped_up_balance: str


class ProviderBalanceView(BaseModel):
    provider_id: str
    is_available: bool
    balance_infos: list[ProviderBalanceInfoView]
    queried_at: datetime


class ProviderUpdateRequest(BaseModel):
    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=500)
    default_model: str | None = Field(default=None, min_length=1, max_length=160)
    default_image_generation_model_id: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    default_transcription_model_id: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    default_vision_model_id: str | None = Field(
        default=None, min_length=1, max_length=160
    )
    extra_headers: dict[str, str] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ProviderUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one provider setting is required")
        return self


class ProviderModelCapabilityUpdateRequest(BaseModel):
    reasoning_efforts: list[Literal["low", "medium", "high", "xhigh"]] = Field(
        default_factory=list
    )
    thinking_mapping: dict[
        Literal["off", "low", "medium", "high", "xhigh"], str | None
    ] = Field(default_factory=dict)
    default_thinking_mode: Literal["off", "low", "medium", "high", "xhigh"] = "off"
    reasoning_parameter: Literal["reasoning_effort", "reasoning.effort"] = "reasoning_effort"
    hosted_web_search: bool = False
    supports_image_input: bool = False
    image_input_mode: Literal["native", "external_vision", "auto"] = "auto"
    default_search_route: Literal[
        "disabled", "model_native", "external", "local", "auto"
    ] = "disabled"
    capability_source: Literal[
        "user_declared", "provider_probe", "official_catalog", "runtime_observation"
    ] = "user_declared"
    context_window_tokens: int = Field(default=256_000, ge=8_000, le=10_000_000)
    context_limit_tokens: int = Field(default=256_000, ge=8_000, le=10_000_000)
    max_output_tokens: int = Field(default=4_096, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_context_limits(self) -> "ProviderModelCapabilityUpdateRequest":
        if self.context_limit_tokens > self.context_window_tokens:
            raise ValueError("context_limit_tokens cannot exceed context_window_tokens")
        if self.max_output_tokens >= self.context_limit_tokens:
            raise ValueError("max_output_tokens must be below context_limit_tokens")
        return self


class ProviderModelCapabilityView(BaseModel):
    provider_id: str
    model_id: str
    capabilities: dict[str, Any]


class ProviderModelStateUpdateRequest(BaseModel):
    enabled: bool


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


class MasterKeyRotationView(BaseModel):
    provider: str
    previous_key_version: int
    active_key_version: int
    reencrypted_secrets: int


class UsageSummary(BaseModel):
    workspace_id: str
    input_tokens: int
    cached_input_tokens: int
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
    price_multiplier: float
    output_usd_per_million: float
    fixed_usd_per_call: float
    usd_cny_rate: float
    latency_ms: int
    created_at: datetime


class PriceVersionCreateRequest(BaseModel):
    provider_id: str = Field(default="*", min_length=1, max_length=80)
    model_id: str = Field(default="*", min_length=1, max_length=160)
    feature: str = Field(default="*", min_length=1, max_length=80)
    input_usd_per_million: float = Field(default=0, ge=0)
    cached_input_usd_per_million: float | None = Field(default=None, ge=0)
    cache_write_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float = Field(default=0, ge=0)
    fixed_usd_per_call: float = Field(default=0, ge=0)
    currency: Literal["USD", "CNY"] = "USD"
    input_cny_per_million: float | None = Field(default=None, ge=0)
    cached_input_cny_per_million: float | None = Field(default=None, ge=0)
    cache_write_cny_per_million: float | None = Field(default=None, ge=0)
    output_cny_per_million: float | None = Field(default=None, ge=0)
    fixed_cny_per_call: float | None = Field(default=None, ge=0)
    effective_at: datetime | None = None
    source: str = Field(default="workspace_manual", min_length=1, max_length=160)
    conditions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_native_currency_rates(self) -> "PriceVersionCreateRequest":
        if self.currency == "CNY" and (
            self.input_cny_per_million is None
            or self.output_cny_per_million is None
        ):
            raise ValueError("CNY pricing requires input_cny_per_million and output_cny_per_million")
        return self


class VersionRetireRequest(BaseModel):
    retired_at: datetime | None = None


class PriceVersionView(ORMModel):
    id: str
    workspace_id: str
    provider_id: str
    model_id: str
    feature: str
    version: int
    input_usd_per_million: float
    cached_input_usd_per_million: float | None
    cache_write_usd_per_million: float | None
    output_usd_per_million: float
    fixed_usd_per_call: float
    effective_at: datetime
    retired_at: datetime | None
    source: str
    conditions: dict[str, Any]
    created_at: datetime


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


class PriceCatalogApplyRequest(BaseModel):
    catalog_id: str = Field(min_length=1, max_length=40)
    provider_id: str | None = Field(default=None, min_length=1, max_length=80)
    feature: str = Field(default="*", min_length=1, max_length=80)
    input_usd_per_million: float | None = Field(default=None, ge=0)
    cached_input_usd_per_million: float | None = Field(default=None, ge=0)
    cache_write_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)


class ExchangeRateCreateRequest(BaseModel):
    base_currency: Literal["USD"] = "USD"
    quote_currency: Literal["CNY"] = "CNY"
    rate: float = Field(gt=0)
    effective_at: datetime | None = None
    source: str = Field(default="workspace_manual", min_length=1, max_length=160)


class ExchangeRateVersionView(ORMModel):
    id: str
    workspace_id: str
    base_currency: str
    quote_currency: str
    version: int
    rate: float
    effective_at: datetime
    retired_at: datetime | None
    source: str
    created_at: datetime


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


class ChatSuggestedPromptsSettingValue(BaseModel):
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


class SettingUpdateRequest(BaseModel):
    value: Any


class SettingView(ORMModel):
    key: str
    value: Any
    updated_at: datetime
