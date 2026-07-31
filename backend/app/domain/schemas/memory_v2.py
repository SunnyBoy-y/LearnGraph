from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class MemoryScopeInput(BaseModel):
    tenant_id: str | None = Field(default=None, max_length=64)
    subject_user_id: str | None = Field(default=None, max_length=64)
    workspace_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)


class MemoryEventAppendRequest(BaseModel):
    aggregate_type: Literal[
        "memory_atom", "task", "episode", "agent_run", "strategy", "artifact", "learning_node"
    ]
    aggregate_id: str = Field(min_length=1, max_length=64)
    expected_stream_version: int | None = Field(default=None, ge=0)
    event_type: str = Field(min_length=3, max_length=100)
    event_schema_version: int = Field(default=1, ge=1)
    producer: Literal["api", "chat", "file", "tool", "scheduler", "migration", "device"] = "api"
    idempotency_key: str = Field(min_length=8, max_length=160)
    correlation_id: str | None = Field(default=None, max_length=64)
    causation_id: str | None = Field(default=None, max_length=64)
    occurred_at: datetime | None = None
    sensitivity: Literal["public", "normal", "private", "sensitive", "restricted"] = "normal"
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    scope: MemoryScopeInput = Field(default_factory=MemoryScopeInput)
    conversation_id: str | None = Field(default=None, max_length=64)
    project_id: str | None = Field(default=None, max_length=64)
    file_id: str | None = Field(default=None, max_length=64)
    knowledge_node_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_scope_shape(self) -> "MemoryEventAppendRequest":
        if self.scope.task_id and not self.scope.workspace_id:
            raise ValueError("task-scoped events require workspace_id")
        return self


class MemoryEventView(BaseModel):
    event_id: str
    stream_id: str
    stream_version: int
    global_position: int
    event_type: str
    event_schema_version: int
    payload_hash: str
    occurred_at: datetime
    ingested_at: datetime
    idempotent_replay: bool = False


class MemoryForgetRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=240)
    reason: str = Field(default="user_requested_forget", max_length=500)


class MemoryFeedbackRequest(BaseModel):
    feedback_type: Literal[
        "correct",
        "stale",
        "wrong",
        "should_not_store",
        "project_only",
        "durable",
        "deny_child",
        "suppress_auto_recall",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class MemorySupersedeRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=50_000)
    expected_revision: int | None = Field(default=None, ge=1)
    reason: str = Field(default="user_supersede", max_length=500)


class MemoryConfirmRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    reason: str = Field(default="user_confirmed", max_length=500)
