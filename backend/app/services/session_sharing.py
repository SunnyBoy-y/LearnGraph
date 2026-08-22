from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import or_, select, update

from app.core.errors import AppError
from app.domain.models import (
    ChatSession,
    Message,
    SessionShare,
    SessionShareMessage,
    SessionShareToken,
    utc_now,
)
from app.repositories.audit import AuditRepository

# --------------------------------------------------------------------------- #
# Display-safe part projection
# --------------------------------------------------------------------------- #
# Part types kept verbatim because they are plain text (reasoning is shown
# collapsible in the read-only view).
_TEXT_KINDS = {
    "acknowledgement",
    "text",
    "reasoning_summary",
    "reasoning_content",
    "agent_step",
}
# Source citations keep only their human-readable surface; internal file ids
# and chunk pointers are dropped so the snapshot cannot resolve them.
_SOURCE_KIND = "source_list"
# Interactive / reference-bearing parts cannot be replayed inside a public
# read-only view without leaking scope. They degrade to a placeholder unless
# they carry a static ``preview_snapshot`` (e.g. a published Magic Card).
_DEGRADE_KINDS = {
    "tool_call",
    "fetch_authorization",
    "fetch_setup_notice",
    "egress_authorization",
    "graph_context",
    "quiz",
    "chart",
    "sandbox",
    "sandbox_artifact",
    "subapp_artifact",
    "subapp_event",
    "sandbox_status",
    "subagent_task",
    "skill_trigger",
    "component",
    "magic_card",
    "user_confirmation",
    "graph_progress",
    "image",
    "attachment",
    "document_selection",
    "selection_quote",
    "error",
}


def _sanitize_parts(parts: list[dict]) -> list[dict]:
    """Project message parts onto a display-safe subset for the snapshot."""
    out: list[dict] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        p = dict(part)
        kind = str(p.get("type") or "")
        if kind in _TEXT_KINDS:
            out.append(p)
            continue
        if kind == _SOURCE_KIND:
            raw = p.get("data") or {}
            raw_items = raw.get("items") if isinstance(raw, dict) else raw
            items: list[dict] = []
            if isinstance(raw_items, list):
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    items.append(
                        {
                            "title": str(item.get("title") or ""),
                            "url": str(item.get("url") or ""),
                            "locator": str(item.get("locator") or ""),
                            "quote": str(item.get("quote") or "")[:500],
                        }
                    )
            out.append(
                {
                    **p,
                    "content": p.get("content") or "",
                    "data": {"items": items},
                }
            )
            continue
        # Interactive / reference-bearing parts.
        data = p.get("data")
        preview = data.get("preview_snapshot") if isinstance(data, dict) else None
        if kind in ("magic_card", "component") and isinstance(preview, dict) and preview:
            out.append(
                {
                    **p,
                    "content": "",
                    "data": {"preview_snapshot": preview, "degraded": False},
                }
            )
            continue
        name = ""
        if isinstance(data, dict):
            name = str(data.get("name") or data.get("title") or "")
        placeholder: dict = {
            **p,
            "content": "",
            "data": {
                "degraded": True,
                "reason": "该附件或交互内容未随分享公开",
            },
        }
        if name:
            placeholder["data"]["name"] = name
        out.append(placeholder)
    return out


class SessionSharingService:
    """Immutable session snapshots + revocable read-only share tokens.

    Memory isolation is structural: snapshot materialization reads only
    ``ChatSession`` + ``Message`` rows and the public resolve path never
    touches memory projections, provider traces, files, or identity data.
    """

    def __init__(
        self,
        db,
        workspace_id: str,
        actor_id: str,
        tenant_id: str,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.tenant_id = tenant_id

    # ------------------------------------------------------------------ #
    # Authenticated operations
    # ------------------------------------------------------------------ #

    def create_share(
        self,
        session_id: str,
        *,
        scope: str = "full",
        from_message_id: str | None = None,
        to_message_id: str | None = None,
        answers_only: bool = False,
        label: str = "",
        expires_at: datetime | None = None,
        max_views: int | None = None,
    ) -> tuple[str, SessionShareToken]:
        """Freeze the selected message range and mint one share token."""
        self._session_for_workspace(session_id)
        messages = list(
            self.db.scalars(
                select(Message)
                .where(
                    Message.workspace_id == self.workspace_id,
                    Message.session_id == session_id,
                )
                .order_by(Message.created_at.asc(), Message.id.asc())
            )
        )
        if not messages:
            raise AppError(400, "session_share_empty", "Session has no messages to share")

        if scope == "range":
            if not from_message_id or not to_message_id:
                raise AppError(
                    400,
                    "session_share_range_invalid",
                    "from_message_id and to_message_id are required for range sharing",
                )
            ids = [m.id for m in messages]
            try:
                start = ids.index(from_message_id)
                end = ids.index(to_message_id)
            except ValueError as exc:
                raise AppError(
                    400,
                    "session_share_range_invalid",
                    "Range boundaries are not in this session",
                ) from exc
            if start > end:
                start, end = end, start
            messages = messages[start : end + 1]
        if scope == "answers" or answers_only:
            messages = [m for m in messages if m.role != "user"]
        if not messages:
            raise AppError(
                400,
                "session_share_empty",
                "Selected range has no messages to share",
            )

        session = self.db.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.workspace_id == self.workspace_id,
            )
        )
        share = SessionShare(
            workspace_id=self.workspace_id,
            tenant_id=self.tenant_id,
            source_session_id=session_id,
            title=(session.title or "对话分享")[:240] if session else "对话分享",
            scope=scope if scope in ("full", "range", "answers") else "full",
            from_message_id=from_message_id if scope == "range" else None,
            to_message_id=to_message_id if scope == "range" else None,
            message_count=len(messages),
            created_by=self.actor_id,
            status="active",
        )
        self.db.add(share)
        self.db.flush()
        for ordinal, message in enumerate(messages):
            self.db.add(
                SessionShareMessage(
                    workspace_id=self.workspace_id,
                    session_share_id=share.id,
                    ordinal=ordinal,
                    role=message.role,
                    content=message.content or "",
                    parts=_sanitize_parts(message.parts or []),
                    parent_message_id=message.parent_message_id,
                    source_message_id=message.id,
                )
            )

        raw_token, token = self._create_token(
            share.id, label=label, expires_at=expires_at, max_views=max_views
        )
        AuditRepository(self.db, self.workspace_id).record(
            actor_id=self.actor_id,
            action="session_share.create",
            resource_type="session_share",
            resource_id=share.id,
            details={
                "session_id": session_id,
                "scope": share.scope,
                "message_count": share.message_count,
            },
        )
        self.db.commit()
        return raw_token, token

    def list_shares(self, session_id: str) -> list[SessionShare]:
        self._session_for_workspace(session_id)
        return list(
            self.db.scalars(
                select(SessionShare)
                .where(
                    SessionShare.workspace_id == self.workspace_id,
                    SessionShare.source_session_id == session_id,
                    SessionShare.status != "deleted",
                )
                .order_by(SessionShare.created_at.desc())
            )
        )

    def list_tokens(self, share_id: str) -> list[SessionShareToken]:
        share = self._share_for_workspace(share_id)
        return list(
            self.db.scalars(
                select(SessionShareToken)
                .where(SessionShareToken.session_share_id == share.id)
                .order_by(SessionShareToken.created_at.desc())
            )
        )

    def revoke_token(self, token_id: str) -> SessionShareToken:
        token = self.db.scalar(
            select(SessionShareToken)
            .join(SessionShare, SessionShare.id == SessionShareToken.session_share_id)
            .where(
                SessionShareToken.id == token_id,
                SessionShare.workspace_id == self.workspace_id,
            )
        )
        if token is None:
            raise AppError(404, "session_share_not_found", "Session share was not found")
        token.revoked_at = utc_now()
        AuditRepository(self.db, self.workspace_id).record(
            actor_id=self.actor_id,
            action="session_share.revoke_token",
            resource_type="session_share_token",
            resource_id=token.id,
            details={"share_id": token.session_share_id},
        )
        self.db.commit()
        self.db.refresh(token)
        return token

    def revoke_share(self, share_id: str) -> SessionShare:
        share = self._share_for_workspace(share_id)
        share.status = "revoked"
        self.db.execute(
            update(SessionShareToken)
            .where(
                SessionShareToken.session_share_id == share.id,
                SessionShareToken.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now())
        )
        AuditRepository(self.db, self.workspace_id).record(
            actor_id=self.actor_id,
            action="session_share.revoke",
            resource_type="session_share",
            resource_id=share.id,
        )
        self.db.commit()
        self.db.refresh(share)
        return share

    # ------------------------------------------------------------------ #
    # Public, unauthenticated
    # ------------------------------------------------------------------ #

    def resolve_share(
        self,
        raw_token: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[SessionShare, list[SessionShareMessage]]:
        """Resolve + atomically claim one view; record the visitor fingerprint."""
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token = self.db.scalar(
            select(SessionShareToken).where(SessionShareToken.token_hash == digest)
        )
        now = utc_now()
        if (
            token is None
            or token.revoked_at is not None
            or (token.expires_at is not None and token.expires_at <= now)
            or (token.max_views is not None and token.view_count >= token.max_views)
        ):
            raise AppError(404, "session_share_not_found", "Session share was not found")
        share = self.db.get(SessionShare, token.session_share_id)
        if share is None or share.status != "active":
            raise AppError(404, "session_share_not_found", "Session share was not found")
        claimed = self.db.execute(
            update(SessionShareToken)
            .where(
                SessionShareToken.id == token.id,
                SessionShareToken.revoked_at.is_(None),
                or_(
                    SessionShareToken.expires_at.is_(None),
                    SessionShareToken.expires_at > now,
                ),
                or_(
                    SessionShareToken.max_views.is_(None),
                    SessionShareToken.view_count < SessionShareToken.max_views,
                ),
            )
            .values(
                view_count=SessionShareToken.view_count + 1,
                last_viewed_at=now,
                last_viewer={
                    "ip": (ip or "")[:64],
                    "user_agent": (user_agent or "")[:400],
                    "viewed_at": now.isoformat(),
                },
            )
        )
        if claimed.rowcount != 1:
            self.db.rollback()
            raise AppError(404, "session_share_not_found", "Session share was not found")
        messages = list(
            self.db.scalars(
                select(SessionShareMessage)
                .where(SessionShareMessage.session_share_id == share.id)
                .order_by(SessionShareMessage.ordinal.asc())
            )
        )
        AuditRepository(self.db, share.workspace_id).record(
            actor_id="anonymous",
            action="session_share.view",
            resource_type="session_share",
            resource_id=share.id,
            details={
                "token_prefix": token.token_prefix,
                "ip": (ip or "")[:64],
                "user_agent_present": bool(user_agent),
            },
        )
        self.db.commit()
        return share, messages

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _create_token(
        self,
        share_id: str,
        *,
        label: str,
        expires_at: datetime | None,
        max_views: int | None,
    ) -> tuple[str, SessionShareToken]:
        raw_token = secrets.token_urlsafe(32)
        record = SessionShareToken(
            workspace_id=self.workspace_id,
            tenant_id=self.tenant_id,
            session_share_id=share_id,
            created_by=self.actor_id,
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            token_prefix=raw_token[:12],
            label=label[:120],
            expires_at=expires_at,
            max_views=max_views if max_views and max_views > 0 else None,
        )
        self.db.add(record)
        self.db.flush()
        return raw_token, record

    def _session_for_workspace(self, session_id: str) -> ChatSession:
        session = self.db.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.workspace_id == self.workspace_id,
            )
        )
        if session is None:
            raise AppError(
                404, "session_not_found", "Session was not found in this workspace"
            )
        return session

    def _share_for_workspace(self, share_id: str) -> SessionShare:
        share = self.db.scalar(
            select(SessionShare).where(
                SessionShare.id == share_id,
                SessionShare.workspace_id == self.workspace_id,
            )
        )
        if share is None or share.status == "deleted":
            raise AppError(404, "session_share_not_found", "Session share was not found")
        return share
