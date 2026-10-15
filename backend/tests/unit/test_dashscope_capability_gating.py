"""DashScope-private capability gating regression tests.

修复背景：第三方 OpenAI 兼容中转（例如 sub2api 式网关）会拒绝 DashScope
专有请求字段（``enable_search`` 等），返回 HTTP 400 ``UNKNOWN_FIELD``。
能力目录按模型名匹配会把 DashScope 托管能力（hosted_web_search、
preserve_thinking、Responses 工具、thinking_budget 等）错误地授给普通
中转。这些测试锁定"能力与供应商协议族绑定"的门控行为：
- 能力目录生成（qwen_model_defaults）按 dashscope_hosted 剥离私有能力；
- 快照归一化（catalog_capability_snapshot）透传门控；
- 运行时合并（_model_capabilities）按持久化的 protocol_family 兜底门控；
- 线路层（QwenChatProvider）只对 DashScope 端点发送 enable_search。
"""
from __future__ import annotations

from app.providers.model_catalog import unified_model_defaults
from app.providers.model_options import (
    catalog_capability_snapshot,
    model_capabilities_for_model,
)
from app.providers.qwen_catalog import (
    PROTOCOL_FAMILY_DASHSCOPE,
    PROTOCOL_FAMILY_OPENAI_COMPATIBLE,
    protocol_family_for,
    qwen_model_defaults,
)
from app.providers.remote.openai import QwenChatProvider

RELAY_URL = "https://tokenrhythm.studio/v1"
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MAAS_URL = "https://demo.cn-beijing.maas.aliyuncs.com/api/v1"


def test_protocol_family_origin_detection():
    assert protocol_family_for("openai_compatible_chat", RELAY_URL) == (
        PROTOCOL_FAMILY_OPENAI_COMPATIBLE
    )
    assert protocol_family_for("openai_compatible_chat", DASHSCOPE_URL) == (
        PROTOCOL_FAMILY_DASHSCOPE
    )
    assert protocol_family_for("qwen", MAAS_URL) == PROTOCOL_FAMILY_DASHSCOPE
    assert protocol_family_for("openai_compatible_chat", None) == (
        PROTOCOL_FAMILY_OPENAI_COMPATIBLE
    )


def test_qwen_defaults_strip_dashscope_private_claims():
    gated = qwen_model_defaults("qwen3.8-max", dashscope_hosted=False)
    assert gated.get("hosted_web_search") is not True
    assert gated.get("hosted_web_fetch") is not True
    assert gated.get("hosted_image_search") is not True
    assert gated.get("preserve_thinking") is not True
    assert gated.get("chat_search_strategy") is None
    assert gated.get("native_tool_protocol") == "chat_completions"
    assert "session" not in (gated.get("cache_modes") or [])

    full = qwen_model_defaults("qwen3.8-max", dashscope_hosted=True)
    assert full.get("hosted_web_search") is True
    assert full.get("native_tool_protocol") == "responses"


def test_qwen_defaults_thinking_budget_fallback():
    gated = qwen_model_defaults("qwen3.7-max", dashscope_hosted=False)
    assert gated.get("reasoning_parameter") == "enable_thinking"
    assert gated.get("thinking_budget_max") is None
    assert gated.get("thinking_mapping") == {
        "off": False,
        "low": True,
        "medium": True,
        "high": True,
        "xhigh": True,
    }

    full = qwen_model_defaults("qwen3.7-max", dashscope_hosted=True)
    assert full.get("reasoning_parameter") == "thinking_budget"
    assert full.get("thinking_budget_max") is not None


def test_catalog_snapshot_gates_by_dashscope_hosted():
    gated = catalog_capability_snapshot(
        "qwen3.8-max",
        provider_type="openai_compatible_chat",
        dashscope_hosted=False,
    )
    assert gated.get("hosted_web_search") is not True
    assert gated.get("default_search_route") == "auto"
    assert gated.get("native_tool_protocol") == "chat_completions"


def test_unified_defaults_threads_dashscope_hosted():
    gated = unified_model_defaults(
        "qwen3.8-max",
        provider_type="openai_compatible_chat",
        dashscope_hosted=False,
    )
    assert gated.get("hosted_web_search") is not True
    full = unified_model_defaults(
        "qwen3.8-max",
        provider_type="openai_compatible_chat",
        dashscope_hosted=True,
    )
    assert full.get("hosted_web_search") is True


def test_runtime_merge_gates_by_protocol_family_without_snapshot():
    # 模拟一个普通中转的 provider 能力：有 models 列表但某个模型没有
    # per-model 快照。运行时合并必须按 protocol_family 兜底，不能把目录里
    # DashScope 私有能力漏进来。
    relay_caps = {
        "protocol_family": PROTOCOL_FAMILY_OPENAI_COMPATIBLE,
        "provider_family": "openai_compatible_chat",
        "models": {"other-model": {"capability_source": "official_catalog"}},
    }
    merged = model_capabilities_for_model(relay_caps, "qwen3.8-max")
    assert merged.get("hosted_web_search") is not True
    assert merged.get("native_tool_protocol") == "chat_completions"

    dashscope_caps = {
        "protocol_family": PROTOCOL_FAMILY_DASHSCOPE,
        "provider_family": "openai_compatible_chat",
        "models": {},
    }
    merged = model_capabilities_for_model(dashscope_caps, "qwen3.8-max")
    assert merged.get("hosted_web_search") is True

    # 缺省（旧数据行）按安全侧处理：不给私有能力。
    legacy_caps = {"provider_family": "openai_compatible_chat"}
    merged = model_capabilities_for_model(legacy_caps, "qwen3.8-max")
    assert merged.get("hosted_web_search") is not True


def _qwen_provider(base_url: str, native_web_search: bool = True):
    from app.providers.model_options import ModelCallOptions

    return QwenChatProvider(
        provider_id="p1",
        model_id="qwen3.8-max",
        base_url=base_url,
        api_key="k",
        call_options=ModelCallOptions(
            thinking_mode="off",
            actual_reasoning_effort=None,
            reasoning_parameter="enable_thinking",
            search_route="model_native" if native_web_search else "disabled",
            native_web_search=native_web_search,
            provider_options={"enable_thinking": False},
        ),
        capabilities={},
    )


def test_wire_never_emits_enable_search_to_relay():
    payload = _qwen_provider(RELAY_URL)._apply_call_options(
        {"model": "qwen3.8-max", "messages": [], "stream": True},
        responses=False,
    )
    assert "enable_search" not in payload
    assert "search_options" not in payload
    # enable_thinking 是普通中转普遍接受的 Qwen 约定，保留。
    assert payload.get("enable_thinking") is False


def test_wire_emits_enable_search_on_dashscope():
    payload = _qwen_provider(DASHSCOPE_URL)._apply_call_options(
        {"model": "qwen3.8-max", "messages": [], "stream": True},
        responses=False,
    )
    assert payload.get("enable_search") is True
    assert payload.get("search_options") == {
        "search_strategy": "turbo",
        "enable_source": True,
        # 角标标注：仅 DashScope 协议支持；turbo/max 策略可下发。
        "enable_citation": True,
        "citation_format": "[ref_<number>]",
    }


def test_wire_agent_strategy_omits_citation():
    # agent/agent_max 策略（多模态模型）按官方文档只支持返回搜索来源，
    # 其他联网搜索功能（含角标）不可用，因此不下发 enable_citation。
    provider = _qwen_provider(DASHSCOPE_URL)
    provider.capabilities = {"chat_search_strategy": "agent"}
    payload = provider._apply_call_options(
        {"model": "qwen3.8-max", "messages": [], "stream": True},
        responses=False,
    )
    assert payload.get("search_options") == {
        "search_strategy": "agent",
        "enable_source": True,
    }
