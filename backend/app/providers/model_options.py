from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.providers.model_catalog import (
    UNKNOWN_MODEL_CONTEXT_TOKENS,
    model_context_defaults,
)


ThinkingMode = Literal["off", "low", "medium", "high", "xhigh"]
SearchRoute = Literal["disabled", "model_native", "external", "local", "auto"]
ImageInputMode = Literal["native", "external_vision", "auto"]

THINKING_MODES: tuple[ThinkingMode, ...] = ("off", "low", "medium", "high", "xhigh")
SEARCH_ROUTES: tuple[SearchRoute, ...] = (
    "disabled",
    "model_native",
    "external",
    "local",
    "auto",
)
IMAGE_INPUT_MODES: tuple[ImageInputMode, ...] = ("native", "external_vision", "auto")


class ModelCapabilityError(ValueError):
    """The requested call mode is not supported by the selected model snapshot."""


@dataclass(frozen=True, slots=True)
class ModelCallOptions:
    thinking_mode: ThinkingMode
    actual_reasoning_effort: str | None
    reasoning_parameter: str
    search_route: SearchRoute
    native_web_search: bool

    def trace(self) -> dict[str, Any]:
        return {
            "thinking_mode": self.thinking_mode,
            "actual_reasoning_effort": self.actual_reasoning_effort,
            "reasoning_parameter": self.reasoning_parameter,
            "search_route": self.search_route,
            "native_web_search": self.native_web_search,
        }


def _model_capabilities(capabilities: dict[str, Any], model_id: str) -> dict[str, Any]:
    merged = model_context_defaults(model_id)
    merged.update(capabilities)
    group_defaults = capabilities.get("model_defaults")
    if isinstance(group_defaults, dict):
        merged.update(group_defaults)
    configured_models = capabilities.get("models")
    if isinstance(configured_models, dict):
        selected = configured_models.get(model_id)
        if isinstance(selected, dict):
            merged.update(selected)
    return merged


def model_capabilities_for_model(
    capabilities: dict[str, Any], model_id: str
) -> dict[str, Any]:
    """Return the effective, versioned capability snapshot for one model."""

    return _model_capabilities(capabilities, model_id)


def resolve_model_call_options(
    capabilities: dict[str, Any],
    model_id: str,
    *,
    thinking_mode: str | None = None,
    search_route: str | None = None,
) -> ModelCallOptions:
    resolved = _model_capabilities(capabilities, model_id)
    requested_thinking = thinking_mode or resolved.get("default_thinking_mode") or "off"
    if requested_thinking not in THINKING_MODES:
        raise ModelCapabilityError("Unsupported LearnGraph thinking mode")

    raw_efforts = resolved.get("reasoning_efforts") or []
    if not isinstance(raw_efforts, list) or any(not isinstance(item, str) for item in raw_efforts):
        raise ModelCapabilityError("Model reasoning_efforts must be an array of strings")
    efforts = {item.strip() for item in raw_efforts if item.strip()}
    raw_mapping = resolved.get("thinking_mapping") or {}
    if not isinstance(raw_mapping, dict):
        raise ModelCapabilityError("Model thinking_mapping must be an object")

    if requested_thinking == "off":
        actual = raw_mapping.get("off")
        if actual is not None and not isinstance(actual, str):
            raise ModelCapabilityError("The off thinking mapping must be a string or null")
    else:
        if requested_thinking not in efforts:
            raise ModelCapabilityError(
                f"The selected model does not support thinking mode '{requested_thinking}'"
            )
        actual = raw_mapping.get(requested_thinking, requested_thinking)
        if not isinstance(actual, str) or not actual.strip():
            raise ModelCapabilityError("The selected thinking mode has no valid provider mapping")
        actual = actual.strip()

    parameter = str(resolved.get("reasoning_parameter") or "reasoning_effort").strip()
    if parameter not in {"reasoning_effort", "reasoning.effort"}:
        raise ModelCapabilityError("Unsupported reasoning parameter mapping")

    requested_route = search_route or resolved.get("default_search_route") or "disabled"
    if requested_route not in SEARCH_ROUTES:
        raise ModelCapabilityError("Unsupported search route")
    native_web_search = requested_route == "model_native"
    if native_web_search and resolved.get("hosted_web_search") is not True:
        raise ModelCapabilityError(
            "The selected model has no verified hosted_web_search capability"
        )
    return ModelCallOptions(
        thinking_mode=requested_thinking,
        actual_reasoning_effort=actual,
        reasoning_parameter=parameter,
        search_route=requested_route,
        native_web_search=native_web_search,
    )


def validate_model_capability_update(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the user-confirmed portion of a per-model capability snapshot."""

    efforts = payload.get("reasoning_efforts", [])
    if not isinstance(efforts, list) or any(item not in THINKING_MODES[1:] for item in efforts):
        raise ModelCapabilityError("reasoning_efforts may only contain low/medium/high/xhigh")
    mapping = payload.get("thinking_mapping", {})
    if not isinstance(mapping, dict) or any(key not in THINKING_MODES for key in mapping):
        raise ModelCapabilityError("thinking_mapping contains an unknown LearnGraph mode")
    if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in mapping.values()):
        raise ModelCapabilityError("thinking_mapping values must be non-empty strings or null")
    default_mode = payload.get("default_thinking_mode", "off")
    if default_mode not in THINKING_MODES:
        raise ModelCapabilityError("default_thinking_mode is invalid")
    if default_mode != "off" and default_mode not in efforts:
        raise ModelCapabilityError("default_thinking_mode is not listed in reasoning_efforts")
    default_route = payload.get("default_search_route", "disabled")
    if default_route not in SEARCH_ROUTES:
        raise ModelCapabilityError("default_search_route is invalid")
    if default_route == "model_native" and payload.get("hosted_web_search") is not True:
        raise ModelCapabilityError("model_native requires hosted_web_search=true")
    parameter = payload.get("reasoning_parameter", "reasoning_effort")
    if parameter not in {"reasoning_effort", "reasoning.effort"}:
        raise ModelCapabilityError("reasoning_parameter is unsupported")
    image_input_mode = payload.get("image_input_mode", "auto")
    if image_input_mode not in IMAGE_INPUT_MODES:
        raise ModelCapabilityError(
            "image_input_mode must be native, external_vision, or auto"
        )
    supports_image_input = payload.get("supports_image_input") is True
    if image_input_mode == "native" and not supports_image_input:
        # Declaring native image transport without confirming image input is a
        # configuration error — callers would otherwise hit a hard 409 mid-turn.
        raise ModelCapabilityError(
            "image_input_mode=native requires supports_image_input=true"
        )
    context_window_tokens = int(
        payload.get("context_window_tokens") or UNKNOWN_MODEL_CONTEXT_TOKENS
    )
    context_limit_tokens = int(
        payload.get("context_limit_tokens") or context_window_tokens
    )
    max_output_tokens = int(payload.get("max_output_tokens") or 4_096)
    if context_window_tokens < 8_000 or context_window_tokens > 10_000_000:
        raise ModelCapabilityError(
            "context_window_tokens must be between 8,000 and 10,000,000"
        )
    if context_limit_tokens < 8_000 or context_limit_tokens > context_window_tokens:
        raise ModelCapabilityError(
            "context_limit_tokens must be between 8,000 and context_window_tokens"
        )
    if max_output_tokens < 1 or max_output_tokens >= context_limit_tokens:
        raise ModelCapabilityError(
            "max_output_tokens must be positive and below context_limit_tokens"
        )
    return {
        "reasoning_efforts": list(dict.fromkeys(efforts)),
        "thinking_mapping": dict(mapping),
        "default_thinking_mode": default_mode,
        "reasoning_parameter": parameter,
        "hosted_web_search": payload.get("hosted_web_search") is True,
        "default_search_route": default_route,
        "supports_image_input": supports_image_input,
        "image_input_mode": image_input_mode,
        "capability_source": payload.get("capability_source", "user_declared"),
        "context_window_tokens": context_window_tokens,
        "context_limit_tokens": context_limit_tokens,
        "max_output_tokens": max_output_tokens,
    }


def resolve_image_input_mode(
    capabilities: dict[str, Any],
    model_id: str,
    *,
    vision_available: bool,
) -> ImageInputMode | None:
    """Decide how images attached to a chat turn should reach a model.

    Returns:
      - ``"native"``: send image parts on the primary model transport
      - ``"external_vision"``: describe images via a vision companion first
      - ``None``: no usable path (caller should raise a typed unavailable error)

    ``auto`` prefers native when the selected model snapshot declares
    ``supports_image_input``, otherwise falls back to an enabled vision Provider.
    """

    resolved = _model_capabilities(capabilities, model_id)
    mode = resolved.get("image_input_mode") or "auto"
    if mode not in IMAGE_INPUT_MODES:
        mode = "auto"
    supports_native = resolved.get("supports_image_input") is True
    if mode == "native":
        return "native" if supports_native else None
    if mode == "external_vision":
        return "external_vision" if vision_available else None
    # auto
    if supports_native:
        return "native"
    if vision_available:
        return "external_vision"
    return None
