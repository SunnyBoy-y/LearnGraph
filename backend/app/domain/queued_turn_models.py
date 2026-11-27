"""Durable per-session queued chat turns (cross-tab & reload safe)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.models import LenientJSON


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class QueuedChatTurn(Base):
    """Server-owned queued user turn waiting for an active generation to finish."""

    __tablename__ = "queued_chat_turns"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "session_id",
            "client_request_id",
            name="uq_queued_chat_turn_client_request",
        ),
        Index(
            "ix_queued_chat_turns_session_status_pos",
            "workspace_id",
            "session_id",
            "status",
            "position",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    created_by: Mapped[str] = mapped_column(String(64), index=True)
    client_request_id: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(LenientJSON, default=dict)
    dispatched_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
