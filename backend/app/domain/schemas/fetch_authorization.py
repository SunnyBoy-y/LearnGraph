from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.schemas.common import ORMModel


class FetchAuthorizationDecisionRequest(BaseModel):
    decision: Literal["allow_once", "allow_always", "deny"]


FetchChannel = Literal["sandbox", "remote", "hosted"]

_DEFAULT_FETCH_PRIORITY: list[FetchChannel] = ["sandbox", "remote", "hosted"]


class WebFetchRuntimeUpdateRequest(BaseModel):
    """Workspace-level web fetch runtime preferences (Provider 管理 -> 网页抓取).

    ``sandbox_enabled`` toggles the sandbox-isolated fetch lane for this
    workspace (the global ``sandbox_web_fetch_enabled`` env gate still applies).
    ``priority`` lists the fetch channels in order of preference; the resolver
    uses the first channel that is actually available, so an unavailable
    channel never blocks a lower-priority one.
    """

    sandbox_enabled: bool = True
    priority: list[FetchChannel] = Field(
        default_factory=lambda: list(_DEFAULT_FETCH_PRIORITY),
        min_length=1,
        max_length=3,
    )

    @field_validator("priority")
    @classmethod
    def _unique_priority(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("priority channels must be unique")
        return value


class WebFetchRuntimeView(BaseModel):
    """Current runtime settings plus the effective channel status for the UI."""

    sandbox_enabled: bool
    priority: list[FetchChannel]
    persisted: bool
    # --- effective status ---------------------------------------------------
    global_sandbox_gate: bool
    egress_enabled: bool
    allowlist_count: int
    allow_all: bool
    image_available: bool
    sandbox_effective: bool
    remote_configured: bool
    hosted_configured: bool
    effective_channel: FetchChannel | None


class FetchAuthorizationRequestView(ORMModel):
    id: str
    chat_session_id: str
    tool_call_id: str
    tool_name: str
    requested_url: str
    hostname: str
    status: str
    decision: str | None
