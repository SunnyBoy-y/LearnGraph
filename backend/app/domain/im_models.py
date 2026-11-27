"""Server-side identities and durable delivery records for private IM channels."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.models import Base, TimestampMixin, WorkspaceScopedMixin, new_id


class IMChannelConfig(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "im_channel_configs"
    __table_args__ = (
        UniqueConstraint("provider", "user_id", name="uq_im_channel_config_user"),
        UniqueConstraint("provider", "app_id", name="uq_im_channel_config_app_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[str] = mapped_column(String(64))
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    secret_algorithm: Mapped[str] = mapped_column(String(64))
    secret_key_provider: Mapped[str] = mapped_column(String(64))
    secret_key_version: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class IMChannelState(Base, TimestampMixin):
    __tablename__ = "im_channel_states"
    config_id: Mapped[str] = mapped_column(
        ForeignKey("im_channel_configs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sync_buf: Mapped[str] = mapped_column(Text, default="")
    context_token: Mapped[str] = mapped_column(Text, default="")
    session_expired: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


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
    session_id: Mapped[str | None] = mapped_column(ForeignKey("chat_sessions.id", ondelete="SET NULL"), index=True)
    preferred_provider_id: Mapped[str | None] = mapped_column(String(36), default=None)
    preferred_model_id: Mapped[str | None] = mapped_column(String(160), default=None)


class IMInbound(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "im_inbound"
    __table_args__ = (UniqueConstraint("connection", "message_id", name="uq_im_inbound_message"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str] = mapped_column(String(128))
    # Immutable generation of the binding. Unbind/rebind never resumes old work.
    binding_id: Mapped[str] = mapped_column(String(36), index=True)
    text: Mapped[str] = mapped_column(Text)
    media_json: Mapped[str] = mapped_column(Text, default="")
    tts_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="queued")


class IMOutbound(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "im_outbound"
    id: Mapped[str] = mapped_column(ForeignKey("im_inbound.id"), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    media_json: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt: Mapped[str | None] = mapped_column(String(128))


class IMMessageContext(Base):
    __tablename__ = "im_message_contexts"
    inbound_id: Mapped[str] = mapped_column(
        ForeignKey("im_inbound.id", ondelete="CASCADE"),
        primary_key=True,
    )
    context_token: Mapped[str] = mapped_column(Text)
