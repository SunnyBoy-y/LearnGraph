from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.schemas.common import ORMModel


class FetchAuthorizationDecisionRequest(BaseModel):
    decision: Literal["allow_once", "allow_always", "deny"]


class FetchAuthorizationRequestView(ORMModel):
    id: str
    chat_session_id: str
    tool_call_id: str
    tool_name: str
    requested_url: str
    hostname: str
    status: str
    decision: str | None
