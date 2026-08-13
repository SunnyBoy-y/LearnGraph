from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import AppError
from app.core.security import Principal, hash_session_token
from app.domain.models import AuthSession, User, Workspace, utc_now
from app.services.authorization import AuthorizationService


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    principal: Principal
    workspace: Workspace
    permissions: frozenset[str]

    @property
    def workspace_id(self) -> str:
        return self.workspace.id

    def require_permission(self, permission: str) -> None:
        if permission not in self.permissions:
            raise AppError(403, "permission_denied", f"Permission '{permission}' is required")


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise AppError(401, "unauthorized", "A valid bearer token is required")
    token_hash = hash_session_token(credentials.credentials)
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    )
    now = utc_now()
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or _utc(auth_session.expires_at) <= now
    ):
        raise AppError(401, "session_invalid", "Authentication session is expired or revoked")
    user = db.scalar(
        select(User).where(
            User.id == auth_session.user_id,
            User.tenant_id == auth_session.tenant_id,
        )
    )
    if user is None or user.status != "active":
        raise AppError(401, "identity_inactive", "The authenticated identity is not active")
    # last_seen is telemetry only. Never fail an otherwise valid request because
    # SQLite is briefly locked by a chat write or background scheduler tick.
    if _utc(auth_session.last_seen_at) + timedelta(minutes=5) < now:
        auth_session.last_seen_at = now
        try:
            db.commit()
        except OperationalError:
            db.rollback()
    return Principal(
        user_id=user.id,
        username=user.username,
        tenant_id=user.tenant_id,
        session_id=auth_session.id,
        display_name=user.display_name or user.username,
        is_system_admin=user.is_system_admin,
        must_change_password=user.must_change_password,
    )


def workspace_context(
    request: Request,
    workspace_id: Annotated[str, Header(alias="X-Workspace-ID", min_length=1, max_length=64)],
    principal: Annotated[Principal, Depends(current_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> WorkspaceContext:
    if principal.must_change_password:
        raise AppError(
            403,
            "password_change_required",
            "Password must be changed before workspace resources can be accessed",
        )
    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == principal.tenant_id,
        )
    )
    if workspace is None:
        raise AppError(403, "workspace_forbidden", "Workspace is not accessible to this user")
    permissions = AuthorizationService(db, principal).workspace_permissions(workspace)
    safe_method = request.method in {"GET", "HEAD", "OPTIONS"}
    required = "workspace.read" if safe_method else "workspace.write"
    sensitive_prefixes = (
        "/api/v1/providers",
        "/api/v1/plugins",
        "/api/v1/components",
        "/api/v1/mcp/",
        # Skill lifecycle endpoints declare their own per-endpoint boundary:
        # install/edit/validate/scan/revoke require workspace.write, while
        # authorize/invoke/sandbox-run require workspace.manage. Keeping them
        # out of the prefix list prevents a trailing-slash mismatch from
        # silently changing that boundary.
        "/api/v1/migrations",
        "/api/v1/settings",
        "/api/v1/audit",
        "/api/v1/workspace/export",
    )
    if (not safe_method and request.url.path.startswith(sensitive_prefixes)) or (
        safe_method and request.url.path.startswith("/api/v1/audit")
    ):
        required = "workspace.manage"
    if required not in permissions:
        raise AppError(403, "workspace_forbidden", "Workspace is not accessible to this user")
    if not safe_method and not request.url.path.startswith("/api/v1/migrations"):
        # Migration control writes are the only writes permitted while the
        # workspace maintenance window owns the source. Import locally so the
        # auth dependency does not become the owner of migration model setup.
        from app.domain.migration_models import MaintenanceLock

        maintenance_lock = db.scalar(
            select(MaintenanceLock).where(
                MaintenanceLock.workspace_id == workspace.id,
                MaintenanceLock.status == "active",
            )
        )
        if maintenance_lock is not None:
            raise AppError(
                503,
                "workspace_maintenance",
                "Workspace writes are paused for a verified offline migration",
                {"migration_job_id": maintenance_lock.job_id},
            )
    path_resource = next(
        (
            (resource_type, str(request.path_params[param_name]))
            for param_name, resource_type in (
                ("project_id", "project"),
                ("goal_id", "goal"),
                ("graph_id", "graph"),
                ("session_id", "session"),
                ("file_id", "file"),
                ("source_id", "source"),
                ("exercise_id", "exercise"),
                ("roadmap_id", "roadmap"),
                ("action_id", "action"),
            )
            if param_name in request.path_params
        ),
        None,
    )
    # Sub-application session routes (`/api/v1/subapps/sessions/{session_id}/...`)
    # reuse the `session_id` path parameter, but that id is a sub-app session
    # capability id, not a chat session id. The subapp router applies its own
    # workspace-scoped session lookup (`SubAppService._get_session` filters by
    # workspace_id), so the generic chat-session ACL gate below would otherwise
    # 404 every session-scoped subapp call before it reaches the service.
    is_subapp_session_path = request.url.path.startswith("/api/v1/subapps/sessions/")
    if path_resource is not None and not is_subapp_session_path:
        resource_type, resource_id = path_resource
        resource_permission = (
            "read"
            if request.method in {"GET", "HEAD", "OPTIONS"}
            else "delete"
            if request.method == "DELETE" or request.url.path.endswith("/delete")
            else "write"
        )
        if not AuthorizationService(db, principal).can_access_resource(
            workspace, resource_type, resource_id, resource_permission
        ):
            raise AppError(404, "not_found", "Resource not found in this workspace")
    return WorkspaceContext(principal=principal, workspace=workspace, permissions=permissions)


DB = Annotated[Session, Depends(get_db)]
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
CurrentWorkspace = Annotated[WorkspaceContext, Depends(workspace_context)]
AppSettings = Annotated[Settings, Depends(get_settings)]
