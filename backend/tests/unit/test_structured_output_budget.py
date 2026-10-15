"""结构化生成必须带上显式输出上限，否则长输出会被上游默认值截断。

试卷、教材这类结构化输出很长（每道题都带 prompt/options/答案/rubric/explanation），
而请求里不写上限时，多个网关的默认值是 4096 tokens：JSON 被截断 → 结构性校验失败 →
无论重试多少次都过不去。这里钉住三种"加/不加"的判定，避免以后又把上限去掉。
"""

from __future__ import annotations

import pytest

from app.providers.model_options import ModelCallOptions
from app.providers.remote.openai import OpenAICompatibleChatProvider, OpenAIResponsesProvider


def provider(cls=OpenAICompatibleChatProvider, **overrides):
    kwargs = {
        "provider_id": "p1",
        "model_id": "m1",
        "base_url": "https://example.invalid/v1",
        "api_key": "secret",
        "max_output_tokens": 64_000,
        "capabilities": {},
    }
    kwargs.update(overrides)
    return cls(**kwargs)


def test_chat_structured_call_asks_for_the_full_output_budget():
    payload = provider()._apply_output_budget({"model": "m1", "messages": []}, responses=False)

    assert payload["max_tokens"] == 64_000


def test_responses_structured_call_uses_the_responses_field_name():
    payload = provider(OpenAIResponsesProvider)._apply_output_budget({"model": "m1", "input": "x"}, responses=True)

    assert payload["max_output_tokens"] == 64_000
    assert "max_tokens" not in payload


@pytest.mark.parametrize("explicit", ["max_tokens", "max_completion_tokens", "max_output_tokens"])
def test_a_caller_chosen_cap_is_never_overwritten(explicit):
    options = ModelCallOptions(
        thinking_mode="off", actual_reasoning_effort=None, reasoning_parameter=None,
        search_route="disabled", native_web_search=False, provider_options={explicit: 512},
    )
    payload = provider(call_options=options)._apply_output_budget({"model": "m1"}, responses=False)

    assert explicit not in payload or payload[explicit] == 512
    assert payload.get("max_tokens", 512) == 512


@pytest.mark.parametrize(
    ("base_url", "expected_key"),
    [
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "max_tokens"),
        ("https://api.deepseek.com/v1", "max_tokens"),
        ("https://api.openai.com/v1", "max_completion_tokens"),
    ],
)
def test_chat_cap_field_follows_the_endpoint_contract(base_url, expected_key):
    """DashScope/Qwen 与 DeepSeek 收 max_tokens；原生 OpenAI 只认 max_completion_tokens。"""
    payload = provider(base_url=base_url)._apply_output_budget({"model": "m1", "messages": []}, responses=False)

    assert payload[expected_key] == 64_000
    other = {"max_tokens", "max_completion_tokens"} - {expected_key}
    assert not (other & set(payload))


def test_thinking_required_gateway_still_gets_a_cap():
    """回归：DashScope 纯推理模型思考 token 与输出共用预算，最需要显式上限。"""
    payload = provider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        capabilities={"thinking_required": True},
    )._apply_output_budget({"model": "m1", "messages": []}, responses=False)

    assert payload["max_tokens"] == 64_000


def test_unknown_output_budget_is_left_alone():
    payload = provider(max_output_tokens=0)._apply_output_budget({"model": "m1"}, responses=False)

    assert "max_tokens" not in payload
