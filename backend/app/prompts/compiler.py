"""Compile workspace response-style settings into model instructions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.prompts.fragments import (
    BASE_STYLE_PROMPTS,
    CORE_INSTRUCTIONS,
    EMOJI_PROMPTS,
    EMOJI_TEXT_ALLOWED_NOTE,
    EMOJI_TEXT_DISABLED_NOTE,
    ENTHUSIASM_PROMPTS,
    HEADINGS_PROMPTS,
    OUTPUT_ADAPTATION_PROSE_RULES,
    OUTPUT_ADAPTATION_RULES,
    SCHEDULED_NON_INTERACTIVE_RULES,
    STICKER_TOOL_ALLOWED_NOTE,
    STICKER_TOOL_DISABLED_NOTE,
    VERBOSITY_OVERRIDE_NOTE,
    VERBOSITY_PROMPTS,
    WARMTH_PROMPTS,
)

PromptChannel = Literal["chat", "voice", "scheduled"]

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


class PromptRuntimeState(BaseModel):
    """Per-turn runtime state used to compile conflict-free prompt instructions."""

    model_config = ConfigDict(extra="forbid")

    channel: PromptChannel = "chat"
    interactive: bool = True
    sticker_enabled: bool = True


DEFAULT_RESPONSE_STYLE = ResponseStyleConfig()
DEFAULT_PROMPT_RUNTIME_STATE = PromptRuntimeState()


def normalize_response_style(value: Any | None) -> ResponseStyleConfig:
    """Return a validated style config; missing values become product defaults."""

    if value is None:
        return DEFAULT_RESPONSE_STYLE.model_copy()
    return ResponseStyleConfig.model_validate(value)


def build_expression_policy(
    style: ResponseStyleConfig,
    *,
    sticker_enabled: bool = True,
) -> str:
    """Compile independent text-emoji and image-sticker policies."""

    emoji_note = (
        EMOJI_TEXT_DISABLED_NOTE if style.emoji == -2 else EMOJI_TEXT_ALLOWED_NOTE
    )
    sticker_note = (
        STICKER_TOOL_ALLOWED_NOTE if sticker_enabled else STICKER_TOOL_DISABLED_NOTE
    )
    return f"{emoji_note}\n{sticker_note}"


def build_style_instructions(
    config: ResponseStyleConfig | None = None,
    *,
    state: PromptRuntimeState | None = None,
) -> str:
    """Compile base style + characteristics into a conflict-free instructions block."""

    style = config or DEFAULT_RESPONSE_STYLE
    runtime = state or DEFAULT_PROMPT_RUNTIME_STATE

    if runtime.channel == "voice":
        # Voice has its own spoken-first system prompt; only carry compatible tone dimensions.
        parts = [
            "# 语音语气偏好",
            WARMTH_PROMPTS[style.warmth].strip(),
            ENTHUSIASM_PROMPTS[style.enthusiasm].strip(),
        ]
        return "\n\n".join(part for part in parts if part)

    output_rules = (
        OUTPUT_ADAPTATION_PROSE_RULES
        if style.headings_and_lists <= -1
        else OUTPUT_ADAPTATION_RULES
    )

    parts = [
        CORE_INSTRUCTIONS.strip(),
        BASE_STYLE_PROMPTS[style.base_style].strip(),
        "# 附加特征",
        WARMTH_PROMPTS[style.warmth].strip(),
        ENTHUSIASM_PROMPTS[style.enthusiasm].strip(),
        HEADINGS_PROMPTS[style.headings_and_lists].strip(),
        EMOJI_PROMPTS[style.emoji].strip(),
        VERBOSITY_PROMPTS[style.verbosity].strip(),
        VERBOSITY_OVERRIDE_NOTE.strip() if style.verbosity <= -1 else "",
        output_rules.strip(),
    ]
    if runtime.channel == "scheduled" or not runtime.interactive:
        parts.append(SCHEDULED_NON_INTERACTIVE_RULES.strip())
    if state is not None:
        parts.append(
            build_expression_policy(style, sticker_enabled=runtime.sticker_enabled)
        )
    return "\n\n".join(part for part in parts if part)
