from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.schemas.common import ORMModel


class SandboxProfileView(BaseModel):
    backend_id: str
    runtime_kind: str = "python-node"
    platform: str
    available: bool
    capabilities: list[str]
    reason: str | None = None
    image_pinned: bool


class SandboxBootstrapStatusView(BaseModel):
    docker_installed: bool
    docker_reachable: bool
    docker_detail: str | None = None
    sandbox_enabled: bool
    image_ready: bool
    image_digest: str | None = None
    browser_image_ready: bool = False
    browser_image_digest: str | None = None
    image_source: str | None = None
    # Whether the deployment configured a prebuilt image
    # (LEARNGRAPH_SANDBOX_PREBUILT_IMAGE or the settings page source config),
    # enabling pull-only bootstrap.
    prebuilt_image_configured: bool = False
    prebuilt_image_ref: str | None = None
    # Effective image source strategy: auto | prebuilt | build.
    bootstrap_mode: str = "auto"
    phase: str
    progress_percent: float
    message: str
    detail: str | None = None
    can_initialize: bool
    # Whether ordinary workspace members may trigger the bootstrap; defaults
    # to True and can be restricted by administrators via the policy endpoint.
    member_bootstrap_allowed: bool = True
    bootstrap_policy: dict[str, Any] | None = None
    active_job: dict[str, Any] | None = None
    last_failed_job: dict[str, Any] | None = None
    remediation_steps: list[str] = Field(default_factory=list)


class SandboxAgentReadinessView(BaseModel):
    available: bool
    code: str | None = None
    message: str
    authorized: bool
    sandbox_enabled: bool
    agent_enabled: bool
    backend_id: str
    platform: str
    capabilities: list[str] = Field(default_factory=list)
    remediation_steps: list[str] = Field(default_factory=list)


class SandboxBootstrapJobView(BaseModel):
    job_id: str
    phase: str
    progress_percent: float
    message: str
    detail: str | None = None
    status: str
    # Bootstrap source requested by the actor: auto | prebuilt | build.
    mode: str = "auto"
    image_digest: str | None = None
    browser_image_digest: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    log_tail: list[str] = Field(default_factory=list)
    log_seq: int = 0
    started_at: float
    finished_at: float | None = None


class SandboxBootstrapStartResponse(BaseModel):
    accepted: bool
    joined_existing: bool = False
    error_code: str | None = None
    error_message: str | None = None
    job: SandboxBootstrapJobView | None = None
    status: SandboxBootstrapStatusView


class SandboxBootstrapStartRequest(BaseModel):
    """Bootstrap source selection.

    - auto: pull the prebuilt image when configured, otherwise build locally.
    - prebuilt: require the deployment-configured prebuilt image.
    - build: force a local Docker build even when a prebuilt image is set.
    """

    mode: Literal["auto", "prebuilt", "build"] = "auto"


class SandboxBootstrapPolicyView(BaseModel):
    member_allowed: bool
    persisted: bool = False
    updated_at: str | None = None
    updated_by: str | None = None


class SandboxBootstrapPolicyUpdateRequest(BaseModel):
    member_allowed: bool


class SandboxBootstrapSourceView(BaseModel):
    """Page-configurable deployment image source (auto | prebuilt | build)."""

    mode: Literal["auto", "prebuilt", "build"] = "auto"
    effective_mode: Literal["auto", "prebuilt", "build"] = "auto"
    prebuilt_image: str | None = None
    env_prebuilt_image: str | None = None
    persisted: bool = False
    updated_at: str | None = None
    updated_by: str | None = None


class SandboxBootstrapSourceUpdateRequest(BaseModel):
    mode: Literal["auto", "prebuilt", "build"]
    prebuilt_image: str | None = Field(default=None, max_length=300)


class SandboxTaskCreateRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    file_id: str = Field(min_length=1, max_length=36)
    task_type: Literal["file_inspect", "extract_inert_text"]
    output_format: Literal["metadata_json", "text_bundle"] = "metadata_json"
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxTaskView(ORMModel):
    id: str
    workspace_id: str
    sandbox_session_id: str
    chat_session_id: str
    file_id: str
    task_type: str
    output_format: str
    status: str
    artifact_json: dict[str, Any]
    error_class: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class SandboxSessionView(ORMModel):
    id: str
    workspace_id: str
    owner_user_id: str
    chat_session_id: str
    backend_id: str
    manifest_hash: str
    policy_revision: str
    runtime_kind: str
    lifecycle_state: str
    status: str
    resource_limits: dict[str, Any]
    network_policy: dict[str, Any]
    last_used_at: datetime
    expires_at: datetime
    runtime_started_at: datetime | None
    runtime_last_used_at: datetime | None
    workspace_expires_at: datetime
    absolute_expires_at: datetime
    cleanup_status: str
    cleanup_error_class: str | None
    created_at: datetime


class SandboxExecutionView(ORMModel):
    id: str
    sandbox_session_id: str
    task_id: str
    attempt_no: int
    argv_redacted: list[str]
    cwd_relative: str
    status: str
    exit_code: int | None
    error_class: str | None
    timed_out: bool
    latency_ms: int
    resource_usage: dict[str, Any]
    stdout_summary: str
    stderr_summary: str
    truncated: bool
    created_at: datetime


class SandboxAgentSessionCreateRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    runtime: Literal["python-node", "python-node-browser"] = "python-node"


class SandboxAgentCommandRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    # The command is deliberately an argv array.  The service applies the
    # policy and never invokes a shell or evaluates a command string.
    argv: list[str] = Field(min_length=1, max_length=32)
    cwd: str = Field(default=".", min_length=1, max_length=255)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    runtime: Literal["python-node", "python-node-browser"] = "python-node"


class SandboxAgentFileWriteRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=0, max_length=1_048_576)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentFileAppendRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=1_048_576)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentFileEditRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    old_string: str = Field(min_length=1, max_length=1_048_576)
    new_string: str = Field(max_length=1_048_576)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # When true, replace every occurrence instead of requiring a single match
    # (safety cap of 100 replacements; bulk rewrites belong in sandbox_exec).
    replace_all: bool = False
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentEnvironmentRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentImagePublishRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    alt: str | None = Field(default=None, max_length=500)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentFileReadRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    # Optional line-range view: read only lines [start_line, end_line] (1-based,
    # inclusive). Combined with max_chars this keeps large files out of the
    # model context. When omitted the whole file is returned.
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_chars: int | None = Field(default=None, ge=1, le=1_048_576)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentFileGrepRequest(BaseModel):
    """Host-side content search over the durable session workspace."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    pattern: str = Field(min_length=1, max_length=500)
    # Optional glob filter over workspace paths, e.g. "work/**/*.py" or
    # "work/main.py". Omit to search every durable workspace file.
    path: str | None = Field(default=None, max_length=255)
    case_sensitive: bool = False
    context_lines: int = Field(default=0, ge=0, le=5)
    max_matches: int = Field(default=50, ge=1, le=500)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentFileDeleteRequest(BaseModel):
    """Delete ONE file under the session work/ tree (single-use authorization)."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentFileListRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    # Optional glob filter over workspace paths (e.g. "work/**/*.py").
    pattern: str | None = Field(default=None, max_length=255)
    max_results: int | None = Field(default=None, ge=1, le=1000)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentVideoInfoRequest(BaseModel):
    """Read safe metadata for a video registered as a session input."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentTranscribeRequest(BaseModel):
    """Host-side bridge: transcribe a workspace audio file with the user's ASR
    Provider.  Credentials and network access stay on the host; the sandbox
    remains offline."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    output_path: str | None = Field(default=None, min_length=1, max_length=255)
    language: str | None = Field(default=None, min_length=2, max_length=16)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentWorkspaceFileView(BaseModel):
    path: str
    size_bytes: int
    role: str | None = None
    source: str | None = None
    mtime: str | None = None


class SandboxAgentFileView(BaseModel):
    sandbox_session_id: str
    path: str
    size_bytes: int
    sha256: str | None = None
    content: str | None = None
    files: list[SandboxAgentWorkspaceFileView] = Field(default_factory=list)
    # read_file line-range view metadata.
    total_lines: int | None = None
    total_bytes: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    # max_chars truncation flag for read_file; match cap for grep.
    truncated: bool | None = None
    # edit_file replace_all outcome.
    replaced_count: int | None = None
    # delete_file outcome.
    deleted: bool | None = None


class SandboxAgentFileGrepMatch(BaseModel):
    path: str
    line_number: int
    text: str
    context: list[dict[str, Any]] = Field(default_factory=list)


class SandboxAgentFileGrepCount(BaseModel):
    path: str
    matches: int


class SandboxAgentFileGrepView(BaseModel):
    sandbox_session_id: str
    pattern: str
    case_sensitive: bool
    searched_files: int
    skipped_binary: int
    skipped_large: int
    skipped_container_only: int
    matches: list[SandboxAgentFileGrepMatch] = Field(default_factory=list)
    file_counts: list[SandboxAgentFileGrepCount] = Field(default_factory=list)
    truncated: bool = False


class SandboxAgentCommandView(ORMModel):
    id: str
    workspace_id: str
    sandbox_session_id: str
    chat_session_id: str
    job_id: str | None = None
    argv_redacted: list[str]
    cwd_relative: str
    status: str
    exit_code: int | None
    error_class: str | None
    error_message: str | None
    timed_out: bool
    latency_ms: int
    resource_usage: dict[str, Any]
    stdout_summary: str
    stderr_summary: str
    truncated: bool
    created_at: datetime
    updated_at: datetime


class SandboxJobView(BaseModel):
    """Public view of a queued/running sandbox job (unified scheduler)."""

    id: str
    workspace_id: str
    owner_user_id: str
    chat_session_id: str
    kind: str
    workload_class: str
    status: str
    reason: str | None = None
    priority: int = 0
    attempt: int = 0
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline_at: datetime | None = None
    error_class: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class SandboxJobSubmitView(BaseModel):
    """HTTP 202 payload returned when a job is queued (or already terminal)."""

    job_id: str
    status: str
    reason: str | None = None
    retry_after_seconds: int = 5


class SessionWorkspaceEntryView(BaseModel):
    id: str
    chat_session_id: str
    sandbox_session_id: str | None = None
    path: str
    role: str
    blob_sha256: str
    file_id: str | None = None
    size_bytes: int
    mime_type: str
    source: str
    created_at: str | None = None
    updated_at: str | None = None


class SessionWorkspaceListResponse(BaseModel):
    chat_session_id: str
    entries: list[SessionWorkspaceEntryView]


class SessionWorkspacePublishRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=1_048_576)
    title: str | None = Field(default=None, max_length=255)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SessionWorkspacePublishResponse(BaseModel):
    id: str | None = None
    chat_session_id: str
    sandbox_session_id: str | None = None
    path: str
    role: str | None = None
    blob_sha256: str | None = None
    file_id: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    source: str | None = None
    title: str | None = None
    download_path: str | None = None
    part: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SandboxWebAppValidateRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    output_root: str = Field(default="dist", min_length=1, max_length=255)
    entry_path: str = Field(default="dist/index.html", min_length=1, max_length=255)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxWebAppValidationView(BaseModel):
    validation_id: str
    status: str
    manifest_sha256: str
    entry_path: str
    file_count: int
    size_bytes: int
    report: dict[str, Any]


class SandboxWebAppPublishRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    validation_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=255)
    preferred_height: int | None = Field(default=None, ge=160, le=900)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxWebAppPublishView(BaseModel):
    bundle_id: str
    title: str
    entry_path: str
    manifest_sha256: str
    status: str
    part: dict[str, Any]


class SubAppBundlePreviewView(BaseModel):
    bundle_id: str
    expires_at: datetime
    url: str


class SandboxPreviewConfigView(BaseModel):
    origin: str | None = None
    source: str  # persisted | env | auto | none
    persisted: bool = False
    updated_at: str | None = None
    updated_by: str | None = None


class SandboxPreviewConfigUpdateRequest(BaseModel):
    origin: str = Field(min_length=1, max_length=255)


class SandboxDestructiveGrantRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    path_prefix: str = Field(min_length=1, max_length=255)
    action: Literal["delete_path"] = "delete_path"
    sandbox_session_id: str = Field(min_length=1, max_length=36)
    command_intent_digest: str = Field(min_length=64, max_length=64)
    ttl_seconds: int = Field(default=300, ge=60, le=1_800)
    reason: str = Field(default="", max_length=500)


class SandboxDestructiveGrantView(BaseModel):
    id: str
    chat_session_id: str
    sandbox_session_id: str | None = None
    action: str
    path_prefix: str
    status: str
    granted_by: str
    expires_at: datetime
    reason: str


# ── Sandbox toolkit (bash / todo / patch / git / search / fetch / subagent /
#    skills / notebook) ────────────────────────────────────────────────────────


class SandboxAgentBashRequest(BaseModel):
    """Run a shell command string inside the sandbox container (bash -lc)."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    command: str = Field(min_length=1, max_length=16_384)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentTodoRequest(BaseModel):
    """Session task checklist operations."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    action: Literal["list", "add", "done", "remove", "clear"] = "list"
    text: str | None = Field(default=None, min_length=1, max_length=500)
    item_id: str | None = Field(default=None, min_length=1, max_length=64)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentPatchRequest(BaseModel):
    """Apply a unified diff to the durable session workspace (host-side)."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    patch: str = Field(min_length=1, max_length=2_000_000)
    fuzz: int = Field(default=3, ge=0, le=10)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentGitRequest(BaseModel):
    """Run local git operations inside the sandbox workspace (offline)."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    args: list[str] = Field(min_length=1, max_length=32)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentGitCloneRequest(BaseModel):
    """Clone a public repository through the reviewed egress approval channel.

    The network transfer runs host-side via the approved external-acquisition
    pipeline (the sandbox container stays offline); the snapshot is materialized
    into the workspace and a container-side git repository is initialized on it.
    """

    chat_session_id: str = Field(min_length=1, max_length=36)
    owner: str = Field(min_length=1, max_length=100)
    repo: str = Field(min_length=1, max_length=100)
    ref: str = Field(default="HEAD", min_length=1, max_length=200)
    path: str = Field(default="", max_length=1000)
    destination_root: str = Field(default="work/git/<repo>", min_length=1, max_length=1000)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentSearchRequest(BaseModel):
    """Host-side web search through the user-authorized SearchProvider."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=6, ge=1, le=12)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentFetchRequest(BaseModel):
    """Host-side page fetch through the reviewed fetch authorization channel."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    url: str = Field(min_length=1, max_length=2000)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentSubagentRequest(BaseModel):
    """Spawn a nested sandbox sub-agent with a restricted tool subset."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1, max_length=16_384)
    tools: list[str] | None = Field(default=None, max_length=16)
    max_rounds: int = Field(default=6, ge=1, le=12)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentSubagentStatusRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    subagent_id: str = Field(min_length=1, max_length=64)


class SandboxAgentSkillListRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentSkillReadRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    skill_key: str = Field(min_length=1, max_length=200)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentNotebookRequest(BaseModel):
    """Persistent in-container REPL kernel (sandbox_notebook)."""

    chat_session_id: str = Field(min_length=1, max_length=36)
    action: Literal["open", "execute", "close", "status"] = "open"
    kernel_id: str | None = Field(default=None, min_length=1, max_length=64)
    code: str | None = Field(default=None, min_length=1, max_length=262_144)
    interpreter: Literal["python"] = "python"
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_at: datetime
