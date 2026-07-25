from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.extensions import (
    BuiltinToolView,
    ExtensionInvocationView,
    ExtensionRevokeRequest,
    MCPCapabilitySnapshotView,
    MCPInvokeRequest,
    MCPRefreshResponse,
    MCPServerCreateRequest,
    MCPServerUpdateRequest,
    MCPServerView,
    PermissionDecisionRequest,
    PermissionGrantView,
    SkillCreateRequest,
    SkillFileContentView,
    SkillFileTreeView,
    SkillFileWriteRequest,
    SkillFileWriteResponse,
    SkillInvokeRequest,
    SkillLocalImportRequest,
    SkillLocalProbePolicyUpdate,
    SkillLocalProbePolicyView,
    SkillLocalProbeScanResponse,
    SkillManualImportRequest,
    SkillMarketInstallRequest,
    SkillMarketListResponse,
    SkillMkdirRequest,
    SkillPackageCreateRequest,
    SkillSandboxRunRequest,
    SkillSandboxRunResponse,
    SkillTranslateRequest,
    SkillTranslateResponse,
    SkillUpdateRequest,
    SkillValidateResponse,
    SkillView,
    TransportCapabilityView,
)
from app.services.mcp_skills import MCPAndSkillService
from app.services.skill_local_probe import SkillLocalProbeService
from app.services.skill_market import SkillMarketService
from app.services.skill_package import SkillPackageService
from app.services.skill_sandbox_run import SkillSandboxRunService
from app.services.skill_translation import SkillTranslationService


router = APIRouter(tags=["mcp-skills"])


def service(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPAndSkillService:
    return MCPAndSkillService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
        workspace=context.workspace,
        principal=context.principal,
    )


def server_view(item, manager: MCPAndSkillService) -> MCPServerView:
    return MCPServerView.model_validate(manager.server_view_data(item))


@router.get("/skills/builtin-tools", response_model=list[BuiltinToolView])
def list_builtin_tools() -> list[BuiltinToolView]:
    """List the reviewed LearnGraph domain tools available to declarative Skills."""

    return [
        BuiltinToolView.model_validate(item)
        for item in MCPAndSkillService.builtin_tool_catalog()
    ]


@router.get("/mcp/transport-capabilities", response_model=list[TransportCapabilityView])
def transport_capabilities(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[TransportCapabilityView]:
    """查询 MCP 传输能力。无请求体，输出当前实现支持的协议、认证方式和可用状态。"""
    del db, context, settings
    return [
        TransportCapabilityView.model_validate(item)
        for item in MCPAndSkillService.transport_capabilities()
    ]


@router.get("/mcp/servers", response_model=list[MCPServerView])
def list_mcp_servers(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[MCPServerView]:
    """列出工作区 MCP Server。无请求体，输出已登记 Server 的安全元数据，不返回密钥。"""
    manager = service(db, context, settings)
    return [server_view(item, manager) for item in manager.list_servers()]


@router.post(
    "/mcp/servers",
    response_model=MCPServerView,
    status_code=status.HTTP_201_CREATED,
)
def register_mcp_server(
    payload: MCPServerCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPServerView:
    """登记 MCP Server。输入名称、地址、传输和认证配置，输出脱敏后的 Server 记录。"""
    manager = service(db, context, settings)
    return server_view(manager.create_server(payload), manager)


@router.get("/mcp/servers/{server_id}", response_model=MCPServerView)
def get_mcp_server(
    server_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPServerView:
    manager = service(db, context, settings)
    return server_view(manager.require_server(server_id), manager)


@router.put("/mcp/servers/{server_id}", response_model=MCPServerView)
def update_mcp_server(
    server_id: str,
    payload: MCPServerUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPServerView:
    manager = service(db, context, settings)
    return server_view(manager.update_server(server_id, payload), manager)


@router.post("/mcp/servers/{server_id}/refresh", response_model=MCPRefreshResponse)
def refresh_mcp_server(
    server_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPRefreshResponse:
    """刷新 MCP 能力快照。输入 Server ID，重新发现远端工具并输出 Server 与能力快照。"""
    manager = service(db, context, settings)
    server = manager.require_server(server_id)
    snapshot = manager.refresh_server(server_id)
    return MCPRefreshResponse(
        server=server_view(server, manager),
        snapshot=MCPCapabilitySnapshotView.model_validate(snapshot),
    )


@router.get(
    "/mcp/servers/{server_id}/snapshots",
    response_model=list[MCPCapabilitySnapshotView],
)
def list_mcp_snapshots(
    server_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[MCPCapabilitySnapshotView]:
    return [
        MCPCapabilitySnapshotView.model_validate(item)
        for item in service(db, context, settings).snapshots_for_server(server_id)
    ]


@router.post(
    "/mcp/servers/{server_id}/authorize",
    response_model=PermissionGrantView,
    status_code=status.HTTP_201_CREATED,
)
def authorize_mcp_server(
    server_id: str,
    payload: PermissionDecisionRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PermissionGrantView:
    return PermissionGrantView.model_validate(
        service(db, context, settings).authorize_server(server_id, payload)
    )


@router.post(
    "/mcp/servers/{server_id}/invoke",
    response_model=ExtensionInvocationView,
    status_code=status.HTTP_201_CREATED,
)
def invoke_mcp_tool(
    server_id: str,
    payload: MCPInvokeRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ExtensionInvocationView:
    return ExtensionInvocationView.model_validate(
        service(db, context, settings).invoke_mcp(server_id, payload)
    )


@router.post("/mcp/servers/{server_id}/revoke", response_model=MCPServerView)
def revoke_mcp_server(
    server_id: str,
    payload: ExtensionRevokeRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPServerView:
    manager = service(db, context, settings)
    return server_view(manager.revoke_server(server_id, payload.reason), manager)


@router.get("/skills", response_model=list[SkillView])
def list_skills(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[SkillView]:
    """列出工作区 Skill。输出 Skill 元数据、版本和当前授权状态。"""
    return [
        SkillView.model_validate(item)
        for item in service(db, context, settings).list_skills()
    ]


@router.post("/skills", response_model=SkillView, status_code=status.HTTP_201_CREATED)
def install_skill(
    payload: SkillCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    """安装 Skill。输入 Skill 清单所需字段，输出已持久化的 Skill 元数据；不会执行未知代码。"""
    return SkillView.model_validate(service(db, context, settings).create_skill(payload))


def package_service(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillPackageService:
    return SkillPackageService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
    )


@router.post(
    "/skills/packages",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
def create_skill_package(
    payload: SkillPackageCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    """Create an agent_skill_package with a SKILL.md template (and optional sample script)."""
    manager = package_service(db, context, settings)
    return manager.skill_view(manager.create_package(payload))


@router.get("/skills/market", response_model=SkillMarketListResponse)
def list_skill_market(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    refresh: bool = Query(default=False),
    q: str = Query(default="", max_length=120),
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=12, ge=1, le=48),
) -> SkillMarketListResponse:
    manager = SkillMarketService(
        db, context.workspace_id, context.principal.user_id, settings
    )
    return manager.list_cards(
        refresh=refresh, query=q, page=page, page_size=page_size
    )


@router.post(
    "/skills/market/install",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
def install_skill_from_market(
    payload: SkillMarketInstallRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    context.require_permission("workspace.write")
    manager = SkillMarketService(
        db, context.workspace_id, context.principal.user_id, settings
    )
    return manager.skill_view(manager.install(payload))


@router.post(
    "/skills/import",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
def import_skill_manual(
    payload: SkillManualImportRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    """Import a user-edited skill package (files with SKILL.md)."""
    context.require_permission("workspace.write")
    manager = SkillMarketService(
        db, context.workspace_id, context.principal.user_id, settings
    )
    return manager.skill_view(manager.import_manual(payload))


@router.get("/skills/local-probe/policy", response_model=SkillLocalProbePolicyView)
def get_local_probe_policy(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillLocalProbePolicyView:
    return SkillLocalProbeService(
        db, context.workspace_id, context.principal.user_id, settings
    ).get_policy()


@router.put("/skills/local-probe/policy", response_model=SkillLocalProbePolicyView)
def update_local_probe_policy(
    payload: SkillLocalProbePolicyUpdate,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillLocalProbePolicyView:
    context.require_permission("workspace.write")
    return SkillLocalProbeService(
        db, context.workspace_id, context.principal.user_id, settings
    ).update_policy(payload)


@router.post("/skills/local-probe/scan", response_model=SkillLocalProbeScanResponse)
def scan_local_skills(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillLocalProbeScanResponse:
    context.require_permission("workspace.write")
    return SkillLocalProbeService(
        db, context.workspace_id, context.principal.user_id, settings
    ).scan()


@router.post(
    "/skills/local-probe/import",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
def import_local_skill(
    payload: SkillLocalImportRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    context.require_permission("workspace.write")
    manager = SkillLocalProbeService(
        db, context.workspace_id, context.principal.user_id, settings
    )
    return manager.skill_view(manager.import_local(payload))


@router.get("/skills/{skill_id}/files", response_model=SkillFileTreeView)
def list_skill_files(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillFileTreeView:
    return package_service(db, context, settings).list_files(skill_id)


@router.get("/skills/{skill_id}/files/{file_path:path}", response_model=SkillFileContentView)
def read_skill_file(
    skill_id: str,
    file_path: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillFileContentView:
    return package_service(db, context, settings).read_file(skill_id, file_path)


@router.put("/skills/{skill_id}/files/{file_path:path}", response_model=SkillFileWriteResponse)
def write_skill_file(
    skill_id: str,
    file_path: str,
    payload: SkillFileWriteRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillFileWriteResponse:
    manager = package_service(db, context, settings)
    skill, file_view, reauth = manager.write_file(skill_id, file_path, payload)
    return SkillFileWriteResponse(
        skill=manager.skill_view(skill),
        file=file_view,
        reauthorization_required=reauth,
    )


@router.delete("/skills/{skill_id}/files/{file_path:path}", response_model=SkillView)
def delete_skill_file(
    skill_id: str,
    file_path: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    manager = package_service(db, context, settings)
    return manager.skill_view(manager.delete_file(skill_id, file_path))


@router.post("/skills/{skill_id}/files/mkdir", response_model=SkillFileTreeView)
def mkdir_skill_path(
    skill_id: str,
    payload: SkillMkdirRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillFileTreeView:
    return package_service(db, context, settings).mkdir(skill_id, payload)


@router.post("/skills/{skill_id}/validate", response_model=SkillValidateResponse)
def validate_skill_package(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillValidateResponse:
    return package_service(db, context, settings).validate(skill_id)


@router.post("/skills/{skill_id}/sandbox-run", response_model=SkillSandboxRunResponse)
def run_skill_script(
    skill_id: str,
    payload: SkillSandboxRunRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillSandboxRunResponse:
    """Trial-run a package script in Docker sandbox only (D-080). Never uses host subprocess."""
    # Same boundary as sandbox agent file/command APIs.
    context.require_permission("workspace.manage")
    manager = SkillSandboxRunService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
        workspace=context.workspace,
        principal=context.principal,
    )
    return manager.run(skill_id, payload)


@router.post("/skills/{skill_id}/translate", response_model=SkillTranslateResponse)
def translate_skill_view(
    skill_id: str,
    payload: SkillTranslateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillTranslateResponse:
    """Translate skill documentation for viewing only; runtime stays on source text (D-081)."""
    manager = SkillTranslationService(
        db, context.workspace_id, context.principal.user_id, settings
    )
    return manager.translate(skill_id, payload)


@router.get("/skills/{skill_id}", response_model=SkillView)
def get_skill(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    return SkillView.model_validate(service(db, context, settings).require_skill(skill_id))


@router.put("/skills/{skill_id}", response_model=SkillView)
def update_skill(
    skill_id: str,
    payload: SkillUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    return SkillView.model_validate(
        service(db, context, settings).update_skill(skill_id, payload)
    )


@router.post(
    "/skills/{skill_id}/authorize",
    response_model=PermissionGrantView,
    status_code=status.HTTP_201_CREATED,
)
def authorize_skill(
    skill_id: str,
    payload: PermissionDecisionRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PermissionGrantView:
    return PermissionGrantView.model_validate(
        service(db, context, settings).authorize_skill(skill_id, payload)
    )


@router.post(
    "/skills/{skill_id}/invoke",
    response_model=ExtensionInvocationView,
    status_code=status.HTTP_201_CREATED,
)
def invoke_skill(
    skill_id: str,
    payload: SkillInvokeRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ExtensionInvocationView:
    return ExtensionInvocationView.model_validate(
        service(db, context, settings).invoke_skill(skill_id, payload)
    )


@router.post("/skills/{skill_id}/revoke", response_model=SkillView)
def revoke_skill(
    skill_id: str,
    payload: ExtensionRevokeRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    return SkillView.model_validate(
        service(db, context, settings).revoke_skill(skill_id, payload.reason)
    )


@router.delete(
    "/skills/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_skill(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    reason: str = Query(default="workspace_user_deleted", max_length=1000),
) -> None:
    """Permanently delete a workspace Skill and its package files. Revoke only disables authorization."""
    context.require_permission("workspace.write")
    service(db, context, settings).delete_skill(skill_id, reason)


@router.get("/extension-permission-grants", response_model=list[PermissionGrantView])
def list_permission_grants(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    subject_type: str | None = Query(default=None, max_length=32),
    subject_id: str | None = Query(default=None, max_length=36),
) -> list[PermissionGrantView]:
    return [
        PermissionGrantView.model_validate(item)
        for item in service(db, context, settings).list_grants(subject_type, subject_id)
    ]


@router.get("/extension-invocations", response_model=list[ExtensionInvocationView])
def list_extension_invocations(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    target_type: str | None = Query(default=None, max_length=32),
    target_id: str | None = Query(default=None, max_length=36),
) -> list[ExtensionInvocationView]:
    return [
        ExtensionInvocationView.model_validate(item)
        for item in service(db, context, settings).list_invocations(target_type, target_id)
    ]
