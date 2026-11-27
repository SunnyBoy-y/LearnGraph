"""Read-only AI discovery contracts, independent of scheduled-task mutations."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


RECOMMENDATION_ICONS = (
    "newspaper", "book-open", "briefcase", "chart-no-axes-column", "code-xml", "bell",
    "shopping-bag", "radar", "file-text", "languages", "calendar-days", "sparkles",
)
RECOMMENDATION_THEMES = ("blue", "sage", "violet", "amber", "rose", "slate")


class ScheduleSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=5, ge=1, le=7, strict=True)


class ScheduleSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=1000)
    description: str | None = Field(default=None, max_length=100)
    schedule: str | None = Field(default=None, max_length=48)
    icon: str | None = None
    theme: str | None = None

    @field_validator("icon", "theme", mode="before")
    @classmethod
    def allowed_visual_metadata(cls, value: Any, info):
        allowed = RECOMMENDATION_ICONS if info.field_name == "icon" else RECOMMENDATION_THEMES
        return value if isinstance(value, str) and value in allowed else None


class ScheduleSuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[ScheduleSuggestion] = Field(default_factory=list, max_length=7)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelScheduleSuggestions(BaseModel):
    """Strict model output; it cannot smuggle task-creation fields or metadata."""
    model_config = ConfigDict(extra="forbid")

    suggestions: list[ScheduleSuggestion] = Field(max_length=7)
