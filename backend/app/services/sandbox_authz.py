"""Destructive command policy + user grants for session workspaces."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import CapabilityGrant, SandboxDestructiveGrant, new_id, utc_now
from app.providers.remote.sandbox import (
    DESTRUCTIVE_COMMANDS,
    SandboxCapabilityMismatch,
    validate_agent_workspace_path,
)
from app.repositories.audit import AuditRepository


DEFAULT_GRANT_TTL_SECONDS = 5 * 60


def classify_destructive_argv(argv: tuple[str, ...]) -> dict[str, Any] | None:
    """Return a destructive intent description, or None if not destructive."""

    if not argv:
        return None
    executable = PurePosixPath(argv[0].replace("\\", "/")).name.casefold()
    if executable not in DESTRUCTIVE_COMMANDS:
        # Soft-detect python shutil-style is out of scope; only argv policy.
        return None
    paths: list[str] = []
    for item in argv[1:]:
        if item.startswith("-"):
            continue
        # Reject absolute / drive paths at classification time.
        if "\\" in item or ":" in item or item.startswith("/") or ".." in item:
            return {
                "action": "delete_path",
                "paths": [item],
                "hard_blocked": True,
                "reason": "Destructive command targets a non-portable or host-like path",
            }
        try:
            paths.append(validate_agent_workspace_path(item))
        except SandboxCapabilityMismatch:
            return {
                "action": "delete_path",
                "paths": [item],
                "hard_blocked": True,
                "reason": "Destructive command path is not a valid session-relative path",
            }
    if not paths:
        return {
            "action": "delete_path",
            "paths": [],
            "hard_blocked": True,
            "reason": "Destructive command without a scoped workspace path is blocked",
        }
    return {
        "action": "delete_path",
        "paths": paths,
        "hard_blocked": False,
        "reason": "Session workspace delete requires explicit user authorization",
    }


def destructive_intent_digest(
    *,
    chat_session_id: str,
    sandbox_session_id: str,
    argv: tuple[str, ...],
    paths: tuple[str, ...],
) -> str:
    encoded = json.dumps(
        {
            "action": "delete_path",
            "argv": list(argv),
            "chat_session_id": chat_session_id,
            "paths": list(paths),
            "sandbox_session_id": sandbox_session_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SandboxAuthorizationService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.audit = AuditRepository(db, workspace_id)

    def list_grants(self, chat_session_id: str) -> list[SandboxDestructiveGrant]:
        return list(
            self.db.scalars(
                select(SandboxDestructiveGrant)
                .where(
                    SandboxDestructiveGrant.workspace_id == self.workspace_id,
                    SandboxDestructiveGrant.owner_user_id == self.actor_id,
                    SandboxDestructiveGrant.chat_session_id == chat_session_id,
                    SandboxDestructiveGrant.status == "active",
                )
                .order_by(SandboxDestructiveGrant.created_at.desc())
            ).all()
        )

    def grant(
        self,
        *,
        chat_session_id: str,
        path_prefix: str,
        action: str = "delete_path",
        sandbox_session_id: str,
        command_intent_digest: str,
        ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
        reason: str = "",
    ) -> SandboxDestructiveGrant:
        if action != "delete_path":
            raise AppError(422, "invalid_grant_action", "Only delete_path grants are supported")
        prefix = validate_agent_workspace_path(path_prefix)
        # Only allow destructive grants under work/ for MVP safety.
        if not (prefix == "work" or prefix.startswith("work/")):
            raise AppError(
                422,
                "sandbox_grant_path_blocked",
                "Destructive grants are limited to the session work/ tree",
            )
        ttl = max(60, min(ttl_seconds, 24 * 3600))
        if len(command_intent_digest) != 64 or any(
            character not in "0123456789abcdef" for character in command_intent_digest
        ):
            raise AppError(
                422,
                "invalid_command_intent_digest",
                "Command intent digest must be a lowercase SHA-256 hex value",
            )
        record = SandboxDestructiveGrant(
            id=new_id(),
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            chat_session_id=chat_session_id,
            sandbox_session_id=sandbox_session_id,
            action=action,
            path_prefix=prefix,
            command_intent_digest=command_intent_digest,
            status="active",
            granted_by=self.actor_id,
            expires_at=utc_now() + timedelta(seconds=ttl),
            reason=reason[:500],
        )
        self.db.add(record)
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.destructive.granted",
            resource_type="sandbox_destructive_grant",
            resource_id=record.id,
            details={
                "path_prefix": prefix,
                "action": action,
                "ttl_seconds": ttl,
                "chat_session_id": chat_session_id,
                "sandbox_session_id": sandbox_session_id,
                "command_intent_digest": command_intent_digest,
            },
        )
        self.db.commit()
        self.db.refresh(record)
        return record

    def revoke(self, grant_id: str) -> SandboxDestructiveGrant:
        record = self.db.scalar(
            select(SandboxDestructiveGrant).where(
                SandboxDestructiveGrant.id == grant_id,
                SandboxDestructiveGrant.workspace_id == self.workspace_id,
                SandboxDestructiveGrant.owner_user_id == self.actor_id,
            )
        )
        if record is None:
            raise AppError(404, "sandbox_grant_not_found", "Destructive grant was not found")
        record.status = "revoked"
        self.audit.record(
            actor_id=self.actor_id,
            action="sandbox.destructive.revoked",
            resource_type="sandbox_destructive_grant",
            resource_id=record.id,
        )
        self.db.commit()
        self.db.refresh(record)
        return record

    def has_active_grant(self, *, chat_session_id: str, path: str, action: str = "delete_path") -> bool:
        try:
            safe = validate_agent_workspace_path(path)
        except SandboxCapabilityMismatch:
            return False
        now = utc_now()
        grants = self.list_grants(chat_session_id)
        for grant in grants:
            expires = grant.expires_at
            if expires.tzinfo is None:
                from datetime import timezone

                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                grant.status = "expired"
                continue
            if grant.consumed_at is not None:
                continue
            if grant.action != action:
                continue
            prefix = grant.path_prefix
            if safe == prefix or safe.startswith(prefix.rstrip("/") + "/"):
                return True
        self.db.commit()
        return False

    def active_delete_prefixes(
        self,
        *,
        chat_session_id: str,
        sandbox_session_id: str,
        consume: bool = False,
    ) -> tuple[str, ...]:
        now = utc_now()
        prefixes: list[str] = []
        for grant in self.list_grants(chat_session_id):
            expires = grant.expires_at
            if expires.tzinfo is None:
                from datetime import timezone

                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                grant.status = "expired"
                continue
            if grant.action != "delete_path":
                continue
            if consume and grant.sandbox_session_id != sandbox_session_id:
                continue
            if not consume and grant.sandbox_session_id not in {None, sandbox_session_id}:
                continue
            prefixes.append(grant.path_prefix)
            if consume:
                claimed = self.db.execute(
                    update(SandboxDestructiveGrant)
                    .where(
                        SandboxDestructiveGrant.id == grant.id,
                        SandboxDestructiveGrant.status == "active",
                        SandboxDestructiveGrant.consumed_at.is_(None),
                    )
                    .values(status="consumed", consumed_at=now)
                )
                if claimed.rowcount != 1:
                    self.db.rollback()
                    raise AppError(
                        409,
                        "sandbox_grant_already_consumed",
                        "The destructive authorization has already been consumed",
                    )
        self.db.commit()
        return tuple(sorted(set(prefixes)))

    def consume_delete_prefixes(
        self,
        *,
        chat_session_id: str,
        sandbox_session_id: str,
        paths: tuple[str, ...],
        command_intent_digest: str,
    ) -> tuple[str, ...]:
        """Atomically consume only grants needed by this exact command."""

        now = utc_now()
        matches: dict[str, SandboxDestructiveGrant] = {}
        for raw_path in paths:
            path = validate_agent_workspace_path(raw_path)
            candidate = next(
                (
                    grant
                    for grant in self.list_grants(chat_session_id)
                    if grant.sandbox_session_id == sandbox_session_id
                    and grant.command_intent_digest == command_intent_digest
                    and grant.consumed_at is None
                    and grant.action == "delete_path"
                    and (path == grant.path_prefix or path.startswith(grant.path_prefix.rstrip("/") + "/"))
                    and (
                        grant.expires_at.replace(tzinfo=timezone.utc)
                        if grant.expires_at.tzinfo is None
                        else grant.expires_at
                    )
                    > now
                ),
                None,
            )
            if candidate is None:
                self.db.rollback()
                raise AppError(
                    403,
                    "sandbox_auth_required",
                    "Destructive session workspace action requires a fresh single-use authorization",
                    details={
                        "action": "delete_path",
                        "paths": [path],
                        "chat_session_id": chat_session_id,
                        "sandbox_session_id": sandbox_session_id,
                        "command_intent_digest": command_intent_digest,
                        "affects_host_files": False,
                        "message_zh": "智能体请求删除会话工作区内的文件；本次授权使用后立即失效。",
                    },
                )
            matches[candidate.id] = candidate
        for grant in matches.values():
            claimed = self.db.execute(
                update(SandboxDestructiveGrant)
                .where(
                    SandboxDestructiveGrant.id == grant.id,
                    SandboxDestructiveGrant.status == "active",
                    SandboxDestructiveGrant.consumed_at.is_(None),
                )
                .values(status="consumed", consumed_at=now)
            )
            if claimed.rowcount != 1:
                self.db.rollback()
                raise AppError(
                    409,
                    "sandbox_grant_already_consumed",
                    "The destructive authorization has already been consumed",
                )
        self.db.commit()
        return tuple(sorted({grant.path_prefix for grant in matches.values()}))

    def authorize_or_raise(
        self,
        *,
        chat_session_id: str,
        argv: tuple[str, ...],
    ) -> dict[str, Any] | None:
        """Return None if allowed; raise AppError if blocked / needs auth."""

        intent = classify_destructive_argv(argv)
        if intent is None:
            return None
        if intent.get("hard_blocked"):
            self.audit.record(
                actor_id=self.actor_id,
                action="sandbox.agent.command.blocked",
                resource_type="sandbox_agent_policy",
                resource_id=chat_session_id,
                outcome="blocked",
                details={"reason": intent["reason"], "paths": intent.get("paths")},
            )
            self.db.commit()
            raise AppError(422, "sandbox_command_blocked", intent["reason"])
        paths = list(intent.get("paths") or [])
        missing = [
            path
            for path in paths
            if not self.has_active_grant(chat_session_id=chat_session_id, path=path)
        ]
        if missing:
            self.audit.record(
                actor_id=self.actor_id,
                action="sandbox.agent.command.auth_required",
                resource_type="sandbox_agent_policy",
                resource_id=chat_session_id,
                outcome="blocked",
                details={"paths": missing, "action": "delete_path"},
            )
            self.db.commit()
            raise AppError(
                403,
                "sandbox_auth_required",
                "Destructive session workspace action requires user authorization",
                details={
                    "action": "delete_path",
                    "paths": missing,
                    "chat_session_id": chat_session_id,
                    "affects_host_files": False,
                    "message_zh": "智能体请求删除会话工作区内的文件；不影响你电脑上的真实文件。",
                },
            )
        return intent

    # ---- Generic capability grant (P1) ----

    def create_capability_grant(
        self,
        *,
        action: str,
        resources: dict[str, Any] | None = None,
        chat_session_id: str | None = None,
        sandbox_session_id: str | None = None,
        command_intent_digest: str | None = None,
        session_origin: str | None = None,
        agent_id: str | None = None,
        single_use: bool = True,
        ttl_seconds: int = DEFAULT_GRANT_TTL_SECONDS,
        reason: str = "",
    ) -> CapabilityGrant:
        ttl = max(60, min(ttl_seconds, 24 * 3600))
        record = CapabilityGrant(
            id=new_id(),
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            action=action,
            resources=resources or {},
            chat_session_id=chat_session_id,
            sandbox_session_id=sandbox_session_id,
            command_intent_digest=command_intent_digest,
            session_origin=session_origin,
            agent_id=agent_id,
            status="active",
            single_use=single_use,
            granted_by=self.actor_id,
            expires_at=utc_now() + timedelta(seconds=ttl),
            reason=reason[:500],
        )
        self.db.add(record)
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="capability.granted",
            resource_type="capability_grant",
            resource_id=record.id,
            details={
                "action": action,
                "resources": resources,
                "ttl_seconds": ttl,
                "single_use": single_use,
            },
        )
        self.db.commit()
        self.db.refresh(record)
        return record

    def consume_capability_grant(
        self,
        grant_id: str,
    ) -> CapabilityGrant | None:
        """Atomically consume a single-use grant.  Returns the grant or None."""
        now = utc_now()
        grant = self.db.scalar(
            select(CapabilityGrant).where(
                CapabilityGrant.id == grant_id,
                CapabilityGrant.workspace_id == self.workspace_id,
                CapabilityGrant.owner_user_id == self.actor_id,
                CapabilityGrant.status == "active",
                CapabilityGrant.expires_at > now,
                CapabilityGrant.consumed_at.is_(None),
            )
        )
        if grant is None:
            return None
        if grant.single_use:
            grant.status = "consumed"
            grant.consumed_at = now
        else:
            grant.usage_count = CapabilityGrant.usage_count + 1
            if grant.usage_count >= grant.usage_limit:
                grant.status = "consumed"
                grant.consumed_at = now
        self.audit.record(
            actor_id=self.actor_id,
            action="capability.consumed",
            resource_type="capability_grant",
            resource_id=grant.id,
            details={"usage_count": grant.usage_count},
        )
        self.db.commit()
        return grant

    def revoke_capability_grant(self, grant_id: str) -> CapabilityGrant:
        record = self.db.scalar(
            select(CapabilityGrant).where(
                CapabilityGrant.id == grant_id,
                CapabilityGrant.workspace_id == self.workspace_id,
                CapabilityGrant.owner_user_id == self.actor_id,
            )
        )
        if record is None:
            raise AppError(404, "capability_grant_not_found", "Capability grant was not found")
        record.status = "revoked"
        record.revoked_at = utc_now()
        self.audit.record(
            actor_id=self.actor_id,
            action="capability.revoked",
            resource_type="capability_grant",
            resource_id=record.id,
        )
        self.db.commit()
        self.db.refresh(record)
        return record
