from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import stat
import threading
import time
from pathlib import Path
from datetime import timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.file_lock import InterProcessFileLock
from app.core.security import Principal
from app.domain.models import (
    ChatSession,
    FileRecord,
    SandboxAgentCommand,
    SandboxExecution,
    SandboxInstance,
    SandboxSession,
    SandboxTask,
    Workspace,
    new_id,
    utc_now,
)
from app.services.sandbox_authz import (
    SandboxAuthorizationService,
    destructive_intent_digest,
)
from app.services.sandbox_toolkit import SandboxToolkitMixin
from app.services.session_workspace import SessionWorkspaceService
from app.domain.schemas.sandbox import (
    SandboxAgentBashRequest,
    SandboxAgentCommandRequest,
    SandboxAgentFileEditRequest,
    SandboxAgentFileAppendRequest,
    SandboxAgentEnvironmentRequest,
    SandboxAgentFetchRequest,
    SandboxAgentGitCloneRequest,
    SandboxAgentGitRequest,
    SandboxAgentImagePublishRequest,
    SandboxAgentFileDeleteRequest,
    SandboxAgentFileGrepRequest,
    SandboxAgentFileListRequest,
    SandboxAgentFileReadRequest,
    SandboxAgentFileWriteRequest,
    SandboxAgentNotebookRequest,
    SandboxAgentPatchRequest,
    SandboxAgentSearchRequest,
    SandboxAgentSessionCreateRequest,
    SandboxAgentSkillListRequest,
    SandboxAgentSkillReadRequest,
    SandboxAgentSubagentRequest,
    SandboxAgentTodoRequest,
    SandboxAgentTranscribeRequest,
    SandboxAgentVideoInfoRequest,
    SandboxTaskCreateRequest,
)
from app.providers.factory import transcription_provider_for_workspace
from app.providers.remote.transcription import TranscriptionProviderError
from app.services.billing import BillingService
from app.services.chat_attachment_policy import AUDIO_EXTENSIONS
from app.providers.ports.sandbox import (
    SandboxBackendPort,
    SandboxCreateSpec,
    SandboxSessionHandle,
)
from app.providers.remote.sandbox import (
    SandboxBackendError,
    SandboxBackendUnavailable,
    SandboxCapabilityMismatch,
    SandboxDestructiveAuthorizationRequired,
    SandboxOutputLimitExceeded,
    SandboxWorkspaceQuotaExceeded,
    image_ref_is_pinned,
    validate_agent_argv,
    validate_agent_cwd,
    validate_agent_workspace_path,
)
from app.providers.storage_factory import object_storage_provider
from app.repositories.audit import AuditRepository
from app.services.authorization import AuthorizationService
from app.services.sandbox_bootstrap import get_bootstrap_service
from app.providers.sandbox_registry import get_sandbox_backend_registry
from app.services.sandbox_runtime import (
    resolve_sandbox_image,
    resolve_sandbox_image_for_runtime,
)

_sandbox_capacity_lock = threading.RLock()
logger = logging.getLogger(__name__)


def _workload_class_for_argv(argv: Iterable[str]) -> str:
    """Map a validated argv to a workload class for server-side admission.

    Read-only scans get the cheapest envelope; heavy toolchains get bounded
    envelopes. The mapping is a hint only — the deployment's authoritative
    per-class resource vectors live in ``Settings.sandbox_workload_classes``.
    """
    lowered = [str(part).casefold() for part in argv]
    joined = " ".join(lowered)
    if any(
        token in lowered
        for token in (
            "grep",
            "rg",
            "find",
            "ls",
            "cat",
            "head",
            "tail",
            "wc",
            "sed",
            "awk",
            "diff",
            "file",
            "stat",
        )
    ) and not any(token in joined for token in ("npm", "pip", "python", "node")):
        return "read_only"
    if any(token in lowered for token in ("npm", "pnpm", "yarn", "vite", "webpack", "tsc")):
        return "build"
    if any(token in lowered for token in ("chromium", "playwright", "puppeteer")):
        return "browser"
    if any(token in lowered for token in ("python", "node", "ffmpeg")):
        return "python"
    return "default"


def _runtime_image_pinned(backend: Any, settings: Settings, local_resolved: str, runtime_kind: str = "python-node") -> bool:
    """Image-pinned status for UI/environment info.

    The sandboxd backend reports whether its control plane has an installed
    (smoke-passed) runtime; the legacy Docker backend and the
    admin-plane-unavailable case fall back to the locally persisted/env digest.
    """
    probe = getattr(backend, "runtime_image_pinned", None)
    if callable(probe):
        try:
            result = probe(runtime_kind)
        except Exception:  # noqa: BLE001 - never let a probe break the profile
            result = None
        if result is not None:
            return result
    return image_ref_is_pinned(local_resolved)

def _sandbox_workspace_root(settings: Settings) -> Path:
    root = settings.resolved_sandbox_workspace_root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sandbox_capacity_file_lock(settings: Settings) -> InterProcessFileLock:
    return InterProcessFileLock(_sandbox_workspace_root(settings) / ".runtime-capacity.lock")


def _sandbox_workspace_path(settings: Settings, relative_path: str) -> Path:
    root = _sandbox_workspace_root(settings)
    # String-based containment. ``Path.resolve()`` consults the filesystem and,
    # on Windows, can transiently return a different spelling (extended-length
    # ``\\?\`` prefix or 8.3 short name) while parent directories are being
    # created concurrently, which made the old ``parents()``-based check
    # spuriously reject valid paths under parallel first-use. ``os.path.abspath``
    # is a deterministic textual normalization, so this security check is
    # race-free while keeping the fail-closed semantics (``..``/absolute/other
    # drive inputs still normalize outside the root and are refused).
    candidate = Path(os.path.abspath(os.path.join(str(root), relative_path)))
    root_key = os.path.normcase(str(root))
    candidate_key = os.path.normcase(str(candidate))
    if candidate_key == root_key or not candidate_key.startswith(
        root_key.rstrip("\\/") + os.sep
    ):
        raise SandboxBackendError(
            f"Sandbox workspace path escaped the managed root (relative={relative_path!r})"
        )
    return candidate


def _initialize_workspace(
    settings: Settings, owner_user_id: str, sandbox_session_id: str
) -> str:
    relative = f"{owner_user_id}/{sandbox_session_id}"
    target = _sandbox_workspace_path(settings, relative)
    for directory in ("inputs", "work", "outputs"):
        (target / directory).mkdir(parents=True, exist_ok=True)
    if settings.sandbox_workspace_uid is not None:
        managed_directories = (
            target,
            target / "inputs",
            target / "work",
            target / "outputs",
        )
        for directory in managed_directories:
            try:
                os.chown(directory, settings.sandbox_workspace_uid, -1)
                os.chmod(directory, 0o750)
                # Setgid so later sub-directories inherit the group, preventing
                # cross-identity writers from losing traversal on nested paths
                # (sub-agent lanes, outputs/, inputs/).
                mode = os.stat(directory).st_mode
                if not (mode & stat.S_ISGID):
                    os.chmod(directory, mode | stat.S_ISGID)
            except OSError as exc:
                logger.error(
                    "Failed to apply sandbox workspace uid and permissions",
                    extra={
                        "workspace_path": str(directory),
                        "sandbox_workspace_uid": settings.sandbox_workspace_uid,
                    },
                    exc_info=True,
                )
                raise AppError(
                    500,
                    "sandbox_workspace_permission_invalid",
                    f"Unable to secure sandbox workspace directory {directory}",
                ) from exc
    return relative


def _session_expirations(settings: Settings, now):
    workspace_idle = now + timedelta(
        seconds=settings.sandbox_workspace_idle_ttl_seconds
    )
    absolute = now + timedelta(
        seconds=settings.sandbox_workspace_absolute_ttl_seconds
    )
    return workspace_idle, absolute


def _enforce_sandbox_capacity(
    db: Session,
    settings: Settings,
    actor_id: str,
    session: SandboxSession,
    backend,
) -> None:
    active_states = ("STARTING", "RUNNING", "WARM_IDLE")
    user_active = db.scalar(
        select(func.count(SandboxSession.id)).where(
            SandboxSession.owner_user_id == actor_id,
            SandboxSession.lifecycle_state.in_(active_states),
            SandboxSession.id != session.id,
        )
    ) or 0
    if user_active >= settings.sandbox_active_per_user:
        raise AppError(
            429,
            "sandbox_user_concurrency_limit",
            "The active sandbox limit for this user has been reached",
        )
    host_active = db.scalar(
        select(func.count(SandboxSession.id)).where(
            SandboxSession.lifecycle_state.in_(active_states),
            SandboxSession.id != session.id,
        )
    ) or 0
    if host_active >= settings.sandbox_host_max_active:
        raise AppError(
            503,
            "sandbox_host_capacity_exhausted",
            "The deployment-wide active sandbox limit has been reached",
        )
    host_cpus, host_memory = backend.host_capacity()
    requested_count = host_active + 1
    if (
        host_memory > 0
        and requested_count * settings.sandbox_memory_bytes
        > host_memory * settings.sandbox_host_max_allocated_memory_ratio
    ):
        raise AppError(
            503,
            "sandbox_host_memory_budget",
            "The deployment-wide sandbox memory allocation budget has been reached",
        )
    if (
        host_cpus > 0
        and requested_count * settings.sandbox_cpu_count
        > host_cpus * settings.sandbox_host_max_allocated_cpu_ratio
    ):
        raise AppError(
            503,
            "sandbox_host_cpu_budget",
            "The deployment-wide sandbox CPU allocation budget has been reached",
        )
    free = shutil.disk_usage(_sandbox_workspace_root(settings)).free
    if free < settings.sandbox_host_minimum_free_disk_bytes:
        raise AppError(
            503,
            "sandbox_host_disk_reserve",
            "The sandbox host free-disk reserve would be violated",
        )


def _enforce_retained_workspace_capacity(
    db: Session, settings: Settings, actor_id: str
) -> None:
    retained = db.scalar(
        select(func.count(SandboxSession.id)).where(
            SandboxSession.owner_user_id == actor_id,
            SandboxSession.cleanup_status != "cleaned",
            SandboxSession.lifecycle_state != "EXPIRED",
        )
    ) or 0
    if retained >= settings.sandbox_retained_workspaces_per_user:
        raise AppError(
            429,
            "sandbox_workspace_quota_exceeded",
            "Retained sandbox workspace quota exceeded; clean an older session first",
        )


def web_fetch_egress_envelope(
    settings: Settings,
    workspace_id: str,
    allowed_domains: Iterable[str],
    *,
    allow_all: bool = False,
) -> dict[str, Any] | None:
    """Build the egress envelope for a fixed ``web_fetch`` runner container.

    Fetch egress is gated by the unified ``access.allowlist`` alone (allow-all
    mode included). The generic per-workspace reviewed policy
    (``_egress_envelope``) is NOT consulted here, so fetch approvals never
    widen generic Agent egress. A non-empty derived policy is persisted to its
    own file and the container joins the egress network with that policy's
    digest. Returns ``None`` (the container stays offline) when egress is
    disabled or the allowlist cannot produce a valid policy.
    """
    if not settings.sandbox_egress_enabled:
        return None
    from app.services.sandbox_network_policy import (
        EgressPolicyInvalid,
        derive_egress_policy_for_fetch,
        store_workspace_fetch_policy_file,
    )

    try:
        policy = derive_egress_policy_for_fetch(
            workspace_id=workspace_id,
            allowed_domains=allowed_domains,
            allow_all_public=allow_all,
        )
    except EgressPolicyInvalid:
        logger.exception("Web fetch egress policy could not be derived; fetch stays offline")
        return None
    store_workspace_fetch_policy_file(settings.sandbox_egress_policy_dir, policy)
    return {
        "policy_digest": policy.digest,
        "network": settings.sandbox_egress_network,
        "proxy_url": settings.sandbox_egress_proxy_url,
    }


def _effective_network_policy(
    envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    """Durable effective network metadata for a sandbox session.

    Reflects what the runtime actually got: without a valid reviewed policy the
    envelope is ``None`` and the container stays fully offline. When egress was
    attached we record the reviewed policy digest only — never proxy
    credentials or raw allow-lists.
    """
    if not envelope:
        return {"mode": "none", "allowed_hosts": []}
    return {
        "mode": "egress",
        "policy_digest": str(envelope.get("policy_digest") or ""),
        "allowed_hosts": [],
    }


def sandbox_backend_report(db: Session) -> dict[str, Any]:
    """Mixed-backend drain report (admin/ops view).

    Counts every non-cleaned sandbox session by owning backend id and lifecycle
    state, plus legacy MCP runner records and sandboxd resource refs. This is
    the single source of truth for deciding whether legacy Docker resources can
    be cut off (see docs/sandboxd-migration-todo.md TODO-029/031).
    """
    from app.domain.extension_models import MCPRunnerSession

    sessions = list(
        db.scalars(
            select(SandboxSession).where(SandboxSession.cleanup_status != "cleaned")
        ).all()
    )
    by_backend: dict[str, dict[str, int]] = {}
    legacy = 0
    sandboxd = 0
    for session in sessions:
        backend_id = session.backend_id or "unknown"
        bucket = by_backend.setdefault(backend_id, {"total": 0, "states": {}})
        bucket["total"] += 1
        state = session.lifecycle_state or "UNKNOWN"
        bucket["states"][state] = bucket["states"].get(state, 0) + 1
        if backend_id == "docker":
            legacy += 1
        elif backend_id == "sandboxd":
            sandboxd += 1
    mcp_legacy = int(
        db.scalar(
            select(func.count()).select_from(MCPRunnerSession).where(
                MCPRunnerSession.backend_id == "docker",
                MCPRunnerSession.status != "terminated",
            )
        )
        or 0
    )
    legacy_sessions_with_ref = sum(
        1
        for session in sessions
        if (session.backend_id or "docker") == "docker" and session.backend_session_ref
    )
    sandboxd_with_resource_ref = sum(
        1
        for session in sessions
        if session.backend_id == "sandboxd" and session.backend_resource_ref
    )
    return {
        "by_backend": by_backend,
        "legacy_docker_sessions_total": legacy,
        "legacy_docker_sessions_with_container_ref": legacy_sessions_with_ref,
        "legacy_mcp_runner_records": mcp_legacy,
        "sandboxd_sessions_total": sandboxd,
        "sandboxd_sessions_with_resource_ref": sandboxd_with_resource_ref,
        "drain_ready": legacy == 0 and legacy_sessions_with_ref == 0 and mcp_legacy == 0,
    }


def agent_sandbox_readiness(settings: Settings, *, authorized: bool) -> dict[str, Any]:
    """Return the single readiness contract used by Agent UI and execution."""

    backend = get_sandbox_backend_registry().default(settings)
    if not authorized:
        return {
            "available": False,
            "code": "sandbox_permission_required",
            "message": "当前账号缺少智能体沙箱使用权限（需要 workspace.write）。",
            "authorized": False,
            "sandbox_enabled": settings.sandbox_enabled,
            "agent_enabled": settings.sandbox_agent_enabled,
            "backend_id": backend.backend_id,
            "platform": backend.platform,
            "capabilities": [],
            "remediation_steps": ["请由工作区管理员授予工作区写入权限后重试。"],
        }
    if not settings.sandbox_agent_enabled:
        return {
            "available": False,
            "code": "sandbox_agent_disabled",
            "message": "部署配置已关闭智能体沙箱执行。",
            "authorized": True,
            "sandbox_enabled": settings.sandbox_enabled,
            "agent_enabled": False,
            "backend_id": backend.backend_id,
            "platform": backend.platform,
            "capabilities": [],
            "remediation_steps": [
                "启用 LEARNGRAPH_SANDBOX_AGENT_ENABLED 后重启后端服务。"
            ],
        }
    capability = backend.probe()
    if not capability.available:
        bootstrap = get_bootstrap_service().status(settings)
        return {
            "available": False,
            "code": "sandbox_backend_unavailable",
            "message": capability.reason or "智能体沙箱运行时不可用。",
            "authorized": True,
            "sandbox_enabled": settings.sandbox_enabled,
            "agent_enabled": True,
            "backend_id": capability.backend_id,
            "platform": capability.platform,
            "capabilities": list(capability.capabilities),
            "remediation_steps": list(bootstrap.get("remediation_steps") or []),
        }
    return {
        "available": True,
        "code": None,
        "message": "智能体沙箱运行时已就绪。",
        "authorized": True,
        "sandbox_enabled": settings.sandbox_enabled,
        "agent_enabled": True,
        "backend_id": capability.backend_id,
        "platform": capability.platform,
        "capabilities": list(capability.capabilities),
        "remediation_steps": [],
    }


class SandboxTaskService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
        *,
        workspace: Workspace | None = None,
        principal: Principal | None = None,
        backend: SandboxBackendPort | None = None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.workspace = workspace
        self.principal = principal
        self.audit = AuditRepository(db, workspace_id)
        self.storage = object_storage_provider(db, workspace_id, settings)
        self.backend = backend or get_sandbox_backend_registry().default(settings)

    def _egress_envelope(self) -> dict[str, Any] | None:
        """Build the reviewed outbound-egress reference for a sandbox runtime.

        Egress is an explicit, per-workspace reviewed policy; without a valid
        policy the envelope is ``None`` and the container stays fully offline.
        """
        if not self.settings.sandbox_egress_enabled:
            return None
        from app.services.egress_approvals import EgressApprovalService
        from app.services.sandbox_network_policy import load_workspace_policy_file

        try:
            EgressApprovalService(
                self.db, self.workspace_id, self.settings
            ).ensure_agent_egress_policy()
        except Exception:
            logger.exception(
                "agent egress policy refresh failed for sandbox task workspace %s",
                self.workspace_id,
            )
        policy = load_workspace_policy_file(
            self.settings.sandbox_egress_policy_dir, self.workspace_id
        )
        if policy is None:
            return None
        return {
            "policy_digest": policy.digest,
            "network": self.settings.sandbox_egress_network,
            "proxy_url": self.settings.sandbox_egress_proxy_url,
        }

    def profile(self) -> dict:
        """Single unified runtime profile.

        One image serves every session (browser, ffmpeg and the frontend
        toolchain always included), so the deployment exposes one honest
        profile instead of the legacy python-node / python-node-browser pair.
        """

        backend = get_sandbox_backend_registry().default(self.settings)
        capability = backend.probe()
        resolved = resolve_sandbox_image(self.settings) or ""
        return {
            "backend_id": f"{capability.backend_id}:unified",
            "runtime_kind": "unified",
            "platform": capability.platform,
            "available": capability.available,
            "capabilities": list(capability.capabilities),
            "reason": capability.reason,
            "image_pinned": _runtime_image_pinned(backend, self.settings, resolved),
        }

    def list_sessions(
        self,
        chat_session_id: str | None = None,
        *,
        include_all: bool = False,
        include_cleaned: bool = False,
    ) -> list[SandboxSession]:
        if include_all and not (self.principal and self.principal.is_system_admin):
            raise AppError(403, "system_admin_required", "System administrator permission is required")
        query = select(SandboxSession).where(
            SandboxSession.workspace_id == self.workspace_id,
        )
        if not include_all:
            query = query.where(SandboxSession.owner_user_id == self.actor_id)
        if not include_cleaned:
            query = query.where(SandboxSession.cleanup_status != "cleaned")
        if chat_session_id:
            self._require_chat_session(chat_session_id)
            query = query.where(SandboxSession.chat_session_id == chat_session_id)
        return list(self.db.scalars(query.order_by(SandboxSession.created_at.desc())).all())

    def get_session(self, session_id: str, *, include_all: bool = False) -> SandboxSession:
        if include_all and not (self.principal and self.principal.is_system_admin):
            raise AppError(403, "system_admin_required", "System administrator permission is required")
        conditions = [
            SandboxSession.id == session_id,
            SandboxSession.workspace_id == self.workspace_id,
        ]
        if not include_all:
            conditions.append(SandboxSession.owner_user_id == self.actor_id)
        session = self.db.scalar(select(SandboxSession).where(*conditions))
        if session is None:
            raise AppError(404, "sandbox_session_not_found", "Sandbox session was not found")
        return session

    def get_task(self, task_id: str) -> SandboxTask:
        task = self.db.scalar(
            select(SandboxTask).where(
                SandboxTask.id == task_id,
                SandboxTask.workspace_id == self.workspace_id,
                SandboxTask.owner_user_id == self.actor_id,
            )
        )
        if task is None:
            raise AppError(404, "sandbox_task_not_found", "Sandbox task was not found")
        return task

    def list_tasks(self, chat_session_id: str | None = None) -> list[SandboxTask]:
        """Return the caller's persisted sandbox task history for this workspace."""
        query = select(SandboxTask).where(
            SandboxTask.workspace_id == self.workspace_id,
            SandboxTask.owner_user_id == self.actor_id,
        )
        if chat_session_id:
            self._require_chat_session(chat_session_id)
            query = query.where(SandboxTask.chat_session_id == chat_session_id)
        return list(self.db.scalars(query.order_by(SandboxTask.created_at.desc())).all())

    def executions(self, task_id: str) -> list[SandboxExecution]:
        self.get_task(task_id)
        return list(
            self.db.scalars(
                select(SandboxExecution).where(
                    SandboxExecution.workspace_id == self.workspace_id,
                    SandboxExecution.task_id == task_id,
                ).order_by(SandboxExecution.attempt_no)
            ).all()
        )

    def _require_chat_session(self, session_id: str) -> ChatSession:
        record = self.db.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.workspace_id == self.workspace_id,
            )
        )
        if record is None:
            raise AppError(404, "session_not_found", "Chat session was not found in this workspace")
        if self.workspace is not None and self.principal is not None:
            if self.workspace.id != self.workspace_id or not AuthorizationService(
                self.db, self.principal
            ).can_access_resource(self.workspace, "session", session_id, "write"):
                raise AppError(404, "session_not_found", "Chat session was not found in this workspace")
        return record

    def _require_file(self, file_id: str) -> FileRecord:
        record = self.db.scalar(
            select(FileRecord).where(
                FileRecord.id == file_id,
                FileRecord.workspace_id == self.workspace_id,
            )
        )
        if record is None:
            raise AppError(404, "file_not_found", "File was not found in this workspace")
        return record

    def _new_session(self, chat_session_id: str, file: FileRecord) -> SandboxSession:
        with _sandbox_capacity_lock, _sandbox_capacity_file_lock(self.settings):
            _enforce_retained_workspace_capacity(
                self.db, self.settings, self.actor_id
            )
            return self._new_session_locked(chat_session_id, file)

    def _new_session_locked(
        self, chat_session_id: str, file: FileRecord
    ) -> SandboxSession:
        manifest_hash = hashlib.sha256(
            f"sandbox-policy-v1:{self.workspace_id}:{chat_session_id}:{file.id}:{file.sha256}".encode()
        ).hexdigest()
        now = utc_now()
        workspace_expires_at, absolute_expires_at = _session_expirations(
            self.settings, now
        )
        session = SandboxSession(
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            chat_session_id=chat_session_id,
            backend_id=self.backend.backend_id,
            manifest_hash=manifest_hash,
            runtime_kind="python-node",
            lifecycle_state="CREATED",
            status="created",
            resource_limits={
                "wall_time_seconds": self.settings.sandbox_wall_time_seconds,
                "memory_bytes": self.settings.sandbox_memory_bytes,
                "pids_max": self.settings.sandbox_pids_max,
                "disk_bytes": self.settings.sandbox_disk_bytes,
                "output_bytes": self.settings.sandbox_output_bytes,
            },
            network_policy={"mode": "none", "allowed_hosts": []},
            last_used_at=now,
            expires_at=workspace_expires_at,
            workspace_expires_at=workspace_expires_at,
            absolute_expires_at=absolute_expires_at,
        )
        self.db.add(session)
        self.db.flush()
        session.workspace_relative_path = _initialize_workspace(
            self.settings, self.actor_id, session.id
        )
        session.lifecycle_state = "COLD"
        # Publish the durable workspace identity before cross-process runtime
        # reservation refreshes this row.
        self.db.commit()
        self.db.refresh(session)
        return session

    def _resolve_session(
        self,
        requested_id: str | None,
        chat_session_id: str,
        file: FileRecord,
    ) -> SandboxSession:
        if requested_id is None:
            return self._new_session(chat_session_id, file)
        session = self.get_session(requested_id)
        if session.chat_session_id != chat_session_id:
            raise AppError(
                409,
                "sandbox_session_scope_mismatch",
                "A sandbox session cannot be reused by a different chat session",
            )
        if session.status != "ready" or not session.backend_session_ref:
            raise AppError(409, "sandbox_session_not_ready", "Sandbox session is not reusable")
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utc_now():
            raise AppError(409, "sandbox_session_expired", "Sandbox session has expired")
        return session

    def extract_legacy_doc(self, file: FileRecord) -> dict[str, Any]:
        """Extract legacy Word text through the fixed network-disabled runner."""

        capability = self.backend.probe()
        if not capability.available or "legacy_doc_extract" not in capability.capabilities:
            raise SandboxBackendUnavailable(
                capability.reason or "The isolated antiword parser is unavailable"
            )
        sandbox_session_id = f"doc-{new_id()}"
        workspace_relative_path = _initialize_workspace(
            self.settings, self.actor_id, sandbox_session_id
        )
        handle: SandboxSessionHandle | None = None
        try:
            handle = self.backend.create(
                SandboxCreateSpec(
                    session_id=sandbox_session_id,
                    image_ref=resolve_sandbox_image(self.settings) or "",
                    memory_bytes=self.settings.sandbox_memory_bytes,
                    memory_swap_bytes=self.settings.sandbox_memory_swap_bytes,
                    cpu_count=self.settings.sandbox_cpu_count,
                    pids_max=self.settings.sandbox_pids_max,
                    disk_bytes=self.settings.sandbox_disk_bytes,
                    workspace_path=str(
                        _sandbox_workspace_path(self.settings, workspace_relative_path)
                    ),
                    runtime_kind="python-node",
                    workspace_key=workspace_relative_path,
                )
            )
            raw = self.storage.read_bytes(
                file.object_key,
                limit_bytes=self.settings.max_document_parse_bytes,
            )
            input_path = f"input/{file.id}.bin"
            output_path = f"output/{sandbox_session_id}.json"
            self.backend.write(handle, input_path, raw)
            result = self.backend.exec_fixed(
                handle,
                (
                    "python",
                    "/opt/learngraph/runner.py",
                    "--task",
                    "extract_legacy_doc",
                    "--input",
                    input_path,
                    "--output",
                    output_path,
                ),
                timeout_seconds=self.settings.sandbox_wall_time_seconds,
                output_limit=self.settings.sandbox_output_bytes,
            )
            if result.truncated:
                raise SandboxOutputLimitExceeded(
                    "Sandbox output exceeded the configured host-side limit"
                )
            if result.timed_out:
                raise SandboxBackendError("The isolated antiword parser timed out")
            if result.exit_code != 0:
                detail = result.stderr.decode("utf-8", errors="replace").strip()[:2_000]
                raise SandboxBackendError(
                    f"The isolated antiword parser failed: {detail or result.exit_code}"
                )
            artifact = json.loads(
                self.backend.read(
                    handle,
                    output_path,
                    self.settings.sandbox_output_bytes,
                )
            )
            if (
                not isinstance(artifact, dict)
                or artifact.get("schema_version") != "1.0"
                or artifact.get("sha256") != file.sha256
                or not isinstance(artifact.get("text"), str)
                or not artifact["text"].strip()
            ):
                raise SandboxBackendError(
                    "The isolated antiword parser returned an invalid artifact"
                )
            return artifact
        finally:
            if handle is not None:
                try:
                    self.backend.delete(handle)
                except Exception:
                    logger.exception(
                        "Failed to clean isolated document parser %s",
                        sandbox_session_id,
                    )
            shutil.rmtree(
                _sandbox_workspace_path(self.settings, workspace_relative_path),
                ignore_errors=True,
            )

    def create_task(
        self,
        payload: SandboxTaskCreateRequest,
        *,
        idempotency_key: str | None,
    ) -> SandboxTask:
        self._require_chat_session(payload.chat_session_id)
        file = self._require_file(payload.file_id)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
        if key_hash:
            existing = self.db.scalar(
                select(SandboxTask).where(
                    SandboxTask.workspace_id == self.workspace_id,
                    SandboxTask.owner_user_id == self.actor_id,
                    SandboxTask.idempotency_key_hash == key_hash,
                )
            )
            if existing is not None:
                if (
                    existing.chat_session_id != payload.chat_session_id
                    or existing.file_id != payload.file_id
                    or existing.task_type != payload.task_type
                ):
                    raise AppError(
                        409,
                        "idempotency_key_conflict",
                        "The sandbox idempotency key was already used for a different task",
                    )
                return existing
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id, file)
        task = SandboxTask(
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            sandbox_session_id=session.id,
            chat_session_id=payload.chat_session_id,
            file_id=file.id,
            task_type=payload.task_type,
            output_format=payload.output_format,
            idempotency_key_hash=key_hash,
            status="created",
        )
        self.db.add(task)
        self.db.flush()
        argv = (
            "python",
            "/opt/learngraph/runner.py",
            "--task",
            payload.task_type,
            "--input",
            f"input/{file.id}.bin",
            "--output",
            f"output/{task.id}.json",
        )
        execution = SandboxExecution(
            workspace_id=self.workspace_id,
            sandbox_session_id=session.id,
            task_id=task.id,
            attempt_no=1,
            argv_digest=hashlib.sha256("\0".join(argv).encode()).hexdigest(),
            argv_redacted=["python", "/opt/learngraph/runner.py", "--task", payload.task_type],
            cwd_relative=".",
            status="created",
        )
        self.db.add(execution)
        self.db.commit()
        capability = self.backend.probe()
        if not capability.available:
            return self._fail_task(
                task,
                session,
                execution,
                "sandbox_backend_unavailable",
                capability.reason or "Sandbox backend is unavailable",
            )
        handle: SandboxSessionHandle | None = None
        try:
            if session.backend_session_ref:
                handle = self.backend.resume(session.id, session.backend_session_ref)
            else:
                with _sandbox_capacity_lock, _sandbox_capacity_file_lock(self.settings):
                    self.db.refresh(session)
                    if session.backend_session_ref:
                        handle = self.backend.resume(
                            session.id, session.backend_session_ref
                        )
                    else:
                        _enforce_sandbox_capacity(
                            self.db,
                            self.settings,
                            self.actor_id,
                            session,
                            self.backend,
                        )
                        session.lifecycle_state = "STARTING"
                        session.runtime_started_at = utc_now()
                        session.runtime_last_used_at = session.runtime_started_at
                        self.db.commit()
                        egress_envelope = self._egress_envelope()
                        try:
                            handle = self.backend.create(
                                SandboxCreateSpec(
                                session_id=session.id,
                                image_ref=resolve_sandbox_image(self.settings) or "",
                                memory_bytes=self.settings.sandbox_memory_bytes,
                                memory_swap_bytes=self.settings.sandbox_memory_swap_bytes,
                                cpu_count=self.settings.sandbox_cpu_count,
                                pids_max=self.settings.sandbox_pids_max,
                                disk_bytes=self.settings.sandbox_disk_bytes,
                                workspace_path=str(
                                    _sandbox_workspace_path(
                                        self.settings, session.workspace_relative_path
                                    )
                                ),
                                runtime_kind=session.runtime_kind,
                                egress=egress_envelope,
                                workspace_key=session.workspace_relative_path or session.id,
                                )
                            )
                            session.backend_session_ref = handle.backend_ref
                            session.network_policy = _effective_network_policy(egress_envelope)
                            self.db.commit()
                        except Exception:
                            session.lifecycle_state = "COLD"
                            session.backend_session_ref = None
                            self.db.commit()
                            raise
            session.status = "running"
            session.lifecycle_state = "RUNNING"
            task.status = "running"
            execution.status = "running"
            self.db.commit()
            raw = self.storage.read_bytes(file.object_key, limit_bytes=self.settings.max_document_parse_bytes)
            self.backend.write(handle, f"input/{file.id}.bin", raw)
            result = self.backend.exec_fixed(
                handle,
                argv,
                timeout_seconds=self.settings.sandbox_wall_time_seconds,
                output_limit=self.settings.sandbox_output_bytes,
            )
            execution.exit_code = result.exit_code
            execution.timed_out = result.timed_out
            execution.latency_ms = result.latency_ms
            execution.truncated = result.truncated
            execution.stdout_summary = result.stdout.decode("utf-8", errors="replace")[:2_000]
            execution.stderr_summary = result.stderr.decode("utf-8", errors="replace")[:2_000]
            if result.truncated:
                raise SandboxOutputLimitExceeded(
                    "Sandbox output exceeded the configured host-side limit"
                )
            if result.timed_out:
                return self._fail_task(task, session, execution, "sandbox_timeout", "Sandbox task timed out")
            if result.exit_code != 0:
                return self._fail_task(task, session, execution, "sandbox_runner_failed", "Sandbox runner failed")
            artifact_bytes = self.backend.read(
                handle,
                f"output/{task.id}.json",
                self.settings.sandbox_output_bytes,
            )
            artifact = json.loads(artifact_bytes)
            if not isinstance(artifact, dict) or artifact.get("schema_version") != "1.0":
                raise SandboxBackendError("Sandbox output failed schema validation")
            if artifact.get("sha256") != file.sha256:
                raise SandboxBackendError("Sandbox output does not match the authorized input hash")
            task.artifact_json = artifact
            task.status = "completed"
            execution.status = "completed"
            session.status = "ready"
            session.last_used_at = utc_now()
            session.runtime_last_used_at = session.last_used_at
            session.lifecycle_state = "WARM_IDLE"
            self.audit.record(
                actor_id=self.actor_id,
                action="sandbox.task.completed",
                resource_type="sandbox_task",
                resource_id=task.id,
                details={
                    "sandbox_session_id": session.id,
                    "chat_session_id": session.chat_session_id,
                    "file_id": file.id,
                    "task_type": task.task_type,
                    "network_mode": "none",
                },
            )
            self.db.commit()
            self.db.refresh(task)
            return task
        except (SandboxBackendUnavailable, SandboxBackendError, ValueError, json.JSONDecodeError) as exc:
            return self._fail_task(
                task,
                session,
                execution,
                "sandbox_execution_failed",
                "Sandbox execution failed; inspect the execution record for the error class",
                type(exc).__name__,
            )

    def _fail_task(
        self,
        task: SandboxTask,
        session: SandboxSession,
        execution: SandboxExecution,
        error_class: str,
        message: str,
        internal_class: str | None = None,
    ) -> SandboxTask:
        task.status = "failed"
        task.error_class = error_class
        task.error_message = message
        execution.status = "failed"
        execution.error_class = internal_class or error_class
        session.status = "failed"
        session.runtime_last_used_at = utc_now()
        if error_class == "sandbox_timeout":
            if session.backend_session_ref:
                try:
                    self.backend.delete(
                        SandboxSessionHandle(
                            session.id, session.backend_session_ref
                        )
                    )
                    session.backend_session_ref = None
                except SandboxBackendError:
                    logger.exception("Failed to remove timed-out fixed-task container")
            session.lifecycle_state = "COLD"
        else:
            session.lifecycle_state = (
                "WARM_IDLE" if session.backend_session_ref else "COLD"
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.task.failed",
            resource_type="sandbox_task",
            resource_id=task.id,
            outcome="failed",
            details={"error_class": error_class, "sandbox_session_id": session.id},
        )
        self.db.commit()
        self.db.refresh(task)
        return task

    def cancel(self, task_id: str) -> SandboxTask:
        task = self.get_task(task_id)
        if task.status in {"completed", "failed", "cancelled"}:
            return task
        session = self.get_session(task.sandbox_session_id)
        if session.backend_session_ref:
            try:
                self.backend.stop(SandboxSessionHandle(session.id, session.backend_session_ref))
            except SandboxBackendError as exc:
                raise AppError(502, "sandbox_cancel_failed", "Sandbox cancellation failed") from exc
        task.status = "cancelled"
        session.status = "stopped"
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.task.cancelled",
            resource_type="sandbox_task",
            resource_id=task.id,
        )
        self.db.commit()
        self.db.refresh(task)
        return task

    def cleanup(self, session_id: str, *, include_all: bool = False) -> SandboxSession:
        session = self.get_session(session_id, include_all=include_all)
        if session.cleanup_status == "cleaned":
            return session
        session.cleanup_status = "running"
        self.db.commit()
        if session.backend_session_ref:
            try:
                get_sandbox_backend_registry().for_backend_id(
                    session.backend_id, self.settings, session.runtime_kind
                ).delete(
                    SandboxSessionHandle(session.id, session.backend_session_ref)
                )
            except SandboxBackendError as exc:
                session.cleanup_status = "cleanup_blocked"
                session.cleanup_error_class = type(exc).__name__
                self.db.commit()
                raise AppError(502, "sandbox_cleanup_failed", "Sandbox cleanup failed and is pending retry") from exc
        if session.workspace_relative_path:
            workspace_path = _sandbox_workspace_path(
                self.settings, session.workspace_relative_path
            )
            if workspace_path.exists():
                shutil.rmtree(workspace_path)
        session.status = "deleted"
        session.lifecycle_state = "EXPIRED"
        session.cleanup_status = "cleaned"
        session.backend_session_ref = None
        session.cleanup_error_class = None
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.session.cleaned",
            resource_type="sandbox_session",
            resource_id=session.id,
        )
        self.db.commit()
        self.db.refresh(session)
        return session


AGENT_SANDBOX_POLICY_REVISION = "sandbox-agent-v2-runtime-profiles"
_AGENT_SECRET_OUTPUT = re.compile(
    r"(?i)(?:\b(?:as_sk|sk)_[a-z0-9_-]{8,}\b|"
    r"\b(?:authorization|api[_-]?key|token|password)\s*[:=]\s*[^\s,;]+)"
)
_AGENT_SECRET_ARGUMENT_FLAGS = frozenset(
    {
        "--api-key",
        "--apikey",
        "--token",
        "--password",
        "--secret",
        "-p",
    }
)


def _redact_agent_text(value: str, *, limit: int = 2_000) -> str:
    """Keep useful command diagnostics without persisting likely credentials."""

    clipped = value[:limit]
    return _AGENT_SECRET_OUTPUT.sub("[REDACTED]", clipped)


def _redacted_agent_argv(argv: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    redact_next = False
    for item in argv:
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        lowered = item.casefold()
        if lowered in _AGENT_SECRET_ARGUMENT_FLAGS:
            result.append(item)
            redact_next = True
            continue
        if any(marker in lowered for marker in ("api_key=", "apikey=", "token=", "password=")):
            result.append("[REDACTED]")
            continue
        result.append(_redact_agent_text(item, limit=512))
    return result


class SandboxAgentWorkspaceService(SandboxToolkitMixin):
    """Persisted, shell-free Agent workspace operations over a hardened backend.

    The service deliberately owns only the execution boundary.  Agent planning
    and business-domain tools stay in Chat/MCP services.  A caller must make a
    separate authorization decision before using ``execute_agent_tool``; HTTP
    routes enforce ``workspace.manage`` directly.

    The extended tool set (bash / todo / patch / git / search / fetch /
    subagent / skills / notebook) lives in :class:`SandboxToolkitMixin`.
    """

    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
        *,
        workspace: Workspace | None = None,
        principal: Principal | None = None,
        backend: SandboxBackendPort | None = None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.workspace = workspace
        self.principal = principal
        self.audit = AuditRepository(db, workspace_id)
        self.backend = backend or get_sandbox_backend_registry().default(settings)
        self.workspace_files = SessionWorkspaceService(db, workspace_id, actor_id, settings)
        self.authz = SandboxAuthorizationService(db, workspace_id, actor_id)

    def _egress_envelope(self) -> dict[str, Any] | None:
        """Build the reviewed outbound-egress reference for an Agent sandbox.

        Without a valid per-workspace reviewed policy the envelope is ``None``
        and the container stays fully offline.
        """
        if not self.settings.sandbox_egress_enabled:
            return None
        from app.services.egress_approvals import EgressApprovalService
        from app.services.sandbox_network_policy import load_workspace_policy_file

        try:
            EgressApprovalService(
                self.db, self.workspace_id, self.settings
            ).ensure_agent_egress_policy()
        except Exception:
            logger.exception(
                "agent egress policy refresh failed for agent workspace %s",
                self.workspace_id,
            )
        policy = load_workspace_policy_file(
            self.settings.sandbox_egress_policy_dir, self.workspace_id
        )
        if policy is None:
            return None
        return {
            "policy_digest": policy.digest,
            "network": self.settings.sandbox_egress_network,
            "proxy_url": self.settings.sandbox_egress_proxy_url,
        }

    def _require_chat_session(self, session_id: str) -> ChatSession:
        record = self.db.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.workspace_id == self.workspace_id,
            )
        )
        if record is None:
            raise AppError(404, "session_not_found", "Chat session was not found in this workspace")
        if self.workspace is not None and self.principal is not None:
            if self.workspace.id != self.workspace_id or not AuthorizationService(
                self.db, self.principal
            ).can_access_resource(self.workspace, "session", session_id, "write"):
                raise AppError(404, "session_not_found", "Chat session was not found in this workspace")
        return record

    def _get_session(self, sandbox_session_id: str) -> SandboxSession:
        session = self.db.scalar(
            select(SandboxSession).where(
                SandboxSession.id == sandbox_session_id,
                SandboxSession.workspace_id == self.workspace_id,
                SandboxSession.owner_user_id == self.actor_id,
            )
        )
        if session is None:
            raise AppError(404, "sandbox_session_not_found", "Sandbox session was not found")
        if session.policy_revision != AGENT_SANDBOX_POLICY_REVISION:
            raise AppError(
                409,
                "sandbox_agent_session_required",
                "The selected sandbox session is not an Agent workspace session",
            )
        return session

    def get_command(self, command_id: str) -> SandboxAgentCommand:
        command = self.db.scalar(
            select(SandboxAgentCommand).where(
                SandboxAgentCommand.id == command_id,
                SandboxAgentCommand.workspace_id == self.workspace_id,
                SandboxAgentCommand.owner_user_id == self.actor_id,
            )
        )
        if command is None:
            raise AppError(404, "sandbox_agent_command_not_found", "Sandbox Agent command was not found")
        return command

    def list_commands(self, chat_session_id: str | None = None) -> list[SandboxAgentCommand]:
        statement = select(SandboxAgentCommand).where(
            SandboxAgentCommand.workspace_id == self.workspace_id,
            SandboxAgentCommand.owner_user_id == self.actor_id,
        )
        if chat_session_id is not None:
            self._require_chat_session(chat_session_id)
            statement = statement.where(SandboxAgentCommand.chat_session_id == chat_session_id)
        return list(self.db.scalars(statement.order_by(SandboxAgentCommand.created_at.desc())).all())

    def _new_session(
        self, chat_session_id: str, runtime_kind: str = "python-node"
    ) -> SandboxSession:
        with _sandbox_capacity_lock, _sandbox_capacity_file_lock(self.settings):
            _enforce_retained_workspace_capacity(
                self.db, self.settings, self.actor_id
            )
            return self._new_session_locked(chat_session_id, runtime_kind)

    def _new_session_locked(
        self, chat_session_id: str, runtime_kind: str
    ) -> SandboxSession:
        now = utc_now()
        workspace_expires_at, absolute_expires_at = _session_expirations(
            self.settings, now
        )
        manifest_hash = hashlib.sha256(
            f"{AGENT_SANDBOX_POLICY_REVISION}:{self.workspace_id}:{chat_session_id}:{self.actor_id}".encode()
        ).hexdigest()
        session = SandboxSession(
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            chat_session_id=chat_session_id,
            backend_id=self.backend.backend_id,
            manifest_hash=manifest_hash,
            policy_revision=AGENT_SANDBOX_POLICY_REVISION,
            runtime_kind=runtime_kind,
            lifecycle_state="CREATED",
            status="created",
            resource_limits={
                "wall_time_seconds": self.settings.sandbox_wall_time_seconds,
                "memory_bytes": self.settings.sandbox_memory_bytes,
                "pids_max": self.settings.sandbox_pids_max,
                "disk_bytes": self.settings.sandbox_disk_bytes,
                "output_bytes": self.settings.sandbox_output_bytes,
                "agent_file_bytes": self.settings.sandbox_agent_file_bytes,
            },
            network_policy={"mode": "none", "allowed_hosts": []},
            last_used_at=now,
            expires_at=workspace_expires_at,
            workspace_expires_at=workspace_expires_at,
            absolute_expires_at=absolute_expires_at,
        )
        self.db.add(session)
        self.db.flush()
        session.workspace_relative_path = _initialize_workspace(
            self.settings, self.actor_id, session.id
        )
        session.lifecycle_state = "COLD"
        # Publish the workspace identity before the runtime reservation path
        # refreshes this row under the deployment lock.
        self.db.commit()
        self.db.refresh(session)
        return session

    @staticmethod
    def _not_expired(session: SandboxSession) -> bool:
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > utc_now()

    def _resolve_session(
        self,
        sandbox_session_id: str | None,
        chat_session_id: str,
        runtime_kind: str = "python-node",
    ) -> SandboxSession:
        self._require_chat_session(chat_session_id)
        if sandbox_session_id is not None:
            session = self._get_session(sandbox_session_id)
            if session.chat_session_id != chat_session_id:
                raise AppError(
                    409,
                    "sandbox_session_scope_mismatch",
                    "A sandbox session cannot be reused by a different chat session",
                )
            if not self._not_expired(session):
                raise AppError(409, "sandbox_session_expired", "Sandbox session has expired")
            if session.cleanup_status == "cleaned" or session.status in {"deleted", "stopped"}:
                raise AppError(409, "sandbox_session_not_ready", "Sandbox session is not reusable")
            return session

        existing = self.db.scalar(
            select(SandboxSession)
            .where(
                SandboxSession.workspace_id == self.workspace_id,
                SandboxSession.owner_user_id == self.actor_id,
                SandboxSession.chat_session_id == chat_session_id,
                SandboxSession.policy_revision == AGENT_SANDBOX_POLICY_REVISION,
                SandboxSession.runtime_kind == runtime_kind,
                SandboxSession.status == "ready",
                SandboxSession.cleanup_status != "cleaned",
            )
            .order_by(SandboxSession.last_used_at.desc())
        )
        if existing is not None and self._not_expired(existing):
            return existing
        return self._new_session(chat_session_id, runtime_kind)

    def _runtime_backend(self, session: SandboxSession):
        return get_sandbox_backend_registry().for_backend_id(
            session.backend_id, self.settings, session.runtime_kind
        )

    def _ensure_runtime_capacity(self, session: SandboxSession) -> None:
        _enforce_sandbox_capacity(
            self.db,
            self.settings,
            self.actor_id,
            session,
            self._runtime_backend(session),
        )

    # ── execution-pool instance reuse (design doc §3.1, §5) ─────────────

    def _chat_workspace_key(self, chat_session_id: str) -> str:
        """Stable, opaque per-chat workspace key inside a shared instance."""
        digest = hashlib.sha256(
            f"{self.workspace_id}:{chat_session_id}".encode("utf-8")
        ).hexdigest()
        return digest[:20]

    def _pooling_enabled(self, session: SandboxSession) -> bool:
        return bool(
            self.settings.sandbox_instance_pooling_enabled
            and (session.backend_id or "docker") == "sandboxd"
        )

    def _container_prefix(self, chat_session_id: str) -> str:
        return f"sessions/{self._chat_workspace_key(chat_session_id)}"

    def _container_path(self, chat_session_id: str, path: str) -> str:
        return f"{self._container_prefix(chat_session_id)}/{path.lstrip('/')}"

    def _acquire_instance(self, runtime_kind: str) -> SandboxInstance:
        """Find the user's pooled warm instance, or create one (bounded).

        Raises ``AppError(429, sandbox_user_concurrency_limit)`` when the
        user's instance quota is exhausted so the unified scheduler queues the
        job instead of failing the caller.
        """
        active_states = ("PROVISIONING", "READY", "BUSY", "SATURATED")
        # Prefer an instance that still has free parallelism.
        instance = self.db.scalar(
            select(SandboxInstance)
            .where(
                SandboxInstance.workspace_id == self.workspace_id,
                SandboxInstance.owner_user_id == self.actor_id,
                SandboxInstance.runtime_profile == runtime_kind,
                SandboxInstance.state.in_(active_states),
                SandboxInstance.active_executions < SandboxInstance.max_parallel_execs,
            )
            .order_by(
                SandboxInstance.active_executions.asc(),
                SandboxInstance.created_at.asc(),
            )
            .limit(1)
        )
        if instance is not None:
            return instance
        # No free-parallelism instance: every active instance is saturated.
        # Create another instance when the user's quota allows; otherwise the
        # caller queues (429 → CAPACITY_CODES → SandboxJob).
        count = int(
            self.db.scalar(
                select(func.count(SandboxInstance.id)).where(
                    SandboxInstance.workspace_id == self.workspace_id,
                    SandboxInstance.owner_user_id == self.actor_id,
                    SandboxInstance.state.in_(active_states),
                )
            )
            or 0
        )
        from app.services.sandbox_scheduler import (
            effective_max_instances,
            effective_max_parallel,
            workspace_scheduling_policy,
        )

        policy = workspace_scheduling_policy(self.db, self.workspace_id)
        effective_max = effective_max_instances(self.settings, policy)
        if count >= effective_max:
            raise AppError(
                429,
                "sandbox_user_concurrency_limit",
                "The active sandbox limit for this user has been reached",
            )
        instance = SandboxInstance(
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            backend_id="sandboxd",
            runtime_profile=runtime_kind,
            state="PROVISIONING",
            resource_envelope={
                "cpu": self.settings.sandbox_cpu_count,
                "memory_bytes": self.settings.sandbox_memory_bytes,
                "pids": self.settings.sandbox_pids_max,
                "disk_bytes": self.settings.sandbox_disk_bytes,
            },
            max_parallel_execs=effective_max_parallel(
                self.settings, workspace_scheduling_policy(self.db, self.workspace_id)
            ),
            active_executions=0,
        )
        self.db.add(instance)
        self.db.commit()
        self.db.refresh(instance)
        return instance

    def _release_instance(self, instance_id: str) -> None:
        instance = self.db.get(SandboxInstance, instance_id)
        if instance is None:
            return
        instance.active_executions = max(0, int(instance.active_executions or 0) - 1)
        if instance.active_executions == 0:
            if instance.idle_since is None:
                instance.idle_since = utc_now()
            instance.state = "READY"
        else:
            instance.state = "BUSY"
            instance.idle_since = None
        instance.last_health_check_at = utc_now()
        self.db.commit()

    def _release_instance_for_session(self, session: SandboxSession) -> None:
        """Release the borrow taken by ``_borrow_instance`` (command runs).

        Pooled sessions point ``backend_session_ref`` at the shared instance's
        resource ref, so the borrow is located through that ref.
        """
        if not self._pooling_enabled(session) or not session.backend_session_ref:
            return
        instance = self.db.scalar(
            select(SandboxInstance).where(
                SandboxInstance.backend_resource_ref == session.backend_session_ref
            )
        )
        if instance is not None:
            self._release_instance(instance.id)

    def _borrow_instance(self, session: SandboxSession) -> None:
        """Count one running command on the shared instance (parallelism)."""
        instance = self.db.scalar(
            select(SandboxInstance).where(
                SandboxInstance.backend_resource_ref == session.backend_session_ref
            )
        )
        if instance is None:
            return
        instance.active_executions = int(instance.active_executions or 0) + 1
        instance.state = "BUSY"
        instance.idle_since = None
        self.db.commit()

    def _ensure_pooled_backend_session(
        self, session: SandboxSession, backend
    ) -> SandboxSessionHandle:
        """Run a chat execution on the user's shared warm instance.

        Chat workspaces are isolated by a ``sessions/{chat_key}`` directory
        prefix inside the instance container (same-user chats are NOT security
        boundaries — the prefix prevents file pollution only).
        """
        instance = self._acquire_instance(session.runtime_kind)
        if instance.backend_resource_ref:
            try:
                handle = backend.resume(instance.id, instance.backend_resource_ref)
                session.backend_session_ref = instance.backend_resource_ref
                self.db.commit()
                self._ensure_chat_dir(backend, handle, session.chat_session_id)
                return handle
            except SandboxBackendError:
                instance.backend_resource_ref = None
        with _sandbox_capacity_lock, _sandbox_capacity_file_lock(self.settings):
            self.db.refresh(instance)
            if instance.backend_resource_ref:
                handle = backend.resume(instance.id, instance.backend_resource_ref)
            else:
                self._ensure_runtime_capacity(session)
                capability = backend.probe()
                if not capability.available:
                    raise SandboxBackendUnavailable(
                        capability.reason or "Sandbox backend is unavailable"
                    )
                image_ref = resolve_sandbox_image_for_runtime(
                    self.settings, session.runtime_kind
                )
                instance.state = "PROVISIONING"
                self.db.commit()
                try:
                    egress_envelope = self._egress_envelope()
                    handle = backend.create(
                        SandboxCreateSpec(
                            session_id=instance.id,
                            image_ref=image_ref or "",
                            memory_bytes=self.settings.sandbox_memory_bytes,
                            memory_swap_bytes=self.settings.sandbox_memory_swap_bytes,
                            cpu_count=self.settings.sandbox_cpu_count,
                            pids_max=self.settings.sandbox_pids_max,
                            disk_bytes=self.settings.sandbox_disk_bytes,
                            workspace_path=str(
                                _sandbox_workspace_path(
                                    self.settings, f"{self.actor_id}/{instance.id}"
                                )
                            ),
                            runtime_kind=session.runtime_kind,
                            egress=egress_envelope,
                            workspace_key=instance.id,
                        )
                    )
                    instance.backend_resource_ref = handle.backend_ref
                    instance.state = "READY"
                    instance.expires_at = utc_now() + timedelta(
                        seconds=self.settings.sandbox_container_absolute_ttl_seconds
                    )
                    self.db.commit()
                except Exception:
                    instance.state = "ERROR"
                    instance.cleanup_status = "cleanup_blocked"
                    self.db.commit()
                    raise
            session.backend_session_ref = handle.backend_ref
            session.lifecycle_state = "RUNNING"
            session.runtime_started_at = utc_now()
            session.runtime_last_used_at = utc_now()
            session.status = "ready"
            self.db.commit()
        self._ensure_chat_dir(backend, handle, session.chat_session_id)
        return handle

    def _ensure_chat_dir(
        self, backend, handle: SandboxSessionHandle, chat_session_id: str
    ) -> None:
        """Best-effort create the chat workspace dir inside the shared instance."""
        try:
            prefix = self._container_prefix(chat_session_id)
            backend.write_agent_file(handle, f"{prefix}/.keep", b"")
        except (SandboxBackendUnavailable, SandboxBackendError):
            pass

    def _touch_session(self, session: SandboxSession) -> None:
        now = utc_now()
        absolute_expires_at = session.absolute_expires_at
        if absolute_expires_at.tzinfo is None:
            absolute_expires_at = absolute_expires_at.replace(tzinfo=timezone.utc)
        session.last_used_at = now
        session.runtime_last_used_at = now
        session.workspace_expires_at = min(
            now
            + timedelta(seconds=self.settings.sandbox_workspace_idle_ttl_seconds),
            absolute_expires_at,
        )
        session.expires_at = session.workspace_expires_at

    def _ensure_backend_session(self, session: SandboxSession) -> SandboxSessionHandle:
        if not self.settings.sandbox_agent_enabled:
            raise SandboxBackendUnavailable("Agent sandbox execution is disabled by deployment configuration")
        backend = self._runtime_backend(session)
        if self._pooling_enabled(session):
            return self._ensure_pooled_backend_session(session, backend)
        if session.backend_session_ref:
            try:
                return backend.resume(session.id, session.backend_session_ref)
            except SandboxBackendError:
                session.backend_session_ref = None
                session.lifecycle_state = "COLD"
        with _sandbox_capacity_lock, _sandbox_capacity_file_lock(self.settings):
            # This ORM object may have been loaded before another worker
            # completed the same cold start. Refresh under the process lock.
            self.db.refresh(session)
            if session.backend_session_ref:
                return backend.resume(session.id, session.backend_session_ref)
            self._ensure_runtime_capacity(session)
            capability = backend.probe()
            if not capability.available:
                raise SandboxBackendUnavailable(
                    capability.reason or "Sandbox backend is unavailable"
                )
            image_ref = resolve_sandbox_image_for_runtime(
                self.settings, session.runtime_kind
            )
            # Persist STARTING before the slow Docker call. Other requests and
            # workers count the reservation instead of oversubscribing the host.
            session.lifecycle_state = "STARTING"
            session.runtime_started_at = utc_now()
            session.runtime_last_used_at = session.runtime_started_at
            self.db.commit()
            try:
                egress_envelope = self._egress_envelope()
                handle = backend.create(
                    SandboxCreateSpec(
                        session_id=session.id,
                        image_ref=image_ref or "",
                        memory_bytes=self.settings.sandbox_memory_bytes,
                        memory_swap_bytes=self.settings.sandbox_memory_swap_bytes,
                        cpu_count=self.settings.sandbox_cpu_count,
                        pids_max=self.settings.sandbox_pids_max,
                        disk_bytes=self.settings.sandbox_disk_bytes,
                        workspace_path=str(
                            _sandbox_workspace_path(
                                self.settings, session.workspace_relative_path
                            )
                        ),
                        runtime_kind=session.runtime_kind,
                        egress=egress_envelope,
                        workspace_key=session.workspace_relative_path or session.id,
                    )
                )
                session.backend_session_ref = handle.backend_ref
                session.network_policy = _effective_network_policy(egress_envelope)
                session.status = "ready"
                self.db.commit()
            except Exception:
                session.lifecycle_state = "COLD"
                session.backend_session_ref = None
                self.db.commit()
                raise
        return handle

    def _mark_session_failed(self, session: SandboxSession) -> None:
        session.status = "failed"
        session.runtime_last_used_at = utc_now()
        session.lifecycle_state = (
            "WARM_IDLE" if session.backend_session_ref else "COLD"
        )

    def _discard_killed_runtime(self, session: SandboxSession) -> None:
        if self._pooling_enabled(session):
            # Shared warm instance: task-level termination happens inside the
            # daemon (process group only); the instance container must survive
            # a single task timeout so sibling chat tasks keep running.
            self._release_instance_for_session(session)
            return
        if session.backend_session_ref:
            try:
                self._runtime_backend(session).delete(
                    SandboxSessionHandle(session.id, session.backend_session_ref)
                )
                session.backend_session_ref = None
            except SandboxBackendError:
                logger.exception("Failed to remove killed Agent sandbox container")
        session.lifecycle_state = "COLD"

    def _record_policy_block(
        self,
        *,
        action: str,
        reason: str,
        argv: tuple[str, ...] | None = None,
        path: str | None = None,
    ) -> None:
        details: dict[str, Any] = {"reason": reason}
        if argv:
            details["argv_digest"] = hashlib.sha256("\0".join(argv).encode()).hexdigest()
        if path is not None:
            details["path_digest"] = hashlib.sha256(path.encode()).hexdigest()
        self.audit.record(
            actor_id=self.actor_id,
            action=action,
            resource_type="sandbox_agent_policy",
            resource_id=new_id(),
            outcome="blocked",
            details=details,
        )
        self.db.commit()

    def _validate_command(
        self, payload: SandboxAgentCommandRequest
    ) -> tuple[tuple[str, ...], dict[str, Any] | None]:
        raw = tuple(payload.argv)
        try:
            argv = validate_agent_argv(
                raw,
                max_args=self.settings.sandbox_agent_command_args_max,
            )
            validate_agent_cwd(payload.cwd)
        except SandboxCapabilityMismatch as exc:
            self._record_policy_block(
                action="sandbox.agent.command.blocked",
                reason=str(exc),
                argv=raw,
            )
            raise AppError(422, "sandbox_command_blocked", str(exc)) from exc
        # Destructive argv is shape-validated above; authorization is separate.
        intent = self.authz.authorize_or_raise(
            chat_session_id=payload.chat_session_id, argv=argv
        )
        return argv, intent

    def create_session(self, payload: SandboxAgentSessionCreateRequest) -> SandboxSession:
        session = self._resolve_session(
            payload.sandbox_session_id,
            payload.chat_session_id,
            payload.runtime,
        )
        try:
            self._ensure_backend_session(session)
            self._touch_session(session)
            session.lifecycle_state = "WARM_IDLE"
            self.audit.record(
                actor_id=self.actor_id,
                action="sandbox.agent.session.ready",
                resource_type="sandbox_session",
                resource_id=session.id,
                details={"chat_session_id": session.chat_session_id, "backend_id": session.backend_id},
            )
            self.db.commit()
            self.db.refresh(session)
            return session
        except SandboxBackendUnavailable as exc:
            self._mark_session_failed(session)
            self.audit.record(
                actor_id=self.actor_id,
                action="sandbox.agent.session.unavailable",
                resource_type="sandbox_session",
                resource_id=session.id,
                outcome="failed",
                details={"error_class": "sandbox_backend_unavailable"},
            )
            self.db.commit()
            raise AppError(503, "sandbox_backend_unavailable", str(exc)) from exc
        except SandboxBackendError as exc:
            logger.exception("Sandbox session startup failed")
            self._mark_session_failed(session)
            self.db.commit()
            raise AppError(502, "sandbox_execution_failed", "Sandbox session startup failed") from exc

    def _claim_command_lease(
        self, session: SandboxSession, command: SandboxAgentCommand
    ) -> tuple[str, int]:
        now = utc_now()
        token_hash = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        lease_expires_at = now + timedelta(
            seconds=self.settings.sandbox_wall_time_seconds + 30
        )
        claimed = self.db.execute(
            update(SandboxSession)
            .where(
                SandboxSession.id == session.id,
                SandboxSession.active_command_id.is_(None),
            )
            .values(
                active_command_id=command.id,
                lease_token_hash=token_hash,
                lease_expires_at=lease_expires_at,
                heartbeat_at=now,
                command_generation=SandboxSession.command_generation + 1,
                status="running",
                lifecycle_state="RUNNING",
            )
        )
        if claimed.rowcount != 1:
            self.db.rollback()
            raise AppError(
                409,
                "sandbox_session_busy",
                "Another command is already running in this sandbox session",
                {"sandbox_session_id": session.id},
            )
        self.db.commit()
        self.db.refresh(session)
        return token_hash, session.command_generation

    def _release_command_lease(
        self,
        session: SandboxSession,
        command: SandboxAgentCommand,
        token_hash: str,
        generation: int,
    ) -> None:
        released = self.db.execute(
            update(SandboxSession)
            .where(
                SandboxSession.id == session.id,
                SandboxSession.active_command_id == command.id,
                SandboxSession.lease_token_hash == token_hash,
                SandboxSession.command_generation == generation,
            )
            .values(
                active_command_id=None,
                lease_token_hash=None,
                lease_expires_at=None,
                heartbeat_at=utc_now(),
            )
        )
        if released.rowcount != 1:
            self.db.rollback()
            raise AppError(
                409,
                "sandbox_command_lease_lost",
                "Sandbox command lease ownership changed before completion",
            )

    def execute_command(
        self,
        payload: SandboxAgentCommandRequest,
        *,
        idempotency_key: str | None,
        timeout_seconds: int | None = None,
    ) -> SandboxAgentCommand:
        argv, _ = self._validate_command(payload)
        argv_digest = hashlib.sha256("\0".join(argv).encode()).hexdigest()
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
        if key_hash:
            existing = self.db.scalar(
                select(SandboxAgentCommand).where(
                    SandboxAgentCommand.workspace_id == self.workspace_id,
                    SandboxAgentCommand.owner_user_id == self.actor_id,
                    SandboxAgentCommand.idempotency_key_hash == key_hash,
                )
            )
            if existing is not None:
                if (
                    existing.chat_session_id != payload.chat_session_id
                    or existing.argv_digest != argv_digest
                    or existing.cwd_relative != payload.cwd
                ):
                    raise AppError(
                        409,
                        "idempotency_key_conflict",
                        "The sandbox idempotency key was already used for a different command",
                    )
                return existing
        session = self._resolve_session(
            payload.sandbox_session_id,
            payload.chat_session_id,
            payload.runtime,
        )
        intent = self.authz.classify_argv(argv)
        destructive_paths = tuple(intent.get("paths") or ()) if intent else ()
        if destructive_paths:
            intent_digest = destructive_intent_digest(
                chat_session_id=payload.chat_session_id,
                sandbox_session_id=session.id,
                argv=argv,
                paths=destructive_paths,
            )
            destructive_prefixes = self.authz.consume_delete_prefixes(
                chat_session_id=payload.chat_session_id,
                sandbox_session_id=session.id,
                paths=destructive_paths,
                command_intent_digest=intent_digest,
            )
        else:
            destructive_prefixes = ()
        command = SandboxAgentCommand(
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            sandbox_session_id=session.id,
            chat_session_id=payload.chat_session_id,
            idempotency_key_hash=key_hash,
            argv_digest=argv_digest,
            argv_redacted=_redacted_agent_argv(argv),
            cwd_relative=payload.cwd,
            status="created",
        )
        self.db.add(command)
        self.db.flush()
        lease_token_hash, command_generation = self._claim_command_lease(session, command)
        try:
            handle = self._ensure_backend_session(session)
            if self._pooling_enabled(session):
                self._borrow_instance(session)
            command.status = "running"
            self.db.commit()
            if self._pooling_enabled(session):
                prefix = self._container_prefix(payload.chat_session_id)
                cwd_relative = (
                    prefix if payload.cwd in ("", ".") else f"{prefix}/{payload.cwd}"
                )
                destructive_prefixes = tuple(
                    f"{prefix}/{item}" for item in destructive_prefixes
                )
            else:
                cwd_relative = payload.cwd
            result = self._runtime_backend(session).exec_agent(
                handle,
                argv,
                cwd_relative=cwd_relative,
                timeout_seconds=timeout_seconds or self.settings.sandbox_wall_time_seconds,
                output_limit=self.settings.sandbox_output_bytes,
                destructive_path_prefixes=destructive_prefixes,
            )
            command.exit_code = result.exit_code
            command.timed_out = result.timed_out
            command.latency_ms = result.latency_ms
            command.truncated = result.truncated
            command.resource_usage = {
                "output_bytes": len(result.stdout) + len(result.stderr),
                "network_mode": "none",
            }
            command.stdout_summary = _redact_agent_text(
                result.stdout.decode("utf-8", errors="replace")
            )
            command.stderr_summary = _redact_agent_text(
                result.stderr.decode("utf-8", errors="replace")
            )
            if result.truncated:
                raise SandboxOutputLimitExceeded(
                    "Sandbox output exceeded the configured host-side limit"
                )
            if result.timed_out:
                command.status = "failed"
                command.error_class = "sandbox_timeout"
                command.error_message = "Sandbox Agent command timed out"
                self._mark_session_failed(session)
            elif result.exit_code != 0:
                command.status = "failed"
                command.error_class = "sandbox_command_failed"
                command.error_message = "Sandbox Agent command exited with a non-zero status"
                session.status = "ready"
            else:
                command.status = "completed"
                session.status = "ready"
            self._touch_session(session)
            if result.timed_out:
                self._discard_killed_runtime(session)
            else:
                session.lifecycle_state = "WARM_IDLE"
            self.audit.record(
                actor_id=self.actor_id,
                action=(
                    "sandbox.agent.command.completed"
                    if command.status == "completed"
                    else "sandbox.agent.command.failed"
                ),
                resource_type="sandbox_agent_command",
                resource_id=command.id,
                outcome="success" if command.status == "completed" else "failed",
                details={
                    "sandbox_session_id": session.id,
                    "argv_digest": argv_digest,
                    "exit_code": command.exit_code,
                    "timed_out": command.timed_out,
                },
            )
            self._release_command_lease(
                session,
                command,
                lease_token_hash,
                command_generation,
            )
            self.db.commit()
            self.db.refresh(command)
            return command
        except AppError as exc:
            if exc.code in {
                "sandbox_auth_required",
                "sandbox_grant_already_consumed",
                "sandbox_session_busy",
                "sandbox_command_lease_lost",
            }:
                exc.details.setdefault("sandbox_session_id", session.id)
            if session.active_command_id == command.id:
                self._release_command_lease(
                    session,
                    command,
                    lease_token_hash,
                    command_generation,
                )
                self.db.commit()
            raise
        except SandboxBackendUnavailable as exc:
            return self._fail_command(
                command,
                session,
                "sandbox_backend_unavailable",
                "Sandbox backend is unavailable",
                type(exc).__name__,
            )
        except SandboxCapabilityMismatch as exc:
            return self._fail_command(
                command,
                session,
                "sandbox_command_blocked",
                "Sandbox command was blocked by policy",
                type(exc).__name__,
            )
        except SandboxWorkspaceQuotaExceeded as exc:
            return self._fail_command(
                command,
                session,
                "sandbox_workspace_quota_exceeded",
                "Sandbox workspace aggregate disk quota was exceeded",
                type(exc).__name__,
            )
        except SandboxDestructiveAuthorizationRequired as exc:
            return self._fail_command(
                command,
                session,
                "sandbox_auth_required",
                "Sandbox code attempted a workspace deletion that requires authorization",
                type(exc).__name__,
            )
        except SandboxOutputLimitExceeded as exc:
            return self._fail_command(
                command,
                session,
                "sandbox_output_limit_exceeded",
                "Sandbox Agent command exceeded the output limit",
                type(exc).__name__,
            )
        except SandboxBackendError as exc:
            return self._fail_command(
                command,
                session,
                "sandbox_execution_failed",
                "Sandbox Agent command execution failed",
                type(exc).__name__,
            )
        finally:
            self._release_instance_for_session(session)

    def _fail_command(
        self,
        command: SandboxAgentCommand,
        session: SandboxSession,
        error_class: str,
        message: str,
        internal_class: str,
        *,
        lease_token_hash: str | None = None,
        command_generation: int | None = None,
    ) -> SandboxAgentCommand:
        command.status = "failed"
        command.error_class = error_class
        command.error_message = message
        self._mark_session_failed(session)
        if error_class in {
            "sandbox_timeout",
            "sandbox_output_limit_exceeded",
            "sandbox_workspace_quota_exceeded",
        }:
            self._discard_killed_runtime(session)
        if lease_token_hash is None:
            lease_token_hash = session.lease_token_hash
        if command_generation is None and session.active_command_id == command.id:
            command_generation = session.command_generation
        if lease_token_hash is not None and command_generation is not None:
            self._release_command_lease(
                session,
                command,
                lease_token_hash,
                command_generation,
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.command.failed",
            resource_type="sandbox_agent_command",
            resource_id=command.id,
            outcome="failed",
            details={
                "sandbox_session_id": session.id,
                "error_class": error_class,
                "internal_class": internal_class,
            },
        )
        self.db.commit()
        self.db.refresh(command)
        return command

    def _resolve_file_session(self, sandbox_session_id: str | None, chat_session_id: str) -> tuple[SandboxSession, SandboxSessionHandle]:
        session = self._resolve_session(sandbox_session_id, chat_session_id)
        try:
            handle = self._ensure_backend_session(session)
            return session, handle
        except SandboxBackendUnavailable as exc:
            self._mark_session_failed(session)
            self.db.commit()
            raise AppError(503, "sandbox_backend_unavailable", str(exc)) from exc
        except SandboxBackendError as exc:
            self._mark_session_failed(session)
            self.db.commit()
            raise AppError(502, "sandbox_execution_failed", "Sandbox workspace is unavailable") from exc

    def environment_info(self, payload: SandboxAgentEnvironmentRequest) -> dict[str, Any]:
        self._require_chat_session(payload.chat_session_id)
        manifest_path = Path(__file__).resolve().parents[2] / "sandbox" / "environment-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError(503, "sandbox_environment_unavailable", "Sandbox environment manifest is unavailable") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0":
            raise AppError(503, "sandbox_environment_unavailable", "Sandbox environment manifest is invalid")
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id)
        backend = self._runtime_backend(session)
        return {
            **manifest,
            "sandbox_session_id": session.id,
            "file_limit_bytes": self.settings.sandbox_agent_file_bytes,
            "workspace_limit_bytes": self.settings.sandbox_disk_bytes,
            "output_limit_bytes": self.settings.sandbox_output_bytes,
            "network": session.network_policy.get("mode", "none"),
            "image_pinned": _runtime_image_pinned(
                backend,
                self.settings,
                resolve_sandbox_image(self.settings) or "",
                session.runtime_kind,
            ),
        }

    def publish_image(self, payload: SandboxAgentImagePublishRequest) -> dict[str, Any]:
        path = validate_agent_workspace_path(payload.path)
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id)
        try:
            data = self.workspace_files.materialize_bytes(payload.chat_session_id, path)
        except AppError:
            try:
                handle = self._ensure_backend_session(session)
                container_path = (
                    self._container_path(payload.chat_session_id, path)
                    if self._pooling_enabled(session)
                    else path
                )
                data = self._runtime_backend(session).read(
                    handle, container_path, self.settings.sandbox_agent_file_bytes
                )
            except (SandboxBackendUnavailable, SandboxBackendError) as exc:
                raise AppError(422, "sandbox_file_unavailable", "Sandbox image file cannot be read") from exc
        mime_type = mimetypes.guess_type(path)[0] or ""
        allowed = {"image/png", "image/jpeg", "image/webp", "image/gif"}
        if mime_type not in allowed or not data:
            raise AppError(415, "sandbox_image_type_unsupported", "Only PNG, JPEG, WebP, and GIF sandbox images can be published")
        try:
            from PIL import Image
            from io import BytesIO

            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
        except Exception as exc:
            raise AppError(422, "sandbox_image_invalid", "Sandbox image bytes are invalid") from exc
        if width < 1 or height < 1 or width * height > 40_000_000:
            raise AppError(422, "sandbox_image_dimensions_invalid", "Sandbox image dimensions exceed the preview limit")
        published = self.workspace_files.publish_path(
            chat_session_id=payload.chat_session_id,
            path=path,
            data=data,
            sandbox_session_id=session.id,
            title=payload.title,
        )
        artifact = published.get("part")
        if isinstance(artifact, dict):
            artifact_data = artifact.get("data")
            if isinstance(artifact_data, dict):
                artifact_data.update({"width": width, "height": height, "alt": payload.alt})
        published.update({"width": width, "height": height, "alt": payload.alt})
        return published

    def write_file(self, payload: SandboxAgentFileWriteRequest) -> dict[str, Any]:
        try:
            path = validate_agent_workspace_path(payload.path)
        except SandboxCapabilityMismatch as exc:
            self._record_policy_block(
                action="sandbox.agent.file_write.blocked",
                reason=str(exc),
                path=payload.path,
            )
            raise AppError(422, "sandbox_path_blocked", str(exc)) from exc
        data = payload.content.encode("utf-8")
        if len(data) > self.settings.sandbox_agent_file_bytes:
            self._record_policy_block(
                action="sandbox.agent.file_write.blocked",
                reason="Sandbox Agent file exceeds the configured byte limit",
                path=payload.path,
            )
            raise AppError(422, "sandbox_file_too_large", "Sandbox Agent file exceeds the configured byte limit")
        session, handle = self._resolve_file_session(payload.sandbox_session_id, payload.chat_session_id)
        try:
            container_path = (
                self._container_path(payload.chat_session_id, path)
                if self._pooling_enabled(session)
                else path
            )
            self._runtime_backend(session).write_agent_file(handle, container_path, data)
        except SandboxWorkspaceQuotaExceeded as exc:
            self.db.commit()
            raise AppError(
                413,
                "sandbox_workspace_quota_exceeded",
                "Sandbox workspace aggregate disk quota was exceeded",
            ) from exc
        except SandboxBackendError as exc:
            self._mark_session_failed(session)
            self.db.commit()
            raise AppError(502, "sandbox_execution_failed", "Sandbox file write failed") from exc
        # Dual-write into the content-addressed session workspace (two-layer store).
        role = "output" if path.startswith("outputs/") else "work"
        workspace_view = self.workspace_files.put_bytes(
            chat_session_id=payload.chat_session_id,
            path=path,
            data=data,
            role=role,
            sandbox_session_id=session.id,
            source="agent_write",
            publish_file=path.startswith("outputs/"),
        )
        session.status = "ready"
        self._touch_session(session)
        session.lifecycle_state = "WARM_IDLE"
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.file_written",
            resource_type="sandbox_session",
            resource_id=session.id,
            details={
                "path": path,
                "size_bytes": len(data),
                "blob_sha256": workspace_view.get("blob_sha256"),
                "file_id": workspace_view.get("file_id"),
            },
        )
        self.db.commit()
        result = {
            "sandbox_session_id": session.id,
            "path": path,
            "size_bytes": len(data),
            "sha256": workspace_view.get("blob_sha256"),
            "blob_sha256": workspace_view.get("blob_sha256"),
            "file_id": workspace_view.get("file_id"),
            "role": workspace_view.get("role"),
        }
        if workspace_view.get("file_id"):
            result["artifact"] = {
                "type": "sandbox_artifact",
                "status": "completed",
                "data": {
                    "kind": "file",
                    "title": path.rsplit("/", 1)[-1],
                    "path": path,
                    "file_id": workspace_view.get("file_id"),
                    "size_bytes": len(data),
                    "sha256": workspace_view.get("blob_sha256"),
                    "mime_type": workspace_view.get("mime_type"),
                    "sandbox_session_id": session.id,
                    "chat_session_id": payload.chat_session_id,
                },
            }
        return result

    def _read_text_for_mutation(
        self, *, chat_session_id: str, path: str, sandbox_session_id: str | None
    ) -> tuple[str, str]:
        result = self.read_file(
            SandboxAgentFileReadRequest(
                chat_session_id=chat_session_id,
                path=path,
                sandbox_session_id=sandbox_session_id,
            )
        )
        content = str(result.get("content") or "")
        return content, hashlib.sha256(content.encode("utf-8")).hexdigest()

    def append_file(self, payload: SandboxAgentFileAppendRequest) -> dict[str, Any]:
        path = validate_agent_workspace_path(payload.path)
        try:
            current, digest = self._read_text_for_mutation(
                chat_session_id=payload.chat_session_id,
                path=path,
                sandbox_session_id=payload.sandbox_session_id,
            )
        except AppError as exc:
            if exc.code != "sandbox_file_unavailable":
                raise
            current, digest = "", hashlib.sha256(b"").hexdigest()
        if payload.expected_sha256 and payload.expected_sha256 != digest:
            raise AppError(409, "sandbox_file_changed", "Sandbox file changed; read it again before appending")
        return self.write_file(
            SandboxAgentFileWriteRequest(
                chat_session_id=payload.chat_session_id,
                path=path,
                content=current + payload.content,
                sandbox_session_id=payload.sandbox_session_id,
            )
        )

    def edit_file(self, payload: SandboxAgentFileEditRequest) -> dict[str, Any]:
        path = validate_agent_workspace_path(payload.path)
        current, digest = self._read_text_for_mutation(
            chat_session_id=payload.chat_session_id,
            path=path,
            sandbox_session_id=payload.sandbox_session_id,
        )
        if payload.expected_sha256 != digest:
            raise AppError(409, "sandbox_file_changed", "Sandbox file changed; read it again before editing")
        count = current.count(payload.old_string)
        if payload.replace_all:
            if count == 0:
                raise AppError(
                    422,
                    "sandbox_edit_match_invalid",
                    "old_string was not found in the sandbox file",
                )
            if count > 100:
                raise AppError(
                    422,
                    "sandbox_edit_too_many_matches",
                    "replace_all matched more than 100 occurrences; use sandbox_exec for bulk rewrites",
                )
            content = current.replace(payload.old_string, payload.new_string)
        else:
            if count != 1:
                raise AppError(
                    422,
                    "sandbox_edit_match_invalid",
                    "old_string must occur exactly once in the sandbox file",
                )
            content = current.replace(payload.old_string, payload.new_string, 1)
        result = self.write_file(
            SandboxAgentFileWriteRequest(
                chat_session_id=payload.chat_session_id,
                path=path,
                content=content,
                sandbox_session_id=payload.sandbox_session_id,
            )
        )
        result["replaced_count"] = count
        return result

    def read_file(self, payload: SandboxAgentFileReadRequest) -> dict[str, Any]:
        try:
            path = validate_agent_workspace_path(payload.path)
        except SandboxCapabilityMismatch as exc:
            self._record_policy_block(
                action="sandbox.agent.file_read.blocked",
                reason=str(exc),
                path=payload.path,
            )
            raise AppError(422, "sandbox_path_blocked", str(exc)) from exc
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id)
        data: bytes | None = None
        # Prefer the content-addressed session workspace so chat-seeded inputs/
        # remain readable even when Docker is unavailable or not yet synced.
        try:
            data = self.workspace_files.materialize_bytes(payload.chat_session_id, path)
        except AppError:
            data = None
        if data is None:
            try:
                handle = self._ensure_backend_session(session)
                container_path = (
                    self._container_path(payload.chat_session_id, path)
                    if self._pooling_enabled(session)
                    else path
                )
                data = self._runtime_backend(session).read(
                    handle, container_path, self.settings.sandbox_agent_file_bytes
                )
            except SandboxBackendUnavailable as exc:
                self._mark_session_failed(session)
                self.db.commit()
                raise AppError(503, "sandbox_backend_unavailable", str(exc)) from exc
            except SandboxBackendError as exc:
                raise AppError(422, "sandbox_file_unavailable", "Sandbox Agent file cannot be read") from exc
        try:
            content = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise AppError(422, "sandbox_file_not_text", "Sandbox Agent file is not UTF-8 text") from exc
        if len(data) > self.settings.sandbox_agent_file_bytes:
            raise AppError(422, "sandbox_file_too_large", "Sandbox Agent file exceeds the configured byte limit")
        # Line-range view: slice by [start_line, end_line] (1-based, inclusive),
        # then optionally cap by max_chars. Whole-file reads stay untouched.
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        lo = (payload.start_line - 1) if payload.start_line is not None else 0
        hi = payload.end_line if payload.end_line is not None else len(lines)
        if payload.start_line is not None and lo > len(lines):
            raise AppError(
                422,
                "sandbox_file_range_out_of_bounds",
                f"start_line {payload.start_line} is beyond the file's {total_lines} lines",
            )
        if hi > len(lines):
            hi = len(lines)
        if lo > hi:
            raise AppError(
                422,
                "sandbox_file_range_invalid",
                "end_line must be greater than or equal to start_line",
            )
        selected = lines[lo:hi]
        content = "".join(selected)
        truncated = False
        if payload.max_chars is not None and len(content) > payload.max_chars:
            content = content[: payload.max_chars]
            truncated = True
        self._touch_session(session)
        session.lifecycle_state = "WARM_IDLE"
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.file_read",
            resource_type="sandbox_session",
            resource_id=session.id,
            details={
                "path": path,
                "size_bytes": len(data),
                "total_lines": total_lines,
                "start_line": lo + 1 if payload.start_line is not None else None,
                "end_line": hi if payload.end_line is not None else None,
            },
        )
        self.db.commit()
        return {
            "sandbox_session_id": session.id,
            "path": path,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": content,
            "total_lines": total_lines,
            "total_bytes": len(data),
            "start_line": lo + 1 if payload.start_line is not None else None,
            "end_line": hi if payload.end_line is not None else None,
            "truncated": truncated,
        }

    def list_files(self, payload: SandboxAgentFileListRequest) -> dict[str, Any]:
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id)
        # Always surface the durable session workspace tree (chat attachments,
        # agent writes, published outputs). Docker listing is best-effort.
        logical = self.workspace_files.list_entries(payload.chat_session_id)
        by_path: dict[str, dict[str, Any]] = {
            entry.path: {
                "path": entry.path,
                "size_bytes": int(entry.size_bytes or 0),
                "role": entry.role,
                "file_id": entry.file_id,
                "source": entry.source,
                "mtime": entry.updated_at.isoformat() if entry.updated_at else None,
            }
            for entry in logical
        }
        try:
            handle = self._ensure_backend_session(session)
            prefix = (
                self._container_prefix(payload.chat_session_id)
                if self._pooling_enabled(session)
                else None
            )
            for item in self._runtime_backend(session).list_files(
                handle, limit_entries=200
            ):
                container_path = item.path
                if prefix and container_path.startswith(prefix + "/"):
                    container_path = container_path[len(prefix) + 1 :]
                elif prefix and container_path == prefix:
                    continue
                existing = by_path.get(container_path)
                if existing is None:
                    by_path[container_path] = {
                        "path": container_path,
                        "size_bytes": item.size_bytes,
                        "role": "work",
                        "file_id": None,
                        "source": "container",
                        "mtime": None,
                    }
                else:
                    existing["size_bytes"] = item.size_bytes or existing["size_bytes"]
        except (SandboxBackendUnavailable, SandboxBackendError):
            # Logical workspace alone is enough for list; agent can still read
            # materializable inputs without a live Docker handle.
            pass
        pattern = payload.pattern
        if pattern:
            filtered = {
                path: item for path, item in by_path.items() if fnmatch.fnmatch(path, pattern)
            }
            by_path = filtered
        limit = payload.max_results or 200
        files = sorted(by_path.values(), key=lambda item: item["path"])[:limit]
        self._touch_session(session)
        session.lifecycle_state = "WARM_IDLE"
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.files_listed",
            resource_type="sandbox_session",
            resource_id=session.id,
            details={
                "file_count": len(files),
                "pattern": pattern,
                "limit": limit,
            },
        )
        self.db.commit()
        return {
            "sandbox_session_id": session.id,
            "path": ".",
            "size_bytes": 0,
            "files": files,
        }

    # Host-side content search over the durable session workspace. Container
    # snapshots are deliberately not streamed here: per-file host materialization
    # keeps the search fast, memory-bounded and Docker-independent.
    GREP_MAX_FILE_BYTES = 4 * 1024 * 1024
    GREP_MAX_TOTAL_BYTES = 64 * 1024 * 1024
    GREP_MAX_LINE_CHARS = 500

    def grep_files(self, payload: SandboxAgentFileGrepRequest) -> dict[str, Any]:
        self._require_chat_session(payload.chat_session_id)
        try:
            flags = 0 if payload.case_sensitive else re.IGNORECASE
            matcher = re.compile(payload.pattern, flags)
        except re.error as exc:
            raise AppError(
                422,
                "sandbox_grep_invalid_pattern",
                f"grep pattern is not a valid regular expression: {exc}",
            ) from exc
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id)
        path_filter = payload.path
        ctx = payload.context_lines
        max_matches = payload.max_matches

        matches: list[dict[str, Any]] = []
        file_counts: list[dict[str, Any]] = []
        searched = 0
        skipped_binary = 0
        skipped_large = 0
        skipped_container_only = 0
        truncated = False
        total_scanned = 0

        for entry in self.workspace_files.list_entries(payload.chat_session_id):
            if path_filter and not fnmatch.fnmatch(entry.path, path_filter):
                continue
            if int(entry.size_bytes or 0) > self.GREP_MAX_FILE_BYTES:
                skipped_large += 1
                continue
            try:
                data = self.workspace_files.materialize_bytes(
                    payload.chat_session_id, entry.path
                )
            except AppError:
                # exec-generated files live only inside the container; the
                # durable logical store cannot materialize them host-side.
                skipped_container_only += 1
                continue
            total_scanned += len(data)
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                skipped_binary += 1
                continue
            searched += 1
            lines = text.splitlines(keepends=True)
            line_count = len(lines)
            file_match_count = 0
            index = 0
            while index < line_count:
                if not matcher.search(lines[index]):
                    index += 1
                    continue
                file_match_count += 1
                window_lo = max(0, index - ctx)
                window_hi = min(line_count - 1, index + ctx)
                context_rows: list[dict[str, Any]] = []
                for row in range(window_lo, window_hi + 1):
                    row_text = lines[row].rstrip("\n").rstrip("\r")
                    if len(row_text) > self.GREP_MAX_LINE_CHARS:
                        row_text = row_text[: self.GREP_MAX_LINE_CHARS] + "…"
                    if row != index:
                        context_rows.append({"line_number": row + 1, "text": row_text})
                matched_text = lines[index].rstrip("\n").rstrip("\r")
                if len(matched_text) > self.GREP_MAX_LINE_CHARS:
                    matched_text = matched_text[: self.GREP_MAX_LINE_CHARS] + "…"
                matches.append(
                    {
                        "path": entry.path,
                        "line_number": index + 1,
                        "text": matched_text,
                        "context": context_rows,
                    }
                )
                if len(matches) >= max_matches:
                    truncated = True
                    break
                index = window_hi + 1
            if file_match_count:
                file_counts.append({"path": entry.path, "matches": file_match_count})
            if truncated or total_scanned >= self.GREP_MAX_TOTAL_BYTES:
                if not truncated:
                    truncated = True
                break

        self._touch_session(session)
        session.lifecycle_state = "WARM_IDLE"
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.files_grep",
            resource_type="sandbox_session",
            resource_id=session.id,
            details={
                "pattern": payload.pattern,
                "searched_files": searched,
                "match_files": len(file_counts),
                "matches": len(matches),
                "truncated": truncated,
            },
        )
        self.db.commit()
        return {
            "sandbox_session_id": session.id,
            "pattern": payload.pattern,
            "case_sensitive": payload.case_sensitive,
            "searched_files": searched,
            "skipped_binary": skipped_binary,
            "skipped_large": skipped_large,
            "skipped_container_only": skipped_container_only,
            "matches": matches,
            "file_counts": file_counts,
            "truncated": truncated,
        }

    def delete_file(self, payload: SandboxAgentFileDeleteRequest) -> dict[str, Any]:
        path = validate_agent_workspace_path(payload.path)
        # Grant API parity: destructive authorizations are limited to the
        # session work/ tree (see SandboxAuthorizationService.grant).
        if not (path == "work" or path.startswith("work/")):
            self._record_policy_block(
                action="sandbox.agent.file_delete.blocked",
                reason="sandbox_delete_file is limited to the session work/ tree",
                path=path,
            )
            raise AppError(
                422,
                "sandbox_path_blocked",
                "sandbox_delete_file is limited to the session work/ tree; use sandbox_exec for other paths",
            )
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id)
        # Reuse the exact single-use grant flow used by destructive argv: the
        # chat authorization dialog grants the intent digest, then the retry
        # consumes it atomically here.
        argv = ("sandbox_delete_file", path)
        intent_digest = destructive_intent_digest(
            chat_session_id=payload.chat_session_id,
            sandbox_session_id=session.id,
            argv=argv,
            paths=(path,),
        )
        self.authz.consume_delete_prefixes(
            chat_session_id=payload.chat_session_id,
            sandbox_session_id=session.id,
            paths=(path,),
            command_intent_digest=intent_digest,
        )
        self.workspace_files.delete_entry(payload.chat_session_id, path)
        # Best-effort container cleanup: the logical store is authoritative;
        # Docker unavailability must not fail the durable delete.
        try:
            handle = self._ensure_backend_session(session)
            container_path = (
                self._container_path(payload.chat_session_id, path)
                if self._pooling_enabled(session)
                else path
            )
            self._runtime_backend(session).delete_agent_file(handle, container_path)
        except (SandboxBackendUnavailable, SandboxBackendError):
            pass
        session.status = "ready"
        self._touch_session(session)
        session.lifecycle_state = "WARM_IDLE"
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.file_deleted",
            resource_type="sandbox_session",
            resource_id=session.id,
            details={"path": path},
        )
        self.db.commit()
        return {
            "sandbox_session_id": session.id,
            "path": path,
            "deleted": True,
        }

    def video_info(self, payload: SandboxAgentVideoInfoRequest) -> dict[str, Any]:
        """Return safe metadata for an Agent video reference without materializing it."""

        try:
            path = validate_agent_workspace_path(payload.path)
        except SandboxCapabilityMismatch as exc:
            raise AppError(422, "sandbox_path_blocked", str(exc)) from exc
        if not path.startswith("inputs/"):
            raise AppError(
                403,
                "sandbox_video_input_required",
                "Video inspection is limited to registered session inputs",
            )
        entry = self.workspace_files.get_entry(payload.chat_session_id, path)
        mime_type = (entry.mime_type or "").casefold().split(";", 1)[0].strip()
        if not mime_type.startswith("video/"):
            raise AppError(415, "video_required", "Only registered video inputs can be inspected")
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.video_info",
            resource_type="session_workspace_entry",
            resource_id=entry.id,
            details={"path": path, "file_id": entry.file_id, "size_bytes": entry.size_bytes},
        )
        self.db.commit()
        return {
            "path": path,
            "file_id": entry.file_id,
            "mime_type": mime_type,
            "size_bytes": entry.size_bytes,
            "sha256": entry.blob_sha256,
            "materialized": False,
            "guidance": (
                "Use sandbox_exec with ffprobe/ffmpeg only after materializing a local copy if "
                "byte-level processing is required. For semantic understanding, use the configured "
                "video analysis provider when available."
            ),
        }

    def transcribe_workspace_audio(
        self, payload: SandboxAgentTranscribeRequest
    ) -> dict[str, Any]:
        """Host-side ASR bridge: the sandbox stays offline and secret-free.

        The workspace bind mount doubles as the data channel — audio bytes are
        read host-side (logical store first, then the bind mount), the user's
        configured ASR Provider runs on the host with host credentials, and
        the transcript is written back through the standard quota-checked
        write path.  No network or secret ever enters the container.
        """

        try:
            path = validate_agent_workspace_path(payload.path)
        except SandboxCapabilityMismatch as exc:
            self._record_policy_block(
                action="sandbox.agent.transcribe.blocked",
                reason=str(exc),
                path=payload.path,
            )
            raise AppError(422, "sandbox_path_blocked", str(exc)) from exc
        if Path(path).suffix.casefold() not in AUDIO_EXTENSIONS:
            raise AppError(
                415,
                "audio_required",
                "Only audio workspace files can be transcribed; use "
                "learngraph_tasks.audio_transcode in the sandbox first for other formats",
            )
        filename = path.rsplit("/", 1)[-1]
        stem = filename.rsplit(".", 1)[0] or "transcript"
        try:
            output_path = validate_agent_workspace_path(
                payload.output_path or f"work/transcripts/{stem}.txt"
            )
        except SandboxCapabilityMismatch as exc:
            raise AppError(422, "sandbox_path_blocked", str(exc)) from exc
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id)
        data: bytes | None = None
        try:
            data = self.workspace_files.materialize_bytes(payload.chat_session_id, path)
        except AppError:
            data = None
        if data is None:
            if session.backend_id == "sandboxd":
                # The sandboxd control plane has no host bind mount: read
                # through the daemon File API with a bounded limit instead.
                try:
                    handle = self._ensure_backend_session(session)
                    container_path = (
                        self._container_path(payload.chat_session_id, path)
                        if self._pooling_enabled(session)
                        else path
                    )
                    data = self._runtime_backend(session).read(
                        handle, container_path, self.settings.sandbox_agent_file_bytes
                    )
                except (SandboxBackendUnavailable, SandboxBackendError):
                    data = None
            else:
                data = self._read_workspace_bytes_from_host(session, path)
        if data is None:
            raise AppError(
                404,
                "sandbox_file_unavailable",
                "Workspace audio file was not found in the session workspace",
            )
        if len(data) > self.settings.max_upload_bytes:
            raise AppError(
                413, "sandbox_file_too_large", "Workspace audio exceeds the upload limit"
            )
        provider = transcription_provider_for_workspace(
            self.db,
            self.workspace_id,
            self.settings,
            purpose="stored",
        )
        if provider is None:
            raise AppError(
                503,
                "transcription_provider_unavailable",
                "No enabled remote ASR Provider is configured for this workspace",
            )
        billing = BillingService(self.db, self.workspace_id, self.actor_id)
        quote = billing.preflight_model_call(
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            feature="audio_transcription",
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            remote_capability=True,
        )
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.transcription.started",
            resource_type="sandbox_session",
            resource_id=session.id,
            details={
                "path": path,
                "size_bytes": len(data),
                "provider_id": provider.provider_id,
                "model_id": provider.model_id,
            },
        )
        self.db.commit()
        started = time.monotonic()
        try:
            result = provider.transcribe(
                filename=filename,
                mime_type=mime_type,
                content=data,
                language=payload.language,
            )
        except TranscriptionProviderError as exc:
            self.audit.record(
                actor_id=self.actor_id,
                action="sandbox.agent.transcription.failed",
                resource_type="sandbox_session",
                resource_id=session.id,
                outcome="failed",
                details={"path": path, "provider_id": provider.provider_id},
            )
            self.db.commit()
            raise AppError(502, "transcription_provider_failed", str(exc)) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        billing.record_usage(
            quote,
            input_tokens=int(result.usage.get("input_tokens") or 0),
            output_tokens=int(result.usage.get("output_tokens") or 0),
            attempt=1,
            latency_ms=latency_ms,
            usage_reported=bool(result.usage),
        )
        self.db.commit()
        transcript = result.text or ""
        # The durable artifact goes through the standard agent write path and
        # must respect its byte cap; truncation is rare and flagged.
        limit_bytes = min(1_048_576, self.settings.sandbox_agent_file_bytes)
        transcript_truncated = False
        stored = transcript
        while stored and len(stored.encode("utf-8")) > limit_bytes:
            transcript_truncated = True
            stored = stored[: max(1, len(stored) - max(1, len(stored) // 10))]
        write_result = self.write_file(
            SandboxAgentFileWriteRequest(
                chat_session_id=payload.chat_session_id,
                path=output_path,
                content=stored,
                sandbox_session_id=session.id,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.agent.transcription.completed",
            resource_type="sandbox_session",
            resource_id=session.id,
            details={
                "path": path,
                "output_path": output_path,
                "provider_id": provider.provider_id,
                "request_id": result.request_id,
                "latency_ms": latency_ms,
            },
        )
        self.db.commit()
        inline_limit = 16_000
        return {
            "sandbox_session_id": session.id,
            "path": path,
            "output_path": output_path,
            "language": result.language or payload.language,
            "duration_seconds": result.duration_seconds,
            "provider_id": provider.provider_id,
            "model_id": provider.model_id,
            "transcript_truncated": transcript_truncated,
            "text": stored[:inline_limit],
            "text_truncated_inline": len(stored) > inline_limit,
            "artifact": write_result.get("artifact"),
        }

    def _read_workspace_bytes_from_host(
        self, session: SandboxSession, path: str
    ) -> bytes | None:
        """Read bind-mount bytes host-side with symlink-escape containment.

        This bypasses the container archive size limit for large media while
        never following a sandbox-created symlink outside the managed
        workspace root. It is a legacy Docker-backend path: sandboxd sessions
        have no host mount and must never read through this function.
        """

        if session.backend_id == "sandboxd":
            return None
        if not session.workspace_relative_path:
            return None
        try:
            host_root = _sandbox_workspace_path(
                self.settings, session.workspace_relative_path
            )
        except SandboxBackendError:
            return None
        root_real = Path(os.path.realpath(host_root))
        candidate = Path(os.path.realpath(host_root / path))
        if candidate == root_real or root_real not in candidate.parents:
            self._record_policy_block(
                action="sandbox.agent.transcribe.blocked",
                reason="Workspace media path escaped the managed workspace root",
                path=path,
            )
            return None
        try:
            if not candidate.is_file():
                return None
            if candidate.stat().st_size > self.settings.max_upload_bytes:
                raise AppError(
                    413,
                    "sandbox_file_too_large",
                    "Workspace audio exceeds the upload limit",
                )
            return candidate.read_bytes()
        except OSError:
            return None

    def seed_chat_attachments(
        self,
        *,
        chat_session_id: str,
        files: list[FileRecord],
        include_images: bool = False,
    ) -> list[dict[str, Any]]:
        """Materialize chat attachments into session workspace inputs/.

        Does not require Docker for the durable logical tree. When a backend
        session can be opened, also best-effort write read-only input files
        into the container so list/read tools see them immediately.
        """

        self._require_chat_session(chat_session_id)
        seeded: list[dict[str, Any]] = []
        handle = None
        session = None
        for file in files:
            if file.storage_status != "stored":
                continue
            if not include_images and self._is_image_like(file):
                # Multimodal images stay on the structured chat path; do not
                # force them into the code workspace unless explicitly
                # requested (e.g. read_session_file target='workspace').
                continue
            view = self.workspace_files.link_file_record(
                chat_session_id=chat_session_id,
                file=file,
                role="input",
                source="chat_attachment",
            )
            seeded.append(view)
            if self._is_video_like(file):
                # Video is retained as a persistent storage reference. Copying a
                # multi-gigabyte input into the Docker bind mount is opt-in for
                # a future materialization tool, not a side effect of sending.
                continue
            # Best-effort Docker dual-write of small/non-video input bytes.
            try:
                if handle is None:
                    session = self._resolve_session(None, chat_session_id)
                    handle = self._ensure_backend_session(session)
                data = self.workspace_files.materialize_bytes(
                    chat_session_id, str(view["path"])
                )
                container_path = (
                    self._container_path(chat_session_id, str(view["path"]))
                    if session is not None and self._pooling_enabled(session)
                    else str(view["path"])
                )
                # backend.write uses mode 0o444 — appropriate for inputs/.
                self._runtime_backend(session).write(
                    handle, container_path, data
                )
                if session is not None:
                    view_path = str(view["path"])
                    # Keep entry sandbox_session_id current when container is live.
                    entry = self.workspace_files.get_entry(chat_session_id, view_path)
                    entry.sandbox_session_id = session.id
                    entry.updated_at = utc_now()
            except (SandboxBackendUnavailable, SandboxBackendError, AppError, KeyError):
                pass
        if session is not None:
            self._touch_session(session)
        return seeded

    @staticmethod
    def _is_image_like(file: FileRecord) -> bool:
        return file.mime_type.casefold().split(";", 1)[0].strip().startswith("image/")

    @staticmethod
    def _is_video_like(file: FileRecord) -> bool:
        return file.mime_type.casefold().split(";", 1)[0].strip().startswith("video/")

    @staticmethod
    def agent_tool_definitions() -> list[dict[str, Any]]:
        """OpenAI-compatible function schemas for the Chat agent dispatcher.

        ``chat_session_id`` is server supplied by ``execute_agent_tool`` and
        cannot be selected by the model.  Tool callers should persist and pass
        back the returned ``sandbox_session_id`` to maintain one workspace.
        """

        session_property = {
            "type": "string",
            "description": (
                "OMIT this field on the first call: the workspace session is created or "
                "reused automatically and every sandbox tool result returns its "
                "sandbox_session_id. On later calls pass back exactly that returned ID. "
                "Never send an empty string or an invented value such as 'new'."
            ),
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": "sandbox_env_info",
                    "description": "Return the declared safe sandbox runtime, browser path, installed toolchain and current resource limits. This does not execute an arbitrary command.",
                    "parameters": {
                        "type": "object",
                        "properties": {"sandbox_session_id": session_property},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_video_info",
                    "description": "Inspect a video registered under inputs/ without loading its bytes into the model context. Use this first for duration, streams, resolution, codecs, and size before deciding whether ffmpeg extraction or a semantic video-analysis tool is needed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Registered inputs/ video path."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_write_file",
                    "description": "Write UTF-8 source or data into the isolated Agent workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative workspace path."},
                            "content": {"type": "string", "description": "Complete UTF-8 file content. Use sandbox_append_file for later chunks and sandbox_edit_file for a unique local change."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_append_file",
                    "description": "Append a UTF-8 chunk to a workspace file. Optionally provide the SHA-256 returned by sandbox_read_file to detect concurrent changes.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                            "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_edit_file",
                    "description": "Atomically replace one unique UTF-8 string in a workspace file. First read the file and pass its SHA-256 as expected_sha256. Set replace_all=true only when the same change must be applied to every occurrence (capped at 100 replacements).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                            "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring exactly one match (safety cap 100)."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path", "old_string", "new_string", "expected_sha256"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_read_file",
                    "description": "Read a UTF-8 file from the isolated Agent workspace. Pass start_line/end_line to read only a line range (1-based, inclusive) and max_chars to bound the returned text; the response reports total_lines/total_bytes so you can page through large files without re-reading everything.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "start_line": {"type": "integer", "minimum": 1, "description": "First line to return (1-based)."},
                            "end_line": {"type": "integer", "minimum": 1, "description": "Last line to return (inclusive); values beyond the file are clamped."},
                            "max_chars": {"type": "integer", "minimum": 1, "maximum": 1048576, "description": "Optional character cap on the returned content (truncated=true when applied)."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_list_files",
                    "description": "List regular files in the isolated Agent workspace. Pass pattern (glob, e.g. work/**/*.py) to filter and max_results to bound the response.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Optional glob filter over workspace paths."},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Optional result cap (default 200)."},
                            "sandbox_session_id": session_property,
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_grep",
                    "description": "Search workspace file contents with a regular expression and return matching lines with optional context. Use this instead of sandbox_exec for locating symbols, error strings, or patterns before editing — it runs host-side without starting a container command. Searches the durable session workspace (chat attachments and files written by sandbox tools); files created by sandbox_exec inside the container are not indexed — read those files first or search them inside the script.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Regular expression to search for."},
                            "path": {"type": "string", "description": "Optional glob filter over workspace paths (e.g. work/**/*.py)."},
                            "case_sensitive": {"type": "boolean", "description": "Match case-sensitively (default false)."},
                            "context_lines": {"type": "integer", "minimum": 0, "maximum": 5, "description": "Lines of context before/after each match (default 0)."},
                            "max_matches": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum matching lines to return (default 50; truncated=true when the cap is hit)."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["pattern"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_delete_file",
                    "description": "Permanently delete ONE regular file under the session work/ tree (e.g. work/tmp.txt). Raises sandbox_auth_required first and the chat UI asks the user to authorize this single-use delete before it proceeds. Cannot delete inputs/, outputs/, or host files; for directories or batch deletion use sandbox_exec (same authorization flow).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative workspace path under work/."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_exec",
                    "description": (
                        "Run a workspace Python (.py) or Node (.js/.mjs/.cjs) file in the isolated, "
                        "offline sandbox (argv is never evaluated by a shell; pip/npm install and "
                        "internet access fail by design). Prefer the dedicated sandbox_write_file / "
                        "sandbox_edit_file / sandbox_read_file / sandbox_grep / sandbox_list_files "
                        "tools for single-file operations — sandbox_exec is for scripts that need "
                        "Chromium, ffmpeg, the Python/Node toolchain, or batch/multi-file work. "
                        "Installed capabilities are summarized by sandbox_env_info; the "
                        "learngraph_tasks library and file-workflow guidance live in the sandbox_files "
                        "skill. Host-path deletes are blocked; session work/ deletes are allowed directly "
                        "(approval-free)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "argv": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": 'For example: ["python", "main.py"] or ["node", "main.js"].',
                            },
                            "cwd": {"type": "string", "enum": ["."], "default": "."},
                            "runtime": {
                                "type": "string",
                                "enum": ["python-node", "python-node-browser"],
                                "default": "python-node",
                                "description": (
                                    "Deprecated: both values run the same unified image; "
                                    "browser, ffmpeg and the toolchain are always available."
                                ),
                            },
                            "sandbox_session_id": session_property,
                        },
                        "required": ["argv"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_get_job",
                    "description": (
                        "Query the status of a previously submitted sandbox job "
                        "(returned as job_id by sandbox_exec when the execution pool "
                        "queues the command because capacity is busy). Status values: "
                        "QUEUED / STARTING / RUNNING / SUCCEEDED / FAILED / CANCELLED / "
                        "EXPIRED. When queued, wait before retrying instead of "
                        "re-submitting the command."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "job_id": {
                                "type": "string",
                                "description": "The job_id returned by sandbox_exec.",
                            },
                        },
                        "required": ["job_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_cancel_job",
                    "description": (
                        "Cancel a queued or running sandbox job by job_id. Queued jobs "
                        "are cancelled immediately; running jobs are terminated after "
                        "the execution supervisor stops the process tree."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "job_id": {
                                "type": "string",
                                "description": "The job_id returned by sandbox_exec.",
                            },
                        },
                        "required": ["job_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_validate_web_app",
                    "description": (
                        "Validate a multi-file HTML/React/Vue teaching app after a successful build. "
                        "Use output_root (usually dist) and entry_path (usually dist/index.html) before "
                        "publishing. The server checks safe paths, relative assets, MIME/size limits and "
                        "returns a validation_id required by sandbox_publish_web_app."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "output_root": {"type": "string"},
                            "entry_path": {"type": "string"},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["output_root", "entry_path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_validate_interaction_contract",
                    "description": (
                        "Validate a bidirectional subapp interaction contract stored as a JSON file "
                        "in the sandbox workspace (convention: lerarngraph.subapp.json). The contract "
                        "shape is {event_schema, state_schema, agent_triggers?, analytics?}. "
                        "Prefer this over inlining the contract into sandbox_publish_web_app: it "
                        "returns precise JSON Pointer errors and a stable checksum, and it cannot be "
                        "truncated by tool-argument JSON escaping. event_schema/state_schema must be "
                        "closed object schemas (top-level additionalProperties:false, depth<=16, "
                        "nodes<=1000, <=64KiB) without executable content, external $ref, or "
                        "caller-supplied patterns; event_schema must NOT include a 'type' field "
                        "(the host routes event type separately)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Workspace-relative path to the contract JSON file, e.g. work/app/learngraph.subapp.json",
                            },
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_publish_web_app",
                    "description": (
                        "Publish a validated multi-file teaching app as a durable interactive subapp. "
                        "Requires a successful validation_id from sandbox_validate_web_app; do not use "
                        "this for ordinary downloadable files. "
                        "Pass an optional interaction_contract to make the app a bidirectional "
                        "sub-application: the user interacts in an isolated iframe, their actions "
                        "reach you via subapp_observe, and you update the UI via subapp_patch_state. "
                        "When interaction_contract is omitted the app is published as a static "
                        "preview bundle (single-file downloads go through sandbox_publish_file)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "validation_id": {"type": "string"},
                            "title": {"type": "string"},
                            "contract_path": {
                                "type": "string",
                                "description": (
                                    "Optional workspace-relative path to a validated "
                                    "lerarngraph.subapp.json contract (run "
                                    "sandbox_validate_interaction_contract first). "
                                    "Pass either contract_path or interaction_contract, "
                                    "never both."
                                ),
                            },
                            "preferred_height": {"type": "integer", "minimum": 160, "maximum": 900},
                            "interaction_contract": {
                                "type": "object",
                                "description": (
                                    "Optional bidirectional contract. Shape: "
                                    "{event_schema: <JSON Schema>, state_schema: <JSON Schema>, "
                                    "agent_triggers: [{event_type, mode: 'explicit'}], "
                                    "analytics: {enabled, track, summary_events, privacy}}. "
                                    "event_schema describes user actions emitted by the app (e.g. "
                                    "{question_id, selected}) — do NOT include a 'type' field, the "
                                    "host routes the event type separately. state_schema describes "
                                    "the complete state you write via subapp_patch_state (e.g. "
                                    "{view, answers}). agent_triggers lists the explicit event "
                                    "types that may invoke the Agent (only 'explicit' mode, never "
                                    "high-frequency telemetry). analytics.enabled turns on generic "
                                    "behavior capture; track lists semantic event names to capture "
                                    "in full, summary_events lists high-frequency events to "
                                    "aggregate, privacy is session|workspace|none. Both schemas "
                                    "must be closed object schemas (top-level additionalProperties:"
                                    "false, depth<=16, nodes<=1000, <=64KiB) and must not declare "
                                    "executable content, callbacks, external $ref, or "
                                    "caller-supplied patterns."
                                ),
                                "properties": {
                                    "event_schema": {"type": "object"},
                                    "state_schema": {"type": "object"},
                                    "agent_triggers": {
                                        "type": "array",
                                        "maxItems": 16,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "event_type": {
                                                    "type": "string",
                                                    "pattern": "^[a-z][a-z0-9_.-]{0,119}$",
                                                },
                                                "mode": {
                                                    "type": "string",
                                                    "enum": ["explicit"],
                                                },
                                            },
                                            "required": ["event_type"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    "analytics": {
                                        "type": "object",
                                        "properties": {
                                            "enabled": {"type": "boolean"},
                                            "track": {
                                                "type": "array",
                                                "maxItems": 64,
                                                "items": {
                                                    "type": "string",
                                                    "pattern": "^[a-z][a-z0-9_.-]{0,119}$",
                                                },
                                            },
                                            "summary_events": {
                                                "type": "array",
                                                "maxItems": 16,
                                                "items": {
                                                    "type": "string",
                                                    "pattern": "^[a-z][a-z0-9_.-]{0,119}$",
                                                },
                                            },
                                            "privacy": {
                                                "type": "string",
                                                "enum": ["session", "workspace", "none"],
                                            },
                                        },
                                        "additionalProperties": False,
                                    },
                                },
                                "required": ["event_schema", "state_schema"],
                                "additionalProperties": False,
                            },
                            "sandbox_session_id": session_property,
                        },
                        "required": ["validation_id", "title"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_transcribe_audio",
                    "description": (
                        "Transcribe a workspace audio file with the user's configured ASR "
                        "Provider. The call runs on the host (the sandbox itself stays "
                        "offline and never sees credentials); the transcript is written back "
                        "into the workspace and returned. For exotic formats, first run "
                        "learngraph_tasks.audio_transcode in the sandbox (e.g. to 16 kHz mono mp3)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative workspace audio path, e.g. inputs/lecture.mp3.",
                            },
                            "output_path": {
                                "type": "string",
                                "description": "Optional transcript path; defaults to work/transcripts/<name>.txt.",
                            },
                            "language": {
                                "type": "string",
                                "description": "Optional language hint such as zh or en.",
                            },
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_publish_image",
                    "description": "Validate and publish a PNG, JPEG, WebP, or GIF already generated in the sandbox. The result is a downloadable, previewable chat artifact.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "title": {"type": "string"},
                            "alt": {"type": "string"},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_publish_file",
                    "description": (
                        "Promote a UTF-8 workspace file into session outputs/, register it in the "
                        "unified file zone, and return a downloadable sandbox_artifact for the chat UI."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative workspace path to publish (copied under outputs/).",
                            },
                            "content": {
                                "type": "string",
                                "description": "Optional UTF-8 content; when omitted the path must already exist in the session workspace store.",
                            },
                            "title": {"type": "string"},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            # ── Sandbox toolkit (bash / todo / patch / git / search / fetch /
            #    subagent / skills / notebook) ──────────────────────────────
            {
                "type": "function",
                "function": {
                    "name": "sandbox_bash",
                    "description": (
                        "Run an arbitrary shell command string inside the sandbox container via "
                        "bash -lc (the string is one argv element; no host shell is involved). "
                        "Use this for toolchain work, file inspection, piping, loops and anything "
                        "sandbox_exec's script-file rule cannot express. Destructive commands "
                        "(rm/mv/... on work/ paths) require the same single-use user authorization "
                        "as sandbox_delete_file. The container stays offline: pip/npm install and "
                        "network access fail unless an approved egress policy is active. Interactive "
                        "or long-running foreground processes are blocked by the wall-clock timeout; "
                        "prefer writing a script for multi-line logic."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Shell command string, e.g. 'ls -la work && python main.py'."},
                            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "description": "Optional wall-clock cap (defaults to the sandbox command limit)."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_todo",
                    "description": (
                        "Maintain a session-scoped task checklist (host-side, survives container "
                        "restarts). Actions: add(text), done(item_id), remove(item_id), list, clear. "
                        "Use it to track multi-step plans instead of relying on conversation memory."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["list", "add", "done", "remove", "clear"], "description": "Checklist operation (default list)."},
                            "text": {"type": "string", "description": "New item text for action=add."},
                            "item_id": {"type": "string", "description": "Item id for action=done/remove (returned by add/list)."},
                            "sandbox_session_id": session_property,
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_apply_patch",
                    "description": (
                        "Apply a unified diff (git-diff format) to the durable session workspace "
                        "host-side. Supports file create/modify/delete with fuzzy context matching "
                        "(fuzz=3 by default). File deletions go through the same single-use "
                        "authorization as sandbox_delete_file. Prefer this over many small "
                        "sandbox_edit_file calls when a change spans multiple hunks or files; the "
                        "patch text is returned as the tool result and applied atomically per file."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "patch": {"type": "string", "description": "Unified diff text (--- a/…, +++ b/…, @@ -l,c +l,c @@ hunks)."},
                            "fuzz": {"type": "integer", "minimum": 0, "maximum": 10, "description": "Context fuzz tolerance (default 3)."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["patch"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_git",
                    "description": (
                        "Run local git operations inside the sandbox workspace (offline; the "
                        "container cannot reach the network). argv-style: sandbox_git(args=[\"-C\", \"work/repo\", \"log\", \"--oneline\"]). "
                        "Destructive worktree mutations (git rm/checkout --/restore/mv on work/ "
                        "paths) require single-use user authorization. Network git commands "
                        "(clone/fetch/pull) fail here — use sandbox_git_clone for approved clones."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "args": {"type": "array", "items": {"type": "string"}, "description": "Git subcommand and arguments, e.g. [\"status\"] or [\"-C\", \"work/repo\", \"commit\", \"-am\", \"msg\"]."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["args"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_git_clone",
                    "description": (
                        "Clone a public GitHub repository through the reviewed egress approval "
                        "channel. The network transfer runs host-side (the sandbox container stays "
                        "offline); the approved snapshot is materialized into the workspace and a "
                        "container-side git repository is initialized on it so subsequent "
                        "sandbox_git operations work. The first call raises an authorization card "
                        "the user must approve (single-use or persistent)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string", "description": "GitHub owner/org, e.g. 'anthropics'."},
                            "repo": {"type": "string", "description": "Repository name."},
                            "ref": {"type": "string", "description": "Branch/tag/commit to pin (default HEAD)."},
                            "path": {"type": "string", "description": "Optional subdirectory to clone."},
                            "destination_root": {"type": "string", "description": "Workspace destination, e.g. 'work/git/<repo>'."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["owner", "repo", "destination_root"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_search_web",
                    "description": (
                        "Search the web through the user-authorized SearchProvider. Host-side: the "
                        "sandbox container stays offline and results are limited to authorized "
                        "domains. Use for current information, docs lookups and quick facts."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query (1-500 chars)."},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Result cap (default 6)."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_fetch",
                    "description": (
                        "Read the full content of a public URL through the reviewed fetch "
                        "authorization channel (host-side; the sandbox container stays offline). "
                        "The first request to a new host raises an authorization card; approved "
                        "domains are reused afterwards. Returns the extracted page text."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "format": "uri", "description": "Public https URL to fetch."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_subagent",
                    "description": (
                        "Spawn a nested sandbox sub-agent: it runs its own agent loop in the "
                        "background with a restricted offline tool subset (file tools, bash, exec, "
                        "todo, patch, git) and returns a subagent_id. Poll with sandbox_subagent_status. "
                        "Status outcomes: completed (final answer delivered), partial (round/tool budget "
                        "ran out but files may exist - inspect the workspace), failed, timed_out, "
                        "cancelled. Pass write_set (writable path prefixes) to keep the sub-agent out of "
                        "shared directories; file writes outside write_set are rejected. Use it to "
                        "parallelize independent research/implementation work inside the same workspace."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Self-contained task description for the sub-agent."},
                            "tools": {"type": "array", "items": {"type": "string"}, "description": "Optional sandbox tool-name subset; defaults to the offline tool set."},
                            "max_rounds": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Tool round cap (default 6)."},
                            "max_tool_calls": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Optional cap on total tool executions."},
                            "write_set": {"type": "array", "items": {"type": "string"}, "description": "Optional writable workspace path prefixes, e.g. [\"work/subagents/task_a\"]. File writes outside these prefixes are rejected."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["prompt"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_subagent_status",
                    "description": "Poll the status and final result of a sandbox_subagent job (status: completed/partial/failed/timed_out/cancelled/queued/running). Only completed means a final answer was delivered; partial means the budget ran out but the sub-agent may have left files behind.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subagent_id": {"type": "string", "description": "The subagent_id returned by sandbox_subagent."},
                        },
                        "required": ["subagent_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_subagent_wait",
                    "description": "Wait for one or more sub-agent tasks to reach a terminal state (completed/partial/failed/timed_out/cancelled) or a timeout. mode=all waits for every task; mode=any returns as soon as one changes. Pass after_event_seq from a previous status/wait call to get incremental events. Prefer this over repeated sandbox_subagent_status polling.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subagent_ids": {"type": "array", "items": {"type": "string"}, "description": "subagent_id values returned by sandbox_subagent."},
                            "mode": {"type": "string", "enum": ["any", "all"], "description": "any: return on first change; all: wait for every task (default all)."},
                            "timeout_ms": {"type": "integer", "minimum": 1000, "maximum": 60000, "description": "Max wait in ms (default 30000)."},
                            "after_event_seq": {"type": "integer", "minimum": 0, "description": "Return only events after this seq."},
                        },
                        "required": ["subagent_ids"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_subagent_cancel",
                    "description": "Request cancellation of a running/queued sub-agent task. The task only reports cancelled after the worker confirms; while cancelling the snapshot may still show running.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subagent_id": {"type": "string", "description": "The subagent_id returned by sandbox_subagent."},
                        },
                        "required": ["subagent_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_subagent_retry",
                    "description": "Re-queue a sub-agent task as a new attempt (new job). scope=same keeps the original spec; scope=scoped lets you pass a narrower prompt_override (e.g. after partial/timeout). Old attempts stay in history.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subagent_id": {"type": "string", "description": "The subagent_id returned by sandbox_subagent."},
                            "scope": {"type": "string", "enum": ["same", "scoped"], "description": "same keeps original spec; scoped allows prompt_override."},
                            "prompt_override": {"type": "string", "description": "New narrower prompt for scope=scoped retries."},
                            "note": {"type": "string", "description": "Optional reason for the retry."},
                        },
                        "required": ["subagent_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_skill_list",
                    "description": "List the official LearnGraph skills (key, category, description) available for sandbox workflows.",
                    "parameters": {
                        "type": "object",
                        "properties": {"sandbox_session_id": session_property},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_skill_read",
                    "description": "Read the full SKILL.md instructions of an official skill (returned key from sandbox_skill_list).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_key": {"type": "string", "description": "Official skill key, e.g. 'sandbox-files'."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["skill_key"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "sandbox_notebook",
                    "description": (
                        "Persistent in-container Python REPL kernel. action=open starts a kernel "
                        "(returns kernel_id); action=execute(kernel_id, code) runs a cell and keeps "
                        "state across calls; action=close(kernel_id) tears it down. Use it instead of "
                        "repeated sandbox_exec when you need live interpreter state (imports, "
                        "variables) across steps."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["open", "execute", "close", "status"], "description": "Kernel operation (default open)."},
                            "kernel_id": {"type": "string", "description": "Kernel id returned by action=open; required for execute/close/status."},
                            "code": {"type": "string", "description": "Python cell source for action=execute."},
                            "interpreter": {"type": "string", "enum": ["python"], "description": "Kernel interpreter (python only in v1)."},
                            "sandbox_session_id": session_property,
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    _AGENT_SESSION_ID_PLACEHOLDERS = frozenset(
        {"", "new", "auto", "none", "null", "default", "create", "latest", "current"}
    )

    def _normalize_agent_session_id(self, raw: Any, chat_session_id: str) -> str | None:
        """Agent-path leniency: models often send "" or "new" instead of omitting
        the field, or replay a stale/foreign session ID.  Anything that is not a
        live session of this chat resolves to ``None`` so ``_resolve_session``
        silently reuses or creates the chat workspace session instead of failing
        the tool call."""

        if not isinstance(raw, str):
            return None
        candidate = raw.strip()
        if candidate.casefold() in self._AGENT_SESSION_ID_PLACEHOLDERS:
            return None
        try:
            session = self._get_session(candidate)
        except AppError:
            return None
        if session.chat_session_id != chat_session_id:
            return None
        if session.cleanup_status == "cleaned" or session.status in {"deleted", "stopped"}:
            return None
        if not self._not_expired(session):
            return None
        return candidate

    def execute_agent_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
        agent_authorized: bool,
    ) -> dict[str, Any]:
        """Execute one allow-listed tool for a caller-authorized Chat Agent.

        This method is intentionally synchronous like the existing Agent tool
        dispatcher.  It raises ``AppError`` for malformed/denied calls so the
        dispatcher can serialize the same stable error shape it uses for other
        LearnGraph tools.
        """

        if not agent_authorized:
            self._record_policy_block(
                action="sandbox.agent.tool.denied",
                reason="workspace.manage permission is required for Agent sandbox tools",
            )
            raise AppError(
                403,
                "sandbox_agent_permission_denied",
                "Workspace management permission is required for Agent sandbox tools",
            )
        if not self.settings.sandbox_agent_enabled:
            raise AppError(
                503,
                "sandbox_agent_disabled",
                "Agent sandbox execution is disabled by deployment configuration",
            )
        if not isinstance(arguments, dict):
            raise AppError(422, "invalid_tool_arguments", "Sandbox Agent tool arguments must be an object")
        payload = {**arguments, "chat_session_id": chat_session_id}
        if "sandbox_session_id" in payload:
            normalized = self._normalize_agent_session_id(
                payload.get("sandbox_session_id"), chat_session_id
            )
            if normalized is None:
                payload.pop("sandbox_session_id")
            else:
                payload["sandbox_session_id"] = normalized
        try:
            if name == "sandbox_env_info":
                return self.environment_info(SandboxAgentEnvironmentRequest.model_validate(payload))
            if name == "sandbox_write_file":
                return self.write_file(SandboxAgentFileWriteRequest.model_validate(payload))
            if name == "sandbox_append_file":
                return self.append_file(SandboxAgentFileAppendRequest.model_validate(payload))
            if name == "sandbox_edit_file":
                return self.edit_file(SandboxAgentFileEditRequest.model_validate(payload))
            if name == "sandbox_read_file":
                return self.read_file(SandboxAgentFileReadRequest.model_validate(payload))
            if name == "sandbox_list_files":
                return self.list_files(SandboxAgentFileListRequest.model_validate(payload))
            if name == "sandbox_grep":
                return self.grep_files(SandboxAgentFileGrepRequest.model_validate(payload))
            if name == "sandbox_delete_file":
                return self.delete_file(SandboxAgentFileDeleteRequest.model_validate(payload))
            if name == "sandbox_exec":
                from app.services.sandbox_scheduler import CAPACITY_CODES, SandboxSchedulerService

                request = SandboxAgentCommandRequest.model_validate(payload)
                try:
                    command = self.execute_command(request, idempotency_key=None)
                except AppError as exc:
                    if exc.code not in CAPACITY_CODES:
                        raise
                    # Capacity shortage → queue the command instead of failing
                    # the Agent. The unified scheduler resumes it when capacity
                    # frees up; the caller follows job state via sandbox_get_job.
                    scheduler = SandboxSchedulerService(self.db, self.settings)
                    job = scheduler.submit_job(
                        workspace_id=self.workspace_id,
                        owner_user_id=self.actor_id,
                        chat_session_id=request.chat_session_id,
                        kind="agent_command",
                        payload=request.model_dump(mode="json"),
                        workload_class=_workload_class_for_argv(request.argv),
                        idempotency_key=None,
                    )
                    self.db.refresh(job)
                    return {
                        "id": job.id,
                        "job_id": job.id,
                        "sandbox_session_id": None,
                        "status": "queued",
                        "reason": job.reason or "waiting_capacity",
                        "exit_code": None,
                        "error_class": None,
                        "stdout": "",
                        "stderr": "",
                        "timed_out": False,
                        "truncated": False,
                        "summary": {
                            "type": "sandbox_status",
                            "status": "queued",
                            "data": {
                                "phase": "queued",
                                "job_id": job.id,
                                "reason": job.reason or "waiting_capacity",
                                "argv_redacted": _redacted_agent_argv(request.argv),
                                "chat_session_id": request.chat_session_id,
                            },
                        },
                    }
                return {
                    "id": command.id,
                    "job_id": getattr(command, "job_id", None),
                    "sandbox_session_id": command.sandbox_session_id,
                    "status": command.status,
                    "exit_code": command.exit_code,
                    "error_class": command.error_class,
                    "stdout": command.stdout_summary,
                    "stderr": command.stderr_summary,
                    "timed_out": command.timed_out,
                    "truncated": command.truncated,
                    "summary": {
                        "type": "sandbox_status",
                        "status": command.status,
                        "data": {
                            "phase": "completed" if command.status == "completed" else "failed",
                            "argv_redacted": command.argv_redacted,
                            "exit_code": command.exit_code,
                            "latency_ms": command.latency_ms,
                            "stdout_summary": (command.stdout_summary or "")[:400],
                            "stderr_summary": (command.stderr_summary or "")[:400],
                            "sandbox_session_id": command.sandbox_session_id,
                            "chat_session_id": command.chat_session_id,
                        },
                    },
                }
            if name == "sandbox_get_job":
                from app.services.sandbox_scheduler import SandboxSchedulerService

                job_id = payload.get("job_id")
                if not isinstance(job_id, str) or not job_id:
                    raise AppError(
                        422, "invalid_tool_arguments", "sandbox_get_job requires a job_id"
                    )
                scheduler = SandboxSchedulerService(self.db, self.settings)
                job = scheduler.get_job(
                    job_id,
                    workspace_id=self.workspace_id,
                    owner_user_id=self.actor_id,
                )
                return {
                    "job_id": job.id,
                    "status": job.status,
                    "reason": job.reason,
                    "kind": job.kind,
                    "attempt": job.attempt,
                    "created_at": job.created_at.isoformat() if job.created_at else None,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                    "error_class": job.error_class,
                    "error_message": job.error_message,
                    "summary": {
                        "type": "sandbox_status",
                        "status": job.status,
                        "data": {
                            "phase": job.status.lower(),
                            "job_id": job.id,
                            "reason": job.reason,
                        },
                    },
                }
            if name == "sandbox_cancel_job":
                from app.services.sandbox_scheduler import SandboxSchedulerService

                job_id = payload.get("job_id")
                if not isinstance(job_id, str) or not job_id:
                    raise AppError(
                        422, "invalid_tool_arguments", "sandbox_cancel_job requires a job_id"
                    )
                scheduler = SandboxSchedulerService(self.db, self.settings)
                job = scheduler.get_job(
                    job_id,
                    workspace_id=self.workspace_id,
                    owner_user_id=self.actor_id,
                )
                job = scheduler.cancel_job(job)
                return {
                    "job_id": job.id,
                    "status": job.status,
                    "reason": job.reason,
                    "summary": {
                        "type": "sandbox_status",
                        "status": job.status,
                        "data": {
                            "phase": job.status.lower(),
                            "job_id": job.id,
                            "reason": job.reason,
                        },
                    },
                }
            if name == "sandbox_validate_interaction_contract":
                from app.services.subapp_bundles import SubAppBundleService

                return SubAppBundleService(
                    self.db, self.workspace_id, self.actor_id, self.settings
                ).validate_interaction_contract(
                    chat_session_id=chat_session_id,
                    path=str(payload.get("path") or ""),
                )
            if name == "sandbox_validate_web_app":
                from app.services.subapp_bundles import SubAppBundleService

                return SubAppBundleService(
                    self.db, self.workspace_id, self.actor_id, self.settings
                ).validate(
                    chat_session_id=chat_session_id,
                    sandbox_session_id=payload.get("sandbox_session_id") if isinstance(payload.get("sandbox_session_id"), str) else None,
                    output_root=str(payload.get("output_root") or "dist"),
                    entry_path=str(payload.get("entry_path") or "dist/index.html"),
                )
            if name == "sandbox_publish_web_app":
                from app.services.subapp_bundles import SubAppBundleService

                return SubAppBundleService(
                    self.db, self.workspace_id, self.actor_id, self.settings
                ).publish(
                    validation_id=str(payload.get("validation_id") or ""),
                    chat_session_id=chat_session_id,
                    sandbox_session_id=payload.get("sandbox_session_id") if isinstance(payload.get("sandbox_session_id"), str) else None,
                    title=str(payload.get("title") or "交互式教学应用"),
                    preferred_height=payload.get("preferred_height") if isinstance(payload.get("preferred_height"), int) else None,
                    interaction_contract=payload.get("interaction_contract") if isinstance(payload.get("interaction_contract"), dict) else None,
                    contract_path=payload.get("contract_path") if isinstance(payload.get("contract_path"), str) else None,
                )
            if name == "sandbox_video_info":
                return self.video_info(SandboxAgentVideoInfoRequest.model_validate(payload))
            if name == "sandbox_transcribe_audio":
                return self.transcribe_workspace_audio(
                    SandboxAgentTranscribeRequest.model_validate(payload)
                )
            if name == "sandbox_publish_image":
                return self.publish_image(SandboxAgentImagePublishRequest.model_validate(payload))
            if name == "sandbox_publish_file":
                return self.publish_workspace_file(
                    chat_session_id=chat_session_id,
                    path=str(payload.get("path") or ""),
                    content=payload.get("content") if isinstance(payload.get("content"), str) else None,
                    title=payload.get("title") if isinstance(payload.get("title"), str) else None,
                    sandbox_session_id=payload.get("sandbox_session_id")
                    if isinstance(payload.get("sandbox_session_id"), str)
                    else None,
                )
            # ── Sandbox toolkit (bash / todo / patch / git / search / fetch /
            #    subagent / skills / notebook) ──────────────────────────────
            if name == "sandbox_bash":
                return self.toolkit_bash(SandboxAgentBashRequest.model_validate(payload))
            if name == "sandbox_todo":
                return self.toolkit_todo(SandboxAgentTodoRequest.model_validate(payload))
            if name == "sandbox_apply_patch":
                return self.toolkit_apply_patch(SandboxAgentPatchRequest.model_validate(payload))
            if name == "sandbox_git":
                return self.toolkit_git(SandboxAgentGitRequest.model_validate(payload))
            if name == "sandbox_git_clone":
                return self.toolkit_git_clone(SandboxAgentGitCloneRequest.model_validate(payload))
            if name == "sandbox_search_web":
                return self.toolkit_search_web(SandboxAgentSearchRequest.model_validate(payload))
            if name == "sandbox_fetch":
                return self.toolkit_fetch(SandboxAgentFetchRequest.model_validate(payload))
            if name == "sandbox_subagent":
                return self.toolkit_subagent(SandboxAgentSubagentRequest.model_validate(payload))
            if name == "sandbox_subagent_status":
                return self.toolkit_subagent_status(SandboxAgentSubagentStatusRequest.model_validate(payload))
            if name == "sandbox_subagent_wait":
                from app.domain.schemas.sandbox import SandboxAgentSubagentWaitRequest
                return self.toolkit_subagent_wait(SandboxAgentSubagentWaitRequest.model_validate(payload))
            if name == "sandbox_subagent_cancel":
                from app.domain.schemas.sandbox import SandboxAgentSubagentCancelRequest
                return self.toolkit_subagent_cancel(SandboxAgentSubagentCancelRequest.model_validate(payload))
            if name == "sandbox_subagent_retry":
                from app.domain.schemas.sandbox import SandboxAgentSubagentRetryRequest
                return self.toolkit_subagent_retry(SandboxAgentSubagentRetryRequest.model_validate(payload))
            if name == "sandbox_skill_list":
                return self.toolkit_skill_list(SandboxAgentSkillListRequest.model_validate(payload))
            if name == "sandbox_skill_read":
                return self.toolkit_skill_read(SandboxAgentSkillReadRequest.model_validate(payload))
            if name == "sandbox_notebook":
                return self.toolkit_notebook(SandboxAgentNotebookRequest.model_validate(payload))
        except ValidationError as exc:
            issues = [
                {
                    "field": ".".join(str(part) for part in error.get("loc", ())) or "arguments",
                    "problem": str(error.get("msg") or "invalid value"),
                }
                for error in exc.errors()[:5]
            ]
            summary = "; ".join(f"{issue['field']}: {issue['problem']}" for issue in issues)
            raise AppError(
                422,
                "invalid_tool_arguments",
                f"Sandbox Agent tool arguments are invalid — {summary}"[:500],
                {
                    "tool": name,
                    "issues": issues,
                    "hint": (
                        "Use the exact argument names from the tool schema (English keys such as "
                        "path, content, argv). Omit sandbox_session_id entirely to reuse or create "
                        "this chat's workspace session automatically; when continuing, pass back "
                        "the exact sandbox_session_id returned by the previous sandbox tool result."
                    ),
                },
            ) from exc
        raise AppError(404, "sandbox_agent_tool_not_found", "Sandbox Agent tool is not registered")

    def materialize_shared_skill(
        self,
        chat_session_id: str,
        skill_key: str,
        files: list[tuple[str, bytes]],
        *,
        readme: str | None = None,
    ) -> str:
        """Write a skill package into the user's shared capability area.

        The shared area lives at ``shared/skills/{skill_key}`` inside the user's
        instance volume — materialized ONCE per user instead of being copied
        into every chat workspace (design doc §10). Only text files reach the
        container; the sandbox stays offline and secret-free. Returns the
        container-relative path (relative to /workspace).
        """
        sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", skill_key or "skill")[:60].strip("._")
        if not sanitized:
            sanitized = "skill"
        session = self._resolve_session(None, chat_session_id)
        handle = self._ensure_backend_session(session)
        backend = self._runtime_backend(session)
        prefix = f"shared/skills/{sanitized}"
        written = 0
        for rel, data in files:
            safe_rel = validate_agent_workspace_path(rel)
            backend.write_agent_file(handle, f"{prefix}/{safe_rel}", data)
            written += 1
        if readme:
            backend.write_agent_file(handle, f"{prefix}/README.md", readme.encode("utf-8"))
        logger.info(
            "materialized shared skill %s (%d files) for chat %s",
            prefix,
            written,
            chat_session_id,
        )
        return prefix

    def publish_workspace_file(
        self,
        *,
        chat_session_id: str,
        path: str,
        content: str | None,
        title: str | None,
        sandbox_session_id: str | None,
    ) -> dict[str, Any]:
        self._require_chat_session(chat_session_id)
        session = self._resolve_session(sandbox_session_id, chat_session_id)
        if content is not None:
            data = content.encode("utf-8")
            if len(data) > self.settings.sandbox_agent_file_bytes:
                raise AppError(422, "sandbox_file_too_large", "Sandbox Agent file exceeds the configured byte limit")
            # Best-effort container dual-write; unified file zone does not require Docker.
            try:
                handle = self._ensure_backend_session(session)
                container_path = (
                    self._container_path(chat_session_id, path)
                    if self._pooling_enabled(session)
                    else path
                )
                self._runtime_backend(session).write_agent_file(handle, container_path, data)
            except (SandboxBackendUnavailable, SandboxBackendError):
                pass
        else:
            try:
                data = self.workspace_files.materialize_bytes(chat_session_id, path)
            except AppError:
                try:
                    handle = self._ensure_backend_session(session)
                    container_path = (
                        self._container_path(chat_session_id, path)
                        if self._pooling_enabled(session)
                        else path
                    )
                    data = self._runtime_backend(session).read(
                        handle, container_path, self.settings.sandbox_agent_file_bytes
                    )
                except (SandboxBackendUnavailable, SandboxBackendError) as exc:
                    raise AppError(422, "sandbox_file_unavailable", "Sandbox Agent file cannot be read") from exc
        published = self.workspace_files.publish_path(
            chat_session_id=chat_session_id,
            path=path if path.startswith("outputs/") else f"outputs/{path.rsplit('/', 1)[-1]}",
            data=data,
            sandbox_session_id=session.id,
            title=title,
        )
        session.status = "ready"
        self._touch_session(session)
        session.lifecycle_state = "WARM_IDLE"
        self.db.commit()
        return published

    def list_workspace_entries(self, chat_session_id: str) -> list[dict[str, Any]]:
        self._require_chat_session(chat_session_id)
        return self.workspace_files.list_views(chat_session_id)
