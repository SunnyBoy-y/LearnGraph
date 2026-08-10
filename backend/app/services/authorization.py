from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import Principal, hash_password, normalize_identity
from app.domain.models import (
    ActionItem,
    AuthSession,
    ChatSession,
    Exercise,
    FileRecord,
    Goal,
    Graph,
    GraphNode,
    Membership,
    Organization,
    Permission,
    Project,
    Roadmap,
    ResourceACL,
    Role,
    RolePermission,
    SecurityEvent,
    SourceRecord,
    User,
    Workspace,
    utc_now,
)
from app.domain.schemas.auth import (
    ACLGrantRequest,
    MembershipCreateRequest,
    MembershipUpdateRequest,
    MembershipView,
    OrganizationCreateRequest,
    RoleCreateRequest,
    RoleUpdateRequest,
    RoleView,
    UserCreateRequest,
    UserStatusUpdate,
)
from app.repositories.audit import AuditRepository
from app.services.auth import validate_new_password


PERMISSION_DESCRIPTIONS = {
    "workspace.read": "Read an explicitly accessible workspace",
    "workspace.write": "Create and modify resources in an accessible workspace",
    "workspace.manage": "Manage workspace-wide settings and integrations",
    "organization.read": "Read organization metadata and member directory",
    "organization.manage_members": "Add, change, and revoke organization memberships",
    "organization.manage_roles": "Create roles and assign declared permissions",
    "acl.manage": "Grant and revoke resource access without bypassing private ownership",
}

OWNER_PERMISSIONS = frozenset(PERMISSION_DESCRIPTIONS)
ADMIN_PERMISSIONS = tuple(PERMISSION_DESCRIPTIONS)
MEMBER_PERMISSIONS = (
    "workspace.read",
    "workspace.write",
    "organization.read",
)
VIEWER_PERMISSIONS = ("workspace.read", "organization.read")

RESOURCE_MODELS = {
    "project": Project,
    "goal": Goal,
    "graph": Graph,
    "session": ChatSession,
    "file": FileRecord,
    "source": SourceRecord,
    "exercise": Exercise,
}


def ensure_permission_catalog(db: Session) -> None:
    existing = {
        item.key: item
        for item in db.scalars(select(Permission).where(Permission.key.in_(PERMISSION_DESCRIPTIONS))).all()
    }
    for key, description in PERMISSION_DESCRIPTIONS.items():
        if key not in existing:
            db.add(Permission(key=key, description=description))
    db.flush()


class AuthorizationService:
    def __init__(self, db: Session, principal: Principal) -> None:
        self.db = db
        self.principal = principal

    def list_workspaces(self) -> Sequence[Workspace]:
        organization_ids = self.db.scalars(
            select(Membership.organization_id).where(
                Membership.tenant_id == self.principal.tenant_id,
                Membership.user_id == self.principal.user_id,
                Membership.status == "active",
            )
        ).all()
        conditions = [Workspace.owner_user_id == self.principal.user_id]
        if organization_ids:
            conditions.append(Workspace.organization_id.in_(organization_ids))
        candidates = self.db.scalars(
            select(Workspace)
            .where(
                Workspace.tenant_id == self.principal.tenant_id,
                or_(*conditions),
            )
            .order_by(Workspace.created_at)
        ).all()
        return [
            item
            for item in candidates
            if "workspace.read" in self.workspace_permissions(item)
        ]

    def workspace_permissions(self, workspace: Workspace) -> frozenset[str]:
        if workspace.tenant_id != self.principal.tenant_id:
            return frozenset()
        if workspace.owner_user_id == self.principal.user_id:
            return OWNER_PERMISSIONS
        if not workspace.organization_id or workspace.workspace_kind != "organization":
            return frozenset()
        return self.organization_permissions(workspace.organization_id)

    def organization_permissions(self, organization_id: str) -> frozenset[str]:
        keys = self.db.scalars(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Membership, Membership.role_id == Role.id)
            .where(
                Membership.tenant_id == self.principal.tenant_id,
                Membership.organization_id == organization_id,
                Membership.user_id == self.principal.user_id,
                Membership.status == "active",
                Role.organization_id == organization_id,
            )
        ).all()
        return frozenset(keys)

    def require_organization_permission(self, organization_id: str, permission: str) -> Organization:
        organization = self.db.scalar(
            select(Organization).where(
                Organization.id == organization_id,
                Organization.tenant_id == self.principal.tenant_id,
                Organization.status == "active",
            )
        )
        if organization is None:
            raise AppError(404, "organization_not_found", "Organization not found")
        if permission not in self.organization_permissions(organization_id):
            raise AppError(403, "permission_denied", f"Permission '{permission}' is required")
        return organization

    def can_access_resource(
        self,
        workspace: Workspace,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> bool:
        # Roadmaps and actions are derived workflow facts rather than
        # independently shareable ACL targets. They inherit every parent
        # boundary they reference, so possession of an opaque derived ID can
        # never bypass a private Goal, Graph, Project, or roadmap.
        if resource_type == "roadmap":
            return self.can_access_roadmap(workspace, resource_id, permission)
        if resource_type == "action":
            return self.can_access_action(workspace, resource_id, permission)
        model = RESOURCE_MODELS.get(resource_type)
        if model is None:
            return False
        resource_exists = self.db.scalar(
            select(model.id).where(
                model.id == resource_id,
                model.workspace_id == workspace.id,
            )
        )
        if resource_exists is None:
            return False
        workspace_permissions = self.workspace_permissions(workspace)
        if workspace.owner_user_id == self.principal.user_id:
            return True
        if permission == "read" and "workspace.read" not in workspace_permissions:
            return False
        if permission in {"write", "delete", "share"} and "workspace.write" not in workspace_permissions:
            return False
        membership = None
        if workspace.organization_id:
            membership = self.db.scalar(
                select(Membership).where(
                    Membership.organization_id == workspace.organization_id,
                    Membership.user_id == self.principal.user_id,
                    Membership.status == "active",
                )
            )
        all_grants = self.db.scalars(
            select(ResourceACL).where(
                ResourceACL.workspace_id == workspace.id,
                ResourceACL.tenant_id == workspace.tenant_id,
                ResourceACL.resource_type == resource_type,
                ResourceACL.resource_id == resource_id,
                ResourceACL.revoked_at.is_(None),
            )
        ).all()
        # An organization workspace is an explicit shared boundary. Resources
        # inherit its RBAC until their first ACL is created; from then on the
        # allow-list is authoritative for every path and collection query.
        if not all_grants:
            return True
        return any(
            permission in (grant.permissions or [])
            and any(
                (
                    (
                    grant.grantee_type == "user"
                    and grant.grantee_id == self.principal.user_id
                    ),
                    (
                    grant.grantee_type == "organization"
                    and grant.grantee_id == workspace.organization_id
                    ),
                    (
                    membership is not None
                    and grant.grantee_type == "role"
                    and grant.grantee_id == membership.role_id
                    ),
                )
            )
            for grant in all_grants
        )

    def filter_accessible_ids(
        self,
        workspace: Workspace,
        resource_type: str,
        resource_ids: Sequence[str],
        permission: str,
    ) -> set[str]:
        """Return the subset of resource_ids the principal can access.

        Equivalent to can_access_resource(...) per id but evaluates workspace
        RBAC and membership once and loads every ACL grant in a single IN
        query (B1-4: kill the list-endpoint N+1).
        """
        if resource_type in {"roadmap", "action"}:
            # Derived facts resolve through parent-graph walks per id; fall
            # back to exact per-item evaluation (no list endpoint uses them).
            return {
                resource_id
                for resource_id in resource_ids
                if resource_id
                and self.can_access_resource(
                    workspace, resource_type, resource_id, permission
                )
            }
        model = RESOURCE_MODELS.get(resource_type)
        if model is None:
            return set()
        ids = [resource_id for resource_id in resource_ids if resource_id]
        if not ids:
            return set()
        existing = set(
            self.db.scalars(
                select(model.id).where(
                    model.id.in_(ids),
                    model.workspace_id == workspace.id,
                )
            ).all()
        )
        if not existing:
            return set()
        if workspace.owner_user_id == self.principal.user_id:
            return existing
        workspace_permissions = self.workspace_permissions(workspace)
        if permission == "read" and "workspace.read" not in workspace_permissions:
            return set()
        if (
            permission in {"write", "delete", "share"}
            and "workspace.write" not in workspace_permissions
        ):
            return set()
        membership = None
        if workspace.organization_id:
            membership = self.db.scalar(
                select(Membership).where(
                    Membership.organization_id == workspace.organization_id,
                    Membership.user_id == self.principal.user_id,
                    Membership.status == "active",
                )
            )
        grants_by_resource: dict[str, list[ResourceACL]] = {}
        for grant in self.db.scalars(
            select(ResourceACL).where(
                ResourceACL.workspace_id == workspace.id,
                ResourceACL.tenant_id == workspace.tenant_id,
                ResourceACL.resource_type == resource_type,
                ResourceACL.resource_id.in_(existing),
                ResourceACL.revoked_at.is_(None),
            )
        ).all():
            grants_by_resource.setdefault(grant.resource_id, []).append(grant)
        accessible: set[str] = set()
        for resource_id in existing:
            grants = grants_by_resource.get(resource_id)
            if not grants:
                # No active ACL yet: resource inherits the workspace RBAC
                # boundary (see can_access_resource).
                accessible.add(resource_id)
                continue
            if any(
                permission in (grant.permissions or [])
                and any(
                    (
                        (
                            grant.grantee_type == "user"
                            and grant.grantee_id == self.principal.user_id
                        ),
                        (
                            grant.grantee_type == "organization"
                            and grant.grantee_id == workspace.organization_id
                        ),
                        (
                            membership is not None
                            and grant.grantee_type == "role"
                            and grant.grantee_id == membership.role_id
                        ),
                    )
                )
                for grant in grants
            ):
                accessible.add(resource_id)
        return accessible

    def can_access_bindings(
        self,
        workspace: Workspace,
        permission: str,
        *,
        project_id: str | None = None,
        goal_id: str | None = None,
        graph_id: str | None = None,
        node_id: str | None = None,
    ) -> bool:
        """Evaluate all ACL-managed parents attached to a derived fact."""

        bindings: list[tuple[str, str]] = []
        for resource_type, resource_id in (
            ("project", project_id),
            ("goal", goal_id),
        ):
            if resource_id:
                bindings.append((resource_type, resource_id))
        graph_ids: list[str] = [graph_id] if graph_id else []
        if node_id:
            node_graph_id = self.db.scalar(
                select(GraphNode.graph_id).where(
                    GraphNode.workspace_id == workspace.id,
                    GraphNode.id == node_id,
                )
            )
            if node_graph_id is None:
                return False
            graph_ids.append(node_graph_id)
        for bound_graph_id in dict.fromkeys(graph_ids):
            graph_goal_id = self.db.scalar(
                select(Graph.goal_id).where(
                    Graph.workspace_id == workspace.id,
                    Graph.id == bound_graph_id,
                )
            )
            if graph_goal_id is None:
                return False
            bindings.extend(
                (("graph", bound_graph_id), ("goal", graph_goal_id))
            )
        return all(
            self.can_access_resource(workspace, resource_type, resource_id, permission)
            for resource_type, resource_id in dict.fromkeys(bindings)
        )

    def can_access_roadmap_record(
        self,
        workspace: Workspace,
        roadmap: Roadmap,
        permission: str,
    ) -> bool:
        return roadmap.workspace_id == workspace.id and self.can_access_bindings(
            workspace,
            permission,
            goal_id=roadmap.goal_id,
            graph_id=roadmap.graph_id,
        )

    def can_access_roadmap(
        self,
        workspace: Workspace,
        roadmap_id: str,
        permission: str,
    ) -> bool:
        roadmap = self.db.scalar(
            select(Roadmap).where(
                Roadmap.workspace_id == workspace.id,
                Roadmap.id == roadmap_id,
            )
        )
        return roadmap is not None and self.can_access_roadmap_record(
            workspace, roadmap, permission
        )

    def can_access_action_record(
        self,
        workspace: Workspace,
        action: ActionItem,
        permission: str,
    ) -> bool:
        if action.workspace_id != workspace.id:
            return False
        if not self.can_access_bindings(
            workspace,
            permission,
            project_id=action.project_id,
            goal_id=action.goal_id,
            graph_id=action.graph_id,
            node_id=action.node_id,
        ):
            return False
        if not action.roadmap_id:
            return True
        return self.can_access_roadmap(workspace, action.roadmap_id, permission)

    def can_access_action(
        self,
        workspace: Workspace,
        action_id: str,
        permission: str,
    ) -> bool:
        action = self.db.scalar(
            select(ActionItem).where(
                ActionItem.workspace_id == workspace.id,
                ActionItem.id == action_id,
            )
        )
        return action is not None and self.can_access_action_record(
            workspace, action, permission
        )


class IdentityManagementService:
    def __init__(self, db: Session, principal: Principal) -> None:
        self.db = db
        self.principal = principal
        ensure_permission_catalog(db)

    def permissions(self) -> Sequence[Permission]:
        return self.db.scalars(select(Permission).order_by(Permission.key)).all()

    def users(self) -> Sequence[User]:
        self._require_system_admin()
        return self.db.scalars(
            select(User)
            .where(User.tenant_id == self.principal.tenant_id)
            .order_by(User.created_at)
        ).all()

    def create_user(self, payload: UserCreateRequest) -> User:
        self._require_system_admin()
        username_normalized = normalize_identity(payload.username)
        email_normalized = normalize_identity(payload.email) if payload.email else None
        duplicate = self.db.scalar(
            select(User).where(
                User.tenant_id == self.principal.tenant_id,
                or_(
                    User.username_normalized == username_normalized,
                    User.email_normalized == email_normalized
                    if email_normalized is not None
                    else User.id == "",
                ),
            )
        )
        if duplicate is not None:
            raise AppError(409, "identity_conflict", "Username or email already exists")
        validate_new_password(payload.password, username=payload.username)
        user = User(
            tenant_id=self.principal.tenant_id,
            username=payload.username.strip(),
            username_normalized=username_normalized,
            email=payload.email.strip() if payload.email else None,
            email_normalized=email_normalized,
            display_name=payload.display_name.strip(),
            password_hash=hash_password(payload.password),
            is_system_admin=payload.is_system_admin,
        )
        self.db.add(user)
        self.db.flush()
        workspace = Workspace(
            id=str(uuid4()),
            tenant_id=self.principal.tenant_id,
            owner_user_id=user.id,
            workspace_kind="personal",
            name=f"{user.display_name} 的学习空间",
            description="用户私有工作区；组织管理员不能绕过此边界。",
        )
        self.db.add(workspace)
        self.db.add(
            SecurityEvent(
                tenant_id=self.principal.tenant_id,
                user_id=user.id,
                event_type="identity.user_created",
                outcome="success",
                details={"created_by": self.principal.user_id, "workspace_id": workspace.id},
            )
        )
        self.db.commit()
        self.db.refresh(user)
        return user

    def update_user_status(self, user_id: str, payload: UserStatusUpdate) -> User:
        self._require_system_admin()
        if user_id == self.principal.user_id and payload.status != "active":
            raise AppError(409, "cannot_disable_current_user", "Current administrator cannot disable itself")
        user = self.db.scalar(
            select(User).where(
                User.id == user_id,
                User.tenant_id == self.principal.tenant_id,
            )
        )
        if user is None:
            raise AppError(404, "user_not_found", "User not found")
        user.status = payload.status
        if payload.status != "active":
            now = utc_now()
            for session in self.db.scalars(
                select(AuthSession).where(
                    AuthSession.user_id == user.id,
                    AuthSession.tenant_id == user.tenant_id,
                    AuthSession.revoked_at.is_(None),
                )
            ).all():
                session.revoked_at = now
                session.revoked_reason = "user_disabled"
        self.db.commit()
        self.db.refresh(user)
        return user

    def organizations(self) -> Sequence[Organization]:
        ids = self.db.scalars(
            select(Membership.organization_id).where(
                Membership.tenant_id == self.principal.tenant_id,
                Membership.user_id == self.principal.user_id,
                Membership.status == "active",
            )
        ).all()
        if not ids:
            return []
        return self.db.scalars(
            select(Organization)
            .where(
                Organization.tenant_id == self.principal.tenant_id,
                Organization.id.in_(ids),
            )
            .order_by(Organization.created_at)
        ).all()

    def create_organization(self, payload: OrganizationCreateRequest) -> Organization:
        duplicate = self.db.scalar(
            select(Organization).where(
                Organization.tenant_id == self.principal.tenant_id,
                Organization.name == payload.name.strip(),
            )
        )
        if duplicate is not None:
            raise AppError(409, "organization_name_conflict", "Organization name already exists")
        organization = Organization(
            tenant_id=self.principal.tenant_id,
            name=payload.name.strip(),
            owner_user_id=self.principal.user_id,
        )
        self.db.add(organization)
        self.db.flush()
        roles = self._create_default_roles(organization)
        membership = Membership(
            tenant_id=self.principal.tenant_id,
            organization_id=organization.id,
            user_id=self.principal.user_id,
            role_id=roles["Organization Admin"].id,
            status="active",
            invited_by=self.principal.user_id,
            joined_at=utc_now(),
        )
        workspace = Workspace(
            id=str(uuid4()),
            tenant_id=self.principal.tenant_id,
            owner_user_id=self.principal.user_id,
            organization_id=organization.id,
            workspace_kind="organization",
            name=(payload.workspace_name or f"{organization.name} 共享空间").strip(),
            description="组织共享工作区；成员访问仍由 Membership、Role 与 ACL 校验。",
        )
        self.db.add_all([membership, workspace])
        self.db.flush()
        AuditRepository(self.db, workspace.id).record(
            actor_id=self.principal.user_id,
            action="organization.create",
            resource_type="organization",
            resource_id=organization.id,
            details={"workspace_id": workspace.id},
        )
        self.db.commit()
        self.db.refresh(organization)
        return organization

    def roles(self, organization_id: str) -> list[RoleView]:
        AuthorizationService(self.db, self.principal).require_organization_permission(
            organization_id, "organization.read"
        )
        roles = self.db.scalars(
            select(Role)
            .where(
                Role.tenant_id == self.principal.tenant_id,
                Role.organization_id == organization_id,
            )
            .order_by(Role.created_at)
        ).all()
        return [self._role_view(item) for item in roles]

    def create_role(self, organization_id: str, payload: RoleCreateRequest) -> RoleView:
        AuthorizationService(self.db, self.principal).require_organization_permission(
            organization_id, "organization.manage_roles"
        )
        duplicate = self.db.scalar(
            select(Role).where(Role.organization_id == organization_id, Role.name == payload.name.strip())
        )
        if duplicate is not None:
            raise AppError(409, "role_name_conflict", "Role name already exists")
        permissions = self._resolve_permissions(payload.permission_keys)
        role = Role(
            tenant_id=self.principal.tenant_id,
            organization_id=organization_id,
            name=payload.name.strip(),
            description=payload.description.strip(),
            is_system=False,
        )
        self.db.add(role)
        self.db.flush()
        self.db.add_all(
            [RolePermission(role_id=role.id, permission_id=item.id) for item in permissions]
        )
        self.db.commit()
        self.db.refresh(role)
        return self._role_view(role)

    def update_role(
        self, organization_id: str, role_id: str, payload: RoleUpdateRequest
    ) -> RoleView:
        AuthorizationService(self.db, self.principal).require_organization_permission(
            organization_id, "organization.manage_roles"
        )
        role = self._require_role(organization_id, role_id)
        if role.is_system:
            raise AppError(409, "system_role_immutable", "Built-in roles are immutable")
        if payload.description is not None:
            role.description = payload.description.strip()
        if payload.permission_keys is not None:
            permissions = self._resolve_permissions(payload.permission_keys)
            self.db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
            self.db.add_all(
                [RolePermission(role_id=role.id, permission_id=item.id) for item in permissions]
            )
        self.db.commit()
        self.db.refresh(role)
        return self._role_view(role)

    def memberships(self, organization_id: str) -> list[MembershipView]:
        AuthorizationService(self.db, self.principal).require_organization_permission(
            organization_id, "organization.read"
        )
        rows = self.db.execute(
            select(Membership, User, Role)
            .join(User, User.id == Membership.user_id)
            .join(Role, Role.id == Membership.role_id)
            .where(
                Membership.tenant_id == self.principal.tenant_id,
                Membership.organization_id == organization_id,
            )
            .order_by(Membership.created_at)
        ).all()
        return [self._membership_view(membership, user, role) for membership, user, role in rows]

    def add_membership(
        self, organization_id: str, payload: MembershipCreateRequest
    ) -> MembershipView:
        AuthorizationService(self.db, self.principal).require_organization_permission(
            organization_id, "organization.manage_members"
        )
        user = self.db.scalar(
            select(User).where(
                User.id == payload.user_id,
                User.tenant_id == self.principal.tenant_id,
                User.status == "active",
            )
        )
        if user is None:
            raise AppError(404, "user_not_found", "User not found")
        role = self._require_role(organization_id, payload.role_id)
        membership = self.db.scalar(
            select(Membership).where(
                Membership.organization_id == organization_id,
                Membership.user_id == user.id,
            )
        )
        now = utc_now()
        if membership is None:
            membership = Membership(
                tenant_id=self.principal.tenant_id,
                organization_id=organization_id,
                user_id=user.id,
                role_id=role.id,
                status="active",
                invited_by=self.principal.user_id,
                joined_at=now,
            )
            self.db.add(membership)
        else:
            membership.role_id = role.id
            membership.status = "active"
            membership.revoked_at = None
            membership.joined_at = membership.joined_at or now
        self.db.commit()
        self.db.refresh(membership)
        return self._membership_view(membership, user, role)

    def update_membership(
        self,
        organization_id: str,
        membership_id: str,
        payload: MembershipUpdateRequest,
    ) -> MembershipView:
        organization = AuthorizationService(
            self.db, self.principal
        ).require_organization_permission(organization_id, "organization.manage_members")
        membership = self.db.scalar(
            select(Membership).where(
                Membership.id == membership_id,
                Membership.tenant_id == self.principal.tenant_id,
                Membership.organization_id == organization_id,
            )
        )
        if membership is None:
            raise AppError(404, "membership_not_found", "Membership not found")
        if membership.user_id == organization.owner_user_id and payload.status == "revoked":
            raise AppError(409, "organization_owner_required", "Organization owner cannot be revoked")
        role = self._require_role(
            organization_id,
            payload.role_id or membership.role_id,
        )
        if payload.role_id is not None:
            membership.role_id = role.id
        if payload.status is not None:
            membership.status = payload.status
            membership.revoked_at = utc_now() if payload.status == "revoked" else None
        user = self.db.get(User, membership.user_id)
        assert user is not None
        self.db.commit()
        self.db.refresh(membership)
        return self._membership_view(membership, user, role)

    def list_acls(self, workspace: Workspace) -> Sequence[ResourceACL]:
        permissions = AuthorizationService(self.db, self.principal).workspace_permissions(workspace)
        if "acl.manage" not in permissions:
            raise AppError(403, "permission_denied", "Permission 'acl.manage' is required")
        return self.db.scalars(
            select(ResourceACL)
            .where(
                ResourceACL.workspace_id == workspace.id,
                ResourceACL.tenant_id == workspace.tenant_id,
            )
            .order_by(ResourceACL.created_at.desc())
        ).all()

    def grant_acl(self, workspace: Workspace, payload: ACLGrantRequest) -> ResourceACL:
        permissions = AuthorizationService(self.db, self.principal).workspace_permissions(workspace)
        if "acl.manage" not in permissions:
            raise AppError(403, "permission_denied", "Permission 'acl.manage' is required")
        model = RESOURCE_MODELS.get(payload.resource_type)
        if model is None:
            raise AppError(422, "unsupported_acl_resource", "Resource type does not support ACL")
        resource = self.db.scalar(
            select(model).where(
                model.id == payload.resource_id,
                model.workspace_id == workspace.id,
            )
        )
        if resource is None:
            raise AppError(404, "resource_not_found", "ACL resource not found in this workspace")
        self._validate_grantee(workspace, payload.grantee_type, payload.grantee_id)
        item = self.db.scalar(
            select(ResourceACL).where(
                ResourceACL.workspace_id == workspace.id,
                ResourceACL.resource_type == payload.resource_type,
                ResourceACL.resource_id == payload.resource_id,
                ResourceACL.grantee_type == payload.grantee_type,
                ResourceACL.grantee_id == payload.grantee_id,
            )
        )
        normalized_permissions = sorted(set(payload.permissions))
        if item is None:
            item = ResourceACL(
                workspace_id=workspace.id,
                tenant_id=workspace.tenant_id,
                resource_type=payload.resource_type,
                resource_id=payload.resource_id,
                grantee_type=payload.grantee_type,
                grantee_id=payload.grantee_id,
                permissions=normalized_permissions,
                granted_by=self.principal.user_id,
            )
            self.db.add(item)
        else:
            item.permissions = normalized_permissions
            item.granted_by = self.principal.user_id
            item.revoked_at = None
        AuditRepository(self.db, workspace.id).record(
            actor_id=self.principal.user_id,
            action="acl.grant",
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            details={
                "grantee_type": payload.grantee_type,
                "grantee_id": payload.grantee_id,
                "permissions": normalized_permissions,
            },
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def revoke_acl(self, workspace: Workspace, acl_id: str) -> ResourceACL:
        permissions = AuthorizationService(self.db, self.principal).workspace_permissions(workspace)
        if "acl.manage" not in permissions:
            raise AppError(403, "permission_denied", "Permission 'acl.manage' is required")
        item = self.db.scalar(
            select(ResourceACL).where(
                ResourceACL.id == acl_id,
                ResourceACL.workspace_id == workspace.id,
                ResourceACL.tenant_id == workspace.tenant_id,
            )
        )
        if item is None:
            raise AppError(404, "acl_not_found", "ACL grant not found")
        if item.revoked_at is None:
            item.revoked_at = utc_now()
            AuditRepository(self.db, workspace.id).record(
                actor_id=self.principal.user_id,
                action="acl.revoke",
                resource_type=item.resource_type,
                resource_id=item.resource_id,
                details={"acl_id": item.id},
            )
            self.db.commit()
            self.db.refresh(item)
        return item

    def _require_system_admin(self) -> None:
        if not self.principal.is_system_admin:
            raise AppError(403, "system_admin_required", "System administrator permission is required")

    def _resolve_permissions(self, keys: list[str]) -> Sequence[Permission]:
        normalized = sorted(set(keys))
        permissions = self.db.scalars(select(Permission).where(Permission.key.in_(normalized))).all()
        if len(permissions) != len(normalized):
            known = {item.key for item in permissions}
            raise AppError(
                422,
                "unknown_permission",
                "One or more permission keys are unknown",
                {"unknown": [key for key in normalized if key not in known]},
            )
        return permissions

    def _create_default_roles(self, organization: Organization) -> dict[str, Role]:
        permission_by_key = {
            item.key: item for item in self.db.scalars(select(Permission)).all()
        }
        definitions = {
            "Organization Admin": ADMIN_PERMISSIONS,
            "Member": MEMBER_PERMISSIONS,
            "Viewer": VIEWER_PERMISSIONS,
        }
        result: dict[str, Role] = {}
        for name, keys in definitions.items():
            role = Role(
                tenant_id=organization.tenant_id,
                organization_id=organization.id,
                name=name,
                description=f"Built-in {name.casefold()} role",
                is_system=True,
            )
            self.db.add(role)
            self.db.flush()
            self.db.add_all(
                [
                    RolePermission(role_id=role.id, permission_id=permission_by_key[key].id)
                    for key in keys
                ]
            )
            result[name] = role
        return result

    def _require_role(self, organization_id: str, role_id: str) -> Role:
        role = self.db.scalar(
            select(Role).where(
                Role.id == role_id,
                Role.tenant_id == self.principal.tenant_id,
                Role.organization_id == organization_id,
            )
        )
        if role is None:
            raise AppError(404, "role_not_found", "Role not found in this organization")
        return role

    def _role_view(self, role: Role) -> RoleView:
        keys = self.db.scalars(
            select(Permission.key)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
            .order_by(Permission.key)
        ).all()
        return RoleView(
            id=role.id,
            tenant_id=role.tenant_id,
            organization_id=role.organization_id,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permission_keys=list(keys),
            created_at=role.created_at,
        )

    @staticmethod
    def _membership_view(membership: Membership, user: User, role: Role) -> MembershipView:
        return MembershipView(
            id=membership.id,
            tenant_id=membership.tenant_id,
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            username=user.username,
            display_name=user.display_name or user.username,
            role_id=membership.role_id,
            role_name=role.name,
            status=membership.status,
            joined_at=membership.joined_at,
            revoked_at=membership.revoked_at,
            created_at=membership.created_at,
        )

    def _validate_grantee(self, workspace: Workspace, grantee_type: str, grantee_id: str) -> None:
        if grantee_type == "user":
            user = self.db.scalar(
                select(User).where(
                    User.id == grantee_id,
                    User.tenant_id == workspace.tenant_id,
                    User.status == "active",
                )
            )
            if user is None:
                raise AppError(404, "acl_grantee_not_found", "ACL user not found")
            if workspace.organization_id:
                membership = self.db.scalar(
                    select(Membership).where(
                        Membership.organization_id == workspace.organization_id,
                        Membership.user_id == user.id,
                        Membership.status == "active",
                    )
                )
                if membership is None:
                    raise AppError(422, "acl_grantee_outside_organization", "User is not an active member")
        elif grantee_type == "role":
            if not workspace.organization_id:
                raise AppError(422, "acl_role_requires_organization", "Personal workspaces cannot grant to roles")
            self._require_role(workspace.organization_id, grantee_id)
        elif grantee_type == "organization":
            if workspace.organization_id != grantee_id:
                raise AppError(422, "acl_organization_mismatch", "ACL organization must own the workspace")
