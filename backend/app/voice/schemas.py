from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ThinkingMode = Literal["off", "low", "medium", "high", "xhigh"]

class RTVIEventEnvelope(BaseModel):
    """Stable event envelope shared by WebRTC/RTVI clients."""
    model_config = ConfigDict(extra="allow")
    type: str = Field(min_length=1, max_length=96)
    seq: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=96)
    session_id: str = Field(min_length=1, max_length=64)
    timestamp: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

class VoiceTaskLink(BaseModel):
    voice_session_id: str
    subagent_id: str
    chat_session_id: str
    status: str = "queued"
    thinking_mode: ThinkingMode = "off"
    title: str = ""
    created_at: datetime
    updated_at: datetime

class VoiceSession(BaseModel):
    id: str
    session_id: str | None = None
    chat_session_id: str
    workspace_id: str
    owner_user_id: str
    status: Literal["active", "ended"] = "active"
    max_thinking_mode: ThinkingMode = "high"
    seq: int = 0
    tasks: list[VoiceTaskLink] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    # Logical session creation is available before the optional Pipecat
    # runtime is deployed. Clients must not treat it as an audio connection.
    runtime_ready: bool = False
    signaling_url: str | None = None
    model_id: str | None = None
    provider_id: str | None = None

class VoiceSessionCreateRequest(BaseModel):
    chat_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    max_thinking_mode: ThinkingMode = "high"
    thinking_limit: ThinkingMode | None = None
    model_id: str | None = Field(default=None, max_length=160)
    provider_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def normalize_aliases(self) -> "VoiceSessionCreateRequest":
        if self.chat_session_id is None:
            self.chat_session_id = self.session_id
        if self.thinking_limit is not None:
            self.max_thinking_mode = self.thinking_limit
        if not self.chat_session_id:
            raise ValueError("chat_session_id is required")
        return self

class VoiceTaskStartRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=16_384)
    title: str = Field(default="", max_length=200)
    role_key: str = Field(default="generic", max_length=40)
    thinking_mode: ThinkingMode | None = None
    tools: list[str] | None = Field(default=None, max_length=16)
    skills: list[str] | None = Field(default=None, max_length=8)
    write_set: list[str] | None = Field(default=None, max_length=32)
    output_contract: dict[str, Any] | None = None
    sandbox_session_id: str | None = Field(default=None, max_length=36)

class VoiceTaskStatusRequest(BaseModel):
    after_event_seq: int | None = Field(default=None, ge=0)

class VoiceTaskView(BaseModel):
    voice_session_id: str
    subagent_id: str
    status: str
    thinking_mode: ThinkingMode
    title: str = ""
    result: str | None = None
    deliverables: dict[str, Any] | None = None
    event_seq: int = 0
    events: list[dict[str, Any]] = Field(default_factory=list)

class VoiceEventResponse(BaseModel):
    event: RTVIEventEnvelope
