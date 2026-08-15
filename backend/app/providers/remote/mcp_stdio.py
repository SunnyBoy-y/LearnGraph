from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import update

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.extension_models import MCPRunnerSession
from app.providers.ports.mcp import (
    MCPProbeResult,
    MCPProtocolFailure,
    MCPRunnerResourceExceeded,
    MCPRunnerTimeout,
    MCPToolCallResult,
    MCPTransportUnavailable,
)
from app.providers.ports.mcp_runner import MCPRunnerPort
from app.providers.ports.sandbox import SandboxCreateSpec
from app.providers.sandbox_registry import get_sandbox_backend_registry
from app.providers.remote.sandbox import (
    SandboxBackendError,
    SandboxOutputLimitExceeded,
    SandboxWorkspaceQuotaExceeded,
)
from app.services.sandbox_runtime import resolve_sandbox_image

PROTOCOL_VERSION = "2025-11-25"
RUNTIME_KIND = "python-node"
RENDER_TASK_PREFIX = ("python", "/opt/learngraph/runner.py")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DockerStdioMCPRunner(MCPRunnerPort):
    """Run approved MCP stdio servers inside the offline Docker sandbox.

    The FastAPI process only speaks the fixed ``mcp_stdio`` task; the actual
    third-party command is launched inside the isolated, non-root, read-only,
    no-network container. The runner stays disabled (default) until both the
    ``mcp_stdio_runner_enabled`` flag and a pinned immutable image are present.

    ``invoke`` is self-contained: when the launch spec carries no provisioned
    session it provisions a container, runs the one-shot JSON-RPC call, and
    terminates it in ``finally`` (matching the HTTP adapter's one-attempt
    semantics). Provisioned containers are persisted best-effort to
    ``MCPRunnerSession`` so a process crash mid-invocation leaves a record the
    cleanup sweep can reap; ``terminate`` marks it ``terminated``.
    """

    transport_id = "stdio"

    def __init__(
        self,
        settings: Any | None = None,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.image_ref = resolve_sandbox_image(self.settings)
        self.enabled = bool(
            self.settings.mcp_stdio_runner_enabled
            and self.image_ref
        )
        # Injected in tests to avoid touching the real SQLite database; defaults
        # to the app session factory. Persistence is always best-effort.
        self._session_factory = session_factory or SessionLocal

    @property
    def available(self) -> bool:
        if not self.enabled:
            return False
        return bool(self._backend().probe().available)

    @property
    def unavailable_reason(self) -> str:
        if not self.settings.mcp_stdio_runner_enabled:
            return (
                "MCP stdio runner is disabled by deployment configuration; "
                "LearnGraph will not launch arbitrary commands in the host process"
            )
        if not self.image_ref:
            return (
                "No pinned immutable sandbox image is configured; "
                "MCP stdio execution stays unavailable"
            )
        return "The configured MCP stdio sandbox image is not available in Docker Engine"

    def _backend(self):
        # Resolves through the backend registry (docker today, sandboxd after
        # the control-plane migration). The factory uses the same pinned image
        # resolution as ``self.image_ref``.
        return get_sandbox_backend_registry().default(self.settings, RUNTIME_KIND)

    def _persist_runner_session(
        self,
        *,
        workspace_id: str,
        server_id: str,
        session_id: str,
        backend_ref: str,
        ttl_seconds: int,
    ) -> None:
        """Best-effort durable record so the cleanup sweep can reap orphans."""

        try:
            with self._session_factory() as db:
                db.add(
                    MCPRunnerSession(
                        workspace_id=workspace_id,
                        server_id=server_id,
                        session_id=session_id,
                        backend_ref=backend_ref,
                        status="running",
                        expires_at=_utc_now() + timedelta(seconds=max(1, ttl_seconds)),
                    )
                )
                db.commit()
        except Exception:  # noqa: BLE001 - persistence is best-effort
            pass

    def _mark_runner_session_terminated(self, *, workspace_id: str, session_id: str) -> None:
        try:
            with self._session_factory() as db:
                db.execute(
                    update(MCPRunnerSession)
                    .where(
                        MCPRunnerSession.workspace_id == workspace_id,
                        MCPRunnerSession.session_id == session_id,
                        MCPRunnerSession.status == "running",
                    )
                    .values(status="terminated")
                )
                db.commit()
        except Exception:  # noqa: BLE001 - persistence is best-effort
            pass

    def probe(self, launch_spec: dict[str, Any]) -> MCPProbeResult:
        del launch_spec
        if not self.enabled:
            raise MCPTransportUnavailable(self.unavailable_reason)
        capability = self._backend().probe()
        if not capability.available:
            raise MCPTransportUnavailable(capability.reason or "MCP stdio runner unavailable")
        return MCPProbeResult(
            protocol_version=PROTOCOL_VERSION,
            server_identity={"runner": "docker-stdio-isolated"},
            capabilities={"stdio": True, "isolated": True, "network": "none"},
            tools=[],
            resources=[],
            prompts=[],
        )

    def provision(self, launch_spec: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise MCPTransportUnavailable(self.unavailable_reason)
        command = launch_spec.get("command")
        if not isinstance(command, (list, tuple)) or not command:
            raise MCPProtocolFailure("MCP stdio launch spec requires a command list")
        if len(command) > int(self.settings.mcp_stdio_command_args_max):
            raise MCPProtocolFailure("MCP stdio launch command exceeds the argument bound")

        workspace_root = Path(self.settings.sandbox_workspace_root).expanduser().resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        session_id = f"mcp-stdio-{launch_spec.get('server_id', 'server')}-{uuid4().hex[:12]}"
        # Each provisioned runner gets its own isolated workspace directory; the
        # shared workspace root is never mounted into a sandbox.
        workspace_path = workspace_root / f"mcp-{session_id}"
        workspace_path.mkdir(parents=True, exist_ok=True)
        handle = self._backend().create(
            SandboxCreateSpec(
                session_id=session_id,
                image_ref=self.image_ref or "",
                memory_bytes=256 * 1024 * 1024,
                memory_swap_bytes=256 * 1024 * 1024,
                cpu_count=1.0,
                pids_max=64,
                disk_bytes=16 * 1024 * 1024,
                workspace_path=str(workspace_path),
                runtime_kind=RUNTIME_KIND,
                # Never enables egress; the container stays on network_mode="none".
                egress=None,
                workspace_key=session_id,
            )
        )
        self._persist_runner_session(
            workspace_id=str(launch_spec.get("workspace_id") or ""),
            server_id=str(launch_spec.get("server_id") or ""),
            session_id=session_id,
            backend_ref=handle.backend_ref,
            ttl_seconds=int(self.settings.mcp_stdio_session_ttl_seconds),
        )
        return {
            "backend_ref": handle.backend_ref,
            "session_id": handle.session_id,
            "server_id": launch_spec.get("server_id"),
            "workspace_id": launch_spec.get("workspace_id"),
            "workspace_dir": str(workspace_path),
        }

    def _launch_file(self, launch_spec: dict[str, Any], credential_envelope: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "command": list(launch_spec["command"]),
            "max_args": int(self.settings.mcp_stdio_command_args_max),
            "timeout_seconds": int(self.settings.mcp_stdio_timeout_seconds),
            "protocol_version": launch_spec.get("protocol_version", PROTOCOL_VERSION),
            "capability_hash": launch_spec.get("capability_hash"),
            "credential": credential_envelope,
        }

    def call_jsonrpc(
        self,
        launch_spec: dict[str, Any],
        *,
        method: str,
        params: dict[str, Any],
        request_id: int,
        credential_envelope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        backend = self._backend()
        session = backend.resume(
            launch_spec["session_id"],
            launch_spec["backend_ref"],
        )
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            backend.write(session, "mcp-request.json", (json.dumps(request) + "\n").encode("utf-8"))
            backend.write(
                session,
                "mcp-launch.json",
                json.dumps(self._launch_file(launch_spec, credential_envelope)).encode("utf-8"),
            )
            try:
                result = backend.exec_fixed(
                    session,
                    (
                        *RENDER_TASK_PREFIX,
                        "--task",
                        "mcp_stdio",
                        "--input",
                        "mcp-request.json",
                        "--spec",
                        "mcp-launch.json",
                        "--output",
                        "mcp-response.json",
                    ),
                    timeout_seconds=int(self.settings.mcp_stdio_timeout_seconds) + 5,
                    output_limit=64 * 1024,
                )
            except SandboxWorkspaceQuotaExceeded as exc:
                raise MCPRunnerResourceExceeded(
                    "MCP stdio runner exceeded its resource quota"
                ) from exc
            except SandboxOutputLimitExceeded as exc:
                raise MCPRunnerResourceExceeded(
                    "MCP stdio runner output exceeded the size limit"
                ) from exc
            except SandboxBackendError as exc:
                raise MCPProtocolFailure(
                    f"MCP stdio runner execution failed: {type(exc).__name__}"
                ) from exc
            if result.timed_out:
                raise MCPRunnerTimeout(
                    "MCP stdio runner exceeded the invocation deadline"
                )
            if result.exit_code != 0:
                stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()[:500]
                raise MCPProtocolFailure(stderr or "MCP stdio runner failed")
            raw = backend.read(session, "mcp-response.json", limit_bytes=int(self.settings.mcp_stdio_result_bytes))
            response = json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MCPProtocolFailure("MCP stdio runner returned invalid JSON") from exc

        if "result" in response:
            return dict(response["result"] or {})
        error = response.get("error")
        if isinstance(error, dict):
            raise MCPProtocolFailure(
                str(error.get("message") or "MCP stdio tool call failed"),
            )
        raise MCPProtocolFailure("MCP stdio runner returned an invalid JSON-RPC response")

    def invoke(
        self,
        launch_spec: dict[str, Any],
        *,
        tool_name: str,
        arguments: dict[str, Any],
        credential_envelope: dict[str, Any] | None = None,
    ) -> MCPToolCallResult:
        session_spec = launch_spec
        if not launch_spec.get("session_id") or not launch_spec.get("backend_ref"):
            # Self-contained one-shot invocation: provision a fresh isolated
            # container, run, and terminate — the FastAPI process never spawns
            # the third-party command itself.
            provisioned = self.provision(launch_spec)
            session_spec = {**launch_spec, **provisioned}
        try:
            result = self.call_jsonrpc(
                session_spec,
                method="tools/call",
                params={"name": tool_name, "arguments": arguments},
                request_id=1,
                credential_envelope=credential_envelope,
            )
        finally:
            self.terminate(session_spec)
        return MCPToolCallResult(result=result)

    def terminate(self, launch_spec: dict[str, Any]) -> None:
        try:
            self._backend().delete(
                self._backend().resume(
                    launch_spec["session_id"],
                    launch_spec["backend_ref"],
                )
            )
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        self._remove_runner_workspace(launch_spec)
        self._mark_runner_session_terminated(
            workspace_id=str(launch_spec.get("workspace_id") or ""),
            session_id=str(launch_spec.get("session_id") or ""),
        )

    def _remove_runner_workspace(self, launch_spec: dict[str, Any]) -> None:
        """Best-effort removal of the isolated runner workspace directory."""
        workspace_dir = launch_spec.get("workspace_dir")
        if workspace_dir:
            shutil.rmtree(workspace_dir, ignore_errors=True)
            return
        session_id = str(launch_spec.get("session_id") or "")
        if not session_id:
            return
        root = Path(self.settings.sandbox_workspace_root).expanduser().resolve()
        shutil.rmtree(root / f"mcp-{session_id}", ignore_errors=True)


class StdioIsolatedMCPAdapter:
    """MCPTransportPort adapter routing through an isolated stdio runner."""

    transport_id = "stdio"

    def __init__(
        self,
        runner: DockerStdioMCPRunner,
        launch_spec: dict[str, Any],
        *,
        credential_envelope: dict[str, Any] | None = None,
        credential_resolver: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self._runner = runner
        self._launch_spec = launch_spec
        self._credential_envelope = credential_envelope
        self._credential_resolver = credential_resolver

    @property
    def available(self) -> bool:
        return bool(self._runner.available)

    @property
    def unavailable_reason(self) -> str:
        return self._runner.unavailable_reason

    def probe(self) -> MCPProbeResult:
        return self._runner.probe(self._launch_spec)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> MCPToolCallResult:
        # A fresh resolver envelope is fetched at call time so OAuth expiry /
        # refresh state is current; a static envelope wins when no resolver is
        # configured (declarative tests / bearer-less stdio).
        envelope = (
            self._credential_resolver()
            if self._credential_resolver is not None
            else self._credential_envelope
        )
        return self._runner.invoke(
            self._launch_spec,
            tool_name=tool_name,
            arguments=arguments,
            credential_envelope=envelope,
        )
