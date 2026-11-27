from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QueuedTurnCreateRequest(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=120)
    content: str = Field(default="", max_length=200_000)
    mode: Literal["append", "interrupt"] = "append"
    payload: dict[str, Any] = Field(default_factory=dict)


class QueuedTurnUpdateRequest(BaseModel):
    content: str | None = Field(default=None, max_length=200_000)
    payload: dict[str, Any] | None = None


class QueuedTurnCompleteRequest(BaseModel):
    dispatched_message_id: str | None = Field(default=None, max_length=64)


class QueuedTurnPromoteRequest(BaseModel):
    cancel_active: bool = False


class QueuedTurnView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    session_id: str
    created_by: str
    client_request_id: str
    position: int
    status: str
    content: str
    payload: dict[str, Any] = Field(default_factory=dict, validation_alias="payload_json")
    dispatched_message_id: str | None = None
    created_at: datetime
    updated_at: datetime


class QueuedTurnListResponse(BaseModel):
    items: list[QueuedTurnView]


class QueuedTurnPromoteResponse(BaseModel):
    turn: QueuedTurnView
    cancelled_message_id: str | None = None
