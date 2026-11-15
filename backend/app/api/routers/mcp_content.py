from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.api.routers.mcp_skills import service
from app.domain.schemas.extensions import ExtensionInvocationView
from app.services.authorization import AuthorizationService
from app.services.mcp_content import invoke_content

router = APIRouter(tags=["mcp-skills"])

class ResourceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uri: str = Field(min_length=1, max_length=2048)
    arguments: dict[str, str] = Field(default_factory=dict)

class PromptGet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, str] = Field(default_factory=dict)


def manager_for(db, context, settings):
    manager = service(db, context, settings)
    manager._require_workspace_permission(AuthorizationService(db, context.principal), context.workspace, "workspace.manage")
    return manager

@router.get("/skills/{skill_id}/sandbox-runs/{invocation_id}", response_model=ExtensionInvocationView)
def skill_run_status(skill_id: str, invocation_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    from app.core.errors import AppError
    from app.services.skill_sandbox_run import SkillSandboxRunService
    manager_for(db, context, settings)
    invocation = SkillSandboxRunService(db, context.workspace_id, context.principal.user_id, settings,
        workspace=context.workspace, principal=context.principal).refresh_invocation(invocation_id)
    if invocation.skill_id != skill_id:
        raise AppError(404, "skill_run_not_found", "Skill run was not found")
    return invocation


@router.post("/skills/{skill_id}/sandbox-runs/{invocation_id}/cancel", response_model=ExtensionInvocationView)
def cancel_skill_run(skill_id: str, invocation_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    from app.core.errors import AppError
    from app.services.skill_sandbox_run import SkillSandboxRunService
    manager_for(db, context, settings)
    runner = SkillSandboxRunService(db, context.workspace_id, context.principal.user_id, settings,
        workspace=context.workspace, principal=context.principal)
    invocation = runner.refresh_invocation(invocation_id)
    if invocation.skill_id != skill_id:
        raise AppError(404, "skill_run_not_found", "Skill run was not found")
    if invocation.status == "running" and invocation.result_json.get("job_id"):
        runner.agent.execute_agent_tool("sandbox_cancel_job", {"job_id":invocation.result_json["job_id"]},
            chat_session_id=invocation.result_json["chat_session_id"], agent_authorized=True)
    return runner.refresh_invocation(invocation_id)


@router.post("/mcp/servers/{server_id}/resources/read", response_model=ExtensionInvocationView)
def read_resource(server_id: str, payload: ResourceRead, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return invoke_content(manager_for(db, context, settings), server_id, "resource", payload.uri, payload.arguments)

@router.post("/mcp/servers/{server_id}/prompts/get", response_model=ExtensionInvocationView)
def get_prompt(server_id: str, payload: PromptGet, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return invoke_content(manager_for(db, context, settings), server_id, "prompt", payload.name, payload.arguments)
