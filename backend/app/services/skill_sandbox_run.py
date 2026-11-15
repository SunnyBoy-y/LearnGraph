"""Skill package script trial runs — Docker sandbox only (D-080)."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.extension_models import ExtensionInvocation, SkillPackageFile, SkillRecord
from app.domain.models import ChatSession, Workspace, new_id, utc_now
from app.domain.schemas.extensions import SkillSandboxRunRequest, SkillSandboxRunResponse
from app.domain.schemas.sandbox import (
    SandboxAgentCommandRequest,
    SandboxAgentFileWriteRequest,
    SandboxAgentSessionCreateRequest,
)
from app.repositories.audit import AuditRepository
from app.repositories.extensions import ExtensionInvocationRepository, SkillRepository
from app.services.sandbox import SandboxAgentWorkspaceService
from app.services.session_workspace import BlobStore
from app.services.skill_package import MAX_SKILL_FILE_BYTES, MAX_SKILL_PACKAGE_BYTES, normalize_skill_relative_path


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _is_utf8(body: bytes) -> bool:
    try:
        body.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _skill_readme(skill: Any, script_paths: list[str]) -> str:
    """Container README for a shared skill package (usage + scope limits).

    The sandbox stays offline and secret-free; scripts can only read files
    inside the shared skill directory and the user's workspace volume.
    """
    scripts = "".join(f"- `{path}`\n" for path in script_paths if path.startswith("scripts/"))
    return (
        f"# {skill.name or skill.skill_key}\n\n"
        f"Skill key: `{skill.skill_key}`\n\n"
        "## 脚本（scripts/）\n"
        f"{scripts or '- 无 scripts/'}\n"
        "## 用法\n"
        "在沙箱中运行（沙箱内离线，无网络、无凭据）：\n\n"
        "```bash\n"
        "# 查看本文件\n"
        "cat README.md\n"
        "# 运行脚本（示例）\n"
        "python scripts/xxx.py --help\n"
        "```\n"
        "## 范围限制\n"
        "- 沙箱默认离线：`pip install`、`npm install`、网络请求会失败。\n"
        "- 脚本只能访问共用区 `shared/skills/` 与当前会话工作区，不能访问宿主文件。\n"
        "- 脚本不得读取或写入密钥、凭据；沙箱不携带任何密钥。\n"
        "- 超出沙箱 wall-time / 输出上限会被任务级终止（不会影响其他任务）。\n"
    )


def materialize_skill_package(agent, chat_session_id: str, sandbox_session_id: str,
                              package_root: str, files: list[tuple[str, bytes]]) -> None:
    """One byte-preserving path for both sandboxd pooling and Docker sessions."""
    from app.providers.remote.sandbox import validate_agent_workspace_path

    agent._require_chat_session(chat_session_id)
    root = validate_agent_workspace_path(package_root)
    session, handle = agent._resolve_file_session(sandbox_session_id, chat_session_id)
    backend = agent._runtime_backend(session)
    for relative_path, data in files:
        path = validate_agent_workspace_path(f"{root}/{relative_path}")
        if len(data) > agent.settings.sandbox_agent_file_bytes:
            raise AppError(413, "sandbox_file_too_large", "Skill package file exceeds sandbox limits")
        container_path = agent._container_path(chat_session_id, path) if agent._pooling_enabled(session) else path
        backend.write_agent_file(handle, container_path, data)
    agent._touch_session(session)


class SkillSandboxRunService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
        *,
        workspace: Workspace | None = None,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.workspace = workspace
        self.principal = principal
        self.skills = SkillRepository(db, workspace_id)
        self.invocations = ExtensionInvocationRepository(db, workspace_id)
        self.blobs = BlobStore(db, workspace_id, settings)
        self.audit = AuditRepository(db, workspace_id)
        self.agent = SandboxAgentWorkspaceService(
            db,
            workspace_id,
            actor_id,
            settings,
            workspace=workspace,
            principal=principal,
        )

    def require_skill(self, skill_id: str) -> SkillRecord:
        return self.skills.require(skill_id, "Skill")

    def refresh_invocation(self, invocation_id: str, *, chat_session_id: str | None = None):
        from app.domain.models import SandboxAgentCommand, SandboxJob
        invocation = self.db.query(ExtensionInvocation).filter_by(
            id=invocation_id, workspace_id=self.workspace_id, target_type="skill_sandbox",
        ).one_or_none()
        if invocation is None:
            raise AppError(404, "skill_run_not_found", "Skill script run was not found")
        result = dict(invocation.result_json or {})
        if self.workspace is not None and self.principal is not None and result.get("chat_session_id"):
            from app.services.authorization import AuthorizationService
            if not AuthorizationService(self.db, self.principal).can_access_resource(self.workspace, "session", result["chat_session_id"], "read"):
                raise AppError(404, "skill_run_not_found", "Skill run was not found")
        if chat_session_id and result.get("chat_session_id") != chat_session_id:
            raise AppError(404, "skill_run_not_found", "Skill run was not found in this chat")
        if invocation.status in {"succeeded", "failed"} and invocation.finished_at is not None:
            return invocation
        job = self.db.get(SandboxJob, result.get("job_id")) if result.get("job_id") else None
        if job is None or job.workspace_id != self.workspace_id:
            return invocation
        command = self.db.query(SandboxAgentCommand).filter_by(workspace_id=self.workspace_id, job_id=job.id).one_or_none()
        if command is not None:
            result.update(command_id=command.id, exit_code=command.exit_code, timed_out=command.timed_out,
                          stdout_summary=command.stdout_summary, stderr_summary=command.stderr_summary)
        terminal = job.status in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}
        invocation.status = ("succeeded" if job.status == "SUCCEEDED" and command is not None and command.exit_code == 0
                             else "failed" if terminal else "running")
        result["cancel_requested"] = bool(job.cancel_requested)
        invocation.result_json = result
        invocation.result_hash = _hash(result)
        invocation.error_code, invocation.error_message = job.error_class, job.error_message
        invocation.finished_at = (job.finished_at or utc_now()) if terminal else None
        invocation.result_size_bytes = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        self.db.commit()
        return invocation

    def _resolve_chat_session_id(self, preferred: str | None) -> str:
        if preferred:
            record = self.db.get(ChatSession, preferred)
            if (
                record is None
                or record.workspace_id != self.workspace_id
            ):
                raise AppError(404, "session_not_found", "Chat session was not found in this workspace")
            return record.id
        # Lightweight ephemeral session for skill trial runs only.
        session = ChatSession(
            workspace_id=self.workspace_id,
            title="Skill sandbox trial",
            status="active",
            session_kind="skill_sandbox_trial",
        )
        self.db.add(session)
        self.db.flush()
        return session.id

    def _script_bytes(self, skill: SkillRecord, script_path: str) -> tuple[str, bytes]:
        path = normalize_skill_relative_path(script_path)
        if not (path == "scripts" or path.startswith("scripts/")):
            raise AppError(
                400,
                "skill_script_path_invalid",
                "Only scripts/ paths may be trial-run in the sandbox",
            )
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix not in {".py", ".js", ".mjs", ".cjs"}:
            raise AppError(
                400,
                "skill_script_type_unsupported",
                "Sandbox trial runs only support .py / .js / .mjs / .cjs scripts",
            )
        row = self.db.query(SkillPackageFile).filter_by(
            workspace_id=self.workspace_id,
            skill_id=skill.id,
            relative_path=path,
            is_directory=False,
        ).one_or_none()
        if row is None:
            raise AppError(404, "skill_file_not_found", "Skill script was not found")
        data = self.blobs.read_bytes(row.blob_sha256, limit_bytes=MAX_SKILL_FILE_BYTES)
        return path, data

    def package_files(self, skill: SkillRecord) -> list[tuple[str, bytes]]:
        files: list[tuple[str, bytes]] = []
        total = 0
        rows = self.db.query(SkillPackageFile).filter_by(
            workspace_id=self.workspace_id, skill_id=skill.id, is_directory=False,
        ).all()
        tree_hash = _hash([{"path": row.relative_path, "sha256": row.blob_sha256, "size": row.size_bytes}
                           for row in sorted(rows, key=lambda row: row.relative_path)])
        if tree_hash != (skill.content_hash or skill.manifest_hash):
            raise AppError(409, "skill_package_version_changed", "Skill package tree no longer matches its authorized version")
        for row in rows:
            path = normalize_skill_relative_path(row.relative_path)
            body = self.blobs.read_bytes(row.blob_sha256, limit_bytes=MAX_SKILL_FILE_BYTES)
            if hashlib.sha256(body).hexdigest() != row.blob_sha256 or len(body) != row.size_bytes:
                raise AppError(409, "skill_package_integrity_failed", "Skill package bytes failed integrity validation")
            total += len(body)
            if total > MAX_SKILL_PACKAGE_BYTES:
                raise AppError(413, "skill_package_too_large", "Skill package exceeds its execution size budget")
            files.append((path, body))
        return files

    def run(self, skill_id: str, payload: SkillSandboxRunRequest, *, require_authorization: bool = False) -> SkillSandboxRunResponse:
        skill = self.require_skill(skill_id)
        self.db.refresh(skill)
        expected_hash = skill.content_hash or skill.manifest_hash
        if require_authorization:
            from app.services.skill_script_runtime import require_authorized_skill
            from app.services.mcp_skills import MCPAndSkillService
            manager = MCPAndSkillService(self.db, self.workspace_id, self.actor_id, self.settings,
                                         workspace=self.workspace, principal=self.principal)
            require_authorized_skill(manager, skill.id)
        if self.workspace is not None and self.principal is not None:
            from app.services.authorization import AuthorizationService
            authz = AuthorizationService(self.db, self.principal)
            if "workspace.manage" not in authz.workspace_permissions(self.workspace):
                raise AppError(403, "permission_denied", "Skill code execution requires workspace.manage")
            if payload.chat_session_id and not authz.can_access_resource(self.workspace, "session", payload.chat_session_id, "write"):
                raise AppError(404, "session_not_found", "Chat session was not found")
        if sum(len(arg.encode("utf-8")) for arg in payload.argv_extra) > 64 * 1024 or any("\x00" in arg for arg in payload.argv_extra):
            raise AppError(422, "skill_argv_invalid", "Skill arguments exceed limits or contain NUL bytes")
        if skill.kind != "agent_skill_package" and skill.package_format != "skill_md_v1":
            raise AppError(400, "skill_not_package", "Only agent_skill_package can run scripts")
        if not skill.has_scripts:
            raise AppError(400, "skill_no_scripts", "This skill package has no scripts/")

        script_path, data = self._script_bytes(skill, payload.script_path)
        suffix = PurePosixPath(script_path).suffix.casefold()
        runner = "python" if suffix == ".py" else "node"
        sandbox_rel = f"skills/{skill.skill_key}/{script_path}"
        argv = [runner, sandbox_rel, *list(payload.argv_extra or [])]
        input_json = {
            "skill_id": skill.id,
            "script_path": script_path,
            "content_hash": skill.content_hash,
            "argv": argv,
        }
        invocation = ExtensionInvocation(
            id=new_id(),
            workspace_id=self.workspace_id,
            target_type="skill_sandbox",
            target_id=skill.id,
            skill_id=skill.id,
            tool_name="skill.sandbox_run",
            status="pending",
            input_json=input_json,
            input_size_bytes=len(json.dumps(input_json)),
            input_hash=_hash(input_json),
            timeout_ms=int((payload.timeout_seconds or getattr(self.settings, "sandbox_wall_time_seconds", 30)) * 1000),
            started_at=utc_now(),
        )
        self.db.add(invocation)
        self.db.flush()

        # Availability gate: never fall back to host subprocess.
        available = True
        unavail_reason = None
        try:
            from app.services.sandbox import SandboxTaskService

            task_svc = SandboxTaskService(
                self.db,
                self.workspace_id,
                self.actor_id,
                self.settings,
                workspace=self.workspace,
                principal=self.principal,
            )
            prof = task_svc.profile()
            available = bool(prof.get("available"))
            unavail_reason = prof.get("reason")
        except Exception:
            available = False
            unavail_reason = "Sandbox profile probe failed; execution remains unavailable"

        if not available:
            invocation.status = "failed"
            invocation.error_code = "sandbox_unavailable"
            invocation.error_message = unavail_reason or "Docker sandbox is not ready"
            invocation.finished_at = utc_now()
            invocation.result_json = {
                "available": False,
                "reason": invocation.error_message,
            }
            self.audit.record(
                actor_id=self.actor_id,
                action="skill.sandbox_run.unavailable",
                resource_type="skill",
                resource_id=skill.id,
                outcome="failed",
                details={
                    "script_path": script_path,
                    "content_hash": skill.content_hash,
                    "reason": invocation.error_message,
                },
            )
            self.db.commit()
            return SkillSandboxRunResponse(
                status="unavailable",
                available=False,
                skill_id=skill.id,
                script_path=script_path,
                content_hash=skill.content_hash or skill.manifest_hash,
                error_code="sandbox_unavailable",
                error_message=invocation.error_message,
                invocation_id=invocation.id,
            )

        chat_session_id = self._resolve_chat_session_id(payload.chat_session_id)
        try:
            sandbox_session = self.agent.create_session(
                SandboxAgentSessionCreateRequest(chat_session_id=chat_session_id)
            )
            # Every original package byte is copied, not only text/scripts. Stage
            # per invocation in the chat workspace so scripts cannot poison a
            # shared cached package or leave deleted files behind after an update.
            files = self.package_files(skill)
            package_root = f"skills/{skill.id}/{invocation.id}"
            materialize_skill_package(self.agent, chat_session_id, sandbox_session.id, package_root, files)
            self.db.refresh(skill)
            if (skill.content_hash or skill.manifest_hash) != expected_hash:
                raise AppError(409, "skill_package_version_changed", "Skill version changed before execution")
            if require_authorization:
                _, grant = require_authorized_skill(manager, skill.id)
                invocation.grant_id = grant.id
                invocation.authorization_hash = manager._skill_authorization_hash(skill)
            run_rel = script_path if payload.cwd_mode == "package" else f"{package_root}/{script_path}"
            from app.domain.models import SandboxAgentCommand
            from types import SimpleNamespace
            execution_argv = [runner, run_rel, *list(payload.argv_extra or [])]
            if payload.cwd_mode == "package":
                from app.services.skill_launcher import LAUNCHER_BYTES
                launcher_name = f"{invocation.id}.launcher.py"
                launcher_parent = f"skills/{skill.id}"
                materialize_skill_package(self.agent, chat_session_id, sandbox_session.id,
                    launcher_parent, [(launcher_name, LAUNCHER_BYTES)])
                execution_argv = ["python", f"{launcher_parent}/{launcher_name}", package_root, script_path, *payload.argv_extra]
            request = SandboxAgentCommandRequest(
                chat_session_id=chat_session_id,
                argv=execution_argv,
                cwd=".",
                timeout_seconds=payload.timeout_seconds, yield_time_ms=payload.yield_time_ms,
                sandbox_session_id=sandbox_session.id,
            )
            invocation.input_json = {**invocation.input_json, "argv": request.argv, "cwd": request.cwd,
                                     "cwd_mode": payload.cwd_mode, "timeout_seconds": payload.timeout_seconds}
            invocation.input_hash = _hash(invocation.input_json)
            invocation.input_size_bytes = len(json.dumps(invocation.input_json).encode("utf-8"))
            invocation.status = "running"
            self.db.commit()
            job_result = self.agent.execute_agent_tool("sandbox_exec", request.model_dump(exclude_none=True),
                chat_session_id=chat_session_id, agent_authorized=True)
            command = self.db.query(SandboxAgentCommand).filter_by(
                workspace_id=self.workspace_id, job_id=job_result["job_id"],
            ).one_or_none()
            if command is None:
                command = SimpleNamespace(id=None, exit_code=None, timed_out=False, stdout_summary="", stderr_summary="",
                    argv_redacted=[], error_class=job_result.get("error_class"), error_message=job_result.get("error_message"), latency_ms=0)
            job_status = str(job_result["status"]).upper()
            invocation.status = ("succeeded" if job_status == "SUCCEEDED" and command.exit_code == 0
                                 else "running" if job_status in {"QUEUED", "STARTING", "RUNNING", "PAUSED"}
                                 else "failed")
            invocation.result_json = {
                "command_id": command.id,
                "job_id": job_result["job_id"],
                "exit_code": command.exit_code,
                "timed_out": command.timed_out,
                "stdout_summary": command.stdout_summary,
                "stderr_summary": command.stderr_summary,
                "argv_redacted": command.argv_redacted,
                "sandbox_session_id": sandbox_session.id,
                "chat_session_id": chat_session_id,
                "package_root": package_root,
                "cwd_mode": payload.cwd_mode,
                "content_hash": skill.content_hash or skill.manifest_hash,
            }
            invocation.result_hash = _hash(invocation.result_json)
            invocation.result_size_bytes = len(json.dumps(invocation.result_json))
            invocation.error_code = command.error_class
            invocation.error_message = command.error_message
            invocation.finished_at = None if invocation.status == "running" else utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="skill.sandbox_run",
                resource_type="skill",
                resource_id=skill.id,
                outcome=invocation.status,
                details={
                    "script_path": script_path,
                    "content_hash": skill.content_hash,
                    "command_id": command.id,
                    "exit_code": command.exit_code,
                    "host_execution": False,
                },
            )
            self.db.commit()
            return SkillSandboxRunResponse(
                status=invocation.status,
                available=True,
                skill_id=skill.id,
                script_path=script_path,
                content_hash=skill.content_hash or skill.manifest_hash,
                chat_session_id=chat_session_id,
                sandbox_session_id=sandbox_session.id,
                command_id=command.id,
                package_root=package_root,
                job_id=job_result["job_id"],
                cwd_mode=payload.cwd_mode,
                argv_redacted=list(command.argv_redacted or []),
                exit_code=command.exit_code,
                timed_out=bool(command.timed_out),
                latency_ms=int(command.latency_ms or 0),
                stdout_summary=command.stdout_summary or "",
                stderr_summary=command.stderr_summary or "",
                error_code=command.error_class,
                error_message=command.error_message,
                invocation_id=invocation.id,
            )
        except AppError as exc:
            invocation.status = "failed"
            invocation.error_code = exc.code
            invocation.error_message = exc.message
            invocation.finished_at = utc_now()
            invocation.result_json = {"available": exc.code != "sandbox_backend_unavailable", "error": exc.message}
            self.audit.record(
                actor_id=self.actor_id,
                action="skill.sandbox_run.failed",
                resource_type="skill",
                resource_id=skill.id,
                outcome="failed",
                details={"script_path": script_path, "error_code": exc.code},
            )
            self.db.commit()
            status = "unavailable" if "unavailable" in (exc.code or "") else "failed"
            return SkillSandboxRunResponse(
                status=status,
                available=status != "unavailable",
                skill_id=skill.id,
                script_path=script_path,
                content_hash=skill.content_hash or skill.manifest_hash,
                chat_session_id=chat_session_id,
                error_code=exc.code,
                error_message=exc.message,
                invocation_id=invocation.id,
            )
