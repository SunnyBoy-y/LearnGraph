"""Read-only AI discovery contracts, independent of scheduled-task mutations."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScheduleSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=5, ge=1, le=7, strict=True)


class ScheduleSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=1000)
    # Validate syntax, not a small icon allowlist: the client loads Lucide dynamically.
    icon: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class ScheduleSuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[ScheduleSuggestion] = Field(default_factory=list, max_length=7)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelScheduleSuggestions(BaseModel):
    """Strict model output; it cannot smuggle task-creation fields or metadata."""
    model_config = ConfigDict(extra="forbid")

    suggestions: list[ScheduleSuggestion] = Field(max_length=7)
