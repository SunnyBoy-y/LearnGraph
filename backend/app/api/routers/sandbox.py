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
    SandboxAgentFileAppendRequest,
    SandboxAgentFileEditRequest,
    SandboxAgentEnvironmentRequest,
    SandboxAgentImagePublishRequest,
    SandboxAgentSessionCreateRequest,
    SandboxBootstrapJobView,
    SandboxBootstrapPolicyUpdateRequest,
    SandboxBootstrapPolicyView,
    SandboxBootstrapStartResponse,
    SandboxBootstrapStatusView,
    SandboxWebAppPublishRequest,
    SandboxWebAppPublishView,
    SandboxWebAppValidateRequest,
    SandboxWebAppValidationView,
    SandboxPreviewConfigUpdateRequest,
    SandboxPreviewConfigView,
    SubAppBundlePreviewView,
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
from app.repositories.audit import AuditRepository
from app.services.sandbox import (
    SandboxAgentWorkspaceService,
    SandboxTaskService,
    agent_sandbox_readiness,
)
from app.services.sandbox_authz import SandboxAuthorizationService
from app.services.sandbox_bootstrap import get_bootstrap_service
from app.services.sandbox_runtime import (
    effective_member_bootstrap_allowed,
    load_bootstrap_policy,
    save_bootstrap_policy,
)
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
    # Readiness is informational UX for every workspace member; the separate
    # bootstrap policy decides whether members may initialize the runtime.
    return SandboxAgentReadinessView.model_validate(
        agent_sandbox_readiness(
            settings,
            authorized="workspace.write" in context.permissions,
        )
    )


@router.get("/bootstrap/policy", response_model=SandboxBootstrapPolicyView)
def get_bootstrap_policy(
    context: CurrentWorkspace, settings: AppSettings
) -> SandboxBootstrapPolicyView:
    # Any workspace reader can see whether member bootstrap is enabled, so
    # banners and buttons can reflect the gate without failing.
    policy = load_bootstrap_policy(settings)
    return SandboxBootstrapPolicyView(
        member_allowed=effective_member_bootstrap_allowed(settings),
        persisted=policy is not None,
        updated_at=policy.updated_at if policy else None,
        updated_by=policy.updated_by if policy else None,
    )


@router.put("/bootstrap/policy", response_model=SandboxBootstrapPolicyView)
def update_bootstrap_policy(
    payload: SandboxBootstrapPolicyUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxBootstrapPolicyView:
    if not context.principal.is_system_admin:
        raise AppError(
            403,
            "deployment_admin_required",
            "Sandbox bootstrap policy requires a deployment administrator",
        )
    policy = save_bootstrap_policy(
        settings,
        member_allowed=payload.member_allowed,
        actor_id=context.principal.user_id,
    )
    AuditRepository(db, context.workspace_id).record(
        actor_id=context.principal.user_id,
        action="sandbox.bootstrap.policy_updated",
        resource_type="deployment_setting",
        resource_id="sandbox-bootstrap-policy",
        details={"member_allowed": policy.member_allowed},
    )
    db.commit()
    return SandboxBootstrapPolicyView(
        member_allowed=policy.member_allowed,
        persisted=True,
        updated_at=policy.updated_at,
        updated_by=policy.updated_by,
    )


@router.get("/preview-config", response_model=SandboxPreviewConfigView)
def get_preview_config(
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxPreviewConfigView:
    """Return the effective subapp preview origin and its resolution source.

    Any workspace reader may view it so the settings page can surface the value
    and remediation; only deployment administrators may change it.
    """
    from app.services.sandbox_preview_config import (
        effective_subapp_preview_origin,
        load_preview_config,
    )

    persisted = load_preview_config(settings)
    origin = effective_subapp_preview_origin(settings)
    if persisted is not None:
        source = "persisted"
    elif (settings.subapp_preview_origin or "").strip():
        source = "env"
    elif settings.subapp_preview_port:
        source = "auto"
    else:
        source = "none"
    return SandboxPreviewConfigView(
        origin=origin,
        source=source,
        persisted=persisted is not None,
        updated_at=persisted.updated_at if persisted else None,
        updated_by=persisted.updated_by if persisted else None,
    )


@router.put("/preview-config", response_model=SandboxPreviewConfigView)
def update_preview_config(
    payload: SandboxPreviewConfigUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxPreviewConfigView:
    """Persist the deployment-scoped subapp preview origin (admin only)."""
    if not context.principal.is_system_admin:
        raise AppError(
            403,
            "deployment_admin_required",
            "Subapp preview origin requires a deployment administrator",
        )
    from app.services.sandbox_preview_config import save_preview_config

    config = save_preview_config(
        settings, origin=payload.origin, actor_id=context.principal.user_id
    )
    AuditRepository(db, context.workspace_id).record(
        actor_id=context.principal.user_id,
        action="sandbox.preview_origin_updated",
        resource_type="deployment_setting",
        resource_id="sandbox-preview-config",
        details={"origin": config.origin},
    )
    db.commit()
    return SandboxPreviewConfigView(
        origin=config.origin,
        source="persisted",
        persisted=True,
        updated_at=config.updated_at,
        updated_by=config.updated_by,
    )


@router.post("/bootstrap", response_model=SandboxBootstrapStartResponse)
def start_bootstrap(
    context: CurrentWorkspace, settings: AppSettings
) -> SandboxBootstrapStartResponse:
    # Members may initialize the sandbox runtime by default; administrators
    # can restrict it via the bootstrap policy toggle (default: allowed).
    if not effective_member_bootstrap_allowed(settings):
        if not (
            context.principal.is_system_admin
            or "workspace.manage" in context.permissions
        ):
            raise AppError(
                403,
                "sandbox_bootstrap_admin_required",
                "当前工作区已限制沙箱初始化权限，请联系管理员开启「允许普通成员初始化沙箱」后重试。",
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


@router.post("/agent/environment")
def get_agent_environment(
    payload: SandboxAgentEnvironmentRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    require_agent_sandbox_permission(context)
    return agent_service(db, context, settings).environment_info(payload)


@router.post("/agent/files/publish-image")
def publish_agent_image(
    payload: SandboxAgentImagePublishRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> dict:
    require_agent_sandbox_permission(context)
    return agent_service(db, context, settings).publish_image(payload)


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


@router.post("/agent/files/append", response_model=SandboxAgentFileView)
def append_agent_file(
    payload: SandboxAgentFileAppendRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxAgentFileView:
    require_agent_sandbox_permission(context)
    return SandboxAgentFileView.model_validate(
        agent_service(db, context, settings).append_file(payload)
    )


@router.post("/agent/files/edit", response_model=SandboxAgentFileView)
def edit_agent_file(
    payload: SandboxAgentFileEditRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxAgentFileView:
    require_agent_sandbox_permission(context)
    return SandboxAgentFileView.model_validate(
        agent_service(db, context, settings).edit_file(payload)
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


@router.post("/workspace/web-app/validate", response_model=SandboxWebAppValidationView)
def validate_web_app(
    payload: SandboxWebAppValidateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxWebAppValidationView:
    require_agent_sandbox_permission(context)
    from app.services.subapp_bundles import SubAppBundleService

    result = SubAppBundleService(
        db, context.workspace_id, context.principal.user_id, settings
    ).validate(
        chat_session_id=payload.chat_session_id,
        sandbox_session_id=payload.sandbox_session_id,
        output_root=payload.output_root,
        entry_path=payload.entry_path,
    )
    return SandboxWebAppValidationView.model_validate(result)


@router.post("/workspace/web-app/publish", response_model=SandboxWebAppPublishView)
def publish_web_app(
    payload: SandboxWebAppPublishRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SandboxWebAppPublishView:
    require_agent_sandbox_permission(context)
    from app.services.subapp_bundles import SubAppBundleService

    result = SubAppBundleService(
        db, context.workspace_id, context.principal.user_id, settings
    ).publish(
        validation_id=payload.validation_id,
        chat_session_id=payload.chat_session_id,
        sandbox_session_id=payload.sandbox_session_id,
        title=payload.title,
        preferred_height=payload.preferred_height,
    )
    return SandboxWebAppPublishView.model_validate(result)


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
        command_intent_digest=payload.command_intent_digest,
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
