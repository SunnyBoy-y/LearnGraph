"""Sandbox toolkit mixin: bash / todo / patch / git / search / fetch /
subagent / skills / notebook tools for the Agent workspace service.

The mixin is composed into ``SandboxAgentWorkspaceService``; every method
returns an OpenAI-compatible tool result dict or raises ``AppError`` with the
stable error shape used by the Agent dispatcher.

Security invariants (see doc/sandbox-toolkit-design.md):
- the sandbox container stays offline; search/fetch/git-clone run host-side
  through the reviewed authorization pipeline;
- destructive workspace mutations go through the single-use grant flow;
- argv policy lives in ``app.providers.remote.sandbox.validate_agent_argv``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

from sqlalchemy import select

from app.core.errors import AppError
from app.domain.models import (
    EXTERNAL_ACQUISITION_CAPABILITY,
    FetchAuthorizationRequest,
    SandboxKernel,
    SandboxTodo,
    UserWebFetchPolicy,
    WorkspaceSetting,
    new_id,
    utc_now,
)
from app.domain.schemas.sandbox import (
    SandboxAgentBashRequest,
    SandboxAgentCommandRequest,
    SandboxAgentFileDeleteRequest,
    SandboxAgentFileReadRequest,
    SandboxAgentFileWriteRequest,
    SandboxAgentFetchRequest,
    SandboxAgentGitCloneRequest,
    SandboxAgentGitRequest,
    SandboxAgentNotebookRequest,
    SandboxAgentPatchRequest,
    SandboxAgentSearchRequest,
    SandboxAgentSkillListRequest,
    SandboxAgentSkillReadRequest,
    SandboxAgentSubagentRequest,
    SandboxAgentTodoRequest,
)
from app.providers.remote.fetch import FetchProviderError, FetchProviderTimeout, UnsafeFetchURL, require_public_http_url
from app.providers.remote.sandbox import (
    SandboxBackendError,
    SandboxBackendUnavailable,
    SandboxCapabilityMismatch,
    SandboxOutputLimitExceeded,
    SandboxWorkspaceQuotaExceeded,
    validate_agent_workspace_path,
)
from app.providers.remote.search import SearchProviderError, SearchProviderTimeout
from app.services.sandbox_diff import DiffApplyError, DiffParseError, apply_hunks, parse_unified_diff

logger = logging.getLogger(__name__)


class SandboxToolkitMixin:
    """Tool implementations for the extended sandbox tool set."""

    # ── shared helpers ────────────────────────────────────────────────────────

    def _format_command_result(self, command: Any) -> dict[str, Any]:
        """Shape a persisted SandboxAgentCommand into the sandbox_exec-style
        tool result consumed by the Agent dispatcher."""
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

    def _run_exec_command(
        self,
        *,
        chat_session_id: str,
        argv: list[str],
        sandbox_session_id: str | None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Run an argv command through the standard command pipeline."""
        command = self.execute_command(
            SandboxAgentCommandRequest(
                chat_session_id=chat_session_id,
                argv=argv,
                cwd=".",
                sandbox_session_id=sandbox_session_id,
                runtime="python-node",
            ),
            idempotency_key=None,
            timeout_seconds=timeout_seconds,
        )
        return self._format_command_result(command)

    # ── sandbox_bash ─────────────────────────────────────────────────────────

    def toolkit_bash(self, payload: SandboxAgentBashRequest) -> dict[str, Any]:
        if not self.settings.sandbox_bash_enabled:
            raise AppError(503, "sandbox_bash_disabled", "Sandbox shell execution is disabled by configuration")
        command = payload.command
        if len(command) > self.settings.sandbox_bash_max_chars:
            raise AppError(422, "sandbox_bash_too_long", "Sandbox shell command exceeds the configured length limit")
        if "\x00" in command or "\r" in command:
            raise AppError(422, "sandbox_bash_invalid", "Sandbox shell command contains invalid control characters")
        timeout = payload.timeout_seconds
        if timeout is not None:
            timeout = max(1, min(timeout, self.settings.sandbox_wall_time_seconds))
        return self._run_exec_command(
            chat_session_id=payload.chat_session_id,
            argv=["bash", "-lc", command],
            sandbox_session_id=payload.sandbox_session_id,
            timeout_seconds=timeout,
        )

    # ── sandbox_todo ─────────────────────────────────────────────────────────

    def toolkit_todo(self, payload: SandboxAgentTodoRequest) -> dict[str, Any]:
        self._require_chat_session(payload.chat_session_id)
        session_id = payload.sandbox_session_id
        if session_id:
            session = self._get_session(session_id)
        else:
            session = None
        record = self.db.scalar(
            select(SandboxTodo).where(
                SandboxTodo.workspace_id == self.workspace_id,
                SandboxTodo.owner_user_id == self.actor_id,
                SandboxTodo.chat_session_id == payload.chat_session_id,
            )
        )
        if record is None:
            record = SandboxTodo(
                id=new_id(),
                workspace_id=self.workspace_id,
                owner_user_id=self.actor_id,
                chat_session_id=payload.chat_session_id,
                sandbox_session_id=session.id if session else None,
                items=[],
                revision=0,
            )
            self.db.add(record)
            self.db.flush()

        action = payload.action
        items = list(record.items or [])
        now = utc_now().isoformat()
        if action == "add":
            if not payload.text:
                raise AppError(422, "invalid_tool_arguments", "sandbox_todo add requires a text field")
            items.append(
                {
                    "id": new_id()[:12],
                    "text": payload.text[:500],
                    "status": "open",
                    "created_at": now,
                    "updated_at": now,
                }
            )
        elif action == "done":
            item = next((entry for entry in items if entry.get("id") == payload.item_id), None)
            if item is None:
                raise AppError(404, "sandbox_todo_item_not_found", "Todo item was not found")
            item["status"] = "done"
            item["updated_at"] = now
        elif action == "remove":
            before = len(items)
            items = [entry for entry in items if entry.get("id") != payload.item_id]
            if len(items) == before:
                raise AppError(404, "sandbox_todo_item_not_found", "Todo item was not found")
        elif action == "clear":
            items = []
        record.items = items
        record.revision += 1
        record.sandbox_session_id = session.id if session else record.sandbox_session_id
        record.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(record)
        return {
            "sandbox_session_id": record.sandbox_session_id,
            "action": action,
            "revision": record.revision,
            "items": record.items,
            "open_count": sum(1 for entry in record.items if entry.get("status") != "done"),
        }

    # ── sandbox_apply_patch ──────────────────────────────────────────────────

    def toolkit_apply_patch(self, payload: SandboxAgentPatchRequest) -> dict[str, Any]:
        patch_bytes = len(payload.patch.encode("utf-8"))
        if patch_bytes > self.settings.sandbox_patch_max_bytes:
            raise AppError(422, "sandbox_patch_too_large", "Sandbox patch exceeds the configured byte limit")
        try:
            files = parse_unified_diff(payload.patch)
        except DiffParseError as exc:
            raise AppError(422, "sandbox_patch_parse_error", f"Patch could not be parsed: {exc}") from exc

        changed: list[dict[str, Any]] = []
        for file in files:
            old_path = "/dev/null" if file.is_create else file.old_path
            new_path = "/dev/null" if file.is_delete else file.new_path
            target = new_path if not file.is_delete else old_path
            try:
                target = validate_agent_workspace_path(target)
            except SandboxCapabilityMismatch as exc:
                raise AppError(422, "sandbox_path_blocked", str(exc)) from exc
            if file.is_delete:
                self.delete_file(
                    SandboxAgentFileDeleteRequest(
                        chat_session_id=payload.chat_session_id,
                        path=target,
                        sandbox_session_id=payload.sandbox_session_id,
                    )
                )
                changed.append({"path": target, "action": "delete"})
                continue
            if file.is_create:
                content = _render_create_content(file)
                self.write_file(
                    SandboxAgentFileWriteRequest(
                        chat_session_id=payload.chat_session_id,
                        path=target,
                        content=content,
                        sandbox_session_id=payload.sandbox_session_id,
                    )
                )
                changed.append({"path": target, "action": "create", "lines": file.hunks[0].new_count if file.hunks else 0})
                continue
            try:
                current = str(
                    self.read_file(
                        SandboxAgentFileReadRequest(
                            chat_session_id=payload.chat_session_id,
                            path=target,
                            sandbox_session_id=payload.sandbox_session_id,
                        )
                    ).get("content") or ""
                )
            except AppError as exc:
                if exc.code != "sandbox_file_unavailable":
                    raise
                raise AppError(
                    422,
                    "sandbox_patch_missing_file",
                    f"Patch targets a file that does not exist in the workspace: {target}",
                ) from exc
            try:
                updated = apply_hunks(current, file.hunks, fuzz=payload.fuzz)
            except DiffApplyError as exc:
                raise AppError(
                    422,
                    "sandbox_patch_apply_error",
                    f"Patch hunk does not match {target}: {exc}",
                ) from exc
            self.write_file(
                SandboxAgentFileWriteRequest(
                    chat_session_id=payload.chat_session_id,
                    path=target,
                    content=updated,
                    sandbox_session_id=payload.sandbox_session_id,
                )
            )
            changed.append({"path": target, "action": "modify"})
        return {
            "sandbox_session_id": payload.sandbox_session_id,
            "files_changed": changed,
            "summary": {"type": "sandbox_status", "status": "completed", "data": {"phase": "completed", "files_changed": changed}},
        }

    # ── sandbox_git ──────────────────────────────────────────────────────────

    def toolkit_git(self, payload: SandboxAgentGitRequest) -> dict[str, Any]:
        if not self.settings.sandbox_git_enabled:
            raise AppError(503, "sandbox_git_disabled", "Sandbox git is disabled by configuration")
        if not payload.args:
            raise AppError(422, "invalid_tool_arguments", "sandbox_git requires an args list")
        if payload.args[0] == "clone":
            raise AppError(
                422,
                "sandbox_git_clone_required",
                "git clone must go through the approved sandbox_git_clone tool (reviewed egress)",
            )
        return self._run_exec_command(
            chat_session_id=payload.chat_session_id,
            argv=["git", *payload.args],
            sandbox_session_id=payload.sandbox_session_id,
        )

    def toolkit_git_clone(self, payload: SandboxAgentGitCloneRequest) -> dict[str, Any]:
        if not self.settings.sandbox_git_enabled:
            raise AppError(503, "sandbox_git_disabled", "Sandbox git is disabled by configuration")
        from app.services.egress_approvals import EgressApprovalService
        from app.services.external_acquisition import (
            AcquisitionApprovalRequired,
            ExternalAcquisitionService,
        )

        acquisition = ExternalAcquisitionService(
            self.db, self.workspace_id, self.actor_id, self.settings
        )
        normalized = {
            "owner": payload.owner.strip(),
            "repo": payload.repo.strip(),
            "ref": payload.ref.strip() or "HEAD",
            "path": payload.path.strip(),
            "destination_root": payload.destination_root.strip(),
        }
        _spec, spec_sha = acquisition.canonical_spec("github_snapshot", normalized)
        approval_service = EgressApprovalService(
            self.db,
            self.workspace_id,
            self.settings,
            capability=EXTERNAL_ACQUISITION_CAPABILITY,
        )
        allowed_hosts = set(approval_service.effective_allowed_hosts(actor_id=self.actor_id))
        purpose = f"克隆 GitHub 源码 {normalized['owner']}/{normalized['repo']}@{normalized['ref']}"
        try:
            result = acquisition.download_github_source(
                chat_session_id=payload.chat_session_id,
                allowed_hosts=allowed_hosts,
                request_spec_sha256=spec_sha,
                **normalized,
            )
        except AcquisitionApprovalRequired as exc:
            request = approval_service.create_request(
                hostname=exc.hostname,
                requested_by=self.actor_id,
                chat_session_id=payload.chat_session_id,
                purpose=purpose,
                request_context={
                    "tool_name": "sandbox_git_clone",
                    "tool_label": "沙箱 Git 克隆工具",
                    "origin": "sandbox_toolkit",
                    "request_spec_sha256": spec_sha,
                    "resource_summary": purpose,
                    "destination_path": normalized["destination_root"],
                },
                dedupe_key=f"acquire:{spec_sha[:32]}:{exc.hostname}"[:80],
            )
            if request.status == "approved":
                return self.toolkit_git_clone(payload)
            raise AppError(
                403,
                "egress_authorization_required",
                "沙箱 Git 克隆需要用户授权",
                details={
                    "authorization_request_id": request.id,
                    "tool_name": "sandbox_git_clone",
                    "tool_label": "沙箱 Git 克隆工具",
                    "hostname": request.hostname,
                    "request_spec_sha256": spec_sha,
                    "resource_summary": purpose,
                    "destination_path": normalized["destination_root"],
                    "message_zh": f"{purpose}，需要访问主机 {request.hostname}，是否批准？",
                },
            )
        self._git_init_snapshot(
            chat_session_id=payload.chat_session_id,
            destination_root=normalized["destination_root"],
            commit_message=f"snapshot {normalized['owner']}/{normalized['repo']}@{normalized['ref']}",
        )
        return {
            "sandbox_session_id": payload.sandbox_session_id,
            "owner": normalized["owner"],
            "repo": normalized["repo"],
            "ref": normalized["ref"],
            "commit_sha": result.get("commit_sha"),
            "manifest_sha256": result.get("manifest_sha256"),
            "destination_root": normalized["destination_root"],
            "file_count": result.get("file_count"),
            "total_bytes": result.get("total_bytes"),
            "git_initialized": True,
            "summary": {
                "type": "sandbox_status",
                "status": "completed",
                "data": {"phase": "completed", "kind": "git_clone", "destination_root": normalized["destination_root"]},
            },
        }

    def _git_init_snapshot(
        self, *, chat_session_id: str, destination_root: str, commit_message: str
    ) -> None:
        """Best-effort container-side git repository on a host-acquired snapshot."""
        try:
            validate_agent_workspace_path(destination_root)
        except SandboxCapabilityMismatch:
            return
        commands = [
            ["git", "-C", destination_root, "init", "-q"],
            ["git", "-C", destination_root, "config", "user.email", "learngraph-agent@localhost"],
            ["git", "-C", destination_root, "config", "user.name", "LearnGraph Agent"],
            ["git", "-C", destination_root, "add", "-A"],
            ["git", "-C", destination_root, "commit", "-q", "-m", commit_message[:200]],
        ]
        for argv in commands:
            try:
                self.execute_command(
                    SandboxAgentCommandRequest(
                        chat_session_id=chat_session_id,
                        argv=argv,
                        cwd=".",
                        sandbox_session_id=None,
                        runtime="python-node",
                    ),
                    idempotency_key=None,
                )
            except (AppError, SandboxBackendError, SandboxBackendUnavailable):
                logger.exception("best-effort git snapshot init failed: %s", argv[:2])
                return

    # ── sandbox_search_web / sandbox_fetch (host-side, offline container) ─────

    def _effective_fetch_policy(self) -> dict[str, Any]:
        setting = self.db.scalar(
            select(WorkspaceSetting).where(
                WorkspaceSetting.workspace_id == self.workspace_id,
                WorkspaceSetting.key == "web_fetch.policy",
            )
        )
        value = setting.value if setting is not None and isinstance(setting.value, dict) else {}
        workspace_domains = value.get("allowed_domains")
        user_policy = self.db.scalar(
            select(UserWebFetchPolicy).where(
                UserWebFetchPolicy.workspace_id == self.workspace_id,
                UserWebFetchPolicy.user_id == self.actor_id,
            )
        )
        workspace_domain_list = (
            [item for item in workspace_domains if isinstance(item, str)]
            if isinstance(workspace_domains, list)
            else []
        )
        user_domains = user_policy.allowed_domains if user_policy is not None else []
        from app.providers.factory import access_allow_all, access_allowlist_domains

        unified_domains = access_allowlist_domains(self.db, self.workspace_id)
        allow_all = access_allow_all(self.db, self.workspace_id)
        return {
            "allow_without_confirmation": bool(
                value.get("allow_without_confirmation", False)
                or (user_policy is not None and user_policy.allow_without_confirmation)
                or allow_all
            ),
            "allowed_domains": list(
                dict.fromkeys(
                    [
                        *workspace_domain_list,
                        *(item for item in user_domains if isinstance(item, str)),
                        *sorted(unified_domains),
                    ]
                )
            ),
        }

    def toolkit_search_web(self, payload: SandboxAgentSearchRequest) -> dict[str, Any]:
        if not self.settings.sandbox_network_tools_enabled:
            raise AppError(503, "sandbox_network_tools_disabled", "Sandbox network tools are disabled by configuration")
        from app.providers.factory import search_provider_for_workspace

        provider = search_provider_for_workspace(self.db, self.workspace_id, self.settings)
        policy = self._effective_fetch_policy()
        domains = {item.strip().casefold() for item in policy["allowed_domains"] if item.strip()}
        try:
            results = provider.search(payload.query.strip(), payload.max_results, allowed_domains=domains or None)
        except SearchProviderTimeout as exc:
            raise AppError(504, "search_provider_timeout", "SearchProvider timed out") from exc
        except SearchProviderError as exc:
            raise AppError(502, "search_provider_failed", "SearchProvider failed") from exc
        return {
            "sandbox_session_id": payload.sandbox_session_id,
            "query": payload.query.strip(),
            "provider_id": provider.provider_id,
            "result_count": len(results),
            "results": [
                {"title": item.title, "url": item.url, "snippet": item.snippet}
                for item in results
            ],
            "summary": {
                "type": "sandbox_status",
                "status": "completed",
                "data": {"phase": "completed", "kind": "web_search", "result_count": len(results)},
            },
        }

    def toolkit_fetch(self, payload: SandboxAgentFetchRequest) -> dict[str, Any]:
        if not self.settings.sandbox_network_tools_enabled:
            raise AppError(503, "sandbox_network_tools_disabled", "Sandbox network tools are disabled by configuration")
        from urllib.parse import urlparse

        raw_url = payload.url.strip()
        hostname = urlparse(raw_url).hostname
        hostname = hostname.casefold() if hostname else ""
        if not hostname:
            raise AppError(422, "fetch_url_blocked", "The requested URL is invalid")
        policy = self._effective_fetch_policy()
        domains = {
            item.strip().casefold()
            for item in policy["allowed_domains"]
            if isinstance(item, str) and item.strip()
        }
        if hostname not in domains and not policy["allow_without_confirmation"]:
            tool_call_id = f"sandbox_fetch:{hashlib.sha256(raw_url.encode()).hexdigest()[:16]}"
            pending = self.db.scalar(
                select(FetchAuthorizationRequest).where(
                    FetchAuthorizationRequest.workspace_id == self.workspace_id,
                    FetchAuthorizationRequest.chat_session_id == payload.chat_session_id,
                    FetchAuthorizationRequest.tool_call_id == tool_call_id,
                )
            )
            if pending is None:
                pending = FetchAuthorizationRequest(
                    workspace_id=self.workspace_id,
                    chat_session_id=payload.chat_session_id,
                    actor_id=self.actor_id,
                    tool_call_id=tool_call_id,
                    requested_url=raw_url,
                    hostname=hostname,
                )
                self.db.add(pending)
                self.db.commit()
            raise AppError(
                403,
                "fetch_domain_authorization_required",
                "网页抓取需要用户授权",
                details={
                    "authorization_request_id": pending.id,
                    "tool_call_id": tool_call_id,
                    "tool_name": "sandbox_fetch",
                    "tool_label": "沙箱网页抓取工具",
                    "requested_url": raw_url,
                    "hostname": hostname,
                    "message_zh": f"我将使用沙箱网页抓取工具抓取 {raw_url} 网页，是否批准？",
                },
            )
        from app.providers.factory import fetch_provider_for_workspace

        provider = fetch_provider_for_workspace(self.db, self.workspace_id, self.settings)
        try:
            require_public_http_url(raw_url, domains)
        except UnsafeFetchURL as exc:
            raise AppError(422, "fetch_url_blocked", "The requested URL is outside the authorized public domains") from exc
        try:
            document = provider.fetch(raw_url)
            require_public_http_url(document.final_url, domains)
        except UnsafeFetchURL as exc:
            raise AppError(422, "fetch_url_blocked", "The fetched page is outside the authorized public domains") from exc
        except FetchProviderTimeout as exc:
            raise AppError(504, "fetch_provider_timeout", "Web extractor timed out") from exc
        except FetchProviderError as exc:
            raise AppError(502, "fetch_provider_failed", "Web extractor failed") from exc
        return {
            "sandbox_session_id": payload.sandbox_session_id,
            "url": document.final_url,
            "title": document.title,
            "content": document.content,
            "content_type": document.content_type,
            "provider_id": provider.provider_id,
            "summary": {
                "type": "sandbox_status",
                "status": "completed",
                "data": {"phase": "completed", "kind": "web_fetch", "content_chars": len(document.content or "")},
            },
        }

    # ── sandbox_subagent ─────────────────────────────────────────────────────

    def toolkit_subagent(self, payload: SandboxAgentSubagentRequest) -> dict[str, Any]:
        if not self.settings.sandbox_subagent_enabled:
            raise AppError(503, "sandbox_subagent_disabled", "Sandbox sub-agents are disabled by configuration")
        from app.services.sandbox_subagent import SubagentRegistry, SubagentSpec

        subagent_id = new_id()[:20]
        spec = SubagentSpec(
            subagent_id=subagent_id,
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            chat_session_id=payload.chat_session_id,
            prompt=payload.prompt,
            tools=payload.tools,
            max_rounds=min(payload.max_rounds, self.settings.sandbox_subagent_max_rounds),
            max_seconds=self.settings.sandbox_subagent_max_seconds,
            sandbox_session_id=payload.sandbox_session_id,
        )
        SubagentRegistry.instance().start(spec)
        return {
            "subagent_id": subagent_id,
            "status": "queued",
            "sandbox_session_id": payload.sandbox_session_id,
            "summary": {
                "type": "sandbox_status",
                "status": "queued",
                "data": {
                    "phase": "queued",
                    "subagent_id": subagent_id,
                    "chat_session_id": payload.chat_session_id,
                },
            },
        }

    def toolkit_subagent_status(self, payload: Any) -> dict[str, Any]:
        from app.services.sandbox_subagent import SubagentRegistry

        subagent_id = (
            payload.get("subagent_id")
            if isinstance(payload, dict)
            else getattr(payload, "subagent_id", None)
        )
        if not isinstance(subagent_id, str) or not subagent_id:
            raise AppError(422, "invalid_tool_arguments", "sandbox_subagent_status requires a subagent_id")
        job = SubagentRegistry.instance().get(subagent_id)
        if job is None:
            raise AppError(404, "sandbox_subagent_not_found", "Sub-agent was not found or already expired")
        return {
            "subagent_id": job.spec.subagent_id,
            "status": job.status,
            "error_class": job.error_class,
            "rounds": job.rounds,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "result": job.result,
            "summary": {
                "type": "sandbox_status",
                "status": job.status,
                "data": {"phase": job.status.lower(), "subagent_id": job.spec.subagent_id},
            },
        }

    # ── sandbox_skill_list / sandbox_skill_read ──────────────────────────────

    def toolkit_skill_list(self, payload: SandboxAgentSkillListRequest) -> dict[str, Any]:
        from app.services.skill_package import OFFICIAL_SKILLS

        skills = [
            {
                "key": spec.key,
                "name": spec.key,
                "category": spec.category,
                "description": spec.description,
                "version": spec.version,
            }
            for spec in OFFICIAL_SKILLS
        ]
        return {
            "sandbox_session_id": payload.sandbox_session_id,
            "skills": skills,
            "count": len(skills),
            "summary": {
                "type": "sandbox_status",
                "status": "completed",
                "data": {"phase": "completed", "kind": "skill_list", "count": len(skills)},
            },
        }

    def toolkit_skill_read(self, payload: SandboxAgentSkillReadRequest) -> dict[str, Any]:
        from app.services.skill_package import OFFICIAL_SKILLS

        spec = next((item for item in OFFICIAL_SKILLS if item.key == payload.skill_key), None)
        if spec is None:
            raise AppError(404, "sandbox_skill_not_found", "Skill was not found in the official skill catalog")
        import os
        from pathlib import Path

        base = Path(__file__).resolve().parents[1] / "skills"
        skill_dir = base / spec.dir_name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            raise AppError(404, "sandbox_skill_not_found", "Skill SKILL.md was not found")
        body = skill_md.read_text(encoding="utf-8")[:20_000]
        return {
            "sandbox_session_id": payload.sandbox_session_id,
            "skill_key": spec.key,
            "name": spec.key,
            "description": spec.description,
            "content": body,
            "summary": {
                "type": "sandbox_status",
                "status": "completed",
                "data": {"phase": "completed", "kind": "skill_read", "skill_key": spec.key},
            },
        }

    # ── sandbox_notebook ─────────────────────────────────────────────────────

    def toolkit_notebook(self, payload: SandboxAgentNotebookRequest) -> dict[str, Any]:
        if not self.settings.sandbox_notebook_enabled:
            raise AppError(503, "sandbox_notebook_disabled", "Sandbox notebook is disabled by configuration")
        self._require_chat_session(payload.chat_session_id)
        session = self._resolve_session(payload.sandbox_session_id, payload.chat_session_id)
        workspace_relative = (
            self._container_prefix(payload.chat_session_id)
            if self._pooling_enabled(session)
            else "."
        )
        backend = self._runtime_backend(session)
        if payload.action == "open":
            try:
                handle = self._ensure_backend_session(session)
                kernel_id = backend.kernel_open(
                    handle, workspace_relative=workspace_relative, interpreter=payload.interpreter
                )
            except (SandboxBackendUnavailable, SandboxBackendError) as exc:
                raise AppError(502, "sandbox_kernel_open_failed", "Sandbox kernel could not be started") from exc
            self.db.add(
                SandboxKernel(
                    id=new_id(),
                    workspace_id=self.workspace_id,
                    owner_user_id=self.actor_id,
                    chat_session_id=payload.chat_session_id,
                    sandbox_session_id=session.id,
                    kernel_id=kernel_id,
                    interpreter=payload.interpreter,
                    status="running",
                )
            )
            self.db.commit()
            return {
                "kernel_id": kernel_id,
                "interpreter": payload.interpreter,
                "status": "running",
                "sandbox_session_id": session.id,
                "summary": {
                    "type": "sandbox_status",
                    "status": "completed",
                    "data": {"phase": "completed", "kind": "notebook_open", "kernel_id": kernel_id},
                },
            }
        if not payload.kernel_id:
            raise AppError(422, "invalid_tool_arguments", "sandbox_notebook execute/close requires kernel_id")
        if payload.action == "execute":
            if not payload.code:
                raise AppError(422, "invalid_tool_arguments", "sandbox_notebook execute requires code")
            try:
                handle = self._ensure_backend_session(session)
                result = backend.kernel_execute(
                    handle,
                    payload.kernel_id,
                    payload.code,
                    timeout_seconds=self.settings.sandbox_wall_time_seconds,
                    output_limit=self.settings.sandbox_output_bytes,
                )
            except (SandboxBackendUnavailable, SandboxBackendError) as exc:
                raise AppError(502, "sandbox_kernel_execution_failed", "Sandbox kernel execution failed") from exc
            row = self.db.scalar(
                select(SandboxKernel).where(
                    SandboxKernel.workspace_id == self.workspace_id,
                    SandboxKernel.kernel_id == payload.kernel_id,
                )
            )
            if row is not None:
                row.last_used_at = utc_now()
                self.db.commit()
            return {
                "kernel_id": payload.kernel_id,
                "ok": result.get("ok"),
                "stdout": result.get("stdout"),
                "stderr": result.get("stderr"),
                "result_repr": result.get("result_repr"),
                "timed_out": result.get("timed_out", False),
                "sandbox_session_id": session.id,
                "summary": {
                    "type": "sandbox_status",
                    "status": "completed" if result.get("ok") else "failed",
                    "data": {
                        "phase": "completed",
                        "kind": "notebook_execute",
                        "kernel_id": payload.kernel_id,
                        "stdout_summary": (result.get("stdout") or "")[:400],
                        "stderr_summary": (result.get("stderr") or "")[:400],
                    },
                },
            }
        if payload.action == "close":
            try:
                handle = self._ensure_backend_session(session)
                backend.kernel_close(handle, payload.kernel_id)
            except (SandboxBackendUnavailable, SandboxBackendError):
                pass
            rows = self.db.scalars(
                select(SandboxKernel).where(
                    SandboxKernel.workspace_id == self.workspace_id,
                    SandboxKernel.kernel_id == payload.kernel_id,
                )
            ).all()
            for row in rows:
                row.status = "closed"
            self.db.commit()
            return {
                "kernel_id": payload.kernel_id,
                "status": "closed",
                "sandbox_session_id": session.id,
                "summary": {
                    "type": "sandbox_status",
                    "status": "completed",
                    "data": {"phase": "completed", "kind": "notebook_close", "kernel_id": payload.kernel_id},
                },
            }
        if payload.action == "status":
            row = self.db.scalar(
                select(SandboxKernel).where(
                    SandboxKernel.workspace_id == self.workspace_id,
                    SandboxKernel.kernel_id == payload.kernel_id,
                )
            )
            return {
                "kernel_id": payload.kernel_id,
                "status": row.status if row is not None else "unknown",
                "sandbox_session_id": session.id,
                "summary": {
                    "type": "sandbox_status",
                    "status": "completed",
                    "data": {"phase": "completed", "kind": "notebook_status", "kernel_id": payload.kernel_id},
                },
            }
        raise AppError(422, "invalid_tool_arguments", "Unknown sandbox_notebook action")


def _render_create_content(file: Any) -> str:
    """Render the added lines of a create patch (including context lines)."""
    lines: list[str] = []
    for hunk in file.hunks:
        lines.extend(line[1:] for line in hunk.lines if line[0] in (" ", "+"))
    if lines and lines[-1] == "":
        return "\n".join(lines)
    return "\n".join(lines) + ("\n" if lines else "")
