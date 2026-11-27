"""Session-free, server-grounded opening recommendations."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.schemas.chat import SuggestedPromptView


class OpeningPromptRequest(BaseModel):
    # Context comes exclusively from authorized backend reads, never the browser.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider_id: str | None = Field(default=None, min_length=1, max_length=120)
    model_id: str | None = Field(default=None, min_length=1, max_length=240)
    project_id: str | None = Field(default=None, min_length=1, max_length=36)
    count: int = Field(default=3, ge=1, le=9, strict=True)
    exhausted_pool_id: str | None = Field(default=None, min_length=1, max_length=36)
    mode: Literal["question", "goal"] = "question"


class OpeningPromptItem(SuggestedPromptView):
    display_text: str | None = None


class OpeningPromptResponse(BaseModel):
    prompts: list[OpeningPromptItem] = Field(default_factory=list)
    pool: list[OpeningPromptItem] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
