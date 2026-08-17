"""Runtime adapter contract for sandboxd.

The controller only speaks this port; Docker is one implementation. The spec
is daemon-internal — callers can never express privileged/host-mount/host-
network options.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RuntimeCreateSpec:
    sandbox_id: str
    session_id: str
    workspace_key: str
    runtime_kind: str
    image_ref: str
    volume_name: str
    deployment_id: str
    memory_bytes: int
    memory_swap_bytes: int
    cpu_count: float
    pids_max: int
    disk_bytes: int
    policy_digest: str | None = None
    egress_network: str | None = None
    egress_proxy_url: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeHandle:
    sandbox_id: str
    container_id: str | None


@dataclass(frozen=True, slots=True)
class RuntimeCapability:
    available: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeExecResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    truncated: bool
    latency_ms: int


@dataclass(frozen=True, slots=True)
class RuntimeFileEntry:
    path: str
    size_bytes: int


class RuntimeBackendPort(Protocol):
    """Minimal daemon-internal runtime contract."""

    def probe(self) -> RuntimeCapability: ...
    def capacity(self) -> tuple[int, int]: ...
    def pull_and_resolve_digest(self, image_tag: str) -> tuple[str, dict[str, str]]: ...
    def smoke_test(self, image_ref: str, runtime_kind: str, *, timeout_seconds: int = 120) -> tuple[bool, str]: ...
    def create(self, spec: RuntimeCreateSpec) -> RuntimeHandle: ...
    def resume(self, sandbox_id: str, container_id: str | None) -> RuntimeHandle: ...
    def stop(self, handle: RuntimeHandle) -> None: ...
    def delete(self, handle: RuntimeHandle) -> None: ...
    def write_file(self, handle: RuntimeHandle, path: str, data: bytes, *, mode: int = 0o644) -> None: ...
    def delete_file(self, handle: RuntimeHandle, path: str) -> None: ...
    def read_file(self, handle: RuntimeHandle, path: str, limit_bytes: int) -> bytes: ...
    def list_files(
        self, handle: RuntimeHandle, prefix: str, limit: int, cursor: str | None
    ) -> tuple[list[RuntimeFileEntry], str | None]: ...
    def workspace_usage(self, handle: RuntimeHandle) -> dict[str, int]: ...
    def exec_fixed(
        self,
        handle: RuntimeHandle,
        argv: tuple[str, ...],
        *,
        execution_id: str,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult: ...
    def exec_agent(
        self,
        handle: RuntimeHandle,
        argv: tuple[str, ...],
        *,
        execution_id: str,
        cwd: str,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult: ...
    def cancel_exec(self, handle: RuntimeHandle, execution_id: str) -> bool: ...
    def start_kernel(
        self, handle: RuntimeHandle, workspace_relative: str, interpreter: str
    ) -> str: ...
    def exec_kernel_cell(
        self,
        handle: RuntimeHandle,
        kernel_id: str,
        workspace_relative: str,
        code: str,
        *,
        timeout_seconds: int,
        output_limit: int,
    ) -> RuntimeExecResult: ...
    def stop_kernel(
        self, handle: RuntimeHandle, kernel_id: str, workspace_relative: str
    ) -> None: ...
