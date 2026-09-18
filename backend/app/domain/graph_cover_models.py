"""Durable AI cover-generation jobs for the graph bookshelf.

One row per *confirmed* generation attempt. The first phase of the two-phase
flow (drafting the prompt) is deliberately not persisted here: it is a cheap,
repeatable text call whose only durable artifact is the text the user confirms,
and the client already owns that text until it submits.
"""
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.models import TimestampMixin, WorkspaceScopedMixin, new_id


# Engines. ``svg`` lets the text model draw a vector cover (no image-model
# spend); ``image`` calls the workspace image generation provider.
COVER_ENGINES = ("svg", "image")

# Statuses. ``queued`` covers "accepted, not claimed yet"; a cancelled job that
# had already started upstream is recorded as ``cancelled`` and its late result
# is dropped instead of written.
COVER_JOB_STATUSES = ("queued", "running", "ready", "failed", "cancelled")
COVER_ACTIVE_STATUSES = ("queued", "running")


class GraphCoverJob(Base, TimestampMixin, WorkspaceScopedMixin):
    """One AI cover generation attempt, with the prompt that produced it."""

    __tablename__ = "graph_cover_jobs"
    __table_args__ = (
        Index("ix_graph_cover_job_active", "workspace_id", "graph_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(
        ForeignKey("graphs.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str] = mapped_column(String(64))
    engine: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    # The confirmed draft (or the user's edited version of it). Truncated on
    # write; never rendered as HTML/instructions anywhere.
    prompt: Mapped[str] = mapped_column(Text, default="")
    prompt_source: Mapped[str] = mapped_column(String(16), default="model")
    provider_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Original (un-cropped) artifact, kept so a future re-crop costs nothing.
    file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
