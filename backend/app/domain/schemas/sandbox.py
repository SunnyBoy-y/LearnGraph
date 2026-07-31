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
    phase: str
    progress_percent: int
    message: str
    can_initialize: bool
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
    progress_percent: int
    message: str
    status: str
    image_digest: str | None = None
    browser_image_digest: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    log_tail: list[str] = Field(default_factory=list)
    started_at: float
    finished_at: float | None = None


class SandboxBootstrapStartResponse(BaseModel):
    accepted: bool
    joined_existing: bool = False
    error_code: str | None = None
    error_message: str | None = None
    job: SandboxBootstrapJobView | None = None
    status: SandboxBootstrapStatusView


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


class SandboxAgentFileReadRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=255)
    sandbox_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SandboxAgentFileListRequest(BaseModel):
    chat_session_id: str = Field(min_length=1, max_length=36)
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


class SandboxAgentFileView(BaseModel):
    sandbox_session_id: str
    path: str
    size_bytes: int
    content: str | None = None
    files: list[SandboxAgentWorkspaceFileView] = Field(default_factory=list)


class SandboxAgentCommandView(ORMModel):
    id: str
    workspace_id: str
    sandbox_session_id: str
    chat_session_id: str
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
    created_at: datetime
