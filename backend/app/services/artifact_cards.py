"""Artifact Card index: aggregate interactive HTML cards emitted in chat.

One row per stable ``card_id`` per workspace. The indexer runs at the single
MessagePart persistence point (``ChatService._emit_sandbox_side_effect_parts``)
so every card-producing path (main stream, retry, agent canvas tools) is
captured. Cards start as ``draft``; publishing freezes a versioned snapshot
(second phase).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import ArtifactCard, utc_now

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
            existing.preview_snapshot = snapshot
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
        db.refresh(row)
        return row


class ArtifactCardService:
    """Query and lifecycle operations over the card index."""

    def __init__(self, db: Session, workspace_id: str, tenant_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.tenant_id = tenant_id

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
    ) -> list[ArtifactCard]:
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
        return list(self.db.scalars(query))

    def get_preview(self, card_id: str) -> ArtifactCard:
        row = self._card_for_workspace(card_id)
        if row.status == CARD_STATUS_DELETED:
            raise AppError(404, "artifact_card_not_found", "Artifact card was not found")
        return row

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
