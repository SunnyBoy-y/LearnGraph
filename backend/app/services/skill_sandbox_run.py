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
from app.services.skill_package import normalize_skill_relative_path


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
        data = self.blobs.read_bytes(row.blob_sha256)
        return path, data

    def run(self, skill_id: str, payload: SkillSandboxRunRequest) -> SkillSandboxRunResponse:
        skill = self.require_skill(skill_id)
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
            timeout_ms=int(getattr(self.settings, "sandbox_wall_time_seconds", 30) * 1000),
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
            available = bool(getattr(self.settings, "sandbox_enabled", True))
            unavail_reason = "sandbox profile probe failed"

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
            # Materialize package scripts into the shared capability area
            # (once per user, not per chat) under shared/skills/<key>/ plus
            # SKILL.md, a generated README, and the controlled
            # references/examples directories. Only text files are copied; the
            # sandbox stays offline and secret-free.
            files = [
                (row.relative_path, self.blobs.read_bytes(row.blob_sha256))
                for row in (
                    self.db.query(SkillPackageFile)
                    .filter_by(workspace_id=self.workspace_id, skill_id=skill.id, is_directory=False)
                    .all()
                )
                if (
                    row.relative_path == "SKILL.md"
                    or row.relative_path.startswith(("scripts/", "references/", "examples/"))
                )
                and _is_utf8(self.blobs.read_bytes(row.blob_sha256))
            ]
            pooling = bool(
                self.settings.sandbox_instance_pooling_enabled
                and (sandbox_session.backend_id or "docker") == "sandboxd"
            )
            if pooling:
                shared_rel = self.agent.materialize_shared_skill(
                    chat_session_id,
                    skill.skill_key,
                    files,
                    readme=_skill_readme(skill, [rel for rel, _ in files]),
                )
                run_rel = f"/workspace/{shared_rel}/{script_path}"
            else:
                for rel, body in files:
                    text = body.decode("utf-8")
                    self.agent.write_file(
                        SandboxAgentFileWriteRequest(
                            chat_session_id=chat_session_id,
                            path=f"skills/{skill.skill_key}/{rel}",
                            content=text,
                            sandbox_session_id=sandbox_session.id,
                        )
                    )
                run_rel = f"skills/{skill.skill_key}/{script_path}"
            command = self.agent.execute_command(
                SandboxAgentCommandRequest(
                    chat_session_id=chat_session_id,
                    argv=[runner, run_rel, *list(payload.argv_extra or [])],
                    cwd=".",
                    sandbox_session_id=sandbox_session.id,
                ),
                idempotency_key=None,
            )
            invocation.status = "succeeded" if command.status in {"completed", "succeeded"} and (command.exit_code or 0) == 0 else "failed"
            if command.status == "failed":
                invocation.status = "failed"
            invocation.result_json = {
                "command_id": command.id,
                "exit_code": command.exit_code,
                "timed_out": command.timed_out,
                "stdout_summary": command.stdout_summary,
                "stderr_summary": command.stderr_summary,
                "argv_redacted": command.argv_redacted,
                "sandbox_session_id": sandbox_session.id,
            }
            invocation.result_hash = _hash(invocation.result_json)
            invocation.result_size_bytes = len(json.dumps(invocation.result_json))
            invocation.error_code = command.error_class
            invocation.error_message = command.error_message
            invocation.finished_at = utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="skill.sandbox_run",
                resource_type="skill",
                resource_id=skill.id,
                outcome="succeeded" if invocation.status == "succeeded" else "failed",
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
