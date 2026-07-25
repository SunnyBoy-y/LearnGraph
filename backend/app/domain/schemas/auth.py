from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.schemas.common import ORMModel


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str | None = Field(default=None, max_length=320)
    display_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=1024)


class DemoLoginRequest(LoginRequest):
    pass


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    session_id: str
    user_id: str
    username: str
    display_name: str
    default_workspace_id: str | None
    must_change_password: bool
    demo_only: bool = False


class DemoLoginResponse(LoginResponse):
    demo_only: bool = True


class CurrentUserView(BaseModel):
    id: str
    tenant_id: str
    username: str
    email: str | None
    display_name: str
    status: str
    is_system_admin: bool
    must_change_password: bool
    session_id: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class DeleteAccountRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    confirmation: str = Field(min_length=1, max_length=120)


class AccountDeletionImpact(BaseModel):
    can_delete: bool
    blockers: list[str]
    active_session_count: int
    active_membership_count: int
    personal_workspace_count: int
    owned_organization_count: int


class AuthSessionView(ORMModel):
    id: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None
    revoked_reason: str
    user_agent: str
    ip_address: str
    current: bool = False


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str | None = Field(default=None, max_length=320)
    display_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=1024)
    is_system_admin: bool = False


class UserStatusUpdate(BaseModel):
    status: Literal["active", "disabled"]


class UserView(ORMModel):
    id: str
    tenant_id: str
    username: str
    email: str | None
    display_name: str
    status: str
    is_system_admin: bool
    must_change_password: bool
    created_at: datetime


class OrganizationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    workspace_name: str | None = Field(default=None, min_length=1, max_length=160)


class OrganizationView(ORMModel):
    id: str
    tenant_id: str
    name: str
    owner_user_id: str
    status: str
    created_at: datetime


class PermissionView(ORMModel):
    id: str
    key: str
    description: str


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=300)
    permission_keys: list[str] = Field(default_factory=list, max_length=50)


class RoleUpdateRequest(BaseModel):
    description: str | None = Field(default=None, max_length=300)
    permission_keys: list[str] | None = Field(default=None, max_length=50)


class RoleView(BaseModel):
    id: str
    tenant_id: str
    organization_id: str
    name: str
    description: str
    is_system: bool
    permission_keys: list[str]
    created_at: datetime


class MembershipCreateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    role_id: str = Field(min_length=1, max_length=64)


class MembershipUpdateRequest(BaseModel):
    role_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: Literal["active", "revoked"] | None = None


class MembershipView(BaseModel):
    id: str
    tenant_id: str
    organization_id: str
    user_id: str
    username: str
    display_name: str
    role_id: str
    role_name: str
    status: str
    joined_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ACLGrantRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    resource_id: str = Field(min_length=1, max_length=80)
    grantee_type: Literal["user", "role", "organization"]
    grantee_id: str = Field(min_length=1, max_length=64)
    permissions: list[Literal["read", "write", "delete", "share"]] = Field(
        min_length=1, max_length=4
    )


class ACLView(ORMModel):
    id: str
    workspace_id: str
    tenant_id: str
    resource_type: str
    resource_id: str
    grantee_type: str
    grantee_id: str
    permissions: list[str]
    granted_by: str
    revoked_at: datetime | None
    created_at: datetime


class WorkspaceView(ORMModel):
    id: str
    tenant_id: str
    owner_user_id: str
    organization_id: str | None
    workspace_kind: str
    name: str
    description: str
    created_at: datetime


class WorkspaceSelectionResponse(BaseModel):
    workspace: WorkspaceView
    header_name: str = "X-Workspace-ID"


class DashboardMetric(BaseModel):
    key: str
    label: str
    value: int | float | str
    status: str = "normal"


class DashboardResponse(BaseModel):
    workspace_id: str
    metrics: list[DashboardMetric]
    next_actions: list["DashboardAction"]
    system_status: dict[str, str]


class DashboardAction(BaseModel):
    id: str
    title: str
    description: str = ""
    status: str
    source: str
    action_type: str
    project_id: str | None = None
    goal_id: str | None = None
    graph_id: str | None = None
    node_id: str | None = None
    due_at: datetime | None = None
    priority: int
