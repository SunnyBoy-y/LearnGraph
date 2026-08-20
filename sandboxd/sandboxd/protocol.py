"""Sandbox API v1 protocol DTOs and stable error envelope.

The contract is additive: unknown JSON fields are ignored on read. Version
negotiation and runner ABI bounds live here; both the daemon and the
LearnGraph client share the same constants (mirrored in the backend client).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from sandboxd.config import PROTOCOL_MAX, PROTOCOL_MIN, RUNNER_ABI_MAX, RUNNER_ABI_MIN

RUNTIME_KINDS = ("python-node", "python-node-browser")


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class OwnerScope(BaseModel):
    deployment_id: str
    workspace_id: str
    session_id: str


class EgressRef(BaseModel):
    policy_digest: str
    policy_revision: str = "sandbox-policy-v1"


class BootstrapJobRequest(BaseModel):
    runtime_kind: Literal["python-node", "python-node-browser"] = "python-node"
    # Runtime-neutral source (registry tag, digest, or bundle ref). Preferred
    # over the legacy ``image_tag`` (v1.0 compat, kept for old clients).
    runtime_source: str | None = Field(default=None, min_length=1, max_length=500)
    image_tag: str | None = Field(default=None, min_length=1, max_length=500)


class CreateSandboxRequest(BaseModel):
    protocol_version: str
    session_id: str
    workspace_key: str
    owner: OwnerScope
    runtime_kind: Literal["python-node", "python-node-browser"] = "python-node"
    memory_bytes: int = Field(ge=8 * 1024 * 1024, le=64 * 1024**3)
    memory_swap_bytes: int = Field(ge=8 * 1024 * 1024, le=64 * 1024**3)
    cpu_count: float = Field(ge=0.1, le=64.0)
    pids_max: int = Field(ge=8, le=1_000_000)
    disk_bytes: int = Field(ge=1024 * 1024, le=16 * 1024**3)
    egress: EgressRef | None = None
    ttl_seconds: int = Field(ge=60, le=7 * 24 * 3600)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SandboxView(BaseModel):
    sandbox_id: str
    state: str
    runtime_kind: str
    image_digest: str
    runner_abi: str
    expires_at: str
    created_at: str
    last_used_at: str
    policy_digest: str | None = None
    workspace_key: str | None = None
    # v1.1 neutral aliases (mirror the legacy fields above).
    runtime_digest: str | None = None
    runtime_backend: str | None = None


class FixedExecRequest(BaseModel):
    task_type: str = Field(min_length=1, max_length=128)
    input_path: str
    output_path: str
    timeout_seconds: int = Field(ge=1, le=3600)
    output_limit: int = Field(ge=1024, le=512 * 1024 * 1024)
    idempotency_key: str = Field(min_length=1, max_length=128)


class AgentExecRequest(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    cwd: str = "."
    timeout_seconds: int = Field(ge=1, le=3600)
    output_limit: int = Field(ge=1024, le=512 * 1024 * 1024)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ExecResult(BaseModel):
    execution_id: str
    exit_code: int | None
    timed_out: bool
    truncated: bool
    stdout: str
    stderr: str
    latency_ms: int
    status: Literal["succeeded", "failed", "timeout", "cancelled", "indeterminate"]


class KernelOpenRequest(BaseModel):
    workspace_relative: str = "."
    interpreter: Literal["python"] = "python"


class KernelOpenResult(BaseModel):
    kernel_id: str
    interpreter: str
    status: str = "running"


class KernelCellRequest(BaseModel):
    code: str = Field(min_length=1, max_length=262_144)
    timeout_seconds: int = Field(ge=1, le=3600, default=180)
    output_limit: int = Field(ge=1024, le=512 * 1024 * 1024, default=20 * 1024 * 1024)


class KernelCellResult(BaseModel):
    kernel_id: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    result_repr: str | None = None
    timed_out: bool = False


class FileListEntry(BaseModel):
    path: str
    size_bytes: int


class FileIndex(BaseModel):
    entries: list[FileListEntry]
    cursor: str | None = None


class Capabilities(BaseModel):
    daemon_version: str
    protocol_min: str = PROTOCOL_MIN
    protocol_max: str = PROTOCOL_MAX
    runner_abi_min: str = RUNNER_ABI_MIN
    runner_abi_max: str = RUNNER_ABI_MAX
    runtime_kinds: list[str] = list(RUNTIME_KINDS)
    features: list[str] = Field(
        default_factory=lambda: [
            "fixed_runner",
            "agent_argv",
            "file_api",
            "ownership_scope",
            "idempotency",
            "per_sandbox_egress_network",
            "resource_usage",
            "kernels",
        ]
    )
    limits: dict[str, int] = Field(
        default_factory=lambda: {
            "max_file_bytes": 256 * 1024 * 1024,
            "max_request_bytes": 16 * 1024 * 1024,
            "max_stdout_bytes": 256 * 1024,
            "max_argv_count": 64,
        }
    )


class HealthReady(BaseModel):
    ready: bool
    reasons: list[str] = Field(default_factory=list)
    docker: bool = False
    store: bool = False
    runtime: bool = False
    reconcile: str = "not_run"
    # v1.1 neutral runtime identity.
    runtime_backend: str | None = None


class Capacity(BaseModel):
    cpu_count: int
    memory_bytes: int
    # Live observed usage of this deployment's managed containers (best-effort;
    # 0 when the probe is unavailable). Used by the scheduler for pressure-based
    # dynamic admission — never as the sole admission signal.
    observed_memory_bytes: int = 0
    observed_cpu_percent: float = 0.0
    active_containers: int = 0
    # v1.1 neutral alias.
    active_sandboxes: int = 0


# Stable error codes (contract). Client maps them onto its own exceptions.
ERROR_CODES = frozenset(
    {
        "unauthorized",
        "owner_mismatch",
        "protocol_incompatible",
        "capability_missing",
        "runner_abi_mismatch",
        "sandbox_not_found",
        "sandbox_expired",
        "invalid_state",
        "invalid_path",
        "file_too_large",
        "workspace_quota_exceeded",
        "command_rejected",
        "destructive_authorization_required",
        "execution_timeout",
        "output_limit_exceeded",
        "execution_failed",
        "execution_indeterminate",
        "capacity_exceeded",
        "runtime_unavailable",
        "docker_unavailable",
        "idempotency_conflict",        "invalid_request",
        "kernel_not_found",
    }
)
