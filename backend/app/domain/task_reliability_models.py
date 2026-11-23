"""Operational history for persistent background jobs."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DurableJobAttempt(Base):
    """One execution lease, recorded atomically with queue state changes."""

    __tablename__ = "durable_job_attempts"
    __table_args__ = (Index("ix_durable_attempt_workspace_started", "workspace_id", "started_at"),)

    lease_token: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("durable_jobs.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    attempt_number: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    outcome: Mapped[str] = mapped_column(String(24), default="running")
    error_code: Mapped[str | None] = mapped_column(String(100))
