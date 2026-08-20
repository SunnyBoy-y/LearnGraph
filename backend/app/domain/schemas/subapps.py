from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.schemas.common import ORMModel


EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")


class SubAppEventIngestRequest(BaseModel):
    """Legacy host-relayed event without session binding.

    Session-scoped events must use ``SubAppSessionEventRequest`` and the
    rotating session capability token.
    """

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
    client_event_id: str | None = None
    sequence: int | None = None
    schema_version: int = 1
    occurred_at: datetime | None = None
    bundle_id: str | None = None
    component_id: str | None = None
    component_version: str | None = None
    source: str = "semantic"
    privacy_class: str = "session"
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
    agent_triggers: list[dict[str, Any]] = Field(default_factory=list)
    analytics: dict[str, Any] | None = None
    agent_status: str = "idle"
    agent_job_id: str | None = None
    agent_error: str | None = None
    agent_updated_at: datetime | None = None
    last_processed_event_id: str | None = None
    agent_consent: str = "ask"


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
    agent: dict[str, Any] = Field(default_factory=dict)


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


class SubAppAgentRunView(BaseModel):
    run_id: str
    session_id: str
    event_id: str
    status: str
    error: str | None = None
    message_id: str | None = None
    message_version_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SubAppAgentConsentView(BaseModel):
    mode: str = "ask"
    allowed: bool = False
    pending_consent_id: str | None = None
    triggers: list[dict[str, Any]] = Field(default_factory=list)


class SubAppAgentTaskStatusView(BaseModel):
    consent_mode: str = "ask"
    allowed: bool = False
    pending_consent_id: str | None = None
    agent_status: str = "idle"
    agent_error: str | None = None
    latest_run: SubAppAgentRunView | None = None


class SubAppAgentConsentDecisionRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)
    decision: Literal["allow_session", "allow_app", "allow_global", "deny"]


class SubAppAgentTaskRetryRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class SubAppAgentTaskRetryView(BaseModel):
    run_id: str | None = None
    status: str = "queued"
