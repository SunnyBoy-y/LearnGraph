"""SandboxdBackend — SandboxBackendPort adapter over the sandboxd control plane.

This adapter is the only LearnGraph-side consumer of the Sandbox API v1. It
maps business DTOs to protocol DTOs and never transmits host paths, Docker
parameters, image refs or proxy credentials to the daemon. The daemon's
opaque ``sandbox_id`` becomes the Port ``backend_ref``.

Fail-closed rules:
- the daemon's protocol version must fall within the configured range;
- transport/protocol failures map to ``SandboxBackendUnavailable``;
- stable protocol errors map onto the existing Port exceptions;
- the adapter NEVER falls back to host or Docker execution.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.providers.ports.sandbox import (
    SandboxBackendPort,
    SandboxCapabilitySnapshot,
    SandboxCreateSpec,
    SandboxExecResult,
    SandboxSessionHandle,
    SandboxWorkspaceFile,
)
from app.providers.remote.sandbox import (
    SandboxBackendError,
    SandboxBackendUnavailable,
    SandboxCapabilityMismatch,
    SandboxDestructiveAuthorizationRequired,
    SandboxOutputLimitExceeded,
    SandboxWorkspaceQuotaExceeded,
)
from app.providers.remote.sandboxd_client import (
    SandboxdClient,
    SandboxdProtocolError,
    SandboxdUnavailable,
)

PROTOCOL_VERSION = "1.0"
_RUNNER_PREFIX = ("python", "/opt/learngraph/runner.py")
_FIXED_FLAGS = {"--task", "--input", "--output"}

# Core Port capabilities always offered by the daemon runtime envelope.
_CORE_CAPABILITIES = (
    "isolated_workspace",
    "network_none",
    "fixed_runner",
    "agent_argv",
    "agent_workspace_files",
    "destructive_command_blocking",
    "process_tree_kill",
    "resource_limits",
    "cold_resume",
    "legacy_doc_extract",
)


def _map_protocol_error(exc: SandboxdProtocolError) -> SandboxBackendError:
    code = exc.code
    if code == "workspace_quota_exceeded":
        return SandboxWorkspaceQuotaExceeded(exc.message)
    if code == "output_limit_exceeded":
        return SandboxOutputLimitExceeded(exc.message)
    if code == "destructive_authorization_required":
        return SandboxDestructiveAuthorizationRequired(exc.message)
    if code in {"runtime_unavailable", "docker_unavailable", "capacity_exceeded", "protocol_incompatible"}:
        return SandboxBackendUnavailable(exc.message)
    return SandboxBackendError(exc.message)


class SandboxdBackend(SandboxBackendPort):
    backend_id = "sandboxd"
    platform = "sandboxd_control_plane"

    def __init__(
        self,
        settings: Settings,
        runtime_kind: str = "python-node",
        *,
        client: SandboxdClient | None = None,
    ) -> None:
        self.settings = settings
        self.runtime_kind = runtime_kind
        self._client = client
        self._client_created = False
        self._token_error: str | None = None
        self._token: str | None = None
        if client is None:
            self._load_token()

    def _load_token(self) -> None:
        token_file = (self.settings.sandboxd_token_file or "").strip()
        if not token_file:
            self._token_error = "sandboxd token file is not configured"
            return
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            self._token_error = f"sandboxd token file is unreadable: {type(exc).__name__}"
            return
        if not token:
            self._token_error = "sandboxd token file is empty"
            return
        self._token = token
        self._token_error = None

    def _daemon(self) -> SandboxdClient:
        if self._client is not None:
            return self._client
        if self._token_error or not self._token:
            raise SandboxBackendUnavailable(
                self._token_error or "sandboxd service token is unavailable"
            )
        if not self._client_created:
            self._client = SandboxdClient(
                url=self.settings.sandboxd_url or "",
                token=self._token,
                admin_token=self._admin_token(),
                deployment_id=self.settings.sandboxd_deployment_id,
                connect_timeout=self.settings.sandboxd_connect_timeout_seconds,
                request_timeout=self.settings.sandboxd_request_timeout_seconds,
                protocol_min=self.settings.sandboxd_protocol_min,
                protocol_max=self.settings.sandboxd_protocol_max,
            )
            self._client_created = True
        return self._client

    def _admin_token(self) -> str | None:
        token_file = (self.settings.sandboxd_admin_token_file or "").strip()
        if not token_file:
            return None
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return token or None

    def _scope(self, session_id: str) -> str:
        return f"{self.settings.sandboxd_deployment_id}|{session_id}"

    def _ttl_seconds(self) -> int:
        return max(60, int(self.settings.sandbox_workspace_absolute_ttl_seconds))

    # --- capability --------------------------------------------------------

    def probe(self) -> SandboxCapabilitySnapshot:
        if not self.settings.sandbox_enabled:
            return SandboxCapabilitySnapshot(
                False,
                self.backend_id,
                self.platform,
                (),
                "Sandbox execution is disabled by deployment configuration",
            )
        try:
            daemon = self._daemon()
            capabilities = daemon.get_capabilities()
            ready = daemon.get_ready()
        except SandboxBackendUnavailable as exc:
            return SandboxCapabilitySnapshot(False, self.backend_id, self.platform, (), str(exc))
        except SandboxdUnavailable as exc:
            return SandboxCapabilitySnapshot(False, self.backend_id, self.platform, (), str(exc))
        except SandboxdProtocolError as exc:
            return SandboxCapabilitySnapshot(False, self.backend_id, self.platform, (), exc.message)

        protocol_ok = (
            self.settings.sandboxd_protocol_min
            <= str(capabilities.get("protocol_max") or "")
            <= self.settings.sandboxd_protocol_max
        )
        if not protocol_ok:
            return SandboxCapabilitySnapshot(
                False,
                self.backend_id,
                self.platform,
                (),
                "sandboxd protocol version is outside the configured range",
            )
        if not ready.get("ready"):
            reasons = ready.get("reasons") or []
            return SandboxCapabilitySnapshot(
                False,
                self.backend_id,
                self.platform,
                (),
                "; ".join(str(reason) for reason in reasons) or "sandboxd is not ready",
            )
        return SandboxCapabilitySnapshot(
            True,
            self.backend_id,
            self.platform,
            _CORE_CAPABILITIES,
            None,
        )

    def host_capacity(self) -> tuple[int, int]:
        try:
            return self._daemon().capacity()
        except (SandboxdUnavailable, SandboxdProtocolError) as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc

    def observed_pressure(self) -> dict[str, Any]:
        """Live observed usage from the daemon (best-effort probe)."""
        try:
            return self._daemon().capacity_detail()
        except (SandboxdUnavailable, SandboxdProtocolError):
            return {}

    def runtime_image_pinned(self, runtime_kind: str = "python-node") -> bool | None:
        """Report whether the daemon has an installed (smoke-passed) runtime.

        Returns ``None`` when the admin control plane is not configured or the
        daemon is unreachable — the caller keeps its legacy fallback (local env
        pin) instead of guessing.
        """
        try:
            runtimes = self._daemon().list_runtimes()
        except (SandboxdUnavailable, SandboxdProtocolError):
            return None
        for record in runtimes:
            if record.get("runtime_kind") == runtime_kind:
                return bool(record.get("image_digest")) and (
                    record.get("smoke_status") == "passed"
                )
        return False

    # --- lifecycle ---------------------------------------------------------

    def create(self, spec: SandboxCreateSpec) -> SandboxSessionHandle:
        egress_payload = None
        if spec.egress and spec.egress.get("policy_digest"):
            egress_payload = {
                "policy_digest": str(spec.egress["policy_digest"]),
                "policy_revision": "sandbox-policy-v1",
            }
        payload: dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": spec.session_id,
            "workspace_key": spec.workspace_key or spec.session_id,
            "owner": {
                "deployment_id": self.settings.sandboxd_deployment_id,
                "workspace_id": "",
                "session_id": spec.session_id,
            },
            "runtime_kind": spec.runtime_kind,
            "memory_bytes": spec.memory_bytes,
            "memory_swap_bytes": spec.memory_swap_bytes,
            "cpu_count": spec.cpu_count,
            "pids_max": spec.pids_max,
            "disk_bytes": spec.disk_bytes,
            "egress": egress_payload,
            "ttl_seconds": self._ttl_seconds(),
            # Stable idempotency key per session: the daemon also reuses a
            # still-alive sandbox for the same session_id (execution pool), so
            # retries with the same key replay or reuse instead of duplicating
            # the physical container.
            "idempotency_key": f"pool-{spec.session_id}",
        }
        try:
            body = self._daemon().create_sandbox(payload)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc
        return SandboxSessionHandle(spec.session_id, str(body["sandbox_id"]))

    def resume(self, session_id: str, backend_ref: str) -> SandboxSessionHandle:
        try:
            self._daemon().resume_sandbox(backend_ref, session_id)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc
        return SandboxSessionHandle(session_id, backend_ref)

    def stop(self, session: SandboxSessionHandle) -> None:
        try:
            self._daemon().stop_sandbox(session.backend_ref, session.session_id)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc

    def delete(self, session: SandboxSessionHandle) -> None:
        try:
            self._daemon().delete_sandbox(session.backend_ref, session.session_id)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            # Delete is idempotent at the daemon; a missing sandbox is success.
            if exc.code == "sandbox_not_found":
                return
            raise _map_protocol_error(exc) from exc

    # --- files -------------------------------------------------------------

    def write(self, session: SandboxSessionHandle, path: str, data: bytes) -> None:
        # Port semantics: ``write`` materializes read-only input files.
        self._put(session, path, data, mode=0o444)

    def write_agent_file(self, session: SandboxSessionHandle, path: str, data: bytes) -> None:
        self._put(session, path, data, mode=0o644)

    def _put(self, session: SandboxSessionHandle, path: str, data: bytes, *, mode: int) -> None:
        try:
            self._daemon().put_file(
                session.backend_ref, session.session_id, path, data, mode=mode
            )
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc

    def delete_agent_file(self, session: SandboxSessionHandle, path: str) -> None:
        try:
            self._daemon().delete_file(session.backend_ref, session.session_id, path)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc

    def read(self, session: SandboxSessionHandle, path: str, limit_bytes: int) -> bytes:
        try:
            return self._daemon().get_file(
                session.backend_ref, session.session_id, path, limit_bytes
            )
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc

    def list_files(
        self, session: SandboxSessionHandle, limit_entries: int
    ) -> list[SandboxWorkspaceFile]:
        try:
            body = self._daemon().file_index(
                session.backend_ref, session.session_id, "", max(1, limit_entries), None
            )
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc
        return [
            SandboxWorkspaceFile(path=str(entry["path"]), size_bytes=int(entry["size_bytes"]))
            for entry in body.get("entries") or []
        ]

    # --- exec --------------------------------------------------------------

    def exec_fixed(
        self,
        session: SandboxSessionHandle,
        argv: tuple[str, ...],
        *,
        timeout_seconds: int,
        output_limit: int,
    ) -> SandboxExecResult:
        task, input_path, output_path = _parse_fixed_argv(argv)
        body = {
            "task_type": task,
            "input_path": input_path,
            "output_path": output_path,
            "timeout_seconds": timeout_seconds,
            "output_limit": output_limit,
            "idempotency_key": f"exec-{uuid.uuid4().hex[:16]}",
        }
        try:
            result = self._daemon().exec_fixed(session.backend_ref, session.session_id, body)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc
        return _exec_result(result)

    def exec_agent(
        self,
        session: SandboxSessionHandle,
        argv: tuple[str, ...],
        *,
        cwd_relative: str,
        timeout_seconds: int,
        output_limit: int,
        destructive_path_prefixes: tuple[str, ...] = (),
    ) -> SandboxExecResult:
        del destructive_path_prefixes  # the daemon enforces destructive guards itself
        body = {
            "argv": list(argv),
            "cwd": cwd_relative,
            "timeout_seconds": timeout_seconds,
            "output_limit": output_limit,
            "idempotency_key": f"exec-{uuid.uuid4().hex[:16]}",
        }
        try:
            result = self._daemon().exec_agent(session.backend_ref, session.session_id, body)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc
        return _exec_result(result)

    def kernel_open(
        self,
        session: SandboxSessionHandle,
        *,
        workspace_relative: str,
        interpreter: str,
    ) -> str:
        body = {"workspace_relative": workspace_relative, "interpreter": interpreter}
        try:
            result = self._daemon().kernel_open(session.backend_ref, session.session_id, body)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc
        kernel_id = str(result.get("kernel_id") or "")
        if not kernel_id:
            raise SandboxBackendError("sandboxd kernel_open returned no kernel_id")
        return kernel_id

    def kernel_execute(
        self,
        session: SandboxSessionHandle,
        kernel_id: str,
        code: str,
        *,
        timeout_seconds: int,
        output_limit: int,
    ) -> dict[str, Any]:
        body = {
            "code": code,
            "timeout_seconds": timeout_seconds,
            "output_limit": output_limit,
        }
        try:
            result = self._daemon().kernel_execute(kernel_id, session.session_id, body)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            raise _map_protocol_error(exc) from exc
        return {
            "ok": bool(result.get("ok")),
            "stdout": str(result.get("stdout") or ""),
            "stderr": str(result.get("stderr") or ""),
            "result_repr": result.get("result_repr"),
            "timed_out": bool(result.get("timed_out")),
        }

    def kernel_close(self, session: SandboxSessionHandle, kernel_id: str) -> None:
        try:
            self._daemon().kernel_close(kernel_id, session.session_id)
        except SandboxdUnavailable as exc:
            raise SandboxBackendUnavailable(str(exc)) from exc
        except SandboxdProtocolError as exc:
            # kernel_not_found is idempotent for close.
            if exc.code != "kernel_not_found":
                raise _map_protocol_error(exc) from exc


def _parse_fixed_argv(argv: tuple[str, ...]) -> tuple[str, str, str]:
    """Parse the fixed runner argv into (task_type, input_path, output_path).

    The Port passes the complete fixed-runner argv; the daemon API only
    accepts the semantic parts (it assembles the runner command itself).
    Unrecognized argv fails closed — it is never forwarded as a raw command.
    """
    if len(argv) < 2 or argv[0] != _RUNNER_PREFIX[0] or argv[1] != _RUNNER_PREFIX[1]:
        raise SandboxCapabilityMismatch(
            "fixed runner argv is not supported by the sandboxd control plane"
        )
    values: dict[str, str] = {}
    index = 2
    while index < len(argv):
        flag = argv[index]
        if flag not in _FIXED_FLAGS or index + 1 >= len(argv):
            raise SandboxCapabilityMismatch(
                "fixed runner argv contains an unsupported flag"
            )
        values[flag] = argv[index + 1]
        index += 2
    if "--task" not in values or "--input" not in values or "--output" not in values:
        raise SandboxCapabilityMismatch(
            "fixed runner argv must carry --task, --input and --output"
        )
    return values["--task"], values["--input"], values["--output"]


def _exec_result(result: dict[str, Any]) -> SandboxExecResult:
    exit_code = result.get("exit_code")
    if exit_code is None:
        exit_code = -1
    return SandboxExecResult(
        exit_code=int(exit_code),
        stdout=str(result.get("stdout") or "").encode("utf-8", errors="replace"),
        stderr=str(result.get("stderr") or "").encode("utf-8", errors="replace"),
        timed_out=bool(result.get("timed_out")),
        latency_ms=int(result.get("latency_ms") or 0),
        truncated=bool(result.get("truncated")),
    )
