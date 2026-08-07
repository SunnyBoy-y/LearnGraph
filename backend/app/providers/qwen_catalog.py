from __future__ import annotations

"""Reviewed Qwen AI Platform model defaults.

The project-wide model catalogue uses the same shape as models.dev and then
applies vendor documentation overrides for fields that models.dev cannot
express (hosted tools, default thinking state, and protocol-specific controls).
Keep request-shape decisions here instead of inferring them in HTTP adapters.
"""

from math import floor
from typing import Any, Iterable
from urllib.parse import urlsplit


MODELS_DEV_SOURCE = "https://models.dev/api.json"
QWEN_THINKING_SOURCE = (
    "https://platform.qianwenai.com/docs/developer-guides/text-generation/thinking"
)
QWEN_CHAT_COMPLETIONS_SOURCE = (
    "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions"
)
QWEN_RESPONSES_SOURCE = (
    "https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses"
)
QWEN_VISION_SOURCE = (
    "https://platform.qianwenai.com/docs/developer-guides/getting-started/vision-models"
)
QWEN_IMAGE_EDIT_SOURCE = (
    "https://help.aliyun.com/zh/model-studio/qwen-image-edit-api"
)

LEARNGRAPH_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh")


def is_dashscope_api_base_url(base_url: str | None) -> bool:
    """Return whether ``base_url`` addresses a DashScope compatible-mode origin.

    DashScope hosts many model families besides Qwen (DeepSeek, GLM, Kimi,
    MiniMax), so a model identifier cannot decide the request shape.  The
    gateway does: thinking is toggled with ``enable_thinking`` and search with
    ``enable_search`` regardless of whose weights answer.

    Unlike the DeepSeek origin check this widens no trust boundary — the saved
    key already goes to this host either way, and the answer only selects which
    adapter formats the request.  The documented base URL therefore carries the
    ``/compatible-mode/v1`` path, which must be accepted rather than rejected.
    """

    if not base_url:
        return False
    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not host:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    # Regional deployments differ only by their leading label
    # (``dashscope``, ``dashscope-intl``, ...).
    label, _, domain = host.partition(".")
    return domain == "aliyuncs.com" and (
        label == "dashscope" or label.startswith("dashscope-")
    )


def proportional_effort_mapping(values: Iterable[str]) -> dict[str, str]:
    """Map four LearnGraph levels onto a provider subset proportionally."""

    provider_values = [str(value).strip() for value in values if str(value).strip()]
    if not provider_values:
        return {}
    if len(provider_values) == 1:
        return {effort: provider_values[0] for effort in LEARNGRAPH_EFFORTS}
    last = len(provider_values) - 1
    denominator = len(LEARNGRAPH_EFFORTS) - 1
    return {
        effort: provider_values[
            floor((index * last / denominator) + 0.5)
        ]
        for index, effort in enumerate(LEARNGRAPH_EFFORTS)
    }


def proportional_budget_mapping(
    maximum: int,
    *,
    minimum: int = 1,
) -> dict[str, int]:
    """Spread the product's four levels evenly across a token budget."""

    if maximum < 1:
        return {}
    minimum = max(1, min(minimum, maximum))
    span = maximum - minimum
    return {
        effort: max(
            minimum,
            min(
                maximum,
                round(minimum + span * ((index + 1) / len(LEARNGRAPH_EFFORTS))),
            ),
        )
        for index, effort in enumerate(LEARNGRAPH_EFFORTS)
    }


def _model_key(model_id: str) -> str:
    return model_id.strip().casefold()


def _starts(model: str, prefixes: tuple[str, ...]) -> bool:
    return any(model.startswith(prefix) for prefix in prefixes)


def _is_pure_reasoning(model: str) -> bool:
    return (
        model in {
            "qwen3.8-max-preview",
            "qwen3.7-max-preview",
            "qwen3.7-max-2026-05-17",
            "qwen3-next-80b-a3b-thinking",
            "qwen3-235b-a22b-thinking-2507",
            "qwen3-30b-a3b-thinking-2507",
            "qwq-plus",
            "deepseek-r1",
            "deepseek-r1-0528",
            "siliconflow/deepseek-r1-0528",
            "vanchin/deepseek-r1",
            "kimi-k2.7-code",
            "kimi-k2-thinking",
            "kimi/kimi-k3",
            "kimi/kimi-k2.7-code-highspeed",
            "kimi/kimi-k2.7-code",
            "minimax-m2.5",
            "minimax-m2.1",
            "minimax/minimax-m2.7",
            "minimax/minimax-m2.5",
            "minimax/minimax-m2.1",
        }
        or "deepseek-r1-distill" in model
    )


def _is_hybrid_default_on(model: str) -> bool:
    return (
        _starts(
            model,
            (
                "qwen3.8-max",
                "qwen3.7-max",
                "qwen3.7-plus",
                "qwen3.7-flash",
                "qwen3.6-max",
                "qwen3.6-plus",
                "qwen3.6-flash",
                "qwen3.6-35b-a3b",
                "qwen3.6-27b",
                "qwen3.5-plus",
                "qwen3.5-flash",
                "qwen3.5-397b-a17b",
                "qwen3.5-122b-a10b",
                "qwen3.5-27b",
                "qwen3.5-35b-a3b",
                "qwen3-235b-a22b",
                "qwen3-32b",
                "qwen3-30b-a3b",
                "qwen3-14b",
                "qwen3-8b",
                "qwen3-4b",
                "qwen3-1.7b",
                "qwen3-0.6b",
                "deepseek-v4-",
                "glm-",
                "kimi/kimi-k2.6",
                "kimi/kimi-k2.5",
                "minimax/minimax-m3",
                "xiaomi/mimo-",
                "mimo-v2.5-pro",
            ),
        )
        or model == "stepfun/step-3.7-flash"
    )


def _is_hybrid_default_off(model: str) -> bool:
    return _starts(
        model,
        (
            "qwen3-max",
            "qwen-plus",
            "qwen-flash",
            "qwen-turbo",
            "qwen3-vl-",
            "qwen3-omni-flash",
            "deepseek-v3.2",
            "deepseek-v3.1",
            "siliconflow/deepseek-v3.2",
            "siliconflow/deepseek-v3.1-terminus",
            "vanchin/deepseek-v3.2-think",
            "vanchin/deepseek-v3.1-terminus",
            "kimi-k2.6",
            "kimi-k2.5",
        ),
    )


def _thinking_budget_max(model: str) -> int | None:
    if model == "qwen3.8-max-preview" or _starts(
        model, ("qwen3.7-max", "qwen3.7-plus", "qwen3.7-flash")
    ):
        return 262_144
    if _starts(model, ("qwen3.6-flash", "qwen3.6-35b", "qwen3.6-27b")):
        return 131_072
    if _starts(model, ("qwen3.6-max", "qwen3.6-plus")):
        return 81_920
    if model in {
        "qwen3-next-80b-a3b-thinking",
        "qwen3-235b-a22b-thinking-2507",
        "qwen3-30b-a3b-thinking-2507",
    }:
        return 81_920
    if _starts(
        model,
        (
            "qwen3.5-",
            "qwen3-vl-",
            "qwen3-max",
            "qwen-plus",
            "qwen-flash",
        ),
    ):
        return 81_920
    if _starts(
        model,
        (
            "qwen3-235b-a22b",
            "qwen3-32b",
            "qwen3-30b-a3b",
            "qwen3-14b",
            "qwen3-8b",
            "qwen3-4b",
            "qwen-turbo",
        ),
    ):
        return 38_912
    if _starts(model, ("qwen3-1.7b", "qwen3-0.6b")):
        return 30_720
    return None


def _context_defaults(model: str) -> tuple[int, int]:
    if _starts(model, ("qwen-math-plus", "qwen-math-turbo")):
        return 32_768, 8_192
    if model == "qwen3.8-max-preview":
        return 1_000_000, 131_072
    if _starts(model, ("qwen3.7-",)):
        return 1_000_000, 65_536
    if model == "qwen3.6-max-preview":
        return 245_800, 65_536
    if _starts(model, ("qwen3.6-plus", "qwen3.6-flash")):
        return 1_000_000, 65_536
    if model in {"qwen3.6-35b-a3b", "qwen3.6-27b"}:
        return 262_144, 65_536
    if _starts(model, ("qwen3.5-plus", "qwen3.5-flash")):
        return 1_000_000, 65_536
    if _starts(
        model,
        (
            "qwen3.5-397b",
            "qwen3.5-122b",
            "qwen3.5-27b",
            "qwen3.5-35b",
        ),
    ):
        return 32_768, 8_192
    if _starts(model, ("qwen3-max",)):
        return 262_144, 65_536
    if _starts(model, ("qwen-plus", "qwen-flash")):
        return 1_000_000, 32_768
    if _starts(model, ("qwen-turbo",)):
        return 1_000_000, 16_384
    if model == "qwen-long":
        return 10_000_000, 8_192
    if _starts(model, ("qwen3-next-",)):
        return 131_072, 32_768
    if _starts(model, ("qwen3-235b", "qwen3-32b", "qwen3-30b")):
        return 131_072, 16_384
    if _starts(model, ("qwen3-14b", "qwen3-8b", "qwen3-4b")):
        return 131_072, 8_192
    if _starts(model, ("qwen3-1.7b", "qwen3-0.6b")):
        return 32_768, 8_192
    if _starts(model, ("qwen3-vl-",)):
        return 262_144, 32_768
    if _starts(model, ("qwen3-omni-",)):
        return 65_536, 16_384
    if _starts(model, ("qwen-omni-turbo",)):
        return 32_768, 2_048
    if _starts(model, ("deepseek-v4-",)):
        return 1_000_000, 384_000
    if _starts(model, ("deepseek-v3.",)):
        return 131_072, 65_536
    if _starts(model, ("siliconflow/deepseek-",)):
        return 163_840, 65_536
    if _starts(model, ("kimi/", "kimi-")):
        return 262_144, 262_144
    if _starts(model, ("glm-5.2",)):
        return 1_000_000, 131_072
    if _starts(model, ("glm-",)):
        return 202_752, 131_072
    if _starts(model, ("minimax/", "minimax-")):
        return 204_800, 131_072
    if _starts(model, ("xiaomi/mimo-", "mimo-")):
        return 262_144, 65_536
    return 256_000, 4_096


def _supports_vision(model: str) -> bool:
    return (
        model
        in {
            "qwen3.7-max-2026-06-08",
            "qwen3.7-plus",
            "qwen3.7-plus-2026-05-26",
            "qwen3.6-plus",
            "qwen3.6-plus-2026-04-02",
            "qwen3.6-flash",
            "qwen3.6-flash-2026-04-16",
            "qwen3.5-plus",
            "qwen3.5-plus-2026-02-15",
            "qwen3.5-flash",
            "qwen3.5-flash-2026-02-23",
        }
        or _starts(
            model,
            (
                "qwen3.6-35b",
                "qwen3.6-27b",
                "qwen3.5-397b",
                "qwen3.5-122b",
                "qwen3.5-27b",
                "qwen3.5-35b",
                "qwen3-vl",
                "qwen-vl",
                "qwen3.5-omni",
                "qwen3-omni",
                "kimi-k2.",
                "kimi/kimi-",
            ),
        )
    )


def _supports_responses_tools(model: str) -> bool:
    return _starts(
        model,
        (
            "qwen3.8-",
            "qwen3.7-",
            "qwen3.6-",
            "qwen3.5-",
        ),
    ) or model in {"qwen3-max", "qwen3-max-2026-01-23"}


def _supports_web_search(model: str) -> bool:
    return (
        _supports_responses_tools(model)
        or _starts(
            model,
            (
                "qwen-max",
                "qwen-plus",
                "qwen-flash",
                "qwen-turbo",
                "qwq-plus",
                "deepseek-",
            ),
        )
        or model in {"moonshot-kimi-k2-instruct", "minimax-m2.1"}
    )


def _supports_image_search(model: str) -> bool:
    if model == "qwen3.6-27b":
        return False
    return (
        _starts(
            model,
            (
                "qwen3.7-plus",
                "qwen3.6-plus",
                "qwen3.5-plus",
                "qwen3.7-flash",
                "qwen3.6-flash",
                "qwen3.5-flash",
                "qwen3.6-",
                "qwen3.5-",
            ),
        )
        or model in {"qwen3.8-max-preview", "qwen3.7-max-2026-06-08"}
    )


def _supports_web_fetch(model: str) -> bool:
    return _supports_responses_tools(model) and model != "qwen3.6-27b"


def _is_native_qwen_model(model: str) -> bool:
    return _starts(model, ("qwen", "qwq"))


def _is_image_generation_model(model: str) -> bool:
    """Return whether a model is a DashScope image generation/editing model.

    These models produce images (not text) and are used through the image
    generation Provider rather than the chat stream.  ``supports_text_output``
    being False is what keeps them out of text-chat model selection.
    """

    return _starts(model, ("qwen-image", "wanx"))


def qwen_model_defaults(model_id: str) -> dict[str, Any]:
    """Return official overrides in the project-wide capability shape."""

    model = _model_key(model_id)
    if _is_image_generation_model(model):
        return {
            "context_window_tokens": 8_192,
            "context_limit_tokens": 8_192,
            "max_output_tokens": 4_096,
            "reasoning_efforts": [],
            "thinking_mapping": {"off": None},
            "default_thinking_mode": "off",
            "reasoning_parameter": "enable_thinking",
            "thinking_required": False,
            "hosted_web_search": False,
            "hosted_web_fetch": False,
            "hosted_image_search": False,
            "supports_image_input": True,
            "supports_video_input": False,
            "supports_image_edit": True,
            "supports_text_output": False,
            "supports_structured_output": False,
            "supports_agent_tools": False,
            "image_input_mode": "native",
            "native_tool_protocol": "chat_completions",
            "default_search_route": "disabled",
            "capability_source": "official_catalog",
            "catalog_base_source": MODELS_DEV_SOURCE,
            "source_url": QWEN_IMAGE_EDIT_SOURCE,
        }
    context, output = _context_defaults(model)
    pure = _is_pure_reasoning(model)
    hybrid_on = _is_hybrid_default_on(model) and not pure
    hybrid_off = _is_hybrid_default_off(model) and not pure
    reasoning = pure or hybrid_on or hybrid_off

    result: dict[str, Any] = {
        "context_window_tokens": context,
        "context_limit_tokens": context,
        "max_output_tokens": min(output, max(1, context - 1)),
        "reasoning_efforts": [],
        "thinking_mapping": {"off": None},
        "default_thinking_mode": "off",
        "reasoning_parameter": "enable_thinking",
        "thinking_required": pure,
        "hosted_web_search": _supports_web_search(model),
        "hosted_web_fetch": _supports_web_fetch(model),
        "hosted_image_search": _supports_image_search(model),
        "supports_image_input": _supports_vision(model),
        "supports_video_input": _supports_vision(model),
        "supports_structured_output": _starts(
            model,
            (
                "qwen3.7-plus",
                "qwen3.6-",
                "qwen3.5-",
                "xiaomi/mimo-",
                "mimo-",
            ),
        ),
        "supports_agent_tools": True,
        # No separate MCP declaration: LearnGraph exposes MCP servers as
        # ordinary function tools in the same request, so agent tool support
        # already covers them and a second key could only contradict it.
        "preserve_thinking": model
        in {
            "qwen3.8-max-preview",
            "qwen3.7-max",
            "qwen3.7-max-2026-06-08",
            "qwen3.7-max-2026-05-20",
            "qwen3.7-max-preview",
            "qwen3.7-max-2026-05-17",
            "qwen3.7-plus",
            "qwen3.7-plus-2026-05-26",
            "qwen3.7-flash",
            "qwen3.7-flash-2026-07-15",
            "qwen3.6-max-preview",
            "qwen3.6-plus",
            "qwen3.6-plus-2026-04-02",
            "qwen3.6-flash",
            "qwen3.6-flash-2026-04-16",
            "kimi-k2.7-code",
            "kimi-k2.6",
            "kimi/kimi-k3",
            "kimi/kimi-k2.7-code-highspeed",
            "kimi/kimi-k2.7-code",
            "kimi/kimi-k2.6",
        },
        "image_input_mode": "native" if _supports_vision(model) else "auto",
        "default_search_route": "auto",
        "native_tool_protocol": (
            "responses" if _supports_responses_tools(model) else "chat_completions"
        ),
        "chat_search_strategy": (
            "agent"
            if _starts(
                model,
                (
                    "qwen3.7-max",
                    "qwen3-max",
                    "qwen3.5-plus",
                    "qwen3.5-flash",
                    "qwen3.5-omni",
                ),
            )
            else "turbo"
        ),
        "native_tool_pricing_cny_per_thousand_calls": {
            "web_search_agent": 4.0,
            "web_search_image": 24.0,
            "image_search": 48.0,
        },
        "capability_source": "official_catalog",
        "catalog_base_source": MODELS_DEV_SOURCE,
        "cache_modes": (
            ["implicit", "explicit"]
            + (["session"] if _supports_responses_tools(model) else [])
            if _is_native_qwen_model(model)
            else ["implicit"]
        ),
        "implicit_cache_read_multiplier": 0.20,
        "explicit_cache_read_multiplier": 0.10,
        "explicit_cache_write_multiplier": 1.25,
        "minimum_implicit_cache_tokens": (
            1_000 if _starts(model, ("qwen3.7-max",)) else 256
        ),
        "minimum_explicit_cache_tokens": 1_024,
        "source_url": QWEN_THINKING_SOURCE,
        "vision_source_url": QWEN_VISION_SOURCE,
    }
    if not _is_native_qwen_model(model):
        for key in (
            "implicit_cache_read_multiplier",
            "explicit_cache_read_multiplier",
            "explicit_cache_write_multiplier",
            "minimum_explicit_cache_tokens",
        ):
            result.pop(key, None)
        result["cache_cost_policy"] = "provider_specific"
    if not reasoning:
        return result

    result["reasoning_efforts"] = list(LEARNGRAPH_EFFORTS)
    result["default_thinking_mode"] = "medium" if (pure or hybrid_on) else "off"

    if model == "qwen3.8-max-preview":
        # The API documents three values and their equivalent token budgets.
        result["reasoning_parameter"] = "reasoning_effort"
        result["default_thinking_mode"] = "xhigh"
        result["thinking_mapping"] = {
            "off": None,
            **proportional_effort_mapping(("low", "medium", "xhigh")),
        }
        result["provider_reasoning_budgets"] = {
            "low": 4_096,
            "medium": 16_384,
            "xhigh": 262_144,
        }
        return result

    if _starts(model, ("deepseek-v4-", "glm-5.2", "glm-5.1")) or model == "glm-5":
        # Alibaba Cloud documents reasoning_effort for these as ``high`` /
        # ``max`` only: ``low`` and ``medium`` map to ``high``, ``xhigh`` maps
        # to ``max``, and ``high`` stays ``high``.  (A proportional spread of
        # {high, max} would wrongly push the ``high`` tier up to ``max``.)
        result["reasoning_parameter"] = "reasoning_effort"
        result["thinking_mapping"] = {
            "off": None,
            **{effort: "high" for effort in ("low", "medium", "high")},
            "xhigh": "max",
        }
        return result

    if model == "minimax/minimax-m3":
        result["reasoning_parameter"] = "thinking"
        result["thinking_mapping"] = {
            "off": "disabled",
            **{effort: "adaptive" for effort in LEARNGRAPH_EFFORTS},
        }
        return result

    budget_max = _thinking_budget_max(model)
    if budget_max is not None:
        result["reasoning_parameter"] = "thinking_budget"
        if result["default_thinking_mode"] != "off":
            result["default_thinking_mode"] = "xhigh"
        result["thinking_budget_max"] = budget_max
        result["thinking_mapping"] = {
            "off": None,
            **proportional_budget_mapping(budget_max),
        }
        return result

    # Boolean-shaped models expose intensity choices in the product, but every
    # non-off choice intentionally becomes enable_thinking=true.
    result["thinking_mapping"] = {
        "off": False,
        **{effort: True for effort in LEARNGRAPH_EFFORTS},
    }
    return result
