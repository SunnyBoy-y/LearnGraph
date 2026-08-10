from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    Principal,
    hash_password,
    hash_session_token,
    new_session_token,
    normalize_identity,
    verify_password,
)
from app.domain.models import (
    AuthSession,
    Membership,
    Organization,
    ResourceACL,
    SecurityEvent,
    Tenant,
    User,
    Workspace,
    new_id,
    utc_now,
)
from app.domain.schemas.auth import (
    AccountDeletionImpact,
    AuthSessionView,
    ChangePasswordRequest,
    DeleteAccountRequest,
    DemoLoginRequest,
    DemoLoginResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
)


_DUMMY_PASSWORD_HASH = hash_password("not-a-real-account-password")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def validate_new_password(password: str, *, username: str = "") -> None:
    if len(password) < 12:
        raise AppError(422, "weak_password", "Password must contain at least 12 characters")
    normalized = password.casefold()
    if normalized in {"password1234", "adminadmin123", "learn-graph-local"}:
        raise AppError(422, "weak_password", "This password is reserved or commonly guessed")
    # Email addresses are valid login identities. Do not reject a long,
    # sufficiently varied password merely because it matches that identity.
    if username and "@" not in username and normalize_identity(username) in normalized:
        raise AppError(422, "weak_password", "Password must not contain the username")
    if len(set(password)) < 6 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise AppError(
            422,
            "weak_password",
            "Password must contain varied characters, including a letter and a number",
        )


class AuthService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def login(
        self,
        payload: LoginRequest,
        *,
        user_agent: str = "",
        ip_address: str = "",
        device_id: str = "",
        demo_only: bool = False,
    ) -> LoginResponse:
        if demo_only:
            if not self.settings.demo_login_enabled:
                raise AppError(404, "demo_auth_disabled", "Development demo authentication is disabled")
            if (
                normalize_identity(payload.username)
                != normalize_identity(self.settings.demo_username)
            ):
                self._record_security(
                    event_type="auth.login",
                    outcome="failure",
                    ip_address=ip_address,
                    user_agent=user_agent,
                    details={"reason": "invalid_credentials", "demo_endpoint": True},
                )
                self.db.commit()
                raise AppError(401, "invalid_credentials", "Invalid username or password")

        identifier = normalize_identity(payload.username)
        identity_query = select(User).where(
                    or_(
                        User.username_normalized == identifier,
                        User.email_normalized == identifier,
                    )
                )
        if payload.tenant_id:
            identity_query = identity_query.where(User.tenant_id == payload.tenant_id)
        if demo_only:
            identity_query = identity_query.where(User.id == "demo-user")
        matches = list(self.db.scalars(identity_query).all())
        user = matches[0] if len(matches) == 1 else None
        password_valid = verify_password(
            payload.password,
            user.password_hash if user is not None else _DUMMY_PASSWORD_HASH,
        )
        now = utc_now()
        if user is not None and user.locked_until is not None and _utc(user.locked_until) > now:
            self._record_security(
                event_type="auth.login",
                outcome="blocked",
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                details={"reason": "temporarily_locked"},
            )
            self.db.commit()
            raise AppError(429, "account_temporarily_locked", "Account is temporarily locked")
        if user is None or not password_valid or user.status != "active":
            if user is not None:
                user.failed_login_count += 1
                if user.failed_login_count >= self.settings.auth_max_failed_logins:
                    user.locked_until = now + timedelta(minutes=self.settings.auth_lockout_minutes)
                    user.failed_login_count = 0
            self._record_security(
                event_type="auth.login",
                outcome="failure",
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                details={"reason": "invalid_credentials"},
            )
            self.db.commit()
            raise AppError(401, "invalid_credentials", "Invalid username or password")
        if demo_only and user.id != "demo-user":
            raise AppError(401, "invalid_credentials", "Invalid username or password")

        # B1-1: a clean account (no prior failed attempts) must not touch the
        # users row, so the common login writes only AuthSession + SecurityEvent
        # in one short transaction; the Workspace SELECT stays a pure read (WAL).
        if user.failed_login_count != 0 or user.locked_until is not None:
            user.failed_login_count = 0
            user.locked_until = None
        token = new_session_token()
        session_id = new_id()
        auth_session = AuthSession(
            id=session_id,
            tenant_id=user.tenant_id,
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=now + timedelta(hours=self.settings.auth_session_hours),
            last_seen_at=now,
            user_agent=user_agent[:500],
            ip_address=ip_address[:64],
            device_id=device_id[:128],
        )
        default_workspace = self.db.scalar(
            select(Workspace)
            .where(
                Workspace.tenant_id == user.tenant_id,
                Workspace.owner_user_id == user.id,
            )
            .order_by(Workspace.created_at)
        )
        self.db.add(auth_session)
        self._record_security(
            event_type="auth.login",
            outcome="success",
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details={"session_id": session_id, "demo_endpoint": demo_only},
        )
        # Single commit: AuthSession INSERT + SecurityEvent INSERT (plus the
        # users UPDATE only after prior failed attempts) flush together, so the
        # SQLite write lock is taken once and held for the shortest window.
        self.db.commit()
        response_type = DemoLoginResponse if demo_only else LoginResponse
        return response_type(
            access_token=token,
            expires_at=auth_session.expires_at,
            session_id=auth_session.id,
            user_id=user.id,
            username=user.username,
            display_name=user.display_name or user.username,
            default_workspace_id=default_workspace.id if default_workspace is not None else None,
            must_change_password=user.must_change_password,
            demo_only=demo_only,
        )

    def register(
        self,
        payload: RegisterRequest,
        *,
        user_agent: str = "",
        ip_address: str = "",
        device_id: str = "",
    ) -> LoginResponse:
        username = payload.username.strip()
        email = payload.email.strip() if payload.email else None
        username_normalized = normalize_identity(username)
        email_normalized = normalize_identity(email) if email else None
        duplicate_conditions = [User.username_normalized == username_normalized]
        if email_normalized:
            duplicate_conditions.append(User.email_normalized == email_normalized)
        if self.db.scalar(select(User).where(or_(*duplicate_conditions))) is not None:
            raise AppError(409, "identity_conflict", "Username or email already exists")
        validate_new_password(payload.password, username=username)

        tenant = Tenant(id=str(uuid4()), name=f"{payload.display_name.strip()} 的 LearnGraph", status="active")
        self.db.add(tenant)
        self.db.flush()
        user = User(
            tenant_id=tenant.id,
            username=username,
            username_normalized=username_normalized,
            email=email,
            email_normalized=email_normalized,
            display_name=payload.display_name.strip(),
            password_hash=hash_password(payload.password),
        )
        self.db.add(user)
        self.db.flush()
        workspace = Workspace(
            id=str(uuid4()),
            tenant_id=tenant.id,
            owner_user_id=user.id,
            workspace_kind="personal",
            name=f"{user.display_name} 的学习空间",
            description="注册时自动创建的个人学习工作区",
        )
        self.db.add(workspace)
        self._record_security(
            event_type="auth.register",
            outcome="success",
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details={"workspace_id": workspace.id},
        )
        # New personal workspaces receive the official first-party Skill set at
        # creation time, so read-only listing endpoints never need to write.
        from app.services.skill_package import ensure_official_skill_packages

        ensure_official_skill_packages(
            self.db,
            workspace.id,
            settings=self.settings,
        )
        self.db.commit()
        return self.login(
            LoginRequest(username=username, password=payload.password),
            user_agent=user_agent,
            ip_address=ip_address,
            device_id=device_id,
        )

    def demo_login(
        self,
        payload: DemoLoginRequest,
        *,
        user_agent: str = "",
        ip_address: str = "",
        device_id: str = "",
    ) -> DemoLoginResponse:
        response = self.login(
            payload,
            user_agent=user_agent,
            ip_address=ip_address,
            device_id=device_id,
            demo_only=True,
        )
        return DemoLoginResponse.model_validate(response.model_dump())

    def logout(self, principal: Principal) -> None:
        auth_session = self.db.get(AuthSession, principal.session_id)
        if auth_session is not None and auth_session.revoked_at is None:
            auth_session.revoked_at = utc_now()
            auth_session.revoked_reason = "logout"
            self._record_security(
                event_type="auth.logout",
                outcome="success",
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                details={"session_id": principal.session_id},
            )
            self.db.commit()

    def sessions(self, principal: Principal) -> list[AuthSessionView]:
        items = self.db.scalars(
            select(AuthSession)
            .where(
                AuthSession.tenant_id == principal.tenant_id,
                AuthSession.user_id == principal.user_id,
                AuthSession.revoked_at.is_(None),
            )
            .order_by(AuthSession.created_at.desc())
        ).all()

        # A browser can create several bearer sessions after refreshes or
        # repeated sign-ins. Keep one visible entry per device signature so
        # the security page stays actionable instead of showing duplicates.
        # The revoke operation below invalidates the whole signature group.
        representatives: dict[tuple[str, str, str], AuthSession] = {}
        for item in items:
            device_signature = (
                ("device", item.device_id, "")
                if item.device_id
                else ("legacy", item.user_agent, item.ip_address)
            )
            current = representatives.get(device_signature)
            if current is None or item.id == principal.session_id:
                representatives[device_signature] = item

        return [
            AuthSessionView.model_validate(item).model_copy(
                update={"current": item.id == principal.session_id}
            )
            for item in sorted(
                representatives.values(),
                key=lambda session: session.created_at,
                reverse=True,
            )
        ]

    def revoke_session(self, session_id: str, principal: Principal) -> None:
        item = self.db.scalar(
            select(AuthSession).where(
                AuthSession.id == session_id,
                AuthSession.tenant_id == principal.tenant_id,
                AuthSession.user_id == principal.user_id,
            )
        )
        if item is None:
            raise AppError(404, "auth_session_not_found", "Authentication session not found")
        if item.revoked_at is None:
            device_sessions = self.db.scalars(
                select(AuthSession).where(
                    AuthSession.tenant_id == principal.tenant_id,
                    AuthSession.user_id == principal.user_id,
                    AuthSession.revoked_at.is_(None),
                )
            ).all()
            device_sessions = [
                device_session
                for device_session in device_sessions
                if (
                    item.device_id
                    and device_session.device_id == item.device_id
                )
                or (
                    not item.device_id
                    and not device_session.device_id
                    and device_session.user_agent == item.user_agent
                    and device_session.ip_address == item.ip_address
                )
            ]
            revoked_at = utc_now()
            for device_session in device_sessions:
                device_session.revoked_at = revoked_at
                device_session.revoked_reason = "user_revoked"
            self._record_security(
                event_type="auth.session_revoked",
                outcome="success",
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                details={
                    "session_id": item.id,
                    "current": item.id == principal.session_id,
                    "revoked_device_session_count": len(device_sessions),
                },
            )
            self.db.commit()

    def change_password(self, payload: ChangePasswordRequest, principal: Principal) -> None:
        user = self.db.get(User, principal.user_id)
        if user is None or user.tenant_id != principal.tenant_id or user.status != "active":
            raise AppError(401, "unauthorized", "The authenticated user is no longer active")
        if not verify_password(payload.current_password, user.password_hash):
            raise AppError(401, "invalid_credentials", "Current password is incorrect")
        if verify_password(payload.new_password, user.password_hash):
            raise AppError(422, "password_unchanged", "New password must be different")
        validate_new_password(payload.new_password, username=user.username)
        user.password_hash = hash_password(payload.new_password)
        user.password_changed_at = utc_now()
        user.must_change_password = False
        other_sessions = self.db.scalars(
            select(AuthSession).where(
                AuthSession.tenant_id == principal.tenant_id,
                AuthSession.user_id == principal.user_id,
                AuthSession.id != principal.session_id,
                AuthSession.revoked_at.is_(None),
            )
        ).all()
        for item in other_sessions:
            item.revoked_at = utc_now()
            item.revoked_reason = "password_changed"
        self._record_security(
            event_type="auth.password_changed",
            outcome="success",
            user=user,
            details={"revoked_session_count": len(other_sessions)},
        )
        self.db.commit()

    def account_deletion_impact(self, principal: Principal) -> AccountDeletionImpact:
        user = self._active_user(principal)
        active_sessions = self.db.scalars(
            select(AuthSession).where(
                AuthSession.tenant_id == principal.tenant_id,
                AuthSession.user_id == principal.user_id,
                AuthSession.revoked_at.is_(None),
            )
        ).all()
        active_memberships = self.db.scalars(
            select(Membership).where(
                Membership.tenant_id == principal.tenant_id,
                Membership.user_id == principal.user_id,
                Membership.status == "active",
            )
        ).all()
        personal_workspaces = self.db.scalars(
            select(Workspace).where(
                Workspace.tenant_id == principal.tenant_id,
                Workspace.owner_user_id == principal.user_id,
                Workspace.workspace_kind == "personal",
            )
        ).all()
        owned_organizations = self.db.scalars(
            select(Organization).where(
                Organization.tenant_id == principal.tenant_id,
                Organization.owner_user_id == principal.user_id,
                Organization.status == "active",
            )
        ).all()
        blockers = [
            f"Transfer ownership of organization '{item.name}' before deleting the account"
            for item in owned_organizations
        ]
        if user.is_system_admin:
            other_admin = self.db.scalar(
                select(User.id).where(
                    User.tenant_id == principal.tenant_id,
                    User.id != principal.user_id,
                    User.status == "active",
                    User.is_system_admin.is_(True),
                )
            )
            if other_admin is None:
                blockers.append(
                    "Create another active system administrator before deleting this account"
                )
        return AccountDeletionImpact(
            can_delete=not blockers,
            blockers=blockers,
            active_session_count=len(active_sessions),
            active_membership_count=len(active_memberships),
            personal_workspace_count=len(personal_workspaces),
            owned_organization_count=len(owned_organizations),
        )

    def delete_account(self, payload: DeleteAccountRequest, principal: Principal) -> None:
        user = self._active_user(principal)
        if not verify_password(payload.current_password, user.password_hash):
            self._record_security(
                event_type="auth.account_deletion",
                outcome="failure",
                user=user,
                details={"reason": "invalid_credentials"},
            )
            self.db.commit()
            raise AppError(401, "invalid_credentials", "Current password is incorrect")
        if payload.confirmation != user.username:
            raise AppError(
                422,
                "account_confirmation_mismatch",
                "Confirmation must exactly match the current username",
            )

        impact = self.account_deletion_impact(principal)
        if not impact.can_delete:
            raise AppError(
                409,
                "account_deletion_blocked",
                "Account deletion is blocked until ownership requirements are resolved",
                {"blockers": impact.blockers},
            )

        deleted_at = utc_now()
        sessions = self.db.scalars(
            select(AuthSession).where(
                AuthSession.tenant_id == principal.tenant_id,
                AuthSession.user_id == principal.user_id,
                AuthSession.revoked_at.is_(None),
            )
        ).all()
        for item in sessions:
            item.revoked_at = deleted_at
            item.revoked_reason = "account_deleted"

        memberships = self.db.scalars(
            select(Membership).where(
                Membership.tenant_id == principal.tenant_id,
                Membership.user_id == principal.user_id,
                Membership.status == "active",
            )
        ).all()
        for item in memberships:
            item.status = "revoked"
            item.revoked_at = deleted_at

        acl_grants = self.db.scalars(
            select(ResourceACL).where(
                ResourceACL.tenant_id == principal.tenant_id,
                ResourceACL.grantee_type == "user",
                ResourceACL.grantee_id == principal.user_id,
                ResourceACL.revoked_at.is_(None),
            )
        ).all()
        for item in acl_grants:
            item.revoked_at = deleted_at

        personal_workspaces = self.db.scalars(
            select(Workspace).where(
                Workspace.tenant_id == principal.tenant_id,
                Workspace.owner_user_id == principal.user_id,
                Workspace.workspace_kind == "personal",
            )
        ).all()
        for workspace in personal_workspaces:
            workspace.name = "Deleted account workspace"

        other_active_user = self.db.scalar(
            select(User.id).where(
                User.tenant_id == principal.tenant_id,
                User.id != principal.user_id,
                User.status == "active",
            )
        )
        if other_active_user is None:
            tenant = self.db.get(Tenant, principal.tenant_id)
            if tenant is not None:
                tenant.name = "Deleted account tenant"

        self._record_security(
            event_type="auth.account_deleted",
            outcome="success",
            user=user,
            details={
                "revoked_session_count": len(sessions),
                "revoked_membership_count": len(memberships),
                "revoked_acl_count": len(acl_grants),
                "preserved_personal_workspace_count": len(personal_workspaces),
            },
        )
        deleted_identity = f"deleted-{user.id}"
        user.username = deleted_identity
        user.username_normalized = deleted_identity
        user.email = None
        user.email_normalized = None
        user.display_name = "Deleted account"
        user.password_hash = hash_password(new_session_token())
        user.status = "deleted"
        user.is_system_admin = False
        user.must_change_password = False
        user.failed_login_count = 0
        user.locked_until = None
        self.db.commit()

    def _active_user(self, principal: Principal) -> User:
        user = self.db.get(User, principal.user_id)
        if user is None or user.tenant_id != principal.tenant_id or user.status != "active":
            raise AppError(401, "unauthorized", "The authenticated user is no longer active")
        return user

    def _record_security(
        self,
        *,
        event_type: str,
        outcome: str,
        user: User | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        ip_address: str = "",
        user_agent: str = "",
        details: dict | None = None,
    ) -> None:
        self.db.add(
            SecurityEvent(
                tenant_id=tenant_id or (user.tenant_id if user is not None else None),
                user_id=user_id or (user.id if user is not None else None),
                event_type=event_type,
                outcome=outcome,
                ip_address=ip_address[:64],
                user_agent=user_agent[:500],
                details=details or {},
            )
        )
