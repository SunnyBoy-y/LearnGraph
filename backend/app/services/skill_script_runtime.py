"""Bridge model tool calls to the installed, version-authorized package bytes."""
from __future__ import annotations

from typing import Any
from pydantic import ValidationError

from app.core.errors import AppError
from app.domain.schemas.extensions import SkillSandboxRunRequest
from app.services.authorization import AuthorizationService
from app.services.skill_sandbox_run import SkillSandboxRunService


def skill_script_tool_definition() -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": "lg_skill_run_script",
        "description": (
            "Execute an original scripts/ file from an enabled, authorized Skill package in the "
            "isolated sandbox. Read SKILL.md first. The full package, including binary assets and "
            "helpers, is staged without rewriting code. cwd defaults to the package root; use "
            "cwd_mode=workspace for session-relative input/output paths. No host shell or network fallback. "
            "When running, use sandbox_get_job with the returned job_id, or sandbox_cancel_job to stop. Never start a duplicate run."
        ),
        "parameters": {"type": "object", "properties": {
            "skill_key": {"type": "string", "minLength": 1, "maxLength": 80},
            "script_path": {"type": "string", "minLength": 1, "maxLength": 500},
            "argv_extra": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 4096}},
            "cwd_mode": {"type": "string", "enum": ["package", "workspace"]},
        }, "required": ["skill_key", "script_path"], "additionalProperties": False},
    }}


def require_authorized_skill(manager, skill_id: str):
    skill = manager.require_skill(skill_id)
    grant = manager._usable_grant("skill", skill.id, manager._skill_authorization_hash(skill))
    if not skill.enabled or skill.status != "enabled" or grant is None or grant.decision != "always":
        raise AppError(403, "agent_tool_not_authorized", "The current Skill version requires durable authorization")
    return skill, grant


def run_skill_script(runtime, arguments: dict[str, Any], *, chat_session_id: str):
    if runtime.sandbox is None or not runtime.sandbox_authorized:
        return runtime._failure("sandbox_agent_unavailable", "Skill code execution requires sandbox authorization")
    manager = runtime.extensions
    if manager.workspace is None or manager.principal is None:
        raise AppError(403, "skill_context_required", "Authenticated workspace context is required")
    manager._require_workspace_permission(
        AuthorizationService(manager.db, manager.principal), manager.workspace, "workspace.manage",
    )
    unknown = set(arguments) - {"skill_key", "script_path", "argv_extra", "cwd_mode"}
    if unknown:
        raise AppError(422, "invalid_tool_arguments", "Unsupported Skill script arguments")
    skill = manager._resolve_skill_by_key(str(arguments.get("skill_key") or ""))
    require_authorized_skill(manager, skill.id)
    try:
        payload = SkillSandboxRunRequest(
            script_path=arguments.get("script_path"),
            argv_extra=arguments.get("argv_extra", []),
            cwd_mode=arguments.get("cwd_mode", "package"),
            # Session binding is host-owned, never an LLM-controlled argument.
            chat_session_id=chat_session_id,
        )
    except ValidationError as exc:
        raise AppError(422, "invalid_tool_arguments", "Invalid Skill script arguments") from exc
    result = SkillSandboxRunService(
        manager.db, manager.workspace_id, manager.actor_id, manager.settings,
        workspace=manager.workspace, principal=manager.principal,
    ).run(skill.id, payload, require_authorization=True).model_dump(mode="json")
    meta = {"extension_invocation_id": result.get("invocation_id"),
            "skill_trigger": manager._skill_trigger_payload(skill, "package_script")}
    if result["status"] not in {"succeeded", "running", "pending"}:
        return runtime._failure(result.get("error_code") or "skill_script_failed",
                                result.get("error_message") or "Skill script did not succeed", data=result)
    return runtime._success(result, meta, [])
