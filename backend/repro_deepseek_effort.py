"""Generic effort-mapping probe across model providers.

Runs the real resolve chain (``resolve_model_call_options``) plus the
adapter-level hard-coded tables, showing whether editing ``thinking_mapping``
actually changes the request-time reasoning effort for each provider family.

Usage:
    cd backend && uv run python repro_deepseek_effort.py
"""

from __future__ import annotations

from app.providers.model_options import (
    model_capabilities_for_model,
    resolve_model_call_options,
)
from app.providers.remote.deepseek import is_deepseek_chat_configuration
from app.providers.remote.ollama import ollama_think_value
from app.providers.qwen_catalog import PROTOCOL_FAMILY_DASHSCOPE

THINKING_MODES = ("low", "medium", "high", "xhigh")


# factory.py:432-450 — DeepSeek official-direct provider-level defaults injection
# (post-fix: passthrough mapping; display == resolve, no separate override).
def inject_deepseek_defaults(capabilities: dict) -> dict:
    return {
        "reasoning_efforts": ["low", "medium", "high", "xhigh"],
        "thinking_mapping": {
            "off": None,
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "max",
        },
        "default_thinking_mode": "off",
        "reasoning_parameter": "reasoning_effort",
        "hosted_web_search": False,
        "default_search_route": "auto",
        **capabilities,
    }


# anthropic.py:246-251 — hard-coded budget_tokens for non-adaptive Claude models.
ANTHROPIC_BUDGET = {"low": 4_000, "medium": 10_000, "high": 20_000, "xhigh": 32_000}


def resolve_row(capabilities: dict, model_id: str, mode: str):
    try:
        return resolve_model_call_options(
            capabilities, model_id, thinking_mode=mode, search_route="disabled"
        )
    except Exception as exc:  # noqa: BLE001 — surface the failure mode
        return exc


def print_resolve_table(label: str, capabilities: dict, model_id: str, *, note: str = ""):
    print(f"\n=== {label} ===")
    if note:
        print(f"    ({note})")
    for mode in THINKING_MODES:
        r = resolve_row(capabilities, model_id, mode)
        if isinstance(r, Exception):
            print(f"  {mode:<6} -> ERROR: {r}")
            continue
        extra = f"  provider_options={r.provider_options}" if r.provider_options else ""
        print(
            f"  {mode:<6} -> actual={r.actual_reasoning_effort!r:<8} "
            f"param={r.reasoning_parameter}{extra}"
        )


# ---------------------------------------------------------------------------
# 1. DeepSeek official direct — factory injection + display override (the bug).
# ---------------------------------------------------------------------------
DS_DEFAULT = {"default_model": "deepseek-reasoner"}
DS_EDITED = {
    "default_model": "deepseek-reasoner",
    "models": {
        "deepseek-reasoner": {
            "reasoning_efforts": ["low", "medium", "high", "xhigh"],
            "thinking_mapping": {
                "off": None,
                "low": "low",
                "medium": "low",
                "high": "max",
                "xhigh": "max",
            },
            "default_thinking_mode": "medium",
            "reasoning_parameter": "reasoning_effort",
        }
    },
}

print("=" * 70)
print("DeepSeek official direct (api.deepseek.com) — is_deepseek =",
      is_deepseek_chat_configuration("deepseek_chat", "https://api.deepseek.com"))
for name, cap in (("default", DS_DEFAULT), ("per-model edited (medium->low)", DS_EDITED)):
    injected = inject_deepseek_defaults(cap)
    merged = model_capabilities_for_model(injected, "deepseek-reasoner")
    print_resolve_table(f"DeepSeek [{name}]", injected, "deepseek-reasoner")
    print(f"  [display = merge] thinking_mapping = {merged.get('thinking_mapping')}")

# ---------------------------------------------------------------------------
# 2. Qwen DashScope — catalog hard-codes but per-model snapshot overrides.
# ---------------------------------------------------------------------------
QWEN_DEFAULT = {
    "default_model": "qwen3.8-max-preview",
    "protocol_family": PROTOCOL_FAMILY_DASHSCOPE,
    "provider_family": "qwen",
}
QWEN_EDITED = {
    **QWEN_DEFAULT,
    "models": {
        "qwen3.8-max-preview": {
            "reasoning_efforts": ["low", "medium", "high", "xhigh"],
            "thinking_mapping": {
                "off": None,
                "low": "low",
                "medium": "low",
                "high": "high",
                "xhigh": "max",
            },
            "default_thinking_mode": "medium",
            "reasoning_parameter": "reasoning_effort",
        }
    },
}

print("\n" + "=" * 70)
print_resolve_table("Qwen DashScope [default]", QWEN_DEFAULT, "qwen3.8-max-preview")
print_resolve_table("Qwen DashScope [per-model edited medium->low]", QWEN_EDITED, "qwen3.8-max-preview")

# ---------------------------------------------------------------------------
# 3. Anthropic — resolve layer + adapter hard-coded budget table.
# ---------------------------------------------------------------------------
ANTHROPIC_DEFAULT = {
    "default_model": "claude-sonnet-4-20250514",
    "provider_family": "anthropic",
}
print("\n" + "=" * 70)
print_resolve_table("Anthropic [default]", ANTHROPIC_DEFAULT, "claude-sonnet-4-20250514")
print(
    f"  [anthropic.py:246-251] non-adaptive budget_tokens HARD-CODED: {ANTHROPIC_BUDGET}\n"
    "  -> user thinking_mapping picks the TIER, but token counts are fixed."
)

# ---------------------------------------------------------------------------
# 4. Ollama — think field mapping.
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("Ollama think mapping (ollama_think_value)")
for mode in THINKING_MODES:
    default = ollama_think_value(mode, None)
    user = ollama_think_value(mode, "high" if mode == "medium" else default)
    print(f"  {mode:<6} default={default!r:<10} (medium user-mapped to high) => {user!r}")
print(
    "  -> user thinking_mapping reaches the 'think' field (no factory override);\n"
    "     the only hard-code is the fallback {low,medium,high,xhigh}->{low,medium,high,max}."
)

# ---------------------------------------------------------------------------
# 5. Codex / Copilot — inherit OpenAI-compatible reasoning handling (no override).
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("Codex / Copilot: inherit OpenAICompatibleChatProvider._apply_call_options\n"
      "  (openai.py:434-443): actual_reasoning_effort -> payload['reasoning_effort']/\n"
      "  payload['reasoning']['effort'], plus provider_options passthrough.\n"
      "  No factory-level or adapter-level effort hard-coding found.")
