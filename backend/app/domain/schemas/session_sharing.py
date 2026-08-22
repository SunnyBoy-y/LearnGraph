from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.schemas.common import ORMModel


class SessionShareCreate(BaseModel):
    """Create an immutable session snapshot and a read-only share token.

    ``scope`` selects which messages are frozen into the snapshot:
    - ``full``: every message in the session (default)
    - ``range``: messages between ``from_message_id`` and ``to_message_id``
    - ``answers``: assistant messages only (no user prompts)
    ``answers_only`` is accepted as a synonym for ``scope=answers``.
    """

    scope: str = Field(default="full", pattern="^(full|range|answers)$")
    from_message_id: str | None = None
    to_message_id: str | None = None
    answers_only: bool = False
    label: str = Field(default="", max_length=120)
    expires_at: datetime | None = None
    max_views: int | None = Field(default=None, ge=1)


class SessionShareTokenView(ORMModel):
    id: str
    token_prefix: str
    label: str
    expires_at: datetime | None
    max_views: int | None
    view_count: int
    last_viewed_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class SessionShareTokenCreated(SessionShareTokenView):
    """Returned once at creation; the raw token is never persisted."""

    token: str


class SessionShareMessageView(BaseModel):
    id: str
    ordinal: int
    role: str
    content: str
    parts: list[dict]
    parent_message_id: str | None
    created_at: datetime


class SessionShareView(ORMModel):
    id: str
    title: str
    scope: str
    message_count: int
    status: str
    created_by: str
    created_at: datetime


class SessionShareDetailView(SessionShareView):
    tokens: list[SessionShareTokenView] = Field(default_factory=list)


class SessionSharePublicView(BaseModel):
    """Payload served by the unauthenticated public viewer endpoint."""

    id: str
    title: str
    scope: str
    message_count: int
    created_at: datetime
    messages: list[SessionShareMessageView]
