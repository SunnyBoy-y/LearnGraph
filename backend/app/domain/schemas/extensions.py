from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.domain.schemas.common import ORMModel


MCPPermission = Literal[
    "network",
    "file.read",
    "file.write",
    "model.invoke",
    "user_data.read",
    "user_data.write",
    "graph.read",
    "graph.write",
    "roadmap.read",
    "roadmap.write",
    "learning.read",
    "learning.write",
    "usage.read",
    "usage.write",
    "sandbox.execute",
]
BuiltinSkillTool = Literal[
    "builtin.review.list_due",
    "builtin.graph.read",
    "builtin.graph.update_candidate_node",
    "builtin.roadmap.read",
    "builtin.roadmap.replan",
    "builtin.action.list",
    "builtin.action.create",
    "builtin.action.update",
    "builtin.learning.mastery.read",
    "builtin.learning.evidence.record",
    "builtin.usage.summary",
    "builtin.usage.budget.create",
    "builtin.usage.budget.update",
]
BuiltinSkillPermission = Literal[
    "mastery.read",
    "graph.read",
    "graph.write",
    "roadmap.read",
    "roadmap.write",
    "learning.read",
    "learning.write",
    "usage.read",
    "usage.write",
]


class MCPServerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    identity: str = Field(min_length=1, max_length=200)
    requested_tools: list[str] = Field(min_length=1, max_length=100)
    permissions: list[MCPPermission] = Field(default_factory=list, max_length=20)
    requested_resources: list[str] = Field(default_factory=list, max_length=100)
    requested_prompts: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("requested_tools")
    @classmethod
    def validate_tool_names(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for name in value:
            stripped = name.strip()
            if not stripped or len(stripped) > 128:
                raise ValueError("MCP tool names must contain 1 to 128 characters")
            if not all(character.isascii() and (character.isalnum() or character in "._-") for character in stripped):
                raise ValueError("MCP tool names may contain only ASCII letters, digits, '.', '_' and '-'")
            if stripped not in normalized:
                normalized.append(stripped)
        return normalized

    @field_validator("permissions")
    @classmethod
    def unique_permissions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class MCPServerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    display_name: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=80)
    transport: Literal["streamable_http", "stdio"]
    endpoint_url: str | None = Field(default=None, max_length=1000)
    bearer_token: SecretStr | None = None
    manifest: MCPServerManifest
    agent_auto_invoke: bool = False
    timeout_ms: int = Field(default=5_000, ge=100, le=30_000)
    max_input_bytes: int = Field(default=64 * 1024, ge=1_024, le=256 * 1024)
    max_result_bytes: int = Field(default=256 * 1024, ge=1_024, le=1024 * 1024)
    max_concurrency: int = Field(default=1, ge=1, le=8)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> "MCPServerCreateRequest":
        if self.transport == "streamable_http" and not (self.endpoint_url or "").strip():
            raise ValueError("streamable_http requires endpoint_url")
        if self.transport == "stdio" and self.endpoint_url is not None:
            raise ValueError("stdio does not accept an endpoint or command in the host process")
        if self.transport == "stdio" and self.bearer_token is not None:
            raise ValueError("stdio does not use an HTTP bearer token")
        return self


class MCPServerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=80)
    endpoint_url: str | None = Field(default=None, max_length=1000)
    bearer_token: SecretStr | None = None
    clear_bearer_token: bool = False
    manifest: MCPServerManifest
    agent_auto_invoke: bool | None = None
    timeout_ms: int | None = Field(default=None, ge=100, le=30_000)
    max_input_bytes: int | None = Field(default=None, ge=1_024, le=256 * 1024)
    max_result_bytes: int | None = Field(default=None, ge=1_024, le=1024 * 1024)
    max_concurrency: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def validate_secret_change(self) -> "MCPServerUpdateRequest":
        if self.bearer_token is not None and self.clear_bearer_token:
            raise ValueError("bearer_token and clear_bearer_token are mutually exclusive")
        return self


class MCPServerView(ORMModel):
    id: str
    workspace_id: str
    server_key: str
    display_name: str
    source: str
    version: str
    transport: str
    endpoint_url: str | None
    auth_configured: bool = False
    auth_masked: str | None = None
    manifest_json: dict[str, Any]
    manifest_hash: str
    requested_tools: list[str]
    required_permissions: list[str]
    status: str
    enabled: bool
    agent_auto_invoke: bool
    timeout_ms: int
    max_input_bytes: int
    max_result_bytes: int
    max_concurrency: int
    current_snapshot_id: str | None
    authorization_generation: int
    last_error: str | None
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MCPCapabilitySnapshotView(ORMModel):
    id: str
    workspace_id: str
    server_id: str
    sequence: int
    protocol_version: str
    server_identity: dict[str, Any]
    capabilities: dict[str, Any]
    tools: list[dict[str, Any]]
    resources: list[dict[str, Any]]
    prompts: list[dict[str, Any]]
    required_permissions: list[str]
    snapshot_hash: str
    changed: bool
    reauthorization_required: bool
    created_at: datetime


class MCPRefreshResponse(BaseModel):
    server: MCPServerView
    snapshot: MCPCapabilitySnapshotView


class PermissionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow_once", "always", "deny"]
    permissions: list[str] = Field(default_factory=list, max_length=150)
    reason: str = Field(default="", max_length=1000)


class PermissionGrantView(ORMModel):
    id: str
    workspace_id: str
    subject_type: str
    subject_id: str
    decision: str
    status: str
    permissions: list[str]
    authorization_hash: str
    decided_by: str
    reason: str
    consumed_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class SkillDeleteRequestView(ORMModel):
    id: str
    workspace_id: str
    skill_id: str
    skill_key: str
    skill_name: str
    requested_by: str
    required_user_id: str
    status: str
    expires_at: datetime
    confirmed_at: datetime | None
    created_at: datetime


class SkillDeleteConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_text: str = Field(min_length=1, max_length=160)
    current_password: SecretStr


class MCPInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SkillStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: BuiltinSkillTool
    arguments: dict[str, Any] = Field(default_factory=dict)


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["declarative_review", "declarative_workflow"]
    instructions_markdown: str = Field(min_length=1, max_length=20_000)
    required_tools: list[BuiltinSkillTool] = Field(min_length=1, max_length=10)
    permissions: list[BuiltinSkillPermission] = Field(min_length=1, max_length=12)
    allowed_components: list[str] = Field(default_factory=list, max_length=20)
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "additionalProperties": False,
        }
    )
    steps: list[SkillStep] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_declared_tools(self) -> "SkillManifest":
        step_tools = list(dict.fromkeys(step.tool for step in self.steps))
        declared = list(dict.fromkeys(self.required_tools))
        if step_tools != declared:
            raise ValueError("required_tools must exactly match tools used by declarative steps")
        return self


class SkillCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    name: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=80)
    generated_by: Literal["user_import", "agent", "builtin"] = "user_import"
    auto_enable_requested: bool = False
    manifest: SkillManifest


class SkillUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=80)
    manifest: SkillManifest


class SkillView(ORMModel):
    id: str
    workspace_id: str
    skill_key: str
    name: str
    source: str
    version: str
    generated_by: str
    kind: str = "declarative_review"
    package_format: str = "declarative_json"
    content_hash: str = ""
    origin_type: str = "user_import"
    origin_ref: str = ""
    origin_hash: str = ""
    has_scripts: bool = False
    locale_source: str = ""
    is_official: bool = False
    manifest_json: dict[str, Any]
    manifest_hash: str
    instructions_markdown: str
    required_tools: list[str]
    required_permissions: list[str]
    allowed_components: list[str]
    validation_report: dict[str, Any]
    status: str
    enabled: bool
    authorization_generation: int
    created_at: datetime
    updated_at: datetime


class SkillPackageCreateRequest(BaseModel):
    """Create an empty agent_skill_package (SKILL.md template)."""

    model_config = ConfigDict(extra="forbid")

    skill_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    source: str = Field(default="user_created", min_length=1, max_length=255)
    version: str = Field(default="1.0.0", min_length=1, max_length=80)
    with_sample_script: bool = False


class SkillFileEntryView(BaseModel):
    relative_path: str
    size_bytes: int
    mime_type: str
    is_directory: bool
    blob_sha256: str = ""
    updated_at: datetime | None = None


class SkillFileTreeView(BaseModel):
    skill_id: str
    content_hash: str
    has_scripts: bool
    files: list[SkillFileEntryView]


class SkillFileContentView(BaseModel):
    relative_path: str
    content: str
    encoding: Literal["utf-8"] = "utf-8"
    size_bytes: int
    mime_type: str
    blob_sha256: str
    content_hash: str


class SkillFileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=2_000_000)
    expected_content_hash: str | None = Field(default=None, max_length=64)


class SkillFileWriteResponse(BaseModel):
    skill: SkillView
    file: SkillFileContentView
    reauthorization_required: bool


class SkillMkdirRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1, max_length=500)


class SkillValidateResponse(BaseModel):
    skill_id: str
    ok: bool
    content_hash: str
    has_scripts: bool
    issues: list[str]
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class SkillSandboxRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_path: str = Field(min_length=1, max_length=500)
    chat_session_id: str | None = Field(default=None, max_length=36)
    argv_extra: list[str] = Field(default_factory=list, max_length=16)


class SkillSandboxRunResponse(BaseModel):
    status: str
    available: bool
    skill_id: str
    script_path: str
    content_hash: str
    chat_session_id: str | None = None
    sandbox_session_id: str | None = None
    command_id: str | None = None
    argv_redacted: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    timed_out: bool = False
    latency_ms: int = 0
    stdout_summary: str = ""
    stderr_summary: str = ""
    error_code: str | None = None
    error_message: str | None = None
    invocation_id: str | None = None


class SkillMarketCardView(BaseModel):
    market_id: str
    slug: str
    name: str
    source: str
    description: str
    install_url: str
    homepage_url: str
    installs: int
    source_type: str
    origin_hash: str
    fetch_status: str
    fetch_error: str | None = None
    fetched_at: datetime | None = None
    rank: int
    file_count: int = 0
    has_scripts: bool = False
    official: bool = False


class SkillMarketListResponse(BaseModel):
    source: str
    refreshed_at: datetime | None = None
    cards: list[SkillMarketCardView]
    page: int = 1
    page_size: int = 12
    total: int = 0
    total_pages: int = 0
    query: str = ""


class SkillMarketInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_id: str = Field(min_length=1, max_length=200)
    skill_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")


class SkillManualImportFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    contents: str = Field(default="", max_length=2_000_000)


class SkillManualImportRequest(BaseModel):
    """Import a skill package from user-edited files (SKILL.md required)."""

    model_config = ConfigDict(extra="forbid")

    skill_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    name: str | None = Field(default=None, min_length=1, max_length=160)
    source: str = Field(default="manual_import", min_length=1, max_length=255)
    version: str = Field(default="1.0.0", min_length=1, max_length=80)
    files: list[SkillManualImportFile] = Field(min_length=1, max_length=200)


class SkillArchiveImportRequest(BaseModel):
    """Import a skill package from an uploaded zip archive (base64-encoded).

    Only UTF-8 text entries are imported; binary or oversized entries are
    skipped and reported. The archive itself never touches the host shell.
    """

    model_config = ConfigDict(extra="forbid")

    # ~20 MB of zip data after base64 expansion (4/3 ratio).
    archive_base64: str = Field(min_length=8, max_length=28_000_000)
    filename: str = Field(default="", max_length=255)
    skill_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    name: str | None = Field(default=None, min_length=1, max_length=160)


class SkillNpxImportRequest(BaseModel):
    """Install skills by parsing an ``npx skills add …`` command server-side.

    LearnGraph never runs npx or any host shell; the command is parsed and the
    referenced skills are installed through the commit-pinned GitHub importer.
    """

    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=3, max_length=1000)
    skill_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")


class SkillNpxSkippedItem(BaseModel):
    target: str
    reason: str


class SkillNpxImportResponse(BaseModel):
    reference: str
    owner: str
    repo: str
    commit: str
    requested_skills: list[str] = Field(default_factory=list)
    installed: list[SkillView] = Field(default_factory=list)
    skipped: list[SkillNpxSkippedItem] = Field(default_factory=list)


class SkillLocalProbePolicyView(BaseModel):
    enabled: bool
    allowed_roots: list[str]
    same_host_available: bool
    unavailable_reason: str | None = None
    last_scanned_at: datetime | None = None
    last_scan_summary: dict[str, Any] = Field(default_factory=dict)
    candidate_roots: list[dict[str, Any]] = Field(default_factory=list)


class SkillLocalProbePolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    allowed_roots: list[str] = Field(default_factory=list, max_length=20)


class SkillLocalProbeItem(BaseModel):
    root_label: str
    root_path: str
    skill_key: str
    name: str
    description: str
    relative_dir: str
    has_scripts: bool
    skill_md_present: bool


class SkillLocalProbeScanResponse(BaseModel):
    available: bool
    unavailable_reason: str | None = None
    scanned_roots: list[str] = Field(default_factory=list)
    items: list[SkillLocalProbeItem] = Field(default_factory=list)


class SkillLocalImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_path: str = Field(min_length=1, max_length=1000)
    relative_dir: str = Field(min_length=1, max_length=500)
    skill_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")


class SkillTranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_locale: str = Field(min_length=2, max_length=32)
    source_path: str = Field(default="SKILL.md", min_length=1, max_length=500)
    force: bool = False


class SkillTranslateResponse(BaseModel):
    skill_id: str
    source_path: str
    content_hash: str
    target_locale: str
    translator_model_id: str
    cached: bool
    translated_text: str
    usage_event_id: str | None = None


class SkillInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict)


class ExtensionRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class ExtensionInvocationView(ORMModel):
    id: str
    workspace_id: str
    target_type: str
    target_id: str
    skill_id: str | None
    tool_name: str
    status: str
    grant_id: str | None
    authorization_hash: str
    input_json: dict[str, Any]
    input_size_bytes: int
    input_hash: str
    result_json: dict[str, Any]
    result_size_bytes: int
    result_hash: str
    timeout_ms: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TransportCapabilityView(BaseModel):
    transport: Literal["streamable_http", "stdio"]
    available: bool
    protocol_version: str | None
    supports_real_execution: bool
    supports_encrypted_bearer_reference: bool
    reason: str


class BuiltinToolView(BaseModel):
    """A first-party, declarative tool that a Skill or Agent may invoke."""

    tool: str
    function_name: str
    description: str
    parameters: dict[str, Any]
    permissions: list[str]


class ExternalCatalogSourceView(BaseModel):
    """One configured external skill/MCP discovery catalog."""

    id: str
    label: str
    kind: Literal["skill", "mcp"]
    enabled: bool
    base_url: str
    auth_required: bool = False
    notes: str = ""


class ExternalSkillSearchItem(BaseModel):
    catalog: str
    external_id: str
    name: str
    description: str = ""
    version: str = ""
    owner: str = ""
    homepage_url: str = ""
    install_hint: str = ""
    trust: dict[str, Any] = Field(default_factory=dict)


class ExternalSkillSearchResponse(BaseModel):
    catalog: str
    query: str
    items: list[ExternalSkillSearchItem] = Field(default_factory=list)


class McpRegistrySearchItem(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    version: str = ""
    status: str = ""
    repository_url: str = ""
    website_url: str = ""
    endpoint_url: str | None = None
    transport: str | None = None
    packages: list[str] = Field(default_factory=list)
    # Required environment variables declared by the server's install packages.
    env_hints: list[str] = Field(default_factory=list)
    # True only when the entry exposes a Streamable HTTP remote we can register.
    supported: bool = False
    unsupported_reason: str = ""


class McpRegistrySearchResponse(BaseModel):
    registry_url: str
    query: str
    items: list[McpRegistrySearchItem] = Field(default_factory=list)
    next_cursor: str | None = None


class SkillGitHubPreviewRequest(BaseModel):
    """Resolve a GitHub reference and list installable Agent Skill directories."""

    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=3, max_length=500)


class SkillGitHubCandidate(BaseModel):
    path: str
    name: str = ""
    description: str = ""
    license: str = ""
    allowed_tools: str = ""
    file_count: int = 0
    total_size_bytes: int = 0
    has_scripts: bool = False
    skipped_file_count: int = 0
    # Permission preview shown before install/authorization.
    required_permissions: list[str] = Field(default_factory=list)
    # Quick static scan over SKILL.md (full-package scan runs at install).
    scan_risk: str = ""
    scan_finding_count: int = 0


class SkillGitHubPreviewResponse(BaseModel):
    owner: str
    repo: str
    ref: str
    commit: str
    tree_truncated: bool = False
    candidates: list[SkillGitHubCandidate] = Field(default_factory=list)


class SkillGitHubInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=3, max_length=500)
    path: str | None = Field(default=None, max_length=500)
    # Pin to the exact commit shown in preview; re-resolved when omitted.
    commit: str | None = Field(default=None, min_length=7, max_length=64)
    skill_key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")


class SkillUpdateCheckResponse(BaseModel):
    skill_id: str
    supported: bool
    current_commit: str = ""
    latest_commit: str = ""
    update_available: bool = False
    checked_ref: str = ""
    message: str = ""


class SkillSecurityFindingView(BaseModel):
    severity: str
    category: str
    path: str
    pattern: str = ""
    explanation: str = ""
    excerpt: str = ""


class SkillSecurityScanResponse(BaseModel):
    skill_id: str
    risk_level: str
    finding_count: int
    counts: dict[str, int] = Field(default_factory=dict)
    findings: list[SkillSecurityFindingView] = Field(default_factory=list)
    scanned_files: int = 0
    content_hash: str = ""


class SkillSemanticReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class SkillSemanticReviewResponse(BaseModel):
    skill_id: str
    cached: bool
    content_hash: str
    verdict: str
    risk_score: int = 0
    reasons: list[str] = Field(default_factory=list)
    summary: str = ""
    model_id: str = ""
