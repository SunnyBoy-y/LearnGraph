from __future__ import annotations

from typing import Any


USD_CNY_REFERENCE = 6.7704
CATALOG_AS_OF = "2026-07-25"


def _usd_from_cny(value: float) -> float:
    return round(value / USD_CNY_REFERENCE, 8)


def _entry(provider: str, model: str, currency: str, input_price: float,
           output_price: float, *, cached: float | None = None,
           cache_write: float | None = None, conditions: dict[str, Any] | None = None,
           source_url: str = "") -> dict[str, Any]:
    convert = (lambda value: value) if currency == "USD" else _usd_from_cny
    return {
        "catalog_id": f"{provider}:{model}:{len(PRICING_CATALOG) if 'PRICING_CATALOG' in globals() else 0}",
        "provider_key": provider,
        "model_id": model,
        "currency": currency,
        "native_input_per_million": input_price,
        "native_cached_input_per_million": cached,
        "native_cache_write_per_million": cache_write,
        "native_output_per_million": output_price,
        "input_usd_per_million": convert(input_price),
        "cached_input_usd_per_million": convert(cached) if cached is not None else None,
        "cache_write_usd_per_million": convert(cache_write) if cache_write is not None else None,
        "output_usd_per_million": convert(output_price),
        "conditions": conditions or {},
        "source_url": source_url,
        "as_of": CATALOG_AS_OF,
    }


PRICING_CATALOG: list[dict[str, Any]] = []


def _add(*args: Any, **kwargs: Any) -> None:
    item = _entry(*args, **kwargs)
    item["catalog_id"] = f"price-{len(PRICING_CATALOG) + 1:03d}"
    PRICING_CATALOG.append(item)


DS = "https://api-docs.deepseek.com/quick_start/pricing"
OPENAI = "https://developers.openai.com/api/docs/pricing"
GEMINI = "https://ai.google.dev/gemini-api/docs/pricing"
KIMI = "https://platform.kimi.com/docs/pricing/chat"
GLM = "https://docs.z.ai/guides/overview/pricing"
MIMO = "https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go"
MINIMAX = "https://platform.minimax.io/docs/guides/pricing-paygo"
QWEN = "https://help.aliyun.com/zh/model-studio/model-pricing"

_add("deepseek", "deepseek-v4-flash", "USD", .14, .28, cached=.0028,
     conditions={"context_window_tokens": 1_000_000, "max_output_tokens": 384_000}, source_url=DS)
_add("deepseek", "deepseek-v4-pro", "USD", .435, .87, cached=.003625,
     conditions={"context_window_tokens": 1_000_000, "max_output_tokens": 384_000}, source_url=DS)

for model, short, long, cache, out, long_out, write in [
    ("gpt-5.6-sol", 5, 10, .5, 30, 45, None),
    ("gpt-5.6-terra", 2.5, 5, .25, 15, 22.5, None),
    ("gpt-5.6-luna", 1, 2, .1, 6, 9, None),
    ("gpt-5.5", 5, 10, .5, 30, 45, None),
    ("gpt-5.4", 2.5, 5, .25, 15, 22.5, None),
]:
    _add("openai", model, "USD", short, out, cached=cache, cache_write=write,
         conditions={"context_tier": "short", "max_input_tokens": 272000}, source_url=OPENAI)
    _add("openai", model, "USD", long, long_out, cached=cache * 2,
         cache_write=write * 2 if write else None,
         conditions={"context_tier": "long", "min_input_tokens": 272001}, source_url=OPENAI)
_add("openai", "gpt-5.5-pro", "USD", 30, 180, conditions={"context_tier": "short", "max_input_tokens": 272000}, source_url=OPENAI)
_add("openai", "gpt-5.5-pro", "USD", 60, 270, conditions={"context_tier": "long", "min_input_tokens": 272001}, source_url=OPENAI)
_add("openai", "gpt-5.4-mini", "USD", .75, 4.5, cached=.075, source_url=OPENAI)
_add("openai", "gpt-5.4-nano", "USD", .2, 1.25, cached=.02, source_url=OPENAI)
_add("openai", "gpt-5.4-pro", "USD", 30, 180, conditions={"context_tier": "short", "max_input_tokens": 272000}, source_url=OPENAI)
_add("openai", "gpt-5.4-pro", "USD", 60, 270, conditions={"context_tier": "long", "min_input_tokens": 272001}, source_url=OPENAI)

for model, inp, cached, out, conditions in [
    ("gemini-3.1-pro-preview", 2, .2, 12, {"max_input_tokens": 200_000}),
    ("gemini-3.1-pro-preview", 4, .4, 18, {"min_input_tokens": 200_001}),
    ("gemini-3.1-flash-lite", .25, .025, 1.5, {}),
    ("gemini-3-flash-preview", .5, .05, 3, {}),
]:
    _add("google", model, "USD", inp, out, cached=cached,
         conditions={"context_window_tokens": 1_000_000, **conditions},
         source_url=GEMINI)

for model, cached, inp, out in [("kimi-k2.7-code", .19, .95, 4), ("kimi-k2.6", .16, .95, 4), ("kimi-k2.5", .10, .60, 3)]:
    _add("kimi", model, "USD", inp, out, cached=cached, source_url=KIMI)
for model, cached, inp, out in [("GLM-5.2", .26, 1.4, 4.4), ("GLM-5.1", .26, 1.4, 4.4), ("GLM-5", .2, 1, 3.2), ("GLM-5-Turbo", .24, 1.2, 4), ("GLM-4.7", .11, .6, 2.2), ("GLM-4.6", .11, .6, 2.2), ("GLM-4.5", .11, .6, 2.2), ("GLM-4.7-FlashX", .01, .07, .4), ("GLM-4.5-Air", .03, .2, 1.1), ("GLM-5V-Turbo", .24, 1.2, 4), ("GLM-4.6V", .05, .3, .9)]:
    _add("zai", model, "USD", inp, out, cached=cached, source_url=GLM)
_add("xiaomi_mimo", "mimo-v2.5", "CNY", 1, 2, cached=.02, source_url=MIMO)
_add("xiaomi_mimo", "mimo-v2.5-pro", "CNY", 3, 6, cached=.025, source_url=MIMO)
for model, inp, out, cached, cond in [
    ("MiniMax-M3", .3, 1.2, .06, {"max_input_tokens": 512000}),
    ("MiniMax-M3", .6, 2.4, .12, {"min_input_tokens": 512001}),
    ("MiniMax-M2.7", .3, 1.2, .06, {}),
    ("MiniMax-M2.7-highspeed", .6, 2.4, .06, {}),
]:
    _add("minimax", model, "USD", inp, out, cached=cached, cache_write=.375 if "M2.7" in model else None, conditions=cond, source_url=MINIMAX)

for model, inp, out, conditions in [
    ("qwen3.7-max", 6, 18, {"promotion": "50_percent", "max_input_tokens": 1000000}),
    ("qwen3-max", 2.5, 10, {"max_input_tokens": 32000}), ("qwen3-max", 4, 16, {"min_input_tokens": 32001, "max_input_tokens": 128000}), ("qwen3-max", 7, 28, {"min_input_tokens": 128001, "max_input_tokens": 256000}),
    ("qwen3.7-plus", 1.6, 6.4, {"max_input_tokens": 256000, "promotion": "80_percent"}), ("qwen3.7-plus", 4.8, 19.2, {"min_input_tokens": 256001, "max_input_tokens": 1000000, "promotion": "80_percent"}),
    ("qwen3.6-plus", 2, 12, {"max_input_tokens": 256000}), ("qwen3.6-plus", 8, 48, {"min_input_tokens": 256001}),
    ("qwen3.5-plus", .8, 4.8, {"max_input_tokens": 128000}), ("qwen3.5-plus", 2, 12, {"min_input_tokens": 128001, "max_input_tokens": 256000}), ("qwen3.5-plus", 4, 24, {"min_input_tokens": 256001}),
    ("qwen3.6-flash", 1.2, 7.2, {"max_input_tokens": 256000}), ("qwen3.6-flash", 4.8, 28.8, {"min_input_tokens": 256001}),
    ("qwen3.5-flash", .2, 2, {"max_input_tokens": 128000}), ("qwen3.5-flash", .8, 8, {"min_input_tokens": 128001, "max_input_tokens": 256000}), ("qwen3.5-flash", 1.2, 12, {"min_input_tokens": 256001}),
    ("qwen-flash", .15, 1.5, {"max_input_tokens": 128000}), ("qwen-flash", .6, 6, {"min_input_tokens": 128001, "max_input_tokens": 256000}), ("qwen-flash", 1.2, 12, {"min_input_tokens": 256001}),
    ("qwen-turbo", .3, .6, {"mode": "non_thinking"}), ("qwen-turbo", .3, 3, {"mode": "thinking"}),
    ("qwen-long", .5, 2, {}), ("qwen-vl-max", 1.6, 4, {}), ("qwen-vl-plus", .8, 2, {}), ("qwen-vl-ocr", .3, .5, {}),
    ("qwen3-vl-plus", 1, 10, {"max_input_tokens": 32000}), ("qwen3-vl-plus", 1.5, 15, {"min_input_tokens": 32001, "max_input_tokens": 128000}), ("qwen3-vl-plus", 3, 30, {"min_input_tokens": 128001}),
    ("qwen3-vl-flash", .15, 1.5, {"max_input_tokens": 32000}), ("qwen3-vl-flash", .3, 3, {"min_input_tokens": 32001, "max_input_tokens": 128000}), ("qwen3-vl-flash", .6, 6, {"min_input_tokens": 128001}),
    ("qwen3-coder-plus", 4, 16, {"max_input_tokens": 32000}), ("qwen3-coder-plus", 6, 24, {"min_input_tokens": 32001, "max_input_tokens": 128000}), ("qwen3-coder-plus", 10, 40, {"min_input_tokens": 128001, "max_input_tokens": 256000}), ("qwen3-coder-plus", 20, 200, {"min_input_tokens": 256001}),
    ("qwen3-coder-flash", 1, 4, {"max_input_tokens": 32000}), ("qwen3-coder-flash", 1.5, 6, {"min_input_tokens": 32001, "max_input_tokens": 128000}), ("qwen3-coder-flash", 2.5, 10, {"min_input_tokens": 128001, "max_input_tokens": 256000}), ("qwen3-coder-flash", 5, 25, {"min_input_tokens": 256001}),
    ("qwen-math-plus", 4, 12, {}), ("qwen-math-turbo", 2, 6, {}), ("qwen-mt-plus", 1.8, 5.4, {}), ("qwen-mt-flash", .7, 1.95, {}), ("qwen-mt-turbo", .7, 1.95, {}), ("qwen-mt-lite", .6, 1.6, {}),
]:
    _add("qwen", model, "CNY", inp, out, conditions=conditions, source_url=QWEN)

for model, text_input, audio_input, text_output, audio_output in [
    ("qwen3.5-omni-plus", 7, 53, 40, 213),
    ("qwen3.5-omni-flash", 2.2, 18, 13.3, 72),
]:
    _add("qwen", model, "CNY", text_input, text_output, conditions={"modality": "text_image_video"}, source_url=QWEN)
    _add("qwen", model, "CNY", audio_input, audio_output, conditions={"modality": "audio"}, source_url=QWEN)


def get_catalog_entry(catalog_id: str) -> dict[str, Any] | None:
    return next((item for item in PRICING_CATALOG if item["catalog_id"] == catalog_id), None)
