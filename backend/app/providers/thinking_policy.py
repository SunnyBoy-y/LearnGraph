"""意图「关闭思考」→ 厂商请求字段的**唯一**解析入口。

``dialects`` 负责"说哪种方言 / 关闭用哪些字段"，本模块负责把意图、能力快照、
实测结论合成为一次请求要带的字段，并给出**可判定的诚实答案**：能表达、靠什么表达、
以及"识别出厂商源却只能靠通用兜底"这种可疑状态。

历史（真机事故）：同一件事——"DeepSeek 怎么表达关闭"——在仓库里曾硬编码三份
（``providers/factory`` 改写能力表、``remote/deepseek`` 适配器拼 payload、
``voice/policy`` 又拼一遍），叠加两份宽严相反的 URL 判定，最终在
``openai_compatible_chat`` + ``/v1`` 这一格上**静默失效**。现在：

* 方言只有一处判定（``dialects.provider_dialect``）；
* 厂商 off 字段只有一份注册表（``dialects.VENDOR_THINKING_OFF_FIELDS``），
  可被能力快照的 ``thinking_off`` 声明覆盖；
* 通用兜底仍走 ``model_options.resolve_model_call_options``（批量调用同源）；
* 真机实测结论回写进快照后，本模块优先采用（见 ``dialects.thinking_off_observation``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.providers.dialects import (
    VENDOR_THINKING_OFF_FIELDS,
    observation_marks_ineffective,
    provider_dialect,
    thinking_off_declaration,
    thinking_off_observation,
)
from app.providers.model_options import (
    ModelCapabilityError,
    model_capabilities_for_model,
    resolve_model_call_options,
)


@dataclass(frozen=True, slots=True)
class ThinkingOff:
    """一次"关闭思考"请求要带的字段，以及这个结论的可信度。"""

    fields: dict[str, Any]
    mechanism: str
    reason: str = ""
    #: 结论从哪来：``declaration``（能力快照声明）/ ``vendor``（厂商注册表）/
    #: ``fallback``（通用兜底）/ ``none``（表达不出）。
    source: str = "none"
    #: 解析时认定的方言（排障用）。
    dialect: str = "generic"
    #: **可疑**：认出了厂商源，却拿不到该厂商原生的关闭字段（只能靠通用兜底），
    #: 或者真机实测已经证明当前机制关不掉。调用方据此提醒用户"首字会变长"。
    suspect: bool = False

    @property
    def expressible(self) -> bool:
        return bool(self.fields)

    @property
    def settings_extra(self) -> dict[str, Any]:
        """pipecat ``Settings.extra`` 的**唯一正确形态**：字段必须裹进 ``extra_body``。

        pipecat 是 `client.chat.completions.create(**params)`（``base_llm.py:339/348``），
        而 openai SDK 的 ``create`` 是显式签名、不接受未知关键字。把厂商字段摊在顶层
        （``extra={"thinking": {...}}``）会让每一轮对话直接抛
        ``TypeError: AsyncCompletions.create() got an unexpected keyword argument
        'thinking'``——**真机踩过**：LLM 全轮次 ErrorFrame，没有回答文本，TTS 因此
        "没有音频可播"，界面报"语音播报已不可用，回答会以文字显示"。

        ``extra_body`` 是 SDK 文档里的正经通道：它原样并进请求 JSON，既不被签名校验拦，
        也不会被当成未知参数丢掉。
        """

        if not self.fields:
            return {}
        return {"extra_body": dict(self.fields)}


def _mechanism_for(dialect: str) -> str:
    if dialect == "deepseek":
        return "deepseek.thinking=disabled"
    return f"{dialect}.thinking=off"


def resolve_thinking_off(
    *,
    provider_type: str | None,
    base_url: str | None,
    model_id: str | None,
    capabilities: Mapping[str, Any] | None,
) -> ThinkingOff:
    """解析"关闭思考"要发的字段。

    优先级：能力快照声明 > 厂商注册表 > 通用兜底 > 空（表达不出，调用方照常服务并提醒）。

    ``suspect`` 的两种来源：①认出了厂商源却没用上厂商原生字段；②快照里的
    ``thinking_off_observed`` 已经实测证明当前机制关不掉。
    """

    caps = dict(capabilities or {})
    model = str(model_id or "").strip()
    if not model:
        return ThinkingOff({}, "", "没有解析到语音模型", "none")

    dialect = provider_dialect(provider_type, base_url, caps)
    effective = model_capabilities_for_model(caps, model)
    if effective.get("thinking_required") is True:
        return ThinkingOff(
            {}, "", f"模型 {model} 只支持思考模式，无法关闭思考", "none", dialect
        )

    declared = thinking_off_declaration(caps, model)
    if declared:
        return ThinkingOff(
            declared,
            "capability.thinking_off",
            source="declaration",
            dialect=dialect,
        )

    vendor = VENDOR_THINKING_OFF_FIELDS.get(dialect) or {}
    if vendor:
        mechanism = _mechanism_for(dialect)
        observed = thinking_off_observation(caps, model)
        ineffective = observation_marks_ineffective(
            observed, mechanism=mechanism, model_id=model
        )
        return ThinkingOff(
            dict(vendor),
            mechanism,
            reason=(
                f"实测记录显示该机制在 {model} 上未生效（{observed.get('detail') or observed.get('reason') or ''}）".strip()
                if ineffective
                else ""
            ),
            source="vendor",
            dialect=dialect,
            suspect=ineffective,
        )

    try:
        options = resolve_model_call_options(
            caps,
            model,
            thinking_mode="off",
            search_route="disabled",
            disable_thinking_fallback=True,
        )
    except ModelCapabilityError as exc:
        return ThinkingOff({}, "", str(exc), "none", dialect)
    fields = {
        key: value
        for key, value in (options.provider_options or {}).items()
        if value is not None
    }
    if not fields:
        return ThinkingOff(
            {},
            "",
            f"Provider 方言（{dialect}）没有可用的关闭思考字段",
            "none",
            dialect,
        )
    return ThinkingOff(
        fields,
        ", ".join(f"{key}={value!r}" for key, value in sorted(fields.items())),
        source="fallback",
        dialect=dialect,
        # 通用兜底对真正的通用网关（以及 DashScope 的 ``enable_thinking``）是**正解**——
        # 仓库既有先例：批量调用靠它变快。只有当这个方言在厂商注册表里**有**原生机制、
        # 我们却没走那条路时，兜底才意味着"原生机制没用上"，那时才可疑。
        suspect=dialect in VENDOR_THINKING_OFF_FIELDS,
    )
