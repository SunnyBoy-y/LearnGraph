from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProviderBalanceState(Base):
    """Durable, at-most-once discovery; no credentials or arbitrary scripts."""

    __tablename__ = "provider_balance_states"
    provider_id: Mapped[str] = mapped_column(ForeignKey("provider_configs.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    binding: Mapped[str] = mapped_column(String(64), default="")
    method_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    run_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deadline_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_attempt_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_query_at: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ProviderBalanceLease(Base):
    """One remote balance operation globally, across tabs/users/workers."""

    __tablename__ = "provider_balance_leases"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    token: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[float] = mapped_column(Float)


class ProviderBalanceManualState(Base):
    """Explicit custom tests never enroll a script for automatic execution."""

    __tablename__ = "provider_balance_manual_states"

    provider_id: Mapped[str] = mapped_column(ForeignKey("provider_configs.id", ondelete="CASCADE"), primary_key=True)
    binding: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="idle")
    run_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadline_at: Mapped[float | None] = mapped_column(Float, nullable=True)
