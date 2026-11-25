"""Server-side identities and durable delivery records for private IM channels."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models import Base, TimestampMixin, WorkspaceScopedMixin, new_id


class IMBindingCode(Base, WorkspaceScopedMixin):
    __tablename__ = "im_binding_codes"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class IMBinding(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "im_bindings"
    __table_args__ = (
        UniqueConstraint("connection", "sender", name="uq_im_binding_sender"),
        UniqueConstraint("connection", "user_id", name="uq_im_binding_user"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection: Mapped[str] = mapped_column(String(64))
    sender: Mapped[str] = mapped_column(String(192))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="SET NULL"))


class IMInbound(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "im_inbound"
    __table_args__ = (UniqueConstraint("connection", "message_id", name="uq_im_inbound_message"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str] = mapped_column(String(128))
    # Immutable generation of the binding. Unbind/rebind never resumes old work.
    binding_id: Mapped[str] = mapped_column(String(36), index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")


class IMOutbound(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "im_outbound"
    id: Mapped[str] = mapped_column(ForeignKey("im_inbound.id"), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt: Mapped[str | None] = mapped_column(String(128))
