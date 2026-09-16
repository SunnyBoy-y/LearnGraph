from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ThinkingMode = Literal["off", "low", "medium", "high", "xhigh"]

VOICE_EVENT_TYPES = (
    "session.created", "session.ready", "session.reconnecting", "session.closed",
    "user.started", "user.interim", "user.final", "turn.accepted", "turn.finalized",
    "turn.interrupted", "assistant.llm.delta", "assistant.sentence.queued",
    "assistant.sentence.ended", "assistant.playback.ack",
    "assistant.sentence.playback_started", "assistant.sentence.playback_ended",
    "processor.error", "processor.retry_scheduled", "processor.notice",
    "context.updated",
    "session.ice",
)

class RTVIEventEnvelope(BaseModel):
    """Stable event envelope shared by WebRTC/RTVI clients."""
    model_config = ConfigDict(extra="allow")
    type: str = Field(min_length=1, max_length=96)
    seq: int = Field(ge=0)
    # Canonical name used by durable replay; ``seq`` remains for RTVI clients.
    event_seq: int | None = Field(default=None, ge=0)
    event_id: str | None = None
    session_epoch: int = 1
    turn_id: str | None = None
    # 音频闸门代次 / 句段身份 / Pipecat audio context，由 durable payload 提升而来
    # （见 ``app.voice.events.envelope_from_record``）。声明成字段是为了让"每个
    # 音频与文字事件都带同一套身份"成为类型契约，而不是靠 extra="allow" 兜底。
    generation_id: int | None = None
    segment_id: str | None = None
    context_id: str | None = None
    phase: Literal["speculative", "authoritative"] = "authoritative"
    causality: dict[str, Any] = Field(default_factory=dict)
    audio_cursor_ms: int | None = Field(default=None, ge=0)
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
    delivery_status: str = "pending"
    trigger_turn_id: str | None = None
    requirement_version: int = Field(default=1, ge=1)
    latest_result_version: int = Field(default=0, ge=0)
    auto_delivery: bool = True
    cancel_requested_at: datetime | None = None
    cancel_acknowledged_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

class VoiceResultView(BaseModel):
    id: str
    voice_session_id: str
    subagent_id: str
    requirement_version: int = Field(ge=1)
    result_version: int = Field(ge=1)
    status: Literal["pending", "ready", "delivered", "dismissed", "stale", "failed"]
    result_type: str
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    source_count: int = Field(default=0, ge=0)
    safe_error: str = ""
    captured_at: datetime
    available_at: datetime
    delivered_at: datetime | None = None
    dismissed_at: datetime | None = None
    stale_at: datetime | None = None
    auto_speak: bool = False

class VoiceResultDeliveryView(BaseModel):
    result: VoiceResultView
    delivery_id: str
    speech_id: str
    request_id: str
    already_delivered: bool = False

class VoiceResultListResponse(BaseModel):
    voice_session_id: str
    results: list[VoiceResultView] = Field(default_factory=list)
    ready_count: int = 0
    auto_speak: bool = False

class VoiceResultDeliveryRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=160)

class VoiceTaskCancelRequest(BaseModel):
    reason: str = Field(default="user_requested", max_length=160)

class VoiceTaskRevisionRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=16_384)
    title: str | None = Field(default=None, max_length=200)
    note: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=160)


class VoiceSession(BaseModel):
    id: str
    session_id: str | None = None
    chat_session_id: str
    workspace_id: str
    owner_user_id: str
    status: Literal["active", "ended"] = "active"
    max_thinking_mode: ThinkingMode = "high"
    seq: int = 0
    event_seq: int = 0
    session_epoch: int = 1
    peer_generation: int = 0
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    tasks: list[VoiceTaskLink] = Field(default_factory=list)
    results: list[VoiceResultView] = Field(default_factory=list)
    ready_result_count: int = 0
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
    after_event_seq: int | None = Field(default=None, ge=0)
    last_event_seq: int | None = Field(default=None, ge=0)

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
    trigger_turn_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)
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
    requirement_version: int = Field(default=1, ge=1)
    latest_result_version: int = Field(default=0, ge=0)
    cancel_requested_at: datetime | None = None
    cancel_acknowledged_at: datetime | None = None
    result: str | None = None
    deliverables: dict[str, Any] | None = None
    event_seq: int = 0
    events: list[dict[str, Any]] = Field(default_factory=list)
    results: list[VoiceResultView] = Field(default_factory=list)

class VoiceEventResponse(BaseModel):
    event: RTVIEventEnvelope


class VoiceEventReplayResponse(BaseModel):
    events: list[RTVIEventEnvelope] = Field(default_factory=list)
    last_event_seq: int = 0
    # 连续覆盖水位：服务端保证 <= 该值的序号已全部投递（不存在空洞）。
    # 客户端只能从它之后继续拉取，否则按类型过滤出来的"跳号"会被误判成丢事件，
    # 而被跳过的序号永远补不回来。
    contiguous_through: int = 0
