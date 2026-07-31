from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.sandbox import (
    SandboxAgentCommandRequest,
    SandboxAgentCommandView,
    SandboxAgentReadinessView,
    SandboxAgentFileListRequest,
    SandboxAgentFileReadRequest,
    SandboxAgentFileView,
    SandboxAgentFileWriteRequest,
    SandboxAgentSessionCreateRequest,
    SandboxBootstrapJobView,
    SandboxBootstrapStartResponse,
    SandboxBootstrapStatusView,
    SandboxDestructiveGrantRequest,
    SandboxDestructiveGrantView,
    SandboxExecutionView,
    SandboxProfileView,
    SandboxSessionView,
    SandboxTaskCreateRequest,
    SandboxTaskView,
    SessionWorkspaceListResponse,
    SessionWorkspaceEntryView,
    SessionWorkspacePublishRequest,
    SessionWorkspacePublishResponse,
)
from app.core.errors import AppError
from app.services.sandbox import (
    SandboxAgentWorkspaceService,
    SandboxTaskService,
    agent_sandbox_readiness,
)
from app.services.sandbox_authz import SandboxAuthorizationService
from app.services.sandbox_bootstrap import get_bootstrap_service
from app.services.session_workspace import SessionWorkspaceService


router = APIRouter(prefix="/sandbox", tags=["sandbox"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> SandboxTaskService:
    return SandboxTaskService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
        workspace=context.workspace,
        principal=context.principal,
    )


def agent_service(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> SandboxAgentWorkspaceService:
    return SandboxAgentWorkspaceService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
        workspace=context.workspace,
        principal=context.principal,
    )


def require_agent_sandbox_permission(context: CurrentWorkspace) -> None:
    """Agent code execution is stronger than the fixed file-task API."""

    context.require_permission("workspace.manage")


@router.get("/profiles", response_model=list[SandboxProfileView])
def list_profiles(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> list[SandboxProfileView]:
    sandbox = service(db, context, settings)
    # The unified runner image serves every runtime kind, so the deployment
    # exposes exactly one profile.
    return [SandboxProfileView.model_validate(sandbox.profile())]


@router.get("/bootstrap/status", response_model=SandboxBootstrapStatusView)
def bootstrap_status(
    context: CurrentWorkspace, settings: AppSettings
) -> SandboxBootstrapStatusView:
    # Any workspace reader can see readiness (needed for session UX banners).
    return SandboxBootstrapStatusView.model_validate(
        get_bootstrap_service().status(settings)
    )


@router.get("/agent/readiness", response_model=SandboxAgentReadinessView)
def agent_readiness(
    context: CurrentWorkspace, settings: AppSettings
) -> SandboxAgentReadinessView:
    return SandboxAgentReadinessView.model_validate(
        agent_sandbox_readiness(
            settings,
            authorized="workspace.manage" in context.permissions,
        )
    )


@router.post("/bootstrap", response_model=SandboxBootstrapStartResponse)
def start_bootstrap(
    context: CurrentWorkspace, settings: AppSettings
) -> SandboxBootstrapStartResponse:
    if not context.principal.is_system_admin:
        raise AppError(
            403,
            "deployment_admin_required",
            "Sandbox runtime bootstrap requires a deployment administrator",
        )
    result = get_bootstrap_service().start(
        settings, actor_id=context.principal.user_id
    )
    return SandboxBootstrapStartResponse.model_validate(result)


@router.get("/bootstrap/jobs/{job_id}", response_model=SandboxBootstrapJobView)
def get_bootstrap_job(
    job_id: str, context: CurrentWorkspace, settings: AppSettings
) -> SandboxBootstrapJobView:
    job = get_bootstrap_service().get_job(job_id)
    if job is None:
        raise AppError(404, "sandbox_bootstrap_job_not_found", "Bootstrap job was not found")
    return SandboxBootstrapJobView.model_validate(job)


@router.post(
    "/agent/sessions",
    response_model=SandboxSessionView,
    status_code=status.HTTP_201_CREATED,
)
def create_agent_session(
    payload: SandboxAgentSessionCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxSessionView:
    require_agent_sandbox_permission(context)
    return SandboxSessionView.model_validate(
        agent_service(db, context, settings).create_session(payload)
    )


@router.post(
    "/agent/commands",
    response_model=SandboxAgentCommandView,
    status_code=status.HTTP_201_CREATED,
)
def execute_agent_command(
    payload: SandboxAgentCommandRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> SandboxAgentCommandView:
    require_agent_sandbox_permission(context)
    return SandboxAgentCommandView.model_validate(
        agent_service(db, context, settings).execute_command(
            payload, idempotency_key=idempotency_key
        )
    )


@router.get("/agent/commands", response_model=list[SandboxAgentCommandView])
def list_agent_commands(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    chat_session_id: Annotated[str | None, Query(max_length=36)] = None,
) -> list[SandboxAgentCommandView]:
    require_agent_sandbox_permission(context)
    return [
        SandboxAgentCommandView.model_validate(item)
        for item in agent_service(db, context, settings).list_commands(chat_session_id)
    ]


@router.get("/agent/commands/{command_id}", response_model=SandboxAgentCommandView)
def get_agent_command(
    command_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxAgentCommandView:
    require_agent_sandbox_permission(context)
    return SandboxAgentCommandView.model_validate(
        agent_service(db, context, settings).get_command(command_id)
    )


@router.post("/agent/files/write", response_model=SandboxAgentFileView)
def write_agent_file(
    payload: SandboxAgentFileWriteRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxAgentFileView:
    require_agent_sandbox_permission(context)
    return SandboxAgentFileView.model_validate(
        agent_service(db, context, settings).write_file(payload)
    )


@router.post("/agent/files/read", response_model=SandboxAgentFileView)
def read_agent_file(
    payload: SandboxAgentFileReadRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxAgentFileView:
    require_agent_sandbox_permission(context)
    return SandboxAgentFileView.model_validate(
        agent_service(db, context, settings).read_file(payload)
    )


@router.post("/agent/files/list", response_model=SandboxAgentFileView)
def list_agent_files(
    payload: SandboxAgentFileListRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxAgentFileView:
    require_agent_sandbox_permission(context)
    return SandboxAgentFileView.model_validate(
        agent_service(db, context, settings).list_files(payload)
    )


@router.get("/sessions", response_model=list[SandboxSessionView])
def list_sessions(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    chat_session_id: Annotated[str | None, Query(max_length=36)] = None,
) -> list[SandboxSessionView]:
    include_all = context.principal.is_system_admin
    return [
        SandboxSessionView.model_validate(item)
        for item in service(db, context, settings).list_sessions(
            chat_session_id, include_all=include_all, include_cleaned=False
        )
    ]


@router.get("/sessions/{sandbox_session_id}", response_model=SandboxSessionView)
def get_session(
    sandbox_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings
) -> SandboxSessionView:
    return SandboxSessionView.model_validate(
        service(db, context, settings).get_session(
            sandbox_session_id, include_all=context.principal.is_system_admin
        )
    )


@router.post("/tasks", response_model=SandboxTaskView, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: SandboxTaskCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> SandboxTaskView:
    return SandboxTaskView.model_validate(
        service(db, context, settings).create_task(
            payload, idempotency_key=idempotency_key
        )
    )


@router.get("/tasks", response_model=list[SandboxTaskView])
def list_tasks(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    chat_session_id: Annotated[str | None, Query(max_length=36)] = None,
) -> list[SandboxTaskView]:
    """List the current user's persisted sandbox task history in this workspace."""
    return [
        SandboxTaskView.model_validate(item)
        for item in service(db, context, settings).list_tasks(chat_session_id)
    ]


@router.get("/tasks/{task_id}", response_model=SandboxTaskView)
def get_task(
    task_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings
) -> SandboxTaskView:
    return SandboxTaskView.model_validate(service(db, context, settings).get_task(task_id))


@router.get("/tasks/{task_id}/executions", response_model=list[SandboxExecutionView])
def list_executions(
    task_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings
) -> list[SandboxExecutionView]:
    return [
        SandboxExecutionView.model_validate(item)
        for item in service(db, context, settings).executions(task_id)
    ]


@router.post("/tasks/{task_id}/cancel", response_model=SandboxTaskView)
def cancel_task(
    task_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings
) -> SandboxTaskView:
    return SandboxTaskView.model_validate(service(db, context, settings).cancel(task_id))


@router.post("/sessions/{sandbox_session_id}/cleanup", response_model=SandboxSessionView)
def cleanup_session(
    sandbox_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings
) -> SandboxSessionView:
    return SandboxSessionView.model_validate(
        service(db, context, settings).cleanup(
            sandbox_session_id, include_all=context.principal.is_system_admin
        )
    )


@router.get(
    "/workspace/entries",
    response_model=SessionWorkspaceListResponse,
)
def list_workspace_entries(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    chat_session_id: Annotated[str, Query(min_length=1, max_length=36)],
) -> SessionWorkspaceListResponse:
    require_agent_sandbox_permission(context)
    entries = agent_service(db, context, settings).list_workspace_entries(chat_session_id)
    return SessionWorkspaceListResponse(
        chat_session_id=chat_session_id,
        entries=[SessionWorkspaceEntryView.model_validate(item) for item in entries],
    )


@router.post(
    "/workspace/publish",
    response_model=SessionWorkspacePublishResponse,
)
def publish_workspace_file(
    payload: SessionWorkspacePublishRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SessionWorkspacePublishResponse:
    require_agent_sandbox_permission(context)
    result = agent_service(db, context, settings).publish_workspace_file(
        chat_session_id=payload.chat_session_id,
        path=payload.path,
        content=payload.content,
        title=payload.title,
        sandbox_session_id=payload.sandbox_session_id,
    )
    return SessionWorkspacePublishResponse.model_validate(result)


@router.get(
    "/authorizations",
    response_model=list[SandboxDestructiveGrantView],
)
def list_destructive_grants(
    db: DB,
    context: CurrentWorkspace,
    chat_session_id: Annotated[str, Query(min_length=1, max_length=36)],
) -> list[SandboxDestructiveGrantView]:
    authz = SandboxAuthorizationService(db, context.workspace_id, context.principal.user_id)
    grants = authz.list_grants(chat_session_id)
    return [
        SandboxDestructiveGrantView(
            id=item.id,
            chat_session_id=item.chat_session_id,
            sandbox_session_id=item.sandbox_session_id,
            action=item.action,
            path_prefix=item.path_prefix,
            status=item.status,
            granted_by=item.granted_by,
            expires_at=item.expires_at,
            reason=item.reason,
            created_at=item.created_at,
        )
        for item in grants
    ]


@router.post(
    "/authorizations",
    response_model=SandboxDestructiveGrantView,
    status_code=status.HTTP_201_CREATED,
)
def create_destructive_grant(
    payload: SandboxDestructiveGrantRequest,
    db: DB,
    context: CurrentWorkspace,
) -> SandboxDestructiveGrantView:
    # Any workspace writer may authorize session-local destructive actions.
    context.require_permission("workspace.write")
    authz = SandboxAuthorizationService(db, context.workspace_id, context.principal.user_id)
    grant = authz.grant(
        chat_session_id=payload.chat_session_id,
        path_prefix=payload.path_prefix,
        action=payload.action,
        sandbox_session_id=payload.sandbox_session_id,
        ttl_seconds=payload.ttl_seconds,
        reason=payload.reason,
    )
    return SandboxDestructiveGrantView(
        id=grant.id,
        chat_session_id=grant.chat_session_id,
        sandbox_session_id=grant.sandbox_session_id,
        action=grant.action,
        path_prefix=grant.path_prefix,
        status=grant.status,
        granted_by=grant.granted_by,
        expires_at=grant.expires_at,
        reason=grant.reason,
        created_at=grant.created_at,
    )


@router.post(
    "/authorizations/{grant_id}/revoke",
    response_model=SandboxDestructiveGrantView,
)
def revoke_destructive_grant(
    grant_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> SandboxDestructiveGrantView:
    context.require_permission("workspace.write")
    authz = SandboxAuthorizationService(db, context.workspace_id, context.principal.user_id)
    grant = authz.revoke(grant_id)
    return SandboxDestructiveGrantView(
        id=grant.id,
        chat_session_id=grant.chat_session_id,
        sandbox_session_id=grant.sandbox_session_id,
        action=grant.action,
        path_prefix=grant.path_prefix,
        status=grant.status,
        granted_by=grant.granted_by,
        expires_at=grant.expires_at,
        reason=grant.reason,
        created_at=grant.created_at,
    )
