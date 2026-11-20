from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.schemas.chat import MessageCreateRequest


class ChatGoalStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=4000)
    max_rounds: int = Field(default=6, ge=1, le=20)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    thinking_mode: Literal["off", "low", "medium", "high", "xhigh", "max"] | None = None
    search_route: Literal["disabled", "model_native", "external", "local", "auto"] = "disabled"

    @field_validator("objective")
    @classmethod
    def strip_objective(cls, value: str) -> str:
        if not (value := value.strip()):
            raise ValueError("Objective cannot be blank")
        return value

    def message(self, content: str) -> MessageCreateRequest:
        return MessageCreateRequest(
            content=content, agent_mode=True, model_id=self.model_id,
            provider_id=self.provider_id, thinking_mode=self.thinking_mode,
            search_route=self.search_route,
        )


class ChatGoalControl(BaseModel):
    goal_id: str = Field(min_length=1, max_length=36)
    action: Literal["pause", "resume", "clear"]


class ChatGoalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    objective: str
    status: Literal[
        "queued", "running", "pausing", "clearing", "paused",
        "completed", "blocked", "failed", "budget_exhausted",
    ]
    rounds_used: int
    max_rounds: int
    summary: str
    last_message_id: str | None
    updated_at: datetime


class ChatGoalVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["continue", "completed", "blocked"]
    summary: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(max_length=1000)
