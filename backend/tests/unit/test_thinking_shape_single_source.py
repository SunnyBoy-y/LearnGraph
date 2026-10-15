"""「关闭思考」形状的单一真源 + 意图/实测不变量 的回归测试。

修复背景（2026-09-18 真机事故）：同一条 provider 行
（``openai_compatible_chat`` + ``https://api.deepseek.com/v1`` + 模型 ``deepseek-flash``）

* 纯文字链路**关得掉**思考（那条行的 URL 恰好不带 ``/v1``，命中严格判定 → 原生适配器）；
* 全双工语音链路**关不掉**：pipecat 侧自己拼字段，而"认 ``/v1``"的那份 predicate 挂在
  ``not provider_type`` 后面永不执行 → 落到通用兜底 ``enable_thinking=False``
  （DeepSeek 官方不认）→ ``TTFAT: 2.773s (2.580s thinking)`` + ``reasoning tokens: 398``，
  占端到端 3638 ms 的 71%，而且**静默**。

本文件锁定四类不变量：

1. 线上那一格必须走厂商原生字段（曾经的失败项）；
2. 两条链路（factory 的适配器选择 / 语音的字段解析）**同结论**；
3. 信任边界不许被放宽：第三方网关上的 DeepSeek 权重仍然说通用方言；
4. 意图 = off 却观察到思考 ⇒ 违约（L3），且"厂商偷偷改行为"这类失效必须能被发现。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.providers.dialects import (
    THINKING_OFF_TOLERANCE_MS,
    THINKING_OFF_OBSERVED_KEY,
    provider_dialect,
    thinking_off_verdict,
    uses_deepseek_native_adapter,
)
from app.providers.thinking_policy import resolve_thinking_off

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: 线上 ``provider_configs.capabilities`` 的裁剪版（DeepSeek（语音测试）那一行）。
DEEPSEEK_CAPS = {
    "brand_id": "deepseek",
    "model_family": "deepseek",
    "protocol_family": "openai_compatible",
    "reasoning_efforts": ["low", "medium", "high", "xhigh"],
    "thinking_mapping": {
        "off": None,
        "low": "high",
        "medium": "high",
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
        }
    },
}

NATIVE_DEEPSEEK_FIELDS = {"thinking": {"type": "disabled"}}

#: 覆盖"同一格必须同结论"的配置矩阵：(provider_type, base_url, capabilities, 期望方言)。
PRODUCTION_MATRIX = (
    ("openai_compatible_chat", "https://api.deepseek.com/v1", DEEPSEEK_CAPS, "deepseek"),
    ("openai_compatible_chat", "https://api.deepseek.com", DEEPSEEK_CAPS, "deepseek"),
    ("deepseek_chat", "https://api.deepseek.com", {}, "deepseek"),
    (None, "https://api.deepseek.com/v1", {}, "deepseek"),
    ("openai_compatible_chat", "http://localhost:8080/v1", {}, "generic"),
    ("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", {}, "dashscope"),
    # 第三方网关上的 DeepSeek 权重：请求体必须说网关自己的方言（否则被判 400）。
    ("openai_compatible_chat", "https://my-relay.example.com/v1", DEEPSEEK_CAPS, "generic"),
)


class TestProductionRegression:
    """线上那一格：官方域名的 ``/v1`` 写法必须被认成 DeepSeek 方言。"""

    def test_openai_compatible_row_with_v1_uses_native_thinking_field(self) -> None:
        result = resolve_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-flash",
            capabilities=DEEPSEEK_CAPS,
        )
        assert result.fields == NATIVE_DEEPSEEK_FIELDS, (
            "官方域名的 /v1 写法必须走厂商原生 thinking 字段；"
            "落到 enable_thinking 兜底就是本次事故"
        )
        assert result.mechanism == "deepseek.thinking=disabled"
        assert result.source == "vendor"
        assert result.expressible is True
        assert result.suspect is False

    def test_settings_extra_wraps_fields_in_extra_body(self) -> None:
        result = resolve_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-flash",
            capabilities=DEEPSEEK_CAPS,
        )
        assert result.settings_extra == {"extra_body": NATIVE_DEEPSEEK_FIELDS}

    def test_declared_thinking_off_beats_builtin_registry(self) -> None:
        """厂商改了字段只需改数据：快照声明优先于内置注册表。"""

        declared = {"custom_switch": {"enabled": False}}
        caps = {**DEEPSEEK_CAPS, "thinking_off": {"mode": "fields", "fields": declared}}
        result = resolve_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-flash",
            capabilities=caps,
        )
        assert result.fields == declared
        assert result.source == "declaration"


class TestTwoSurfacesAgree:
    """factory 的适配器选择与语音的字段解析必须同结论（本次分叉的根因）。"""

    @pytest.mark.parametrize("provider_type,base_url,caps,expected", PRODUCTION_MATRIX)
    def test_matrix(self, provider_type, base_url, caps, expected) -> None:
        assert provider_dialect(provider_type, base_url, caps) == expected

    @pytest.mark.parametrize("provider_type,base_url,caps,expected", PRODUCTION_MATRIX)
    def test_native_adapter_iff_native_field(self, provider_type, base_url, caps, expected) -> None:
        native_adapter = uses_deepseek_native_adapter(provider_type, base_url, caps)
        result = resolve_thinking_off(
            provider_type=provider_type,
            base_url=base_url,
            model_id="deepseek-flash",
            capabilities=caps,
        )
        native_field = result.fields == NATIVE_DEEPSEEK_FIELDS
        assert native_adapter == native_field, (
            "文字链路走原生适配器、语音链路却发通用方言字段（或反过来）——"
            "正是本次事故的形状"
        )
        assert native_adapter is (expected == "deepseek")


class TestTrustBoundary:
    """只信官方域名这条边界不许被"声明优先"放宽。"""

    def test_third_party_gateway_with_deepseek_brand_stays_generic(self) -> None:
        assert (
            provider_dialect(
                "openai_compatible_chat", "https://my-relay.example.com/v1", DEEPSEEK_CAPS
            )
            == "generic"
        )
        result = resolve_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://my-relay.example.com/v1",
            model_id="deepseek-flash",
            capabilities=DEEPSEEK_CAPS,
        )
        assert "thinking" not in result.fields

    def test_official_host_with_unknown_path_uses_declared_brand(self) -> None:
        """官方主机上的新路径：靠声明的 brand 兜底，不必再改代码。"""

        assert (
            provider_dialect(
                "openai_compatible_chat", "https://api.deepseek.com/openai/v1", DEEPSEEK_CAPS
            )
            == "deepseek"
        )
        assert (
            provider_dialect(
                "openai_compatible_chat", "https://api.deepseek.com/openai/v1", {}
            )
            == "generic"
        )

    def test_voice_path_has_no_second_url_predicate(self) -> None:
        """语音侧不许再自己解析 URL 判方言（死分支就是这么来的）。"""

        source = (APP_ROOT / "voice" / "policy.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        assert "urlsplit" not in code
        assert "is_official_deepseek" not in code


class TestThinkingOffVerdict:
    """L3 不变量：意图 = off，实测却观察到思考 ⇒ 违约。"""

    def test_reasoning_tokens_violate(self) -> None:
        verdict = thinking_off_verdict(intent_off=True, reasoning_tokens=398)
        assert verdict is not None and verdict.violated is True
        assert "398" in verdict.detail

    def test_thinking_time_beyond_tolerance_violates(self) -> None:
        verdict = thinking_off_verdict(intent_off=True, thinking_time_ms=2580.0)
        assert verdict is not None and verdict.violated is True

    def test_small_thinking_time_is_noise(self) -> None:
        verdict = thinking_off_verdict(
            intent_off=True, thinking_time_ms=THINKING_OFF_TOLERANCE_MS - 1
        )
        assert verdict is not None and verdict.violated is False

    def test_no_intent_means_nothing_to_judge(self) -> None:
        assert thinking_off_verdict(intent_off=False, reasoning_tokens=398) is None

    def test_observed_failure_marks_mechanism_suspect(self) -> None:
        """L2：实测证明关不掉之后，下一次解析必须把这套机制标成可疑。"""

        caps = {
            **DEEPSEEK_CAPS,
            THINKING_OFF_OBSERVED_KEY: {
                "verified": False,
                "mechanism": "deepseek.thinking=disabled",
                "model": "deepseek-flash",
                "reasoning_tokens": 398,
                "thinking_time_ms": 2580.0,
                "detail": "reasoning_tokens=398",
            },
        }
        result = resolve_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-flash",
            capabilities=caps,
        )
        assert result.fields == NATIVE_DEEPSEEK_FIELDS
        assert result.suspect is True
        assert "未生效" in result.reason

    def test_stale_observation_for_other_mechanism_is_ignored(self) -> None:
        caps = {
            **DEEPSEEK_CAPS,
            THINKING_OFF_OBSERVED_KEY: {
                "verified": False,
                "mechanism": "enable_thinking=False",
                "model": "deepseek-flash",
            },
        }
        result = resolve_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-flash",
            capabilities=caps,
        )
        assert result.suspect is False

    def test_verified_observation_keeps_mechanism_clean(self) -> None:
        caps = {
            **DEEPSEEK_CAPS,
            THINKING_OFF_OBSERVED_KEY: {
                "verified": True,
                "mechanism": "deepseek.thinking=disabled",
                "model": "deepseek-flash",
            },
        }
        result = resolve_thinking_off(
            provider_type="openai_compatible_chat",
            base_url="https://api.deepseek.com/v1",
            model_id="deepseek-flash",
            capabilities=caps,
        )
        assert result.suspect is False


class TestVoiceSurfaceWiring:
    """L3 的语音接线：pipecat 的两条证据必须真的能触发上报（每会话一次）。"""

    def test_ttfat_and_usage_trigger_one_report(self) -> None:
        import asyncio

        from pipecat.frames.frames import MetricsFrame
        from pipecat.metrics.metrics import (
            LLMUsageMetricsData,
            LLMTokenUsage,
            TTFATMetricsData,
        )
        from pipecat.processors.frame_processor import FrameDirection

        from app.voice.embedded_bot import LatencyPercentileProcessor

        seen: list[object] = []

        async def report(verdict: object) -> None:
            seen.append(verdict)

        processor = LatencyPercentileProcessor(
            thinking_off_intent=True, on_thinking_off_verdict=report
        )
        idle = LatencyPercentileProcessor(
            thinking_off_intent=False, on_thinking_off_verdict=report
        )
        ttfat = TTFATMetricsData(
            processor="OpenAILLMService#0",
            model="deepseek-flash",
            ttfat=2.773,
            ttfb=0.193,
            thinking_time=2.58,
        )
        usage = LLMUsageMetricsData(
            processor="OpenAILLMService#0",
            model="deepseek-flash",
            value=LLMTokenUsage(
                prompt_tokens=5274,
                completion_tokens=590,
                total_tokens=5864,
                reasoning_tokens=398,
            ),
        )

        async def feed() -> None:
            await processor.process_frame(MetricsFrame(data=[ttfat]), FrameDirection.DOWNSTREAM)
            await processor.process_frame(MetricsFrame(data=[usage]), FrameDirection.DOWNSTREAM)
            await idle.process_frame(MetricsFrame(data=[usage]), FrameDirection.DOWNSTREAM)

        asyncio.run(feed())
        assert len(seen) == 1, "违规每会话只报一次；会话没要求 off 时一次都不报"
        assert getattr(seen[0], "violated") is True
