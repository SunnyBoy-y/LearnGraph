from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.providers.model_catalog import (
    UNKNOWN_MODEL_CONTEXT_TOKENS,
    unified_model_defaults,
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
    actual_reasoning_effort: str | int | bool | None
    reasoning_parameter: str
    search_route: SearchRoute
    native_web_search: bool
    provider_options: dict[str, Any]

    def trace(self) -> dict[str, Any]:
        return {
            "thinking_mode": self.thinking_mode,
            "actual_reasoning_effort": self.actual_reasoning_effort,
            "reasoning_parameter": self.reasoning_parameter,
            "search_route": self.search_route,
            "native_web_search": self.native_web_search,
            "provider_options": dict(self.provider_options),
        }


def _model_capabilities(capabilities: dict[str, Any], model_id: str) -> dict[str, Any]:
    merged = unified_model_defaults(
        model_id,
        provider_type=str(capabilities.get("provider_family") or ""),
    )
    merged.update(capabilities)
    configured_models = capabilities.get("models")
    if isinstance(configured_models, dict):
        selected = configured_models.get(model_id)
        if isinstance(selected, dict):
            merged.update(selected)
    # The group template is an all-or-nothing global override: while the
    # workspace switch is on it wins over per-model snapshots; while off,
    # each model falls back to its own snapshot or catalog defaults.
    group_defaults = capabilities.get("model_defaults")
    if (
        isinstance(group_defaults, dict)
        and group_defaults
        and capabilities.get("model_defaults_enabled") is not False
    ):
        merged.update(group_defaults)
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
        if actual is not None and not isinstance(actual, (str, int, bool)):
            raise ModelCapabilityError(
                "The off thinking mapping must be a string, integer, boolean, or null"
            )
        if resolved.get("thinking_required") is True:
            raise ModelCapabilityError(
                "The selected model only supports thinking mode; fast mode is unavailable"
            )
    else:
        if requested_thinking not in efforts:
            raise ModelCapabilityError(
                f"The selected model does not support thinking mode '{requested_thinking}'"
            )
        actual = raw_mapping.get(requested_thinking, requested_thinking)
        if not isinstance(actual, (str, int, bool)) or (
            isinstance(actual, str) and not actual.strip()
        ):
            raise ModelCapabilityError("The selected thinking mode has no valid provider mapping")
        if isinstance(actual, str):
            actual = actual.strip()

    parameter = str(resolved.get("reasoning_parameter") or "reasoning_effort").strip()
    if parameter not in {
        "reasoning_effort",
        "reasoning.effort",
        "enable_thinking",
        "thinking_budget",
        "thinking",
    }:
        raise ModelCapabilityError("Unsupported reasoning parameter mapping")
    provider_options: dict[str, Any] = {}
    if parameter == "enable_thinking":
        if requested_thinking == "off":
            provider_options["enable_thinking"] = False
        elif isinstance(actual, bool):
            provider_options["enable_thinking"] = actual
        elif isinstance(actual, str) and actual.casefold() in {"true", "false"}:
            provider_options["enable_thinking"] = actual.casefold() == "true"
        else:
            raise ModelCapabilityError(
                "enable_thinking mappings must resolve to true or false"
            )
    elif parameter == "thinking_budget":
        provider_options["enable_thinking"] = requested_thinking != "off"
        if requested_thinking != "off":
            if not isinstance(actual, int) or isinstance(actual, bool) or actual < 1:
                raise ModelCapabilityError(
                    "The selected thinking mode has no valid positive token budget"
                )
            provider_options["thinking_budget"] = actual
    elif parameter == "thinking":
        provider_options["thinking"] = actual

    requested_route = search_route or resolved.get("default_search_route") or "auto"
    if requested_route not in SEARCH_ROUTES:
        raise ModelCapabilityError("Unsupported search route")
    if requested_route == "auto":
        requested_route = (
            "model_native"
            if resolved.get("hosted_web_search") is True
            else "external"
        )
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
        provider_options=provider_options,
    )


def validate_model_capability_update(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the user-confirmed portion of a per-model capability snapshot."""

    efforts = payload.get("reasoning_efforts", [])
    if not isinstance(efforts, list) or any(item not in THINKING_MODES[1:] for item in efforts):
        raise ModelCapabilityError("reasoning_efforts may only contain low/medium/high/xhigh")
    mapping = payload.get("thinking_mapping", {})
    if not isinstance(mapping, dict) or any(key not in THINKING_MODES for key in mapping):
        raise ModelCapabilityError("thinking_mapping contains an unknown LearnGraph mode")
    if any(
        value is not None
        and (
            not isinstance(value, (str, int, bool))
            or (isinstance(value, str) and not value.strip())
            or (isinstance(value, int) and not isinstance(value, bool) and value < 1)
        )
        for value in mapping.values()
    ):
        raise ModelCapabilityError(
            "thinking_mapping values must be strings, positive integers, booleans, or null"
        )
    default_mode = payload.get("default_thinking_mode", "off")
    if default_mode not in THINKING_MODES:
        raise ModelCapabilityError("default_thinking_mode is invalid")
    if default_mode != "off" and default_mode not in efforts:
        raise ModelCapabilityError("default_thinking_mode is not listed in reasoning_efforts")
    thinking_required = payload.get("thinking_required") is True
    if thinking_required and default_mode == "off":
        raise ModelCapabilityError(
            "thinking-only models require a non-off default_thinking_mode"
        )
    default_route = payload.get("default_search_route", "auto")
    if default_route not in SEARCH_ROUTES:
        raise ModelCapabilityError("default_search_route is invalid")
    if default_route == "model_native" and payload.get("hosted_web_search") is not True:
        raise ModelCapabilityError("model_native requires hosted_web_search=true")
    parameter = payload.get("reasoning_parameter", "reasoning_effort")
    if parameter not in {
        "reasoning_effort",
        "reasoning.effort",
        "enable_thinking",
        "thinking_budget",
        "thinking",
    }:
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
    try:
        chat_compaction_ratio = float(payload.get("chat_compaction_ratio", 0.8))
        agent_compaction_ratio = float(payload.get("agent_compaction_ratio", 1 / 3))
    except (TypeError, ValueError) as exc:
        raise ModelCapabilityError("compaction ratios must be numeric") from exc
    if context_window_tokens < 8_000 or context_window_tokens > 10_000_000:
        raise ModelCapabilityError(
            "context_window_tokens must be between 8,000 and 10,000,000"
        )
    if context_limit_tokens < 8_000 or context_limit_tokens > context_window_tokens:
        raise ModelCapabilityError(
            "context_limit_tokens must be between 8,000 and context_window_tokens"
        )
    if max_output_tokens < 1 or max_output_tokens > 1_000_000:
        raise ModelCapabilityError(
            "max_output_tokens must be between 1 and 1,000,000"
        )
    if not 0.1 <= chat_compaction_ratio <= 1.0:
        raise ModelCapabilityError("chat_compaction_ratio must be between 0.1 and 1.0")
    if not 0.1 <= agent_compaction_ratio <= 1.0:
        raise ModelCapabilityError("agent_compaction_ratio must be between 0.1 and 1.0")
    return {
        "reasoning_efforts": list(dict.fromkeys(efforts)),
        "thinking_mapping": dict(mapping),
        "default_thinking_mode": default_mode,
        "reasoning_parameter": parameter,
        "thinking_required": thinking_required,
        "hosted_web_search": payload.get("hosted_web_search") is True,
        "hosted_web_fetch": payload.get("hosted_web_fetch") is True,
        "hosted_image_search": payload.get("hosted_image_search") is True,
        "default_search_route": default_route,
        "supports_image_input": supports_image_input,
        "supports_video_input": payload.get("supports_video_input") is True,
        "supports_structured_output": payload.get("supports_structured_output") is True,
        "supports_agent_tools": payload.get("supports_agent_tools") is True,
        "image_input_mode": image_input_mode,
        "capability_source": payload.get("capability_source", "user_declared"),
        "context_window_tokens": context_window_tokens,
        "context_limit_tokens": context_limit_tokens,
        "max_output_tokens": max_output_tokens,
        "chat_compaction_ratio": chat_compaction_ratio,
        "agent_compaction_ratio": agent_compaction_ratio,
    }


# Mirrors the frontend's blank capability form: the baseline a catalog record
# is merged over so the persisted snapshot is always complete.
_CATALOG_BASE_CAPABILITIES: dict[str, Any] = {
    "reasoning_efforts": ["low", "medium", "high", "xhigh"],
    "thinking_mapping": {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh"},
    "default_thinking_mode": "medium",
    "reasoning_parameter": "reasoning_effort",
    "thinking_required": False,
    "hosted_web_search": False,
    "hosted_web_fetch": False,
    "hosted_image_search": False,
    "supports_image_input": False,
    "supports_video_input": False,
    "supports_structured_output": False,
    "supports_agent_tools": True,
    "image_input_mode": "auto",
    "default_search_route": "auto",
    "capability_source": "official_catalog",
    "context_window_tokens": UNKNOWN_MODEL_CONTEXT_TOKENS,
    "context_limit_tokens": UNKNOWN_MODEL_CONTEXT_TOKENS,
    "max_output_tokens": 4_096,
    "chat_compaction_ratio": 0.8,
    "agent_compaction_ratio": 1 / 3,
}


def catalog_capability_snapshot(
    model_id: str, *, provider_type: str | None = None
) -> dict[str, Any]:
    """Official catalog defaults normalized into a savable capability snapshot.

    Catalog records are partial and occasionally self-inconsistent (for
    example a thinking-only model without a default mode); this repairs them
    so the result always passes ``validate_model_capability_update``.
    """

    merged: dict[str, Any] = {
        **_CATALOG_BASE_CAPABILITIES,
        "thinking_mapping": dict(_CATALOG_BASE_CAPABILITIES["thinking_mapping"]),
        "reasoning_efforts": list(_CATALOG_BASE_CAPABILITIES["reasoning_efforts"]),
    }
    merged.update(unified_model_defaults(model_id, provider_type=provider_type))
    efforts = [
        item
        for item in (merged.get("reasoning_efforts") or [])
        if item in THINKING_MODES[1:]
    ]
    merged["reasoning_efforts"] = efforts
    raw_mapping = merged.get("thinking_mapping")
    merged["thinking_mapping"] = {
        key: value
        for key, value in (raw_mapping.items() if isinstance(raw_mapping, dict) else [])
        if key in THINKING_MODES
    }
    default_mode = merged.get("default_thinking_mode", "off")
    if default_mode != "off" and default_mode not in efforts:
        default_mode = "off"
    if default_mode == "off" and merged.get("thinking_required") is True:
        if efforts:
            default_mode = "medium" if "medium" in efforts else efforts[0]
        else:
            merged["thinking_required"] = False
    merged["default_thinking_mode"] = default_mode
    if (
        merged.get("default_search_route") == "model_native"
        and merged.get("hosted_web_search") is not True
    ):
        merged["default_search_route"] = "auto"
    if (
        merged.get("image_input_mode") == "native"
        and merged.get("supports_image_input") is not True
    ):
        merged["image_input_mode"] = "auto"
    merged["capability_source"] = "official_catalog"
    window = int(merged.get("context_window_tokens") or UNKNOWN_MODEL_CONTEXT_TOKENS)
    limit = int(merged.get("context_limit_tokens") or window)
    merged["context_window_tokens"] = window
    merged["context_limit_tokens"] = min(limit, window)
    output = int(merged.get("max_output_tokens") or 4_096)
    merged["max_output_tokens"] = max(1, min(output, 1_000_000))
    merged["chat_compaction_ratio"] = min(
        1.0, max(0.1, float(merged.get("chat_compaction_ratio", 0.8)))
    )
    merged["agent_compaction_ratio"] = min(
        1.0, max(0.1, float(merged.get("agent_compaction_ratio", 1 / 3)))
    )
    return merged


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
