"""语音侧的策略小工具：思考档位裁剪、事件序号、以及"关闭思考"解析的入口。

本模块**不再**自己判定厂商方言。历史事故见 ``app/providers/dialects.py`` 的模块注释：
这里曾有一份"认 ``/v1``"的 predicate，却挂在 ``not provider_type`` 后面——provider 行
一旦存在（永远存在），那个分支永不执行，于是 DeepSeek 官方源被当成通用网关，
发出去的关闭字段是 ``enable_thinking=False``（官方不认），思考照旧，首字慢 2.6 s
且**静默**。现在方言判定与厂商字段只有一份，在 ``providers/dialects.py`` /
``providers/thinking_policy.py``。
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from app.providers.thinking_policy import (
    ThinkingOff,
    resolve_thinking_off,
)

ThinkingMode = Literal["off", "low", "medium", "high", "xhigh"]
_ORDER: tuple[str, ...] = ("off", "low", "medium", "high", "xhigh")

#: 兼容旧名（``LlmThinkingOff.settings_extra`` 的文档与调用点仍按这个名字引用）。
LlmThinkingOff = ThinkingOff


def clip_thinking_mode(requested: str | None, maximum: str | None) -> ThinkingMode:
    """Clamp a requested mode to the voice session's user-selected ceiling."""
    req = requested if requested in _ORDER else "off"
    cap = maximum if maximum in _ORDER else "high"
    return _ORDER[min(_ORDER.index(req), _ORDER.index(cap))]  # type: ignore[return-value]


def next_event_seq(current: int) -> int:
    return max(0, int(current)) + 1


def resolve_llm_thinking_off(
    *,
    provider_type: str | None,
    base_url: str | None,
    model_id: str | None,
    capabilities: Mapping[str, Any] | None,
) -> ThinkingOff:
    """前台实时语音 LLM 的"关闭思考"字段（转调共享解析）。

    薄封装的存在只为了保住语音侧的调用点/测试名；**判定逻辑一律不在本模块**。
    """

    return resolve_thinking_off(
        provider_type=provider_type,
        base_url=base_url,
        model_id=model_id,
        capabilities=capabilities,
    )
