from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Query, Request, status

from app.api.deps import AppSettings, CurrentPrincipal, CurrentWorkspace, DB
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import SlidingWindowRateLimiter, enforce_auth_rate_limit
from app.domain.models import User
from app.domain.schemas.auth import (
    ACLGrantRequest,
    ACLView,
    AuthSessionView,
    AccountDeletionImpact,
    ChangePasswordRequest,
    CurrentUserView,
    DemoLoginRequest,
    DemoLoginResponse,
    DeleteAccountRequest,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    MembershipCreateRequest,
    MembershipUpdateRequest,
    MembershipView,
    OrganizationCreateRequest,
    OrganizationView,
    PermissionView,
    RoleCreateRequest,
    RoleUpdateRequest,
    RoleView,
    UserCreateRequest,
    UserStatusUpdate,
    UserView,
    WorkspaceSelectionResponse,
    WorkspaceView,
)
from app.domain.schemas.common import ActionResponse
from app.services.auth import AuthService
from app.services.authorization import AuthorizationService, IdentityManagementService


router = APIRouter(tags=["authentication-rbac"])


@lru_cache(maxsize=1)
def _auth_rate_limiter() -> SlidingWindowRateLimiter:
    settings = get_settings()
    return SlidingWindowRateLimiter(
        max_requests=settings.auth_rate_limit_max,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )


def _enforce_auth_rate_limit(request: Request) -> None:
    enforce_auth_rate_limit(request, _auth_rate_limiter())


def _request_metadata(request: Request) -> tuple[str, str, str]:
    ip_address = request.client.host if request.client is not None else ""
    device_id = request.headers.get("x-device-id", "")
    return request.headers.get("user-agent", ""), ip_address, device_id


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, settings: AppSettings, db: DB) -> LoginResponse:
    """用户登录。输入用户名和密码，输出可撤销的 Bearer 会话、当前用户和可选工作区信息。"""
    _enforce_auth_rate_limit(request)
    user_agent, ip_address, device_id = _request_metadata(request)
    return AuthService(db, settings).login(
        payload,
        user_agent=user_agent,
        ip_address=ip_address,
        device_id=device_id,
    )


@router.post("/auth/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, settings: AppSettings, db: DB) -> LoginResponse:
    """注册用户。输入注册资料，创建用户并返回初始认证会话。"""
    _enforce_auth_rate_limit(request)
    user_agent, ip_address, device_id = _request_metadata(request)
    return AuthService(db, settings).register(
        payload,
        user_agent=user_agent,
        ip_address=ip_address,
        device_id=device_id,
    )


@router.post("/auth/demo-login", response_model=DemoLoginResponse)
def demo_login(
    payload: DemoLoginRequest,
    request: Request,
    settings: AppSettings,
    db: DB,
) -> DemoLoginResponse:
    """开发环境 Demo 登录。输入固定的本地演示账号，输出正常的、可撤销的认证会话。"""

    _enforce_auth_rate_limit(request)
    user_agent, ip_address, device_id = _request_metadata(request)
    return AuthService(db, settings).demo_login(
        payload,
        user_agent=user_agent,
        ip_address=ip_address,
        device_id=device_id,
    )


@router.get("/auth/me", response_model=CurrentUserView)
def current_user(principal: CurrentPrincipal, db: DB) -> CurrentUserView:
    """获取当前登录用户。无请求体，输出用户身份、状态、会话 ID 和密码变更提示。"""
    user = db.get(User, principal.user_id)
    if user is None or user.tenant_id != principal.tenant_id:
        raise AppError(401, "identity_inactive", "The authenticated identity is not active")
    return CurrentUserView(
        id=user.id,
        tenant_id=user.tenant_id,
        username=user.username,
        email=user.email,
        display_name=user.display_name or user.username,
        status=user.status,
        is_system_admin=user.is_system_admin,
        must_change_password=user.must_change_password,
        session_id=principal.session_id,
    )


@router.post("/auth/logout", response_model=ActionResponse)
def logout(principal: CurrentPrincipal, settings: AppSettings, db: DB) -> ActionResponse:
    """退出当前会话。无请求体，撤销当前 Bearer 会话并返回操作状态。"""
    AuthService(db, settings).logout(principal)
    return ActionResponse(status="revoked", message="Current authentication session was revoked")


@router.get("/auth/sessions", response_model=list[AuthSessionView])
def list_auth_sessions(
    principal: CurrentPrincipal, settings: AppSettings, db: DB
) -> list[AuthSessionView]:
    return AuthService(db, settings).sessions(principal)


@router.delete("/auth/sessions/{session_id}", response_model=ActionResponse)
def revoke_auth_session(
    session_id: str,
    principal: CurrentPrincipal,
    settings: AppSettings,
    db: DB,
) -> ActionResponse:
    AuthService(db, settings).revoke_session(session_id, principal)
    return ActionResponse(
        status="revoked",
        message="Authentication session was revoked",
        resource_id=session_id,
    )


@router.post("/auth/change-password", response_model=ActionResponse)
def change_password(
    payload: ChangePasswordRequest,
    principal: CurrentPrincipal,
    settings: AppSettings,
    db: DB,
) -> ActionResponse:
    AuthService(db, settings).change_password(payload, principal)
    return ActionResponse(
        status="updated",
        message="Password was changed and all other sessions were revoked",
        resource_id=principal.user_id,
    )


@router.get("/auth/account/deletion-impact", response_model=AccountDeletionImpact)
def account_deletion_impact(
    principal: CurrentPrincipal,
    settings: AppSettings,
    db: DB,
) -> AccountDeletionImpact:
    return AuthService(db, settings).account_deletion_impact(principal)


@router.post("/auth/delete-account", response_model=ActionResponse)
def delete_account(
    payload: DeleteAccountRequest,
    principal: CurrentPrincipal,
    settings: AppSettings,
    db: DB,
) -> ActionResponse:
    AuthService(db, settings).delete_account(payload, principal)
    return ActionResponse(
        status="deleted",
        message="Account identity was deleted and all authentication sessions were revoked",
        resource_id=principal.user_id,
    )


@router.get("/workspaces", response_model=list[WorkspaceView])
def list_workspaces(principal: CurrentPrincipal, db: DB) -> list[WorkspaceView]:
    """列出当前用户可访问的工作区。无请求体，输出工作区及成员关系摘要。"""
    return [
        WorkspaceView.model_validate(item)
        for item in AuthorizationService(db, principal).list_workspaces()
    ]


@router.post("/workspaces/{workspace_id}/select", response_model=WorkspaceSelectionResponse)
def select_workspace(
    workspace_id: str, principal: CurrentPrincipal, db: DB
) -> WorkspaceSelectionResponse:
    """选择工作区。输入路径中的工作区 ID，输出工作区信息；后续资源请求仍需发送该 ID Header。"""
    workspace = next(
        (
            item
            for item in AuthorizationService(db, principal).list_workspaces()
            if item.id == workspace_id
        ),
        None,
    )
    if workspace is None:
        raise AppError(403, "workspace_forbidden", "Workspace is not accessible to this user")
    return WorkspaceSelectionResponse(workspace=WorkspaceView.model_validate(workspace))


@router.get("/permissions", response_model=list[PermissionView])
def list_permissions(principal: CurrentPrincipal, db: DB) -> list[PermissionView]:
    return [
        PermissionView.model_validate(item)
        for item in IdentityManagementService(db, principal).permissions()
    ]


@router.get("/users", response_model=list[UserView])
def list_users(principal: CurrentPrincipal, db: DB) -> list[UserView]:
    return [UserView.model_validate(item) for item in IdentityManagementService(db, principal).users()]


@router.post("/users", response_model=UserView, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest, principal: CurrentPrincipal, db: DB
) -> UserView:
    return UserView.model_validate(IdentityManagementService(db, principal).create_user(payload))


@router.patch("/users/{user_id}/status", response_model=UserView)
def update_user_status(
    user_id: str,
    payload: UserStatusUpdate,
    principal: CurrentPrincipal,
    db: DB,
) -> UserView:
    return UserView.model_validate(
        IdentityManagementService(db, principal).update_user_status(user_id, payload)
    )


@router.get("/organizations", response_model=list[OrganizationView])
def list_organizations(principal: CurrentPrincipal, db: DB) -> list[OrganizationView]:
    return [
        OrganizationView.model_validate(item)
        for item in IdentityManagementService(db, principal).organizations()
    ]


@router.post(
    "/organizations",
    response_model=OrganizationView,
    status_code=status.HTTP_201_CREATED,
)
def create_organization(
    payload: OrganizationCreateRequest, principal: CurrentPrincipal, db: DB
) -> OrganizationView:
    return OrganizationView.model_validate(
        IdentityManagementService(db, principal).create_organization(payload)
    )


@router.get("/organizations/{organization_id}/roles", response_model=list[RoleView])
def list_roles(
    organization_id: str, principal: CurrentPrincipal, db: DB
) -> list[RoleView]:
    return IdentityManagementService(db, principal).roles(organization_id)


@router.post(
    "/organizations/{organization_id}/roles",
    response_model=RoleView,
    status_code=status.HTTP_201_CREATED,
)
def create_role(
    organization_id: str,
    payload: RoleCreateRequest,
    principal: CurrentPrincipal,
    db: DB,
) -> RoleView:
    return IdentityManagementService(db, principal).create_role(organization_id, payload)


@router.patch("/organizations/{organization_id}/roles/{role_id}", response_model=RoleView)
def update_role(
    organization_id: str,
    role_id: str,
    payload: RoleUpdateRequest,
    principal: CurrentPrincipal,
    db: DB,
) -> RoleView:
    return IdentityManagementService(db, principal).update_role(
        organization_id, role_id, payload
    )


@router.get(
    "/organizations/{organization_id}/memberships",
    response_model=list[MembershipView],
)
def list_memberships(
    organization_id: str, principal: CurrentPrincipal, db: DB
) -> list[MembershipView]:
    return IdentityManagementService(db, principal).memberships(organization_id)


@router.post(
    "/organizations/{organization_id}/memberships",
    response_model=MembershipView,
    status_code=status.HTTP_201_CREATED,
)
def add_membership(
    organization_id: str,
    payload: MembershipCreateRequest,
    principal: CurrentPrincipal,
    db: DB,
) -> MembershipView:
    return IdentityManagementService(db, principal).add_membership(organization_id, payload)


@router.patch(
    "/organizations/{organization_id}/memberships/{membership_id}",
    response_model=MembershipView,
)
def update_membership(
    organization_id: str,
    membership_id: str,
    payload: MembershipUpdateRequest,
    principal: CurrentPrincipal,
    db: DB,
) -> MembershipView:
    return IdentityManagementService(db, principal).update_membership(
        organization_id, membership_id, payload
    )


@router.get("/acl", response_model=list[ACLView])
def list_acl(context: CurrentWorkspace, db: DB) -> list[ACLView]:
    return [
        ACLView.model_validate(item)
        for item in IdentityManagementService(db, context.principal).list_acls(context.workspace)
    ]


@router.post("/acl", response_model=ACLView, status_code=status.HTTP_201_CREATED)
def grant_acl(payload: ACLGrantRequest, context: CurrentWorkspace, db: DB) -> ACLView:
    return ACLView.model_validate(
        IdentityManagementService(db, context.principal).grant_acl(context.workspace, payload)
    )


@router.delete("/acl/{acl_id}", response_model=ACLView)
def revoke_acl(acl_id: str, context: CurrentWorkspace, db: DB) -> ACLView:
    return ACLView.model_validate(
        IdentityManagementService(db, context.principal).revoke_acl(context.workspace, acl_id)
    )


@router.get("/acl/evaluate")
def evaluate_acl(
    context: CurrentWorkspace,
    db: DB,
    resource_type: str = Query(min_length=1, max_length=80),
    resource_id: str = Query(min_length=1, max_length=80),
    permission: str = Query(pattern=r"^(read|write|delete|share)$"),
) -> dict[str, str | bool]:
    allowed = AuthorizationService(db, context.principal).can_access_resource(
        context.workspace, resource_type, resource_id, permission
    )
    return {
        "workspace_id": context.workspace_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "permission": permission,
        "allowed": allowed,
    }
