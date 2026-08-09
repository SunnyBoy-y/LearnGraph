from __future__ import annotations

"""API schemas for the generic Agent egress approval queue (D2.1).

Contract A (design doc md-D2-1 §1.2): the only authorization resource is a
canonical exact hostname. The create schema forbids extra fields by default so
a command / argv / prompt / URL-path field cannot silently slip into a request.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import (
    EGRESS_APPROVAL_DEFAULT_TTL_SECONDS,
    EGRESS_APPROVAL_MAX_TTL_SECONDS,
)
from app.domain.schemas.common import ORMModel


class EgressAuthorizationCreateRequest(BaseModel):
    """Request user approval before a generic Agent egress host may be used.

    ``hostname`` is the ONLY authorization resource. ``purpose`` /
    ``request_context`` are display/audit context only and are never used for
    authorization matching (contract A). ``ttl_seconds`` bounds how long the
    request may stay pending before it is expired asynchronously.
    """

    model_config = ConfigDict(extra="forbid")

    hostname: str = Field(min_length=1, max_length=253)
    chat_session_id: str | None = Field(default=None, max_length=36)
    purpose: str | None = Field(default=None, max_length=200)
    request_context: dict[str, Any] | None = Field(default=None)
    ttl_seconds: int = Field(
        default=EGRESS_APPROVAL_DEFAULT_TTL_SECONDS,
        ge=60,
        le=EGRESS_APPROVAL_MAX_TTL_SECONDS,
    )


class EgressAuthorizationDecisionRequest(BaseModel):
    decision: Literal["allow_once", "allow_always", "deny"]


class EgressAuthorizationRequestView(ORMModel):
    id: str
    workspace_id: str
    hostname: str
    capability: str
    requested_by: str
    chat_session_id: str | None
    status: str
    decision: str | None
    allow_always: bool
    decided_by: str | None
    decided_at: datetime | None
    expires_at: datetime
    ttl_seconds: int
    request_context: dict[str, Any] | None
    consumed_at: datetime | None
    resume_payload: dict[str, Any] | None
    assistant_message_id: str | None
    user_message_id: str | None
    tool_call_id: str | None
    created_at: datetime
    updated_at: datetime


class EgressAuthorizationListResponse(BaseModel):
    items: list[EgressAuthorizationRequestView]
    total: int
    offset: int
    limit: int
