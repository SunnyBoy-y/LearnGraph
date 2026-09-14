"""Artifact Card index: aggregate interactive HTML cards emitted in chat.

One row per stable ``card_id`` per workspace. The indexer runs at the single
MessagePart persistence point (``ChatService._emit_sandbox_side_effect_parts``)
so every card-producing path (main stream, retry, agent canvas tools) is
captured. Cards start as ``draft``; publishing freezes a versioned snapshot
into :class:`ArtifactCardVersion` and flips the card to ``published``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.domain.models import (
    ArtifactCard,
    ArtifactCardShareToken,
    ArtifactCardVersion,
    utc_now,
)
from app.services.provider_secrets import (
    PROVIDER_SECRET_ALGORITHM,
    ProviderSecretUnavailable,
    decrypt_secret_fields,
    encrypt_provider_secret,
)

logger = logging.getLogger(__name__)

# Runtimes that round-trip user events back to the agent (bidirectional card).
BIDIRECTIONAL_RUNTIMES = frozenset({"react-sandbox-v1", "opaque-origin-subapp-v1"})

# Part types indexed as cards.
INDEXED_PART_TYPES = frozenset({"magic_card", "component"})

# Card lifecycle states.
CARD_STATUS_DRAFT = "draft"
CARD_STATUS_PUBLISHED = "published"
CARD_STATUS_DELETED = "deleted"


def _clean_title(value: Any, fallback: str = "交互卡片") -> str:
    if not isinstance(value, str):
        return fallback
    title = " ".join(value.split())
    return title[:240] or fallback


def _aware(value: datetime) -> datetime:
    """SQLite drops tzinfo on storage; treat stored timestamps as UTC."""

    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _share_token_columns(token: ArtifactCardShareToken) -> dict[str, Any]:
    """Detached snapshot of a share row so it survives a hard delete."""

    return {
        column.name: getattr(token, column.name)
        for column in ArtifactCardShareToken.__table__.columns
    }


def _share_token_expired(token: ArtifactCardShareToken) -> bool:
    """True when the link can no longer grant a view (revoked / expired / capped)."""

    if token.revoked_at is not None:
        return True
    if token.expires_at is not None and _aware(token.expires_at) <= utc_now():
        return True
    return token.max_views is not None and token.view_count >= token.max_views


def _encrypted_share_token_columns(raw_token: str) -> dict[str, Any]:
    """Best-effort encrypted copy of the raw token for 「已分享页面」 re-copy.

    Encryption is metadata, not the resolution path (``token_hash`` is), so a
    missing master key must never fail share creation: the row is then simply
    not re-copyable from the management view.
    """

    try:
        encrypted = encrypt_provider_secret(get_settings(), raw_token)
    except Exception:  # noqa: BLE001 - optional metadata, never block sharing
        logger.warning(
            "Card share token encryption unavailable; the link cannot be re-copied later",
            exc_info=True,
        )
        return {
            "token_ciphertext": None,
            "token_algorithm": None,
            "token_key_provider": None,
            "token_key_version": None,
        }
    return {
        "token_ciphertext": encrypted.ciphertext,
        "token_algorithm": encrypted.algorithm,
        "token_key_provider": encrypted.key_provider,
        "token_key_version": encrypted.key_version,
    }


def _card_identity(part_type: str, data: dict[str, Any]) -> tuple[str, str] | None:
    """Return (card_id, card_instance_id) for an indexed part, or None."""
    if not isinstance(data, dict):
        return None
    if part_type == "magic_card":
        card_id = data.get("card_id")
        instance_id = data.get("card_instance_id")
    elif part_type == "component":
        card_id = data.get("component_id")
        instance_id = data.get("component_id")
    else:
        return None
    if isinstance(card_id, str) and card_id.strip():
        return card_id.strip()[:64], (instance_id if isinstance(instance_id, str) else card_id)[:64]
    return None


def _is_interactive(part_type: str, data: dict[str, Any]) -> bool:
    if part_type == "magic_card":
        runtime = data.get("runtime")
        return isinstance(runtime, str) and runtime in BIDIRECTIONAL_RUNTIMES
    if part_type == "component":
        allowed_events = data.get("allowed_events")
        return isinstance(allowed_events, list) and len(allowed_events) > 0
    return False


def _snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    """Hash render-affecting data while ignoring transport instance metadata."""
    normalized = deepcopy(snapshot)
    for key in ("card_instance_id", "message_version_id", "chat_session_id"):
        normalized.pop(key, None)
    return hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ArtifactCardIndexer:
    """Upserts card rows from completed MessageParts at emit time."""

    @staticmethod
    def upsert_from_part(
        db: Session,
        *,
        workspace_id: str,
        tenant_id: str,
        chat_session_id: str | None,
        message_id: str | None,
        message_version_id: str | None,
        part_id: str | None,
        part_type: str,
        data: dict[str, Any] | None,
    ) -> ArtifactCard | None:
        if part_type not in INDEXED_PART_TYPES:
            return None
        payload = data if isinstance(data, dict) else {}
        identity = _card_identity(part_type, payload)
        if identity is None:
            return None
        card_id, instance_id = identity

        snapshot = dict(payload)
        # Renderers may need the origin session even after message pruning.
        if chat_session_id and "chat_session_id" not in snapshot:
            snapshot["chat_session_id"] = chat_session_id
        if message_version_id and "message_version_id" not in snapshot:
            snapshot["message_version_id"] = message_version_id

        existing = db.scalar(
            select(ArtifactCard).where(
                ArtifactCard.workspace_id == workspace_id,
                ArtifactCard.card_id == card_id,
            )
        )
        if existing is not None:
            if existing.status == CARD_STATUS_DELETED:
                # A deleted card re-emitted by the agent becomes a fresh draft.
                existing.status = CARD_STATUS_DRAFT
                existing.deleted_at = None
            existing.card_instance_id = instance_id
            existing.card_type = part_type
            existing.interactive = _is_interactive(part_type, payload)
            existing.title = _clean_title(payload.get("title") or payload.get("fallback_text"))
            existing.chat_session_id = chat_session_id or existing.chat_session_id
            existing.message_id = message_id or existing.message_id
            existing.message_version_id = message_version_id or existing.message_version_id
            existing.part_id = part_id or existing.part_id
            changed = _snapshot_fingerprint(existing.preview_snapshot or {}) != _snapshot_fingerprint(snapshot)
            existing.preview_snapshot = snapshot
            db.flush()
            if changed:
                # A substantive AI edit reserves the next revision immediately.
                # It stays a draft until the user/agent publishes that revision.
                latest_revision = db.scalar(
                    select(ArtifactCardVersion)
                    .where(ArtifactCardVersion.card_id == existing.id)
                    .order_by(ArtifactCardVersion.version.desc())
                )
                if latest_revision is None or _snapshot_fingerprint(latest_revision.preview_snapshot or {}) != _snapshot_fingerprint(snapshot):
                    db.add(
                        ArtifactCardVersion(
                            tenant_id=tenant_id,
                            workspace_id=workspace_id,
                            card_id=existing.id,
                            version=(latest_revision.version if latest_revision else 0) + 1,
                            preview_snapshot=deepcopy(snapshot),
                            release_notes="",
                            published_by="agent",
                            publish_source="agent",
                            status="draft",
                        )
                    )
                    db.flush()
            db.refresh(existing)
            return existing

        row = ArtifactCard(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            card_id=card_id,
            card_instance_id=instance_id,
            card_type=part_type,
            interactive=_is_interactive(part_type, payload),
            title=_clean_title(payload.get("title") or payload.get("fallback_text")),
            status=CARD_STATUS_DRAFT,
            chat_session_id=chat_session_id,
            message_id=message_id,
            message_version_id=message_version_id,
            part_id=part_id,
            preview_snapshot=snapshot,
        )
        db.add(row)
        db.flush()
        # Reserve v1 at creation time; publishing changes this revision's state
        # instead of creating a second version number.
        db.add(
            ArtifactCardVersion(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                card_id=row.id,
                version=1,
                preview_snapshot=deepcopy(snapshot),
                release_notes="",
                published_by="agent",
                publish_source="agent",
                status="draft",
            )
        )
        db.flush()
        db.refresh(row)
        return row


class ArtifactCardService:
    """Query, versioning, and sharing operations over the card index."""

    def __init__(self, db: Session, workspace_id: str, tenant_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.tenant_id = tenant_id

    # ------------------------------------------------------------------ #
    # Cards
    # ------------------------------------------------------------------ #

    def list_cards(
        self,
        *,
        status: str | None = None,
        card_type: str | None = None,
        interactive: bool | None = None,
        sort: str = "updated_at",
        order: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return card views with version stats, newest version first."""
        query = select(ArtifactCard).where(
            ArtifactCard.workspace_id == self.workspace_id,
            ArtifactCard.status != CARD_STATUS_DELETED,
        )
        if status in {CARD_STATUS_DRAFT, CARD_STATUS_PUBLISHED}:
            query = query.where(ArtifactCard.status == status)
        if card_type in {"magic_card", "component"}:
            query = query.where(ArtifactCard.card_type == card_type)
        if interactive is not None:
            query = query.where(ArtifactCard.interactive == interactive)
        sort_column = (
            ArtifactCard.updated_at
            if sort == "updated_at"
            else ArtifactCard.created_at
        )
        column = sort_column.asc() if order == "asc" else sort_column.desc()
        query = query.order_by(column, ArtifactCard.id.desc())
        if limit > 0:
            query = query.limit(min(limit, 200)).offset(max(offset, 0))
        cards = list(self.db.scalars(query))
        if not cards:
            return []
        card_ids = [card.id for card in cards]
        version_rows = self.db.execute(
            select(
                ArtifactCardVersion.card_id,
                func.count(ArtifactCardVersion.id),
                func.max(ArtifactCardVersion.version),
                func.max(ArtifactCardVersion.created_at),
            )
            .where(
                ArtifactCardVersion.card_id.in_(card_ids),
                ArtifactCardVersion.status == "active",
            )
            .group_by(ArtifactCardVersion.card_id)
        ).all()
        draft_rows = self.db.execute(
            select(ArtifactCardVersion.card_id, func.count(ArtifactCardVersion.id))
            .where(
                ArtifactCardVersion.card_id.in_(card_ids),
                ArtifactCardVersion.status == "draft",
            )
            .group_by(ArtifactCardVersion.card_id)
        ).all()
        draft_counts = {card_id: int(count or 0) for card_id, count in draft_rows}
        stats: dict[str, dict[str, Any]] = {}
        for card_id, count, max_version, latest_created in version_rows:
            stats[card_id] = {
                "version_count": int(count or 0),
                "latest_version": int(max_version or 0),
                "latest_version_at": latest_created,
            }
        views: list[dict[str, Any]] = []
        for card in cards:
            card_stats = stats.get(card.id) or {
                "version_count": 0,
                "latest_version": 0,
                "latest_version_at": None,
            }
            views.append(
                {
                    **card.__dict__,
                    "version_count": card_stats["version_count"],
                    "latest_version": card_stats["latest_version"],
                    "draft_dirty": bool(
                        card.status == CARD_STATUS_PUBLISHED
                        and (
                            draft_counts.get(card.id, 0) > 0
                            or (
                                card_stats["latest_version_at"] is not None
                                and card.updated_at > card_stats["latest_version_at"]
                            )
                        )
                    ),
                }
            )
        return views

    def get_preview(
        self, card_id: str, version: int | None = None
    ) -> tuple[ArtifactCard, dict[str, Any]]:
        """Return (card, snapshot). ``version`` selects a frozen snapshot."""
        row = self._card_for_workspace(card_id)
        if row.status == CARD_STATUS_DELETED:
            raise AppError(404, "artifact_card_not_found", "Artifact card was not found")
        if version is not None:
            frozen = self._card_version_for_workspace(card_id, version)
            return row, dict(frozen.preview_snapshot or {})
        return row, dict(row.preview_snapshot or {})

    def delete_card(self, card_id: str) -> ArtifactCard:
        """Soft-delete a card; a later agent re-emit revives it as a draft."""
        row = self._card_for_workspace(card_id)
        if row.status == CARD_STATUS_DELETED:
            raise AppError(404, "artifact_card_not_found", "Artifact card was not found")
        row.status = CARD_STATUS_DELETED
        row.deleted_at = utc_now()
        self.db.commit()
        self.db.refresh(row)
        return row

    # ------------------------------------------------------------------ #
    # Versioned immutable snapshots
    # ------------------------------------------------------------------ #

    def publish_version(
        self,
        card_id: str,
        *,
        release_notes: str = "",
        actor_id: str = "user",
        publish_source: str = "user",
    ) -> ArtifactCardVersion:
        """Publish the latest AI-created draft revision as an immutable version."""
        if publish_source not in {"user", "agent"}:
            publish_source = "user"
        row = self._card_for_workspace(card_id)
        if row.status == CARD_STATUS_DELETED:
            raise AppError(404, "artifact_card_not_found", "Artifact card was not found")
        # Publishing promotes the latest AI-created draft revision. This keeps
        # version identity independent from the publish action.
        version = self.db.scalar(
            select(ArtifactCardVersion)
            .where(
                ArtifactCardVersion.card_id == row.id,
                ArtifactCardVersion.status == "draft",
            )
            .order_by(ArtifactCardVersion.version.desc())
        )
        if version is None:
            current = self.db.scalar(
                select(func.max(ArtifactCardVersion.version)).where(
                    ArtifactCardVersion.card_id == row.id,
                    ArtifactCardVersion.status != "deleted",
                )
            )
            version = ArtifactCardVersion(
                tenant_id=self.tenant_id,
                workspace_id=self.workspace_id,
                card_id=row.id,
                version=(current or 0) + 1,
                preview_snapshot=deepcopy(row.preview_snapshot or {}),
                release_notes=(release_notes or "")[:4000],
                published_by=actor_id,
                publish_source=publish_source,
            )
            self.db.add(version)
        else:
            version.status = "active"
            version.release_notes = (release_notes or "")[:4000]
            version.published_by = actor_id
            version.publish_source = publish_source
        if row.status == CARD_STATUS_DRAFT:
            row.status = CARD_STATUS_PUBLISHED
        self.db.commit()
        self.db.refresh(version)
        return version

    def list_versions(self, card_id: str) -> list[ArtifactCardVersion]:
        row = self._card_for_workspace(card_id)
        if row.status == CARD_STATUS_DELETED:
            raise AppError(404, "artifact_card_not_found", "Artifact card was not found")
        return list(
            self.db.scalars(
                select(ArtifactCardVersion)
                .where(
                    ArtifactCardVersion.card_id == row.id,
                    ArtifactCardVersion.status == "active",
                )
                .order_by(ArtifactCardVersion.version.desc())
            )
        )

    def delete_version(self, version_id: str) -> ArtifactCardVersion:
        version = self._version_for_workspace(version_id)
        if version.status != "active":
            raise AppError(404, "artifact_card_version_not_found", "Artifact card version was not found")
        version.status = "deleted"
        version.deleted_at = utc_now()
        self.db.commit()
        self.db.refresh(version)
        return version

    # ------------------------------------------------------------------ #
    # Share tokens (public read-only HTML viewer)
    # ------------------------------------------------------------------ #

    def create_share_token(
        self,
        version_id: str,
        *,
        label: str = "",
        expires_at: datetime | None = None,
        max_views: int | None = None,
    ) -> tuple[str, ArtifactCardShareToken]:
        version = self._version_for_workspace(version_id)
        if version.status != "active":
            raise AppError(404, "artifact_card_version_not_found", "Artifact card version was not found")
        raw_token = secrets.token_urlsafe(32)
        record = ArtifactCardShareToken(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            artifact_card_version_id=version.id,
            created_by="user",
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            token_prefix=raw_token[:12],
            label=label[:120],
            expires_at=expires_at,
            max_views=max_views if max_views and max_views > 0 else None,
            **_encrypted_share_token_columns(raw_token),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return raw_token, record

    def list_share_tokens(self, version_id: str) -> list[ArtifactCardShareToken]:
        version = self._version_for_workspace(version_id)
        return list(
            self.db.scalars(
                select(ArtifactCardShareToken)
                .where(ArtifactCardShareToken.artifact_card_version_id == version.id)
                .order_by(ArtifactCardShareToken.created_at.desc())
            )
        )

    def list_all_share_tokens(self) -> list[dict[str, Any]]:
        """Return every share in this workspace with card/version context."""
        rows = self.db.execute(
            select(ArtifactCardShareToken, ArtifactCardVersion, ArtifactCard)
            .join(ArtifactCardVersion, ArtifactCardVersion.id == ArtifactCardShareToken.artifact_card_version_id)
            .join(ArtifactCard, ArtifactCard.id == ArtifactCardVersion.card_id)
            .where(
                ArtifactCardShareToken.workspace_id == self.workspace_id,
                ArtifactCardShareToken.tenant_id == self.tenant_id,
                ArtifactCard.status != CARD_STATUS_DELETED,
            )
            .order_by(ArtifactCardShareToken.created_at.desc())
        ).all()
        return [
            {
                **token.__dict__,
                "card_id": card.card_id,
                "card_title": card.title,
                "card_version": version.version,
                "card_type": card.card_type,
                # Legacy rows carry no encrypted token, so 「复制链接」 stays off.
                "share_token_available": bool(token.token_ciphertext),
            }
            for token, version, card in rows
        ]

    def _find_share_token(self, token_id: str) -> ArtifactCardShareToken | None:
        return self.db.scalar(
            select(ArtifactCardShareToken)
            .join(
                ArtifactCardVersion,
                ArtifactCardVersion.id == ArtifactCardShareToken.artifact_card_version_id,
            )
            .join(ArtifactCard, ArtifactCard.id == ArtifactCardVersion.card_id)
            .where(
                ArtifactCardShareToken.id == token_id,
                ArtifactCard.workspace_id == self.workspace_id,
                ArtifactCard.tenant_id == self.tenant_id,
            )
        )

    def _share_token_for_workspace(self, token_id: str) -> ArtifactCardShareToken:
        token = self._find_share_token(token_id)
        if token is None:
            raise AppError(404, "artifact_card_share_not_found", "Artifact card share was not found")
        return token

    def revoke_share_token(self, token_id: str) -> ArtifactCardShareToken:
        token = self._share_token_for_workspace(token_id)
        token.revoked_at = utc_now()
        self.db.commit()
        self.db.refresh(token)
        return token

    def reveal_share_token(self, token_id: str) -> str:
        """Open the encrypted raw token so its share link can be copied again."""

        token = self._share_token_for_workspace(token_id)
        if not token.token_ciphertext:
            raise AppError(
                409,
                "artifact_card_share_token_unavailable",
                "This share link was created before raw tokens were stored; generate a new link instead",
            )
        try:
            return decrypt_secret_fields(
                get_settings(),
                ciphertext=token.token_ciphertext,
                algorithm=token.token_algorithm or PROVIDER_SECRET_ALGORITHM,
                key_provider=token.token_key_provider or "",
                key_version=token.token_key_version or 1,
            )
        except (ProviderSecretUnavailable, ValueError) as exc:
            raise AppError(
                409,
                "artifact_card_share_token_unavailable",
                "This share link cannot be decrypted with the current master key",
            ) from exc

    def delete_share_token(self, token_id: str) -> dict[str, Any]:
        """Permanently drop one share record from the management view.

        Only already-invalid rows can be removed: a live link must be revoked
        first so deleting a record can never silently break an active share.
        """

        token = self._share_token_for_workspace(token_id)
        if not _share_token_expired(token):
            raise AppError(
                409,
                "artifact_card_share_token_active",
                "Revoke this share before deleting its record",
            )
        snapshot = _share_token_columns(token)
        self.db.delete(token)
        self.db.commit()
        return snapshot

    def batch_share_token_action(
        self, token_ids: list[str], *, action: str
    ) -> dict[str, Any]:
        """Revoke or purge many shares in a single transaction.

        Per-item guards mirror the single-row endpoints, but an ineligible row
        never fails the batch — it is reported in ``skipped`` so the UI can say
        exactly what happened. ``revoke`` skips links already revoked;
        ``purge`` skips anything still usable (an active link must be revoked
        before its record can be dropped).
        """

        if action not in {"revoke", "purge"}:
            raise AppError(422, "artifact_card_share_batch_action", "Unsupported batch action")

        affected: list[str] = []
        skipped: list[dict[str, str]] = []
        revoked_at = utc_now()
        for token_id in dict.fromkeys(token_ids):
            token = self._find_share_token(token_id)
            if token is None:
                skipped.append({"id": token_id, "reason": "not_found"})
                continue
            if action == "revoke":
                if token.revoked_at is not None:
                    skipped.append({"id": token_id, "reason": "already_revoked"})
                    continue
                token.revoked_at = revoked_at
            else:
                if not _share_token_expired(token):
                    skipped.append({"id": token_id, "reason": "still_active"})
                    continue
                self.db.delete(token)
            affected.append(token_id)
        # A batch that changes nothing still committed nothing, so skip the commit.
        if affected:
            self.db.commit()
        return {
            "action": action,
            "requested_count": len(set(token_ids)),
            "affected_count": len(affected),
            "skipped": skipped,
        }

    def resolve_card_share(
        self, raw_token: str
    ) -> tuple[ArtifactCardVersion, ArtifactCard]:
        """Resolve + atomically claim one view for a public card share link."""
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token = self.db.scalar(
            select(ArtifactCardShareToken).where(ArtifactCardShareToken.token_hash == digest)
        )
        now = utc_now()
        if (
            token is None
            or token.revoked_at is not None
            or (token.expires_at is not None and token.expires_at <= now)
            or (token.max_views is not None and token.view_count >= token.max_views)
        ):
            raise AppError(404, "artifact_card_share_not_found", "Artifact card share was not found")
        version = self.db.get(ArtifactCardVersion, token.artifact_card_version_id)
        if version is None or version.status != "active":
            raise AppError(404, "artifact_card_share_not_found", "Artifact card share was not found")
        card = self.db.get(ArtifactCard, version.card_id)
        if card is None or card.status == CARD_STATUS_DELETED:
            raise AppError(404, "artifact_card_share_not_found", "Artifact card share was not found")
        claimed = self.db.execute(
            update(ArtifactCardShareToken)
            .where(
                ArtifactCardShareToken.id == token.id,
                ArtifactCardShareToken.revoked_at.is_(None),
                or_(
                    ArtifactCardShareToken.expires_at.is_(None),
                    ArtifactCardShareToken.expires_at > now,
                ),
                or_(
                    ArtifactCardShareToken.max_views.is_(None),
                    ArtifactCardShareToken.view_count < ArtifactCardShareToken.max_views,
                ),
            )
            .values(view_count=ArtifactCardShareToken.view_count + 1)
        )
        if claimed.rowcount != 1:
            self.db.rollback()
            raise AppError(404, "artifact_card_share_not_found", "Artifact card share was not found")
        self.db.commit()
        return version, card

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _card_for_workspace(self, card_id: str) -> ArtifactCard:
        row = self.db.scalar(
            select(ArtifactCard).where(
                ArtifactCard.card_id == card_id,
                ArtifactCard.workspace_id == self.workspace_id,
                ArtifactCard.tenant_id == self.tenant_id,
            )
        )
        if row is None:
            raise AppError(404, "artifact_card_not_found", "Artifact card was not found")
        return row

    def _card_version_for_workspace(self, card_id: str, version: int) -> ArtifactCardVersion:
        row = self._card_for_workspace(card_id)
        frozen = self.db.scalar(
            select(ArtifactCardVersion).where(
                ArtifactCardVersion.card_id == row.id,
                ArtifactCardVersion.version == version,
                ArtifactCardVersion.status == "active",
            )
        )
        if frozen is None:
            raise AppError(404, "artifact_card_version_not_found", "Artifact card version was not found")
        return frozen

    def _version_for_workspace(self, version_id: str) -> ArtifactCardVersion:
        version = self.db.scalar(
            select(ArtifactCardVersion)
            .join(ArtifactCard, ArtifactCard.id == ArtifactCardVersion.card_id)
            .where(
                ArtifactCardVersion.id == version_id,
                ArtifactCard.workspace_id == self.workspace_id,
                ArtifactCard.tenant_id == self.tenant_id,
            )
        )
        if version is None:
            raise AppError(404, "artifact_card_version_not_found", "Artifact card version was not found")
        return version
