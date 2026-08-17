"""sandboxd controller: ownership, idempotency, protocol checks, lifecycle.

The controller has no Docker knowledge — it delegates to the injected
RuntimeBackendPort and persists state in the store. Error codes follow the
stable protocol contract (see protocol.ERROR_CODES).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sandboxd.config import (
    PROTOCOL_MAX,
    PROTOCOL_MIN,
    RUNNER_ABI_MAX,
    RUNNER_ABI_MIN,
    SandboxdConfig,
)
from sandboxd.paths import InvalidPathError, scope_key, validate_relative_path
from sandboxd.protocol import (
    AgentExecRequest,
    Capacity,
    Capabilities,
    CreateSandboxRequest,
    EgressRef,
    ExecResult,
    FileIndex,
    FileListEntry,
    FixedExecRequest,
    HealthReady,
    KernelCellRequest,
    KernelCellResult,
    KernelOpenRequest,
    KernelOpenResult,
    RUNTIME_KINDS,
    SandboxView,
)
from sandboxd.runtime.port import RuntimeBackendPort, RuntimeCreateSpec, RuntimeHandle
from sandboxd.runtime.docker import (
    DockerOutputLimitExceeded,
    DockerRuntimeError,
    DockerRuntimeUnavailable,
    DockerWorkspaceQuotaExceeded,
    sanitize_name,
)
from sandboxd.store import SandboxRecord, SandboxdStore

logger = logging.getLogger("sandboxd.controller")

DEFAULT_RUNNER_ABI = "1"
_SANDBOX_ID_PREFIX = "sb_"


class SandboxdError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def _version_ok(version: str, minimum: str, maximum: str) -> bool:
    def parts(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in value.split(".") if part.isdigit()) or (0,)

    return parts(minimum) <= parts(version) <= parts(maximum)


def _validate_policy_digest(digest: str) -> str:
    value = digest.strip()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.casefold()):
        raise SandboxdError("invalid_request", "policy digest must be a sha256 hex digest")
    return value.casefold()


def _safe_path(path: str, *, allow_dot: bool = False) -> str:
    """Validate a workspace path and map containment failures to the protocol error."""
    try:
        return validate_relative_path(path, allow_dot=allow_dot).value
    except InvalidPathError as exc:
        raise SandboxdError("invalid_path", str(exc)) from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _limits_match(existing_limits: dict[str, Any], body: CreateSandboxRequest) -> bool:
    """True when an existing sandbox's resource/egress profile is compatible.

    Same-user instance reuse must not silently downgrade/upgrade the physical
    container; incompatible requests keep the legacy idempotency semantics.
    """
    return (
        int(existing_limits.get("memory_bytes") or 0) == body.memory_bytes
        and int(existing_limits.get("memory_swap_bytes") or 0) == body.memory_swap_bytes
        and abs(float(existing_limits.get("cpu_count") or 0) - body.cpu_count) < 1e-6
        and int(existing_limits.get("pids_max") or 0) == body.pids_max
        and int(existing_limits.get("disk_bytes") or 0) == body.disk_bytes
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ExecOutcome:
    result: ExecResult | None
    indeterminate_execution_id: str | None = None


class SandboxController:
    def __init__(
        self,
        config: SandboxdConfig,
        store: SandboxdStore,
        runtime: RuntimeBackendPort,
    ) -> None:
        self.config = config
        self.store = store
        self.runtime = runtime
        self._lock = threading.RLock()
        self._reconcile_status = "not_run"
        self._reconcile_reason: list[str] = []
        # kernel_id -> {kernel_id, sandbox_id, scope, interpreter, workspace_relative}
        self._kernels: dict[str, dict[str, str]] = {}

    # --- protocol surface --------------------------------------------------

    def capabilities(self) -> Capabilities:        return Capabilities(
            daemon_version="0.1.0",
            limits={
                "max_file_bytes": self.config.max_file_bytes,
                "max_request_bytes": self.config.max_request_bytes,
                "max_stdout_bytes": self.config.max_stdout_bytes,
                "max_argv_count": 64,
            },
        )

    def capacity(self) -> Capacity:
        cpu, memory = self.runtime.capacity()
        observed: dict[str, Any] = {}
        observe = getattr(self.runtime, "observe", None)
        if callable(observe):
            try:
                observed = observe() or {}
            except Exception:  # noqa: BLE001 - probe never fails the endpoint
                observed = {}
        return Capacity(
            cpu_count=cpu,
            memory_bytes=memory,
            observed_memory_bytes=int(observed.get("observed_memory_bytes") or 0),
            observed_cpu_percent=float(observed.get("observed_cpu_percent") or 0.0),
            active_containers=int(observed.get("active_containers") or 0),
        )

    # --- bootstrap ---------------------------------------------------------

    def _runtime_image_for(self, runtime_kind: str) -> str:
        """Immutable runtime image for a kind: env pin first, then installed record."""
        if self.config.runtime_image:
            return self.config.runtime_image
        record = self.store.get_runtime(runtime_kind)
        if record is not None:
            return str(record["image_digest"])
        return ""

    def install_runtime(self, runtime_kind: str, image_tag: str) -> dict[str, Any]:
        """Validate a prebuilt runner image and record it as the active runtime.

        This is the prebuilt (pull) bootstrap path: the tag is pulled, resolved
        to a single immutable RepoDigest, its runner ABI label is verified, and
        a bounded offline smoke (Python + Node probes under the hardened
        profile) must pass before the record is persisted. Local image builds
        stay a legacy app-side operation for the ``docker`` backend only.
        """
        if runtime_kind not in RUNTIME_KINDS:
            raise SandboxdError("invalid_request", f"unsupported runtime kind: {runtime_kind}")
        try:
            digest, labels = self.runtime.pull_and_resolve_digest(image_tag)
        except Exception as exc:  # noqa: BLE001 - map runtime failures to the protocol
            raise SandboxdError(
                "runtime_unavailable",
                f"bootstrap pull failed for {image_tag}: {type(exc).__name__}",
                retryable=True,
            ) from exc
        runner_abi = str(labels.get("com.learngraph.runner-abi") or "")
        if not runner_abi:
            raise SandboxdError(
                "runner_abi_mismatch",
                f"bootstrap image {image_tag} has no com.learngraph.runner-abi label",
            )
        if not (RUNNER_ABI_MIN <= runner_abi <= RUNNER_ABI_MAX):
            raise SandboxdError(
                "runner_abi_mismatch",
                f"runner ABI {runner_abi} is outside [{RUNNER_ABI_MIN}, {RUNNER_ABI_MAX}]",
            )
        try:
            smoke_ok, smoke_detail = self.runtime.smoke_test(digest, runtime_kind)
        except Exception as exc:  # noqa: BLE001 - smoke must not crash the daemon
            smoke_ok, smoke_detail = False, f"{type(exc).__name__}: {exc}"
        if not smoke_ok:
            raise SandboxdError(
                "runtime_unavailable",
                f"bootstrap smoke failed for {image_tag}: {smoke_detail or 'unknown error'}",
            )
        self.store.upsert_runtime(
            runtime_kind=runtime_kind,
            image_digest=digest,
            runner_abi=runner_abi,
            source="prebuilt",
            labels=labels,
            smoke_status="passed",
        )
        return {
            "runtime_kind": runtime_kind,
            "image_digest": digest,
            "runner_abi": runner_abi,
            "source": "prebuilt",
            "smoke_status": "passed",
        }

    def list_runtimes(self) -> list[dict[str, Any]]:
        return [
            {
                "runtime_kind": record["runtime_kind"],
                "image_digest": record["image_digest"],
                "runner_abi": record["runner_abi"],
                "source": record["source"],
                "smoke_status": record["smoke_status"],
            }
            for record in self.store.list_runtimes()
        ]

    def health(self) -> HealthReady:
        reasons: list[str] = []
        docker_ok = True
        store_ok = self.store.ping()
        runtime_ok = True
        capability = self.runtime.probe()
        if not capability.available:
            docker_ok = False
            reasons.append(capability.reason or "docker runtime unavailable")
        if not store_ok:
            reasons.append("state store is not writable")
        if not self._runtime_image_for("python-node") and not capability.available:
            runtime_ok = False
            reasons.append("no runtime image is configured or installed")
        return HealthReady(
            ready=docker_ok and store_ok and runtime_ok,
            reasons=reasons,
            docker=docker_ok,
            store=store_ok,
            runtime=runtime_ok,
            reconcile=self._reconcile_status,
        )

    # --- create ------------------------------------------------------------

    def create(self, body: CreateSandboxRequest) -> SandboxView:
        if not _version_ok(body.protocol_version, PROTOCOL_MIN, PROTOCOL_MAX):
            raise SandboxdError(
                "protocol_incompatible",
                f"protocol version {body.protocol_version} is outside [{PROTOCOL_MIN}, {PROTOCOL_MAX}]",
            )
        if body.owner.deployment_id != self.config.deployment_id:
            raise SandboxdError("owner_mismatch", "deployment id does not match this sandboxd instance")

        owner_scope = scope_key(body.owner.deployment_id, body.owner.session_id)
        # Execution-pool reuse: the same deployment+session_id maps to one
        # physical sandbox (a user's warm instance). A still-alive sandbox for
        # this session with a compatible resource/egress profile is returned
        # instead of creating a duplicate, so every chat workspace of the same
        # user shares the user's instance container.
        existing = self.store.get_sandbox_by_session(
            self.config.deployment_id, body.session_id
        )
        if existing is not None:
            try:
                expires = datetime.fromisoformat(existing.expires_at)
            except ValueError:
                expires = _utc_now()
            if (
                expires > _utc_now()
                and existing.state not in {"DELETING", "ERROR"}
                and existing.owner_scope == owner_scope
                and _limits_match(existing.limits, body)
                and (existing.policy_digest or None)
                == ((body.egress.policy_digest.casefold()) if body.egress else None)
            ):
                return self._view(existing)
        payload_hash = _payload_hash(body.model_dump(mode="json"))
        proceed, replay = self.store.begin_idempotent(
            owner_scope, body.idempotency_key, "create", payload_hash
        )
        if not proceed:
            if replay.payload_hash != payload_hash:
                raise SandboxdError(
                    "idempotency_conflict",
                    "idempotency key was already used with a different payload",
                )
            if replay.state == "succeeded" and replay.result_json:
                view = SandboxView.model_validate_json(replay.result_json)
                # The sandbox record may have been swept by the TTL sweep while
                # the idempotency ledger survived; re-create in that case
                # instead of returning a dead resource id.
                if self.store.get_sandbox(view.sandbox_id) is not None:
                    return view
            raise SandboxdError(
                "execution_indeterminate",
                "create is still in progress or its outcome is unknown; retry with the same key",
                retryable=True,
            )

        if self.store.count_active(self.config.deployment_id) >= self.config.max_active:
            raise SandboxdError("capacity_exceeded", "sandbox capacity exceeded", retryable=True)
        runtime_image = self._runtime_image_for(body.runtime_kind)
        if not runtime_image:
            raise SandboxdError("runtime_unavailable", "no immutable runtime image is configured")

        try:
            view = self._create_impl(body, owner_scope)
        except Exception:
            self.store.complete_idempotent(
                owner_scope, body.idempotency_key, state="failed", error_code="execution_failed"
            )
            raise
        self.store.complete_idempotent(
            owner_scope, body.idempotency_key, state="succeeded", result_json=view.model_dump_json()
        )
        return view

    def _create_impl(self, body: CreateSandboxRequest, owner_scope: str) -> SandboxView:
        sandbox_id = f"{_SANDBOX_ID_PREFIX}{uuid.uuid4().hex[:12]}"
        volume_name = sanitize_name(f"{self.config.deployment_id}-sb-{sandbox_id}")
        egress = self._resolve_egress(body.egress, sandbox_id)
        policy_digest = egress[0] if egress else None
        egress_network = egress[1] if egress else None

        now = _utc_now()
        expires_at = now + timedelta(seconds=body.ttl_seconds)
        runtime_image = self._runtime_image_for(body.runtime_kind)
        limits_json = _canonical_json(
            {
                "memory_bytes": body.memory_bytes,
                "memory_swap_bytes": body.memory_swap_bytes,
                "cpu_count": body.cpu_count,
                "pids_max": body.pids_max,
                "disk_bytes": body.disk_bytes,
                "wall_time_seconds": body.ttl_seconds,
            }
        )
        spec = RuntimeCreateSpec(
            sandbox_id=sandbox_id,
            session_id=body.session_id,
            workspace_key=body.workspace_key,
            runtime_kind=body.runtime_kind,
            image_ref=runtime_image,
            volume_name=volume_name,
            deployment_id=self.config.deployment_id,
            memory_bytes=body.memory_bytes,
            memory_swap_bytes=body.memory_swap_bytes,
            cpu_count=body.cpu_count,
            pids_max=body.pids_max,
            disk_bytes=body.disk_bytes,
            policy_digest=policy_digest,
            egress_network=egress_network,
            egress_proxy_url=self.config.egress_proxy_url,
        )
        handle = self.runtime.create(spec)
        record = SandboxRecord(
            sandbox_id=sandbox_id,
            deployment_id=self.config.deployment_id,
            owner_scope=owner_scope,
            owner_workspace_id=body.owner.workspace_id,
            owner_session_id=body.owner.session_id,
            session_id=body.session_id,
            workspace_key=body.workspace_key,
            runtime_kind=body.runtime_kind,
            state="RUNNING",
            volume_name=volume_name,
            container_id=handle.container_id,
            image_digest=runtime_image,
            runner_abi=DEFAULT_RUNNER_ABI,
            policy_digest=policy_digest,
            egress_network=egress_network,
            limits_json=limits_json,
            ttl_seconds=body.ttl_seconds,
            expires_at=expires_at.isoformat(timespec="seconds"),
            created_at=now.isoformat(timespec="seconds"),
            updated_at=now.isoformat(timespec="seconds"),
            last_used_at=now.isoformat(timespec="seconds"),
        )
        self.store.insert_sandbox(record)
        return self._view(record)

    def _resolve_egress(self, egress: EgressRef | None, sandbox_id: str) -> tuple[str, str] | None:
        if egress is None:
            return None
        if not self.config.egress_network_enabled:
            raise SandboxdError("capability_missing", "egress is disabled on this sandboxd instance")
        digest = _validate_policy_digest(egress.policy_digest)
        network = sanitize_name(f"{self.config.deployment_id}-eg-{sandbox_id}")
        return digest, network

    # --- helpers -----------------------------------------------------------

    def _require(self, sandbox_id: str, scope: str, fence: int | None = None) -> SandboxRecord:
        record = self.store.get_sandbox(sandbox_id)
        if record is None:
            raise SandboxdError("sandbox_not_found", "sandbox was not found")
        if record.owner_scope != scope:
            raise SandboxdError("owner_mismatch", "sandbox is owned by a different scope")
        if record.deployment_id != self.config.deployment_id:
            raise SandboxdError("owner_mismatch", "sandbox belongs to a different deployment")
        # Fencing: once a sandbox enters DELETING its fence generation is
        # bumped, invalidating every in-flight RPC that still holds the old
        # generation. A caller without a fence header is only accepted while
        # the fence is still 0 (pre-delete steady state).
        fence_required = int(record.fence_generation or 0)
        if fence_required > 0 and fence != fence_required:
            raise SandboxdError(
                "stale_fence",
                "sandbox lease generation is stale; the sandbox is being reclaimed",
            )
        try:
            expires = datetime.fromisoformat(record.expires_at)
        except ValueError:
            expires = _utc_now()
        if expires <= _utc_now():
            raise SandboxdError("sandbox_expired", "sandbox has expired")
        return record

    def _touch(self, sandbox_id: str) -> None:
        now = _utc_now().isoformat(timespec="seconds")
        self.store.update_sandbox(sandbox_id, last_used_at=now)

    def _view(self, record: SandboxRecord) -> SandboxView:
        return SandboxView(
            sandbox_id=record.sandbox_id,
            state=record.state,
            runtime_kind=record.runtime_kind,
            image_digest=record.image_digest,
            runner_abi=record.runner_abi,
            expires_at=record.expires_at,
            created_at=record.created_at,
            last_used_at=record.last_used_at,
            policy_digest=record.policy_digest,
            workspace_key=record.workspace_key,
        )

    def get(self, sandbox_id: str, scope: str, fence: int | None = None) -> SandboxView:
        return self._view(self._require(sandbox_id, scope, fence=fence))

    # --- lifecycle ---------------------------------------------------------

    def _ensure_runtime(self, record: SandboxRecord) -> RuntimeHandle:
        """Resume a runtime, recreating its container when it is missing."""
        try:
            return self.runtime.resume(record.sandbox_id, record.container_id)
        except DockerRuntimeError:
            # Container is missing: rebuild from the durable record (volume and
            # egress network are re-attached by the runtime adapter).
            spec = RuntimeCreateSpec(
                sandbox_id=record.sandbox_id,
                session_id=record.session_id,
                workspace_key=record.workspace_key,
                runtime_kind=record.runtime_kind,
                image_ref=record.image_digest,
                volume_name=record.volume_name,
                deployment_id=self.config.deployment_id,
                memory_bytes=int(record.limits.get("memory_bytes") or 512 * 1024 * 1024),
                memory_swap_bytes=int(record.limits.get("memory_swap_bytes") or 512 * 1024 * 1024),
                cpu_count=float(record.limits.get("cpu_count") or 1.0),
                pids_max=int(record.limits.get("pids_max") or 64),
                disk_bytes=int(record.limits.get("disk_bytes") or 1024 * 1024 * 1024),
                policy_digest=record.policy_digest,
                egress_network=record.egress_network,
                egress_proxy_url=self.config.egress_proxy_url,
            )
            return self.runtime.create(spec)

    def resume(self, sandbox_id: str, scope: str, fence: int | None = None) -> SandboxView:
        with self._lock:
            record = self._require(sandbox_id, scope, fence=fence)
            if record.state in {"DELETING", "ERROR"}:
                raise SandboxdError("invalid_state", f"sandbox is in state {record.state}")
            handle = self._ensure_runtime(record)
            now = _utc_now().isoformat(timespec="seconds")
            self.store.update_sandbox(
                sandbox_id, state="RUNNING", container_id=handle.container_id, last_used_at=now
            )
            return self._view(self.store.get_sandbox(sandbox_id))

    def stop(self, sandbox_id: str, scope: str, fence: int | None = None) -> SandboxView:
        with self._lock:
            record = self._require(sandbox_id, scope, fence=fence)
            if record.state not in {"RUNNING", "STARTING"}:
                return self._view(record)
            self._close_kernels_for(sandbox_id, scope)
            self.runtime.stop(RuntimeHandle(record.sandbox_id, record.container_id))
            now = _utc_now().isoformat(timespec="seconds")
            self.store.update_sandbox(sandbox_id, state="STOPPED", last_used_at=now)
            return self._view(self.store.get_sandbox(sandbox_id))

    def delete(self, sandbox_id: str, scope: str) -> None:
        with self._lock:
            record = self.store.get_sandbox(sandbox_id)
            if record is None:
                # Idempotent: already deleted.
                return
            if record.owner_scope != scope or record.deployment_id != self.config.deployment_id:
                raise SandboxdError("owner_mismatch", "sandbox is owned by a different scope")
            # Bump the fence BEFORE deleting resources: every in-flight exec/file
            # RPC carrying the old generation is rejected while the writable
            # layer is being torn down.
            self.store.update_sandbox(
                sandbox_id,
                state="DELETING",
                fence_generation=int(record.fence_generation or 0) + 1,
            )
            try:
                self._close_kernels_for(sandbox_id, scope)
                self.runtime.delete(RuntimeHandle(record.sandbox_id, record.container_id))
            finally:
                self.store.delete_sandbox(sandbox_id)

    def _close_kernels_for(self, sandbox_id: str, scope: str) -> None:
        """Best-effort teardown of every kernel attached to a sandbox."""
        for kernel_id, record in list(self._kernels.items()):
            if record.get("sandbox_id") != sandbox_id or record.get("scope") != scope:
                continue
            try:
                handle = RuntimeHandle(sandbox_id, None)
                self.runtime.stop_kernel(
                    handle,
                    kernel_id,
                    record.get("workspace_relative") or ".",
                )
            except Exception:  # noqa: BLE001 - teardown is best-effort
                logger.debug("kernel %s teardown failed during sandbox %s cleanup", kernel_id, sandbox_id)
            self._kernels.pop(kernel_id, None)

    # --- kernels ------------------------------------------------------------

    def kernel_open(
        self, sandbox_id: str, scope: str, body: KernelOpenRequest
    ) -> KernelOpenResult:
        with self._lock:
            record = self._require(sandbox_id, scope)
            workspace_relative = _safe_path(body.workspace_relative or ".", allow_dot=True)
            handle = self._ensure_runtime(record)
            try:
                kernel_id = self.runtime.start_kernel(
                    handle, workspace_relative, body.interpreter
                )
            except Exception as exc:  # noqa: BLE001 - map runtime failures
                raise SandboxdError(
                    "execution_failed",
                    f"kernel start failed: {type(exc).__name__}: {exc}",
                ) from exc
            self._kernels[kernel_id] = {
                "kernel_id": kernel_id,
                "sandbox_id": sandbox_id,
                "scope": scope,
                "interpreter": body.interpreter,
                "workspace_relative": workspace_relative,
            }
            self._touch(sandbox_id)
            return KernelOpenResult(kernel_id=kernel_id, interpreter=body.interpreter)

    def kernel_execute(
        self, kernel_id: str, scope: str, body: KernelCellRequest
    ) -> KernelCellResult:
        with self._lock:
            kernel = self._kernels.get(kernel_id)
            if kernel is None or kernel.get("scope") != scope:
                raise SandboxdError("kernel_not_found", "kernel was not found or is not owned by this scope")
            sandbox_id = kernel["sandbox_id"]
            record = self._require(sandbox_id, scope)
            handle = self._ensure_runtime(record)
            try:
                result = self.runtime.exec_kernel_cell(
                    handle,
                    kernel_id,
                    kernel.get("workspace_relative") or ".",
                    body.code,
                    timeout_seconds=body.timeout_seconds,
                    output_limit=body.output_limit,
                )
            except Exception as exc:  # noqa: BLE001 - map runtime failures
                raise SandboxdError(
                    "execution_failed",
                    f"kernel cell execution failed: {type(exc).__name__}: {exc}",
                ) from exc
            self._touch(sandbox_id)
        stdout_text = result.stdout.decode("utf-8", errors="replace")
        try:
            payload = json.loads(stdout_text)
        except json.JSONDecodeError:
            payload = {
                "ok": False,
                "stdout": "",
                "stderr": stdout_text[:4_096] or "kernel client returned no structured result",
                "result_repr": None,
                "timed_out": result.timed_out,
            }
        return KernelCellResult(
            kernel_id=kernel_id,
            ok=bool(payload.get("ok")),
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
            result_repr=payload.get("result_repr"),
            timed_out=bool(payload.get("timed_out") or result.timed_out),
        )

    def kernel_close(self, kernel_id: str, scope: str) -> dict[str, Any]:
        with self._lock:
            kernel = self._kernels.get(kernel_id)
            if kernel is None or kernel.get("scope") != scope:
                raise SandboxdError("kernel_not_found", "kernel was not found or is not owned by this scope")
            sandbox_id = kernel["sandbox_id"]
            self._kernels.pop(kernel_id, None)
            try:
                record = self.store.get_sandbox(sandbox_id)
                if record is not None and record.owner_scope == scope:
                    handle = self._ensure_runtime(record)
                    self.runtime.stop_kernel(
                        handle,
                        kernel_id,
                        kernel.get("workspace_relative") or ".",
                    )
            except Exception:  # noqa: BLE001 - kernel teardown is best-effort
                logger.debug("kernel %s close teardown failed", kernel_id)
            return {"kernel_id": kernel_id, "closed": True}

    # --- files -------------------------------------------------------------

    def write_file(self, sandbox_id: str, scope: str, path: str, data: bytes, *, mode: int = 0o644, fence: int | None = None) -> None:
        safe_value = _safe_path(path)
        if len(data) > self.config.max_file_bytes:
            raise SandboxdError("file_too_large", "file exceeds the daemon file limit")
        with self._lock:
            record = self._require(sandbox_id, scope, fence=fence)
            handle = self._ensure_runtime(record)
            try:
                self.runtime.write_file(handle, safe_value, data, mode=mode)
            except DockerWorkspaceQuotaExceeded as exc:
                raise SandboxdError("workspace_quota_exceeded", str(exc)) from exc
            except DockerOutputLimitExceeded as exc:
                raise SandboxdError("output_limit_exceeded", str(exc)) from exc
            self._touch(sandbox_id)

    def delete_file(self, sandbox_id: str, scope: str, path: str, *, fence: int | None = None) -> None:
        safe_value = _safe_path(path)
        with self._lock:
            record = self._require(sandbox_id, scope, fence=fence)
            handle = self._ensure_runtime(record)
            self.runtime.delete_file(handle, safe_value)
            self._touch(sandbox_id)

    def read_file(self, sandbox_id: str, scope: str, path: str, limit_bytes: int, *, fence: int | None = None) -> bytes:
        safe_value = _safe_path(path)
        with self._lock:
            record = self._require(sandbox_id, scope, fence=fence)
            handle = self._ensure_runtime(record)
            data = self.runtime.read_file(handle, safe_value, limit_bytes)
            self._touch(sandbox_id)
            return data

    def list_files(self, sandbox_id: str, scope: str, prefix: str, limit: int, cursor: str | None, *, fence: int | None = None) -> FileIndex:
        safe_prefix = _safe_path(prefix or ".", allow_dot=True)
        if safe_prefix == ".":
            safe_prefix = ""
        with self._lock:
            record = self._require(sandbox_id, scope, fence=fence)
            handle = self._ensure_runtime(record)
            entries, next_cursor = self.runtime.list_files(handle, safe_prefix, limit, cursor)
            self._touch(sandbox_id)
            return FileIndex(
                entries=[FileListEntry(path=e.path, size_bytes=e.size_bytes) for e in entries],
                cursor=next_cursor,
            )

    # --- exec --------------------------------------------------------------

    def _validate_argv(self, argv: list[str]) -> tuple[str, ...]:
        if not argv or len(argv) > 64:
            raise SandboxdError("command_rejected", "argv must contain 1..64 arguments")
        for item in argv:
            if not isinstance(item, str) or not item:
                raise SandboxdError("command_rejected", "argv entries must be non-empty strings")
            if len(item) > 1024:
                raise SandboxdError("command_rejected", "argv entry exceeds the length limit")
            if "\x00" in item:
                raise SandboxdError("command_rejected", "argv entry contains a NUL byte")
        return tuple(argv)

    def _exec_common(
        self,
        sandbox_id: str,
        scope: str,
        *,
        operation: str,
        argv: list[str],
        cwd: str,
        timeout_seconds: int,
        output_limit: int,
        idempotency_key: str,
        fence: int | None = None,
    ) -> ExecOutcome:
        validated_argv = self._validate_argv(argv)
        safe_cwd = _safe_path(cwd or ".", allow_dot=True)
        # Short critical section: ownership/state/idempotency/record bookkeeping
        # only. The actual Docker exec runs OUTSIDE the global lock so parallel
        # executions (and other sandboxes) are not serialized by this daemon.
        with self._lock:
            record = self._require(sandbox_id, scope, fence=fence)
            payload = {
                "argv": argv,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "output_limit": output_limit,
            }
            payload_hash = _payload_hash(payload)
            key_scope = f"{record.owner_scope}|{sandbox_id}"
            proceed, replay = self.store.begin_idempotent(
                key_scope, idempotency_key, operation, payload_hash
            )
            if not proceed:
                if replay.payload_hash != payload_hash:
                    raise SandboxdError(
                        "idempotency_conflict",
                        "idempotency key was already used with a different payload",
                    )
                if replay.state == "succeeded" and replay.result_json:
                    return ExecOutcome(result=ExecResult.model_validate_json(replay.result_json))
                execution_id = None
                if replay.result_json:
                    try:
                        execution_id = str(json.loads(replay.result_json).get("execution_id") or "")
                    except (json.JSONDecodeError, AttributeError):
                        execution_id = None
                raise SandboxdError(
                    "execution_indeterminate",
                    "execution outcome is unknown; do not re-run the command",
                    retryable=True,
                    details={"execution_id": execution_id} if execution_id else {},
                )

            execution_id = f"ex_{uuid.uuid4().hex[:16]}"
            argv_digest = hashlib.sha256("\0".join(validated_argv).encode("utf-8")).hexdigest()
            self.store.create_execution(
                execution_id=execution_id,
                sandbox_id=sandbox_id,
                deployment_id=self.config.deployment_id,
                operation=operation,
                argv_digest=argv_digest,
            )
            self.store.complete_idempotent(
                key_scope,
                idempotency_key,
                state="in_progress",
                result_json=json.dumps({"execution_id": execution_id}),
            )
            handle = self._ensure_runtime(record)
        # Outside the global lock: a slow/hung exec must not block other
        # sandboxes or executions.
        try:
            if operation == "fixed":
                result = self.runtime.exec_fixed(
                    handle,
                    validated_argv,
                    execution_id=execution_id,
                    timeout_seconds=timeout_seconds,
                    output_limit=output_limit,
                )
            else:
                result = self.runtime.exec_agent(
                    handle,
                    validated_argv,
                    execution_id=execution_id,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    output_limit=output_limit,
                )
        except DockerRuntimeUnavailable as exc:
            with self._lock:
                self.store.complete_idempotent(key_scope, idempotency_key, state="failed", error_code="runtime_unavailable")
            raise SandboxdError("runtime_unavailable", str(exc), retryable=True) from exc
        except DockerRuntimeError as exc:
            with self._lock:
                self.store.complete_idempotent(key_scope, idempotency_key, state="failed", error_code="execution_failed")
            raise SandboxdError("execution_failed", str(exc)) from exc

        with self._lock:
            self._touch(sandbox_id)
            max_transport = self.config.max_stdout_bytes
            stdout_text = result.stdout.decode("utf-8", errors="replace")[:max_transport]
            stderr_text = result.stderr.decode("utf-8", errors="replace")[:max_transport]
            if result.timed_out:
                status = "timeout"
            elif result.exit_code == 0:
                status = "succeeded"
            else:
                status = "failed"
            exec_result = ExecResult(
                execution_id=execution_id,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                truncated=result.truncated,
                stdout=stdout_text,
                stderr=stderr_text,
                latency_ms=result.latency_ms,
                status=status,
            )
            self.store.finish_execution(
                execution_id=execution_id,
                status=status,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                truncated=result.truncated,
                latency_ms=result.latency_ms,
                finished_reason=status,
            )
            self.store.complete_idempotent(
                key_scope, idempotency_key, state="succeeded", result_json=exec_result.model_dump_json()
            )
            return ExecOutcome(result=exec_result)

    def cancel_execution(self, execution_id: str, scope: str) -> dict[str, Any]:
        """Request cancellation of a live execution.

        Marks the execution ``cancelling`` durably and best-effort asks the
        runtime to stop the process tree. The synchronous exec path cannot be
        interrupted mid-flight without a container supervisor; the mark is what
        the scheduler and operators observe, and the supervisor phase (task
        cgroup / process-group TERM→KILL) plugs in at ``runtime.cancel_exec``.
        """
        record = self.store.get_execution(execution_id)
        if record is None:
            raise SandboxdError("sandbox_not_found", "execution was not found")
        sandbox = self.store.get_sandbox(record["sandbox_id"])
        if (
            sandbox is None
            or sandbox.owner_scope != scope
            or sandbox.deployment_id != self.config.deployment_id
        ):
            raise SandboxdError("owner_mismatch", "execution belongs to a different scope")
        with self._lock:
            cancelled = self.store.cancel_execution(execution_id)
        if cancelled:
            cancel_callable = getattr(self.runtime, "cancel_exec", None)
            if callable(cancel_callable):
                try:
                    cancel_callable(
                        RuntimeHandle(sandbox.sandbox_id, sandbox.container_id),
                        execution_id,
                    )
                except Exception:  # noqa: BLE001 - best-effort process termination
                    logger.exception("runtime cancel failed for execution %s", execution_id)
        return {
            "execution_id": execution_id,
            "sandbox_id": sandbox.sandbox_id,
            "status": "cancelling" if cancelled else record["status"],
        }

    def exec_fixed(self, sandbox_id: str, scope: str, body: FixedExecRequest, *, fence: int | None = None) -> ExecOutcome:
        _safe_path(body.input_path)
        _safe_path(body.output_path)
        argv = (
            "python",
            "/opt/learngraph/runner.py",
            "--task",
            body.task_type,
            "--input",
            body.input_path,
            "--output",
            body.output_path,
        )
        return self._exec_common(
            sandbox_id,
            scope,
            operation="fixed",
            argv=list(argv),
            cwd=".",
            timeout_seconds=body.timeout_seconds,
            output_limit=body.output_limit,
            idempotency_key=body.idempotency_key,
            fence=fence,
        )

    def exec_agent(self, sandbox_id: str, scope: str, body: AgentExecRequest, *, fence: int | None = None) -> ExecOutcome:
        return self._exec_common(
            sandbox_id,
            scope,
            operation="agent",
            argv=body.argv,
            cwd=body.cwd,
            timeout_seconds=body.timeout_seconds,
            output_limit=body.output_limit,
            idempotency_key=body.idempotency_key,
            fence=fence,
        )

    # --- reconciliation and TTL --------------------------------------------

    def reconcile(self) -> dict[str, int]:
        with self._lock:
            totals = {"adopted": 0, "orphan_deleted": 0, "missing_marked": 0, "errors": 0}
            try:
                orphans = self.runtime_reconcile_orphans()
                totals["orphan_deleted"] = orphans
                self._reconcile_status = "done"
                self._reconcile_reason = []
            except Exception as exc:  # noqa: BLE001
                self._reconcile_status = "degraded"
                self._reconcile_reason = [f"{type(exc).__name__}: {exc}"]
                totals["errors"] = 1
            return totals

    def runtime_reconcile_orphans(self) -> int:
        """Delete managed containers/volumes/networks whose sandbox record is gone.

        Only objects bearing this deployment's managed labels are considered;
        objects younger than the reconcile grace period are skipped so a
        concurrent create is never raced.
        """
        if not hasattr(self.runtime, "list_managed"):
            return 0
        cutoff = (_utc_now() - timedelta(seconds=self.config.reconcile_grace_seconds)).timestamp()
        known: set[str] = set()
        for record in self.store.list_sandboxes(self.config.deployment_id):
            known.add(record.sandbox_id)
        deleted = 0
        for sandbox_id, created_at in self.runtime.list_managed(self.config.deployment_id):
            if sandbox_id in known:
                continue
            try:
                created = datetime.fromisoformat(created_at).timestamp() if created_at else 0
            except ValueError:
                created = 0
            if created > cutoff:
                continue
            try:
                self.runtime.delete(RuntimeHandle(sandbox_id, None))
                deleted += 1
            except Exception:  # noqa: BLE001
                logger.exception("orphan cleanup failed for sandbox %s", sandbox_id)
        return deleted

    def sweep_expired(self) -> int:
        with self._lock:
            deleted = 0
            now = _utc_now()
            for record in self.store.list_sandboxes(self.config.deployment_id):
                try:
                    expires = datetime.fromisoformat(record.expires_at)
                except ValueError:
                    expires = now
                if expires > now:
                    continue
                try:
                    self.runtime.delete(RuntimeHandle(record.sandbox_id, record.container_id))
                except Exception:  # noqa: BLE001
                    logger.exception("TTL cleanup failed for sandbox %s", record.sandbox_id)
                    continue
                self.store.delete_sandbox(record.sandbox_id)
                deleted += 1
            return deleted
