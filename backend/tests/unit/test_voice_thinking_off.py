"""前台实时语音 LLM 恒为 off：请求字段解析的回归测试。

修复背景：前台实时回合跑的是 pipecat 的 ``OpenAILLMService``，它自己**没有**思考开关，
``Settings.extra`` 是唯一能落到请求体上的通道。旧代码只传了 model / system_instruction，
于是请求体里没有任何 thinking 字段，会话上的 ``thinking=off`` 完全失效——混合模型
``deepseek-flash`` 继续按厂商默认开着思考。真机实测同一请求体（无 thinking 字段）
会返回 reasoning_content 且首字晚 0.6–1.0s。

锁定规则（``policy.resolve_llm_thinking_off``）：
- DeepSeek 官方直连只说 ``thinking:{"type":"disabled"}``，不夹带 ``enable_thinking``；
- 其它 OpenAI 兼容方言（DashScope ``enable_thinking``、relay 的 ``off→null`` 兜底）
  产出各自的关闭字段；
- ``thinking_required`` 的模型、以及解析不出任何字段的情形一律返回"不可表达"，
  调用方据此拒绝启动这通电话（绝不允许前台偷偷带思考跑）。
"""
from __future__ import annotations

import pytest

from app.voice.policy import resolve_llm_thinking_off

DEEPSEEK_CAPS = {
    "provider_family": "openai_compatible",
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
    "thinking_required": False,
    "models": {
        "deepseek-flash": {
            "reasoning_efforts": ["low", "medium", "high", "xhigh"],
            "thinking_mapping": {
                "off": None,
                "low": "low",
                "medium": "medium",
                "high": "high",
                "xhigh": "xhigh",
            },
            "default_thinking_mode": "medium",
            "reasoning_parameter": "reasoning_effort",
            "thinking_required": False,
            "capability_source": "runtime_observation",
        }
    },
}

DASHSCOPE_CAPS = {
    "protocol_family": "dashscope",
    "provider_family": "qwen",
    "models": {
        "qwen3.8-max-preview": {
            "reasoning_efforts": ["low", "medium", "high", "xhigh"],
            "thinking_mapping": {
                "off": False,
                "low": True,
                "medium": True,
                "high": True,
                "xhigh": True,
            },
            "default_thinking_mode": "medium",
            "reasoning_parameter": "enable_thinking",
            "thinking_required": False,
        }
    },
}


class TestDeepSeekDialect:
    def test_official_host_gets_thinking_disabled_only(self) -> None:
        decision = resolve_llm_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://api.deepseek.com",
            model_id="deepseek-flash",
            capabilities=DEEPSEEK_CAPS,
        )
        # 只发 DeepSeek 方言字段：``off→null`` 的 reasoning_effort 画像会诱发通用
        # ``enable_thinking`` 兜底，DeepSeek 原生网关拒绝外来字段，必须摘掉。
        assert decision.fields == {"thinking": {"type": "disabled"}}
        assert decision.mechanism == "deepseek.thinking=disabled"
        assert decision.expressible

    def test_official_host_without_provider_type_still_deepseek(self) -> None:
        # 纯环境变量兜底（没有 Provider 行）时，只凭官方域名判定方言。
        decision = resolve_llm_thinking_off(
            provider_type=None,
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-chat",
            capabilities=None,
        )
        assert decision.fields == {"thinking": {"type": "disabled"}}


class TestOtherDialects:
    def test_dashscope_enable_thinking_switch(self) -> None:
        decision = resolve_llm_thinking_off(
            provider_type="qwen",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model_id="qwen3.8-max-preview",
            capabilities=DASHSCOPE_CAPS,
        )
        assert decision.fields == {"enable_thinking": False}
        assert decision.expressible

    def test_relay_gateway_uses_disable_fallback(self) -> None:
        # relay 式网关（非 DeepSeek 官方域名）：off 只能靠通用兜底字段表达。
        decision = resolve_llm_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="http://localhost:8080/v1",
            model_id="deepseek-flash",
            capabilities=DEEPSEEK_CAPS,
        )
        assert decision.fields == {"enable_thinking": False}
        assert decision.expressible


class TestStrictRefusal:
    def test_thinking_required_model_is_not_expressible(self) -> None:
        caps = {
            **DEEPSEEK_CAPS,
            "models": {
                "deepseek-reasoner": {
                    "reasoning_efforts": ["low", "medium", "high", "xhigh"],
                    "thinking_mapping": {"off": None, "low": "low"},
                    "default_thinking_mode": "medium",
                    "reasoning_parameter": "reasoning_effort",
                    "thinking_required": True,
                }
            },
        }
        decision = resolve_llm_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="http://localhost:8080/v1",
            model_id="deepseek-reasoner",
            capabilities=caps,
        )
        assert not decision.expressible
        assert "只支持思考模式" in decision.reason

    def test_missing_model_is_not_expressible(self) -> None:
        decision = resolve_llm_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="http://localhost:8080/v1",
            model_id="",
            capabilities=DEEPSEEK_CAPS,
        )
        assert not decision.expressible
        assert decision.fields == {}


class TestPipecatSettingsExtra:
    """`Settings.extra` 的合法形态：厂商字段必须裹在 ``extra_body`` 里。

    真机事故（2026-09-18 03:25）：把 ``thinking`` 摊在顶层后，pipecat 的
    ``client.chat.completions.create(**params)`` 直接抛
    ``TypeError: AsyncCompletions.create() got an unexpected keyword argument 'thinking'``，
    每一轮 LLM 都失败（45 次 ErrorFrame）→ 没有回答文本 → TTS 无音频可播 →
    界面报"语音播报已不可用，回答会以文字显示"。这条用 SDK 的真实签名把形态钉死。
    """

    def _decision(self):
        return resolve_llm_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://api.deepseek.com",
            model_id="deepseek-flash",
            capabilities=DEEPSEEK_CAPS,
        )

    def test_settings_extra_wraps_vendor_fields_in_extra_body(self) -> None:
        decision = self._decision()
        assert decision.settings_extra == {
            "extra_body": {"thinking": {"type": "disabled"}}
        }
        # 顶层形态是**禁止**的：那正是引发 TypeError 的写法。
        assert "thinking" not in decision.settings_extra

    def test_not_expressible_yields_empty_settings_extra(self) -> None:
        decision = resolve_llm_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="http://localhost:8080/v1",
            model_id="",
            capabilities=DEEPSEEK_CAPS,
        )
        assert decision.settings_extra == {}

    def test_pipecat_params_bind_to_the_real_sdk_signature(self) -> None:
        import inspect

        from openai import AsyncOpenAI
        from pipecat.services.openai.llm import OpenAILLMService

        decision = self._decision()
        service = OpenAILLMService(
            api_key="dummy",
            base_url="https://api.deepseek.com",
            settings=OpenAILLMService.Settings(
                model="deepseek-flash",
                extra=decision.settings_extra,
            ),
        )
        params = service.build_chat_completion_params(
            {"messages": [{"role": "user", "content": "你好"}]}
        )
        assert params["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "thinking" not in params

        # pipecat 是 **params 调 SDK：签名必须接受这组关键字。
        create = AsyncOpenAI(api_key="dummy").chat.completions.create
        inspect.signature(create).bind(**params)
        # 反例：摊在顶层会被同一处拦下（这就是真机上的那条 TypeError）。
        with pytest.raises(TypeError, match="unexpected keyword argument 'thinking'"):
            inspect.signature(create).bind(**{**params, "thinking": {"type": "disabled"}})


class TestClientFacingNotice:
    """表达不出"关闭"时按口径**照常服务**，代价由前端提醒条说出来。

    文案与 code 是跨端契约：前端 `voice-session-controller.tsx` 只对
    ``THINKING_OFF_UNAVAILABLE_NOTICE_CODE`` 这一个 code 打开提醒（其余 notice 是
    排障信息）。改名/改文案而不改前端，用户就再也看不到"为什么变慢了"。
    """

    def test_notice_text_and_code_are_pinned(self) -> None:
        from app.voice.embedded_bot import (
            VOICE_THINKING_OFF_UNAVAILABLE_NOTICE,
        )

        assert "{model}" in VOICE_THINKING_OFF_UNAVAILABLE_NOTICE
        assert "无法关闭思考" in VOICE_THINKING_OFF_UNAVAILABLE_NOTICE
        assert "延迟" in VOICE_THINKING_OFF_UNAVAILABLE_NOTICE
        assert (
            VOICE_THINKING_OFF_UNAVAILABLE_NOTICE.format(model="deepseek-flash")
            == "检测到模型 deepseek-flash 无法关闭思考，回复延迟会明显增加"
        )
