from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.schemas.common import ORMModel


EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")


class SubAppEventIngestRequest(BaseModel):
    """P1 host-ingested event; T2 capability-token checks are not available yet."""

    model_config = ConfigDict(extra="forbid")

    # T1.1 intentionally permits this to be null until T2.3 persists sessions.
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    chat_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    artifact_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    event_type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        normalized = value.strip()
        if not EVENT_TYPE_PATTERN.fullmatch(normalized):
            raise ValueError(
                "event_type must be a bounded lowercase event identifier"
            )
        return normalized


class SubAppInteractionEventView(ORMModel):
    id: str
    workspace_id: str
    session_id: str | None
    actor_id: str | None
    chat_session_id: str | None
    artifact_version_id: str | None
    event_type: str
    payload: dict[str, Any]
    payload_sha256: str
    created_at: datetime


class SubAppEventIngestedView(BaseModel):
    accepted: Literal[True] = True
    event: SubAppInteractionEventView


class SubAppEventListView(BaseModel):
    items: list[SubAppInteractionEventView]
    offset: int
    limit: int
    total: int


# --------------------------------------------------------------------------- #
# T2.4 session management
# --------------------------------------------------------------------------- #


class SubAppSessionCreateRequest(BaseModel):
    """Instantiate one published sub-application version as a live session."""

    model_config = ConfigDict(extra="forbid")

    artifact_version_id: str = Field(min_length=1, max_length=36)
    chat_session_id: str | None = Field(default=None, min_length=1, max_length=36)


class SubAppSessionView(ORMModel):
    """Read-model for one sub-application session (never exposes the token)."""

    id: str
    workspace_id: str
    actor_id: str
    chat_session_id: str | None
    artifact_version_id: str | None
    event_schema: dict[str, Any]
    state_schema: dict[str, Any]
    status: str
    state_version: int
    state_sha256: str | None
    terminated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SubAppSessionCreatedView(BaseModel):
    """Envelope handed back once to the host on instantiation.

    ``token`` is the raw session capability and is returned exactly once; only
    its SHA-256 digest is persisted. ``unlock`` is a ready-to-forward
    ``renderer.unlock`` protocol message for the sandboxed iframe.
    """

    session_id: str
    status: str
    state_version: int
    state_sha256: str | None
    token: str
    token_prefix: str
    component_id: str
    render_ref: str
    artifact_version_id: str | None
    chat_session_id: str | None
    event_schema: dict[str, Any]
    state_schema: dict[str, Any]
    unlock_message: dict[str, Any]


class SubAppSessionEventRequest(BaseModel):
    """One user event relayed from a session's iframe via the host."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        normalized = value.strip()
        if not EVENT_TYPE_PATTERN.fullmatch(normalized):
            raise ValueError(
                "event_type must be a bounded lowercase event identifier"
            )
        return normalized


class SubAppSessionEventAcceptedView(BaseModel):
    """202 ack carrying the freshly rotated capability token."""

    accepted: Literal[True] = True
    session_id: str
    event: SubAppInteractionEventView
    next_token: str
    next_token_prefix: str


class SubAppStateView(ORMModel):
    """Immutable, versioned state snapshot."""

    id: str
    session_id: str
    version: int
    sha256: str
    state: dict[str, Any]
    created_at: datetime


class SubAppStateListView(BaseModel):
    items: list[SubAppStateView]
    offset: int
    limit: int
    total: int
