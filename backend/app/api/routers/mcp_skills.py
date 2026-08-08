from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.schemas.extensions import (
    BuiltinToolView,
    ExtensionInvocationView,
    ExtensionRevokeRequest,
    ExternalCatalogSourceView,
    ExternalSkillSearchResponse,
    McpRegistrySearchResponse,
    MCPCapabilitySnapshotView,
    MCPInvokeRequest,
    MCPOAuthBeginRequest,
    MCPOAuthBeginView,
    MCPOAuthClientRegisterRequest,
    MCPOAuthClientRegistrationView,
    MCPOAuthCredentialView,
    MCPOAuthExchangeRequest,
    MCPOAuthRefreshRequest,
    MCPRefreshResponse,
    MCPServerCreateRequest,
    MCPServerUpdateRequest,
    MCPServerView,
    MCPStdioLaunchSpecRequest,
    MCPStdioLaunchSpecView,
    PermissionDecisionRequest,
    PermissionGrantView,
    SkillCreateRequest,
    SkillDeleteConfirmRequest,
    SkillDeleteRequestView,
    SkillFileContentView,
    SkillFileTreeView,
    SkillFileWriteRequest,
    SkillFileWriteResponse,
    SkillGitHubInstallRequest,
    SkillGitHubPreviewRequest,
    SkillGitHubPreviewResponse,
    SkillInvokeRequest,
    SkillLocalImportRequest,
    SkillLocalProbePolicyUpdate,
    SkillLocalProbePolicyView,
    SkillLocalProbeScanResponse,
    SkillArchiveImportRequest,
    SkillManualImportRequest,
    SkillMarketInstallRequest,
    SkillMarketListResponse,
    SkillMkdirRequest,
    SkillNpxImportRequest,
    SkillNpxImportResponse,
    SkillPackageCreateRequest,
    SkillSandboxRunRequest,
    SkillSandboxRunResponse,
    SkillSecurityScanResponse,
    SkillSemanticReviewRequest,
    SkillSemanticReviewResponse,
    SkillTranslateRequest,
    SkillTranslateResponse,
    SkillUpdateCheckResponse,
    SkillUpdateRequest,
    SkillValidateResponse,
    SkillView,
    TransportCapabilityView,
)
from app.services.mcp_skills import MCPAndSkillService
from app.services.skill_archive_import import SkillArchiveImportService
from app.services.skill_catalog_sources import ExternalCatalogService
from app.services.skill_github_import import SkillGitHubImportService
from app.services.skill_local_probe import SkillLocalProbeService
from app.services.skill_market import SkillMarketService
from app.services.skill_package import (
    SkillPackageService,
    ensure_official_skill_packages,
)
from app.services.skill_sandbox_run import SkillSandboxRunService
from app.services.skill_semantic_review import SkillSemanticReviewService
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
def list_builtin_tools(
    context: CurrentWorkspace,
) -> list[BuiltinToolView]:
    """List the reviewed LearnGraph domain tools available to declarative Skills."""

    del context
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


@router.post(
    "/mcp/servers/{server_id}/stdio-launch-spec",
    response_model=MCPStdioLaunchSpecView,
)
def register_stdio_launch_spec(
    server_id: str,
    payload: MCPStdioLaunchSpecRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPStdioLaunchSpecView:
    """注册已审 stdio 启动规范（未审批，不执行）。

    注册与运行分离：此处只记录 digest 固定的命令供后续审批；未审批的
    stdio 仍保持 UnavailableStdioMCPAdapter 默认拒绝语义。
    """
    manager = service(db, context, settings)
    server = manager.register_stdio_launch_spec(
        server_id,
        image_digest=payload.image_digest,
        command=payload.command,
    )
    return MCPStdioLaunchSpecView(
        server_id=server.id,
        workspace_id=server.workspace_id,
        image_digest=server.runner_image_digest,
        command=list(server.launch_command),
        launch_spec_hash=server.launch_spec_hash,
        launch_status=server.launch_status,
        launch_approved_by=server.launch_approved_by,
        launch_approved_at=server.launch_approved_at,
    )


@router.post(
    "/mcp/servers/{server_id}/stdio-launch-spec/approve",
    response_model=MCPStdioLaunchSpecView,
)
def approve_stdio_launch_spec(
    server_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPStdioLaunchSpecView:
    """审批已注册的 stdio 启动规范，使其可被隔离 runner 执行（可审计）。"""
    manager = service(db, context, settings)
    server = manager.approve_stdio_launch_spec(server_id)
    return MCPStdioLaunchSpecView(
        server_id=server.id,
        workspace_id=server.workspace_id,
        image_digest=server.runner_image_digest,
        command=list(server.launch_command),
        launch_spec_hash=server.launch_spec_hash,
        launch_status=server.launch_status,
        launch_approved_by=server.launch_approved_by,
        launch_approved_at=server.launch_approved_at,
    )


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


@router.post(
    "/mcp/servers/{server_id}/oauth/begin",
    response_model=MCPOAuthBeginView,
    status_code=status.HTTP_201_CREATED,
)
def begin_mcp_oauth(
    server_id: str,
    payload: MCPOAuthBeginRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPOAuthBeginView:
    """Start an OAuth authorization-code flow and return the PKCE auth URL."""
    return MCPOAuthBeginView.model_validate(
        service(db, context, settings).begin_server_oauth(
            server_id,
            auth_endpoint=payload.auth_endpoint,
            redirect_uri=payload.redirect_uri,
            scope=payload.scope,
            client_id=payload.client_id,
        )
    )


@router.post(
    "/mcp/servers/{server_id}/oauth/exchange",
    response_model=MCPOAuthCredentialView,
)
def exchange_mcp_oauth(
    server_id: str,
    payload: MCPOAuthExchangeRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPOAuthCredentialView:
    """Exchange an authorization code for a persisted OAuth token."""
    return MCPOAuthCredentialView.model_validate(
        service(db, context, settings).exchange_server_oauth(
            server_id,
            code=payload.code,
            state=payload.state,
            token_endpoint=payload.token_endpoint,
            client_id=payload.client_id,
            client_secret=(
                payload.client_secret.get_secret_value()
                if payload.client_secret is not None
                else None
            ),
        )
    )


@router.post(
    "/mcp/servers/{server_id}/oauth/refresh",
    response_model=MCPOAuthCredentialView,
)
def refresh_mcp_oauth(
    server_id: str,
    payload: MCPOAuthRefreshRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPOAuthCredentialView:
    """Refresh an OAuth access token under the single-flight lock."""
    view = service(db, context, settings).refresh_server_oauth_token(
        server_id,
        token_endpoint=payload.token_endpoint,
        force=payload.force,
    )
    if view is None:
        raise AppError(
            409,
            "mcp_oauth_not_configured",
            "This MCP server does not hold an OAuth authorization-code credential",
        )
    return MCPOAuthCredentialView.model_validate(view)


@router.post(
    "/mcp/servers/{server_id}/oauth/register",
    response_model=MCPOAuthClientRegistrationView,
    status_code=status.HTTP_201_CREATED,
)
def register_mcp_oauth_client(
    server_id: str,
    payload: MCPOAuthClientRegisterRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPOAuthClientRegistrationView:
    """Dynamically register an OAuth client for an explicitly trusted issuer."""
    return MCPOAuthClientRegistrationView.model_validate(
        service(db, context, settings).register_server_oauth_client(
            server_id,
            issuer=payload.issuer,
            registration_endpoint=payload.registration_endpoint,
            client_name=payload.client_name,
            redirect_uris=payload.redirect_uris,
            grant_types=payload.grant_types,
        )
    )


@router.post(
    "/mcp/servers/{server_id}/oauth/revoke",
    response_model=MCPOAuthCredentialView,
)
def revoke_mcp_oauth(
    server_id: str,
    payload: ExtensionRevokeRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPOAuthCredentialView:
    """Revoke the OAuth credential without disabling the MCP server."""
    view = service(db, context, settings).revoke_server_oauth(
        server_id,
        reason=payload.reason,
    )
    if view is None:
        raise AppError(
            409,
            "mcp_oauth_not_configured",
            "This MCP server does not hold an OAuth credential",
        )
    return MCPOAuthCredentialView.model_validate(view)


@router.get(
    "/mcp/servers/{server_id}/oauth/credential",
    response_model=MCPOAuthCredentialView | None,
)
def get_mcp_oauth_credential(
    server_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MCPOAuthCredentialView | None:
    """Return the redacted OAuth credential projection for one server."""
    view = service(db, context, settings).server_oauth_credential(server_id)
    if view is None:
        return None
    return MCPOAuthCredentialView.model_validate(view)


@router.delete(
    "/mcp/servers/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_mcp_server(
    server_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    reason: str = Query(default="workspace_user_deleted", max_length=1000),
) -> None:
    """彻底删除 MCP Server（含凭据、能力快照与授权记录）；撤销仅停用。"""
    context.require_permission("workspace.write")
    service(db, context, settings).delete_server(server_id, reason)


@router.get("/skills", response_model=list[SkillView])
def list_skills(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[SkillView]:
    """列出工作区 Skill。输出 Skill 元数据、版本和当前授权状态。

    GET 保持只读；官方（第一方）Skill 的安装/刷新由注册初始化、
    Agent 回合或 POST /skills/official-refresh 显式触发。
    """
    return [
        SkillView.model_validate(item)
        for item in service(db, context, settings).list_skills()
    ]


@router.post("/skills/official-refresh", response_model=list[SkillView])
def refresh_official_skills(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[SkillView]:
    """显式安装/刷新官方 Skill 包（幂等）。

    官方包初始化属于写操作，必须通过 POST 触发，避免读取接口产生提交。
    """
    context.require_permission("workspace.write")
    try:
        ensure_official_skill_packages(db, context.workspace_id, settings=settings)
        db.commit()
    except Exception:
        db.rollback()
        raise
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
    context.require_permission("workspace.write")
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
    context.require_permission("workspace.write")
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


@router.get("/skills/market/catalogs", response_model=list[ExternalCatalogSourceView])
def list_skill_catalog_sources(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[ExternalCatalogSourceView]:
    """列出已配置的外部 Skill/MCP 发现目录（聚合索引来源，只读）。"""
    return ExternalCatalogService(
        db, context.workspace_id, context.principal.user_id, settings
    ).catalog_sources()


@router.get("/skills/market/external-search", response_model=ExternalSkillSearchResponse)
def search_external_skill_catalog(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    catalog: str = Query(default="clawhub", max_length=32),
    q: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=10, ge=1, le=20),
) -> ExternalSkillSearchResponse:
    """搜索外部 Skill 目录（ClawHub / skills.sh）。只做发现，不直接安装。"""
    return ExternalCatalogService(
        db, context.workspace_id, context.principal.user_id, settings
    ).search_skills(catalog, q, limit=limit)


@router.get("/mcp/registry/search", response_model=McpRegistrySearchResponse)
def search_mcp_registry(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    q: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=10, ge=1, le=20),
) -> McpRegistrySearchResponse:
    """搜索官方 MCP Registry；返回可用于预填登记表单的服务器信息。"""
    return ExternalCatalogService(
        db, context.workspace_id, context.principal.user_id, settings
    ).search_mcp_registry(q, limit=limit)


@router.get("/mcp/registry/browse", response_model=McpRegistrySearchResponse)
def browse_mcp_registry(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    q: str = Query(default="", max_length=120),
    cursor: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=12, ge=1, le=20),
) -> McpRegistrySearchResponse:
    """浏览官方 MCP Registry 市场（游标分页；支持一键预填可用的远程服务器）。"""
    return ExternalCatalogService(
        db, context.workspace_id, context.principal.user_id, settings
    ).browse_mcp_registry(query=q, cursor=cursor, limit=limit)


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


@router.post(
    "/skills/import-archive",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
def import_skill_archive(
    payload: SkillArchiveImportRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    """从 zip 压缩包导入 Skill（仅文本文件；内容不在宿主执行）。"""
    context.require_permission("workspace.write")
    manager = SkillArchiveImportService(
        db, context.workspace_id, context.principal.user_id, settings
    )
    return manager.skill_view(manager.import_archive(payload))


@router.post(
    "/skills/npx-import",
    response_model=SkillNpxImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_skill_npx(
    payload: SkillNpxImportRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillNpxImportResponse:
    """解析 `npx skills add …` 命令并等价安装（服务端解析，不执行 npx）。"""
    context.require_permission("workspace.write")
    return SkillGitHubImportService(
        db, context.workspace_id, context.principal.user_id, settings
    ).install_from_command(payload)


def github_service(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillGitHubImportService:
    return SkillGitHubImportService(
        db, context.workspace_id, context.principal.user_id, settings
    )


@router.post("/skills/github/preview", response_model=SkillGitHubPreviewResponse)
def preview_skill_github(
    payload: SkillGitHubPreviewRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillGitHubPreviewResponse:
    """解析 GitHub 引用并列出可安装的 Skill 目录（含安装前权限预览）。"""
    context.require_permission("workspace.write")
    return github_service(db, context, settings).preview(payload)


@router.post(
    "/skills/github/install",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
def install_skill_github(
    payload: SkillGitHubInstallRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    """按固定 commit 从 GitHub 安装 Skill 全量文件；安装后仍需授权。"""
    context.require_permission("workspace.write")
    manager = github_service(db, context, settings)
    return manager.skill_view(manager.install(payload))


@router.post("/skills/{skill_id}/check-update", response_model=SkillUpdateCheckResponse)
def check_skill_update(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillUpdateCheckResponse:
    """对 GitHub 固定导入的 Skill 比较上游 commit，报告是否有更新。"""
    context.require_permission("workspace.write")
    return github_service(db, context, settings).check_update(skill_id)


@router.post("/skills/{skill_id}/upgrade", response_model=SkillView)
def upgrade_skill_from_upstream(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillView:
    """把 GitHub 固定导入的 Skill 升级到上游最新 commit；内容变化会失效授权。"""
    context.require_permission("workspace.write")
    manager = github_service(db, context, settings)
    return manager.skill_view(manager.upgrade(skill_id))


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
    context.require_permission("workspace.write")
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
    context.require_permission("workspace.write")
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
    context.require_permission("workspace.write")
    return package_service(db, context, settings).mkdir(skill_id, payload)


@router.post("/skills/{skill_id}/validate", response_model=SkillValidateResponse)
def validate_skill_package(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillValidateResponse:
    context.require_permission("workspace.write")
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


@router.post("/skills/{skill_id}/security-scan", response_model=SkillSecurityScanResponse)
def scan_skill_security(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillSecurityScanResponse:
    """静态安全扫描（审核第二层）：危险命令、注入诱饵、隐藏字符。结果为建议性。"""
    context.require_permission("workspace.write")
    return SkillSecurityScanResponse.model_validate(
        package_service(db, context, settings).security_scan(skill_id)
    )


@router.post(
    "/skills/{skill_id}/semantic-review",
    response_model=SkillSemanticReviewResponse,
)
def review_skill_semantics(
    skill_id: str,
    payload: SkillSemanticReviewRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillSemanticReviewResponse:
    """模型语义审核（审核第三层）：描述与行为一致性、检索诱饵、越权与隐瞒指令。"""
    context.require_permission("workspace.write")
    manager = SkillSemanticReviewService(
        db, context.workspace_id, context.principal.user_id, settings
    )
    return manager.review(skill_id, force=payload.force)


@router.post("/skills/{skill_id}/translate", response_model=SkillTranslateResponse)
def translate_skill_view(
    skill_id: str,
    payload: SkillTranslateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillTranslateResponse:
    """Translate skill documentation for viewing only; runtime stays on source text (D-081)."""
    context.require_permission("workspace.write")
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
    context.require_permission("workspace.write")
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
    # Grants are workspace-wide runtime decisions, not per-user self-service.
    context.require_permission("workspace.manage")
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
    # Invocation executes the Skill's builtin-tool steps, so it stays on the
    # same workspace.manage boundary as sandbox-run.
    context.require_permission("workspace.manage")
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
    context.require_permission("workspace.write")
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
    """Legacy direct-delete path is deliberately disabled by a hard-coded gate."""
    context.require_permission("workspace.write")
    del reason
    confirmation = service(db, context, settings).request_skill_deletion(
        skill_id,
        "legacy_delete_endpoint",
    )
    raise AppError(
        409,
        "skill_delete_confirmation_required",
        "Skill deletion requires a second confirmation by the user",
        {"confirmation_id": confirmation.id, "skill_name": confirmation.skill_name},
    )


@router.post(
    "/skills/{skill_id}/delete-request",
    response_model=SkillDeleteRequestView,
    status_code=status.HTTP_201_CREATED,
)
def request_skill_delete(
    skill_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    reason: str = Query(default="workspace_user_requested", max_length=1000),
) -> SkillDeleteRequestView:
    context.require_permission("workspace.write")
    return SkillDeleteRequestView.model_validate(
        service(db, context, settings).request_skill_deletion(skill_id, reason)
    )


@router.post(
    "/skills/delete-confirmations/{confirmation_id}/confirm",
    response_model=SkillDeleteRequestView,
)
def confirm_skill_delete(
    confirmation_id: str,
    payload: SkillDeleteConfirmRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SkillDeleteRequestView:
    context.require_permission("workspace.write")
    return SkillDeleteRequestView.model_validate(
        service(db, context, settings).confirm_skill_deletion(
            confirmation_id,
            confirmation_text=payload.confirmation_text,
            current_password=payload.current_password.get_secret_value(),
            principal=context.principal,
        )
    )


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
