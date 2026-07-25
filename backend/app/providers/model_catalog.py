from __future__ import annotations

from typing import Any


UNKNOWN_MODEL_CONTEXT_TOKENS = 256_000


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
    return {
        "context_window_tokens": UNKNOWN_MODEL_CONTEXT_TOKENS,
        "source_url": None,
    }
