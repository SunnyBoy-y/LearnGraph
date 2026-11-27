"""Turn-level sandbox file checkpoints for safe, conflict-checked rewind."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SandboxFileCheckpoint(Base):
    """Pre-image / post-image hashes for one file touched during one assistant turn."""

    __tablename__ = "sandbox_file_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "chat_session_id",
            "turn_id",
            "path",
            name="uq_sandbox_checkpoint_turn_path",
        ),
        Index(
            "ix_sandbox_checkpoint_session_created",
            "workspace_id",
            "chat_session_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    existed_before: Mapped[bool] = mapped_column(Boolean, default=False)
    pre_blob_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pre_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    post_blob_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    post_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
