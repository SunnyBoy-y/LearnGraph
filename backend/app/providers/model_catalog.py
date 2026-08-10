from __future__ import annotations

from typing import Any

from app.providers.qwen_catalog import qwen_model_defaults


UNKNOWN_MODEL_CONTEXT_TOKENS = 256_000
UNKNOWN_MODEL_CONTEXT_SOURCE = "conservative_default"


# This is a reviewed defaults catalogue, not a discovery substitute. Provider
# discovery remains authoritative for which model IDs are actually available.
# Values are only used when a workspace has not saved a group or per-model
# override.
MODEL_CONTEXT_DEFAULTS: dict[str, dict[str, Any]] = {
    "deepseek-v4-flash": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 384_000,
        "source_url": "https://api-docs.deepseek.com/quick_start/pricing",
    },
    "deepseek-v4-pro": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 384_000,
        "source_url": "https://api-docs.deepseek.com/quick_start/pricing",
    },
    "gpt-5.5": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "source_url": "https://developers.openai.com/api/docs/models/gpt-5.5",
    },
    "gpt-5.6-sol": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "source_url": "https://developers.openai.com/api/docs/models",
    },
    "gpt-5.6-terra": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "source_url": "https://developers.openai.com/api/docs/models",
    },
    "gpt-5.6-luna": {
        "context_window_tokens": 1_050_000,
        "max_output_tokens": 128_000,
        "source_url": "https://developers.openai.com/api/docs/models",
    },
    "gemini-3.1-pro-preview": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 64_000,
        "source_url": "https://ai.google.dev/gemini-api/docs/gemini-3",
    },
    "gemini-3.1-flash-lite": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 64_000,
        "source_url": "https://ai.google.dev/gemini-api/docs/gemini-3",
    },
    "gemini-3-flash-preview": {
        "context_window_tokens": 1_000_000,
        "max_output_tokens": 64_000,
        "source_url": "https://ai.google.dev/gemini-api/docs/gemini-3",
    },
    "kimi-k2.5": {
        "context_window_tokens": 256_000,
        "source_url": "https://platform.kimi.com/docs/models",
    },
    "kimi-k2.6": {
        "context_window_tokens": 256_000,
        "source_url": "https://platform.kimi.com/docs/models",
    },
    "kimi-k2.7-code": {
        "context_window_tokens": 256_000,
        "source_url": "https://platform.kimi.com/docs/pricing/chat-k27-code",
    },
    "kimi-k2.7-code-highspeed": {
        "context_window_tokens": 256_000,
        "source_url": "https://platform.kimi.com/docs/pricing/chat-k27-code",
    },
    "kimi-k3": {
        "context_window_tokens": 1_000_000,
        "source_url": "https://platform.kimi.com/docs/pricing/chat-k3",
    },
    "qwen3.7-max": {
        "context_window_tokens": 1_000_000,
        "source_url": "https://help.aliyun.com/en/model-studio/model-pricing",
    },
    "qwen3.7-max-2026-06-08": {
        "context_window_tokens": 1_000_000,
        "source_url": "https://help.aliyun.com/en/model-studio/model-pricing",
    },
    "qwen3.7-max-2026-05-20": {
        "context_window_tokens": 1_000_000,
        "source_url": "https://help.aliyun.com/en/model-studio/model-pricing",
    },
    "qwen3.7-plus": {
        "context_window_tokens": 1_000_000,
        "source_url": "https://help.aliyun.com/en/model-studio/model-pricing",
    },
    "qwen3.6-plus": {
        "context_window_tokens": 1_000_000,
        "source_url": "https://help.aliyun.com/en/model-studio/model-pricing",
    },
    "qwen3.5-plus": {
        "context_window_tokens": 1_000_000,
        "source_url": "https://help.aliyun.com/en/model-studio/model-pricing",
    },
    "qwen3-max": {
        "context_window_tokens": 256_000,
        "source_url": "https://help.aliyun.com/en/model-studio/model-pricing",
    },
}


def model_context_defaults(model_id: str) -> dict[str, Any]:
    normalized = model_id.strip().casefold()
    exact = MODEL_CONTEXT_DEFAULTS.get(normalized)
    if exact is not None:
        return dict(exact)
    if normalized.startswith("gpt-5.6"):
        return dict(MODEL_CONTEXT_DEFAULTS["gpt-5.6-sol"])
    if normalized.startswith("kimi-k2."):
        return {
            "context_window_tokens": 256_000,
            "source_url": "https://platform.kimi.com/docs/models",
        }
    if (
        normalized.startswith(
            (
                "qwen",
                "qwq",
                "deepseek",
                "siliconflow/deepseek",
                "vanchin/deepseek",
                "glm-",
                "kimi/",
                "minimax",
                "xiaomi/mimo-",
                "mimo-",
                "stepfun/",
            )
        )
    ):
        return qwen_model_defaults(model_id)
    return {
        "context_window_tokens": UNKNOWN_MODEL_CONTEXT_TOKENS,
        "source_url": None,
    }


def unified_model_defaults(
    model_id: str,
    *,
    provider_type: str | None = None,
    dashscope_hosted: bool = True,
) -> dict[str, Any]:
    """Project-wide defaults interface built on the models.dev-shaped schema.

    Base defaults come from the reviewed vendor catalogues; when the model id
    matches a models.dev record (regardless of provider), the models.dev
    snapshot overrides context window, output limit, thinking availability,
    and multimodal input support.  Workspace-saved per-Provider configuration
    is merged later in ``model_options`` and always wins over both.

    ``dashscope_hosted`` gates the DashScope-private catalogue claims (hosted
    search, preserve thinking, Responses tools, budget thinking): they are
    only granted when the endpoint actually speaks the DashScope dialect.
    """

    from app.providers.models_dev import capability_overlay

    normalized_provider = (provider_type or "").strip().casefold()
    normalized_model = model_id.strip().casefold()
    if normalized_provider == "qwen" or normalized_model.startswith(
        (
            "qwen",
            "qwq",
            "deepseek",
            "siliconflow/deepseek",
            "vanchin/deepseek",
            "glm-",
            "kimi",
            "minimax",
            "xiaomi/mimo-",
            "mimo-",
            "stepfun/",
        )
    ):
        base = qwen_model_defaults(model_id, dashscope_hosted=dashscope_hosted)
    else:
        base = model_context_defaults(model_id)
    overlay = capability_overlay(model_id, base)
    if overlay:
        base.update(overlay)
    # Keep a runtime-only provenance bit. It is intentionally not part of the
    # user-editable capability schema: image routing uses it to distinguish a
    # catalog-confirmed text model from a private model that still needs one
    # native vision probe.
    base["models_dev_known"] = bool(overlay)
    # Local Ollama deployments: the official docs confirm DeepSeek-R1 (and its
    # distilled variants) accept ``think: false`` to disable reasoning
    # (``ollama run deepseek-r1 --think=false``). ``qwen_model_defaults`` marks
    # them thinking-only because the Alibaba Cloud *hosted* DeepSeek-R1 cannot
    # turn thinking off; on Ollama they are hybrid and 极速 must stay available.
    if normalized_provider == "ollama" and (
        normalized_model in {"deepseek-r1", "deepseek-r1-0528"}
        or "deepseek-r1-distill" in normalized_model
    ):
        base["thinking_required"] = False
        base["default_thinking_mode"] = "off"
    return base
