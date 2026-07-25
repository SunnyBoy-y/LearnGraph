"""Compile workspace response-style settings into model instructions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.prompts.fragments import (
    BASE_STYLE_PROMPTS,
    CORE_INSTRUCTIONS,
    EMOJI_PROMPTS,
    ENTHUSIASM_PROMPTS,
    HEADINGS_PROMPTS,
    OUTPUT_ADAPTATION_RULES,
    VERBOSITY_PROMPTS,
    WARMTH_PROMPTS,
)

BaseStyle = Literal[
    "default",
    "professional",
    "friendly",
    "candid",
    "efficient",
    "exploratory",
    "quirky",
    "cynical",
]

StyleLevel = Literal[-2, -1, 0, 1, 2]

BASE_STYLES: tuple[BaseStyle, ...] = (
    "default",
    "professional",
    "friendly",
    "candid",
    "efficient",
    "exploratory",
    "quirky",
    "cynical",
)


class ResponseStyleConfig(BaseModel):
    """Persisted workspace chat response style."""

    model_config = ConfigDict(extra="forbid")

    base_style: BaseStyle = "default"
    warmth: StyleLevel = 0
    enthusiasm: StyleLevel = 0
    headings_and_lists: StyleLevel = 0
    emoji: StyleLevel = 0
    verbosity: StyleLevel = 0

    @field_validator(
        "warmth",
        "enthusiasm",
        "headings_and_lists",
        "emoji",
        "verbosity",
        mode="before",
    )
    @classmethod
    def _coerce_level(cls, value: Any) -> int:
        if isinstance(value, bool) or value is None:
            raise ValueError("style level must be an integer from -2 to 2")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int):
            raise ValueError("style level must be an integer from -2 to 2")
        if value not in (-2, -1, 0, 1, 2):
            raise ValueError("style level must be an integer from -2 to 2")
        return value


DEFAULT_RESPONSE_STYLE = ResponseStyleConfig()


def normalize_response_style(value: Any | None) -> ResponseStyleConfig:
    """Return a validated style config; missing values become product defaults."""

    if value is None:
        return DEFAULT_RESPONSE_STYLE.model_copy()
    return ResponseStyleConfig.model_validate(value)


def build_style_instructions(config: ResponseStyleConfig | None = None) -> str:
    """Compile base style + characteristics into a single instructions block."""

    style = config or DEFAULT_RESPONSE_STYLE
    parts = [
        CORE_INSTRUCTIONS.strip(),
        BASE_STYLE_PROMPTS[style.base_style].strip(),
        "# 附加特征",
        WARMTH_PROMPTS[style.warmth].strip(),
        ENTHUSIASM_PROMPTS[style.enthusiasm].strip(),
        HEADINGS_PROMPTS[style.headings_and_lists].strip(),
        EMOJI_PROMPTS[style.emoji].strip(),
        VERBOSITY_PROMPTS[style.verbosity].strip(),
        OUTPUT_ADAPTATION_RULES.strip(),
    ]
    return "\n\n".join(part for part in parts if part)
