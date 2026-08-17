from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class SandboxCapabilitySnapshot:
    available: bool
    backend_id: str
    platform: str
    capabilities: tuple[str, ...]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxCreateSpec:
    session_id: str
    image_ref: str
    memory_bytes: int
    memory_swap_bytes: int
    cpu_count: float
    pids_max: int
    disk_bytes: int
    workspace_path: str
    runtime_kind: str
    # Optional reviewed outbound-egress reference. When absent the container
    # must stay fully offline (network_mode="none"). ``egress`` is an opaque
    # validated envelope carrying the policy digest, internal network name and
    # proxy URL; it never carries secrets or raw allow-list hostnames.
    egress: dict[str, Any] | None = None
    # Logical, portable identity of the workspace (opaque relative key owned by
    # the backend). Backends MUST NOT interpret it as a host path. The legacy
    # ``workspace_path`` above remains only for the pre-daemon Docker backend.
    workspace_key: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxSessionHandle:
    session_id: str
    backend_ref: str


@dataclass(frozen=True, slots=True)
class SandboxExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    latency_ms: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class SandboxWorkspaceFile:
    path: str
    size_bytes: int


class SandboxBackendPort(Protocol):
    backend_id: str
    platform: str

    def probe(self) -> SandboxCapabilitySnapshot: ...

    def host_capacity(self) -> tuple[int, int]: ...

    def create(self, spec: SandboxCreateSpec) -> SandboxSessionHandle: ...

    def resume(self, session_id: str, backend_ref: str) -> SandboxSessionHandle: ...

    def write(self, session: SandboxSessionHandle, path: str, data: bytes) -> None: ...

    def write_agent_file(
        self, session: SandboxSessionHandle, path: str, data: bytes
    ) -> None: ...

    def delete_agent_file(self, session: SandboxSessionHandle, path: str) -> None: ...

    def exec_fixed(
        self,
        session: SandboxSessionHandle,
        argv: tuple[str, ...],
        *,
        timeout_seconds: int,
        output_limit: int,
    ) -> SandboxExecResult: ...

    def exec_agent(
        self,
        session: SandboxSessionHandle,
        argv: tuple[str, ...],
        *,
        cwd_relative: str,
        timeout_seconds: int,
        output_limit: int,
        destructive_path_prefixes: tuple[str, ...] = (),
    ) -> SandboxExecResult: ...

    def read(self, session: SandboxSessionHandle, path: str, limit_bytes: int) -> bytes: ...

    def list_files(
        self, session: SandboxSessionHandle, limit_entries: int
    ) -> list[SandboxWorkspaceFile]: ...

    def stop(self, session: SandboxSessionHandle) -> None: ...

    def delete(self, session: SandboxSessionHandle) -> None: ...

    # Persistent in-container REPL kernels (sandbox_notebook).  Backends that
    # do not support kernels raise SandboxCapabilityMismatch.
    def kernel_open(
        self,
        session: SandboxSessionHandle,
        *,
        workspace_relative: str,
        interpreter: str,
    ) -> str: ...

    def kernel_execute(
        self,
        session: SandboxSessionHandle,
        kernel_id: str,
        code: str,
        *,
        timeout_seconds: int,
        output_limit: int,
    ) -> dict[str, Any]: ...

    def kernel_close(self, session: SandboxSessionHandle, kernel_id: str) -> None: ...
