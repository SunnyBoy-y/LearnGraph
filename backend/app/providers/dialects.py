"""厂商方言与"关闭思考字段"的**单一真源**（纯函数：无 IO、无框架依赖）。

## 为什么要有这个模块（真机事故记录）

同一条 provider 行（``openai_compatible_chat`` + ``https://api.deepseek.com/v1``）：

* **纯文字链路能关掉思考**——因为那条行的 ``base_url`` 恰好写成不带 ``/v1`` 的官方域名，
  命中 ``remote/deepseek.is_deepseek_chat_configuration``，于是走上原生
  ``DeepSeekChatProvider``，请求体里是 ``thinking:{"type":"disabled"}``。
* **全双工语音链路关不掉**——pipecat 的 ``OpenAILLMService`` 没有适配器层，字段由
  ``voice/policy`` 自己拼；而它判定方言时把"认 ``/v1``"的那份 predicate 挂在
  ``not provider_type`` 后面，provider 行一旦存在（永远存在）那个分支就**永不执行**，
  于是落到通用兜底 ``enable_thinking=False``——DeepSeek 官方不认这个参数。
* 真机后果：``TTFAT: 2.773s (2.580s thinking)`` + ``reasoning tokens: 398``，
  占一次 3638 ms 端到端延迟的 71%，而且**静默**（字段看起来发了，语义上什么都没关掉）。

根因不是"某个 URL 判定写错了"，而是：

1. **同一个问题有两份判定**："这个 URL 是不是官方 DeepSeek" 被同时当成①带凭据余额接口的
   **信任边界**（必须严格）和②请求体的**方言语义**（必须认 ``/v1``）。两份宽严相反，
   调用点各取一半，正好漏在中间。
2. **身份靠猜**：方言由 URL 字符串推断，而不是由 provider 已经声明过的身份
   （``provider_type`` / 能力快照里的 ``brand_id`` / ``model_family`` / ``protocol_family``）决定。
3. **失败不可见**：兜底路径产出了一个"看起来对"的非空字段，于是"无法关闭思考"的告警
   （只在字段为空时触发）永远发不出来。

本模块把方言判定、厂商 off 字段注册表、以及"意图 vs 实测"的判定收在一处；
调用方（``thinking_policy`` / ``providers.factory`` / ``voice.policy`` / 各 adapter）
一律从这里取，**不再各写一份**。

## 三层优先级

``provider_dialect``（说哪种话） → ``thinking_off_declaration``（快照里怎么声明关闭）
→ :data:`VENDOR_THINKING_OFF_FIELDS`（内置注册表） → 通用兜底（``enable_thinking``）。

能力快照里的 ``thinking_off`` 声明永远优先于内置注册表：厂商改了字段、或者用户接了
一个新网关，只需要改**数据**，不需要改代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

#: 请求体"说哪种方言"。``generic`` = 通用 OpenAI 兼容（含各种中转网关）。
ProviderDialect = Literal["deepseek", "dashscope", "generic"]

#: 内置厂商注册表：**唯一**一份"这家厂商怎么表达关闭思考"的代码知识。
#: 快照里的 ``thinking_off`` 声明优先于它（见 :func:`thinking_off_declaration`）。
VENDOR_THINKING_OFF_FIELDS: dict[str, dict[str, Any]] = {
    "deepseek": {"thinking": {"type": "disabled"}},
}

#: "实测仍在思考"的容差（毫秒）。首包到首字之间的思考时长低于它按噪声处理：
#: 服务端计时与模型抖动都在几十毫秒量级，只有几百毫秒以上的思考才值得报警。
THINKING_OFF_TOLERANCE_MS = 300

#: 能力快照里记录"实测结论"的键（L2：把真机观测写回数据，供下一次解析优先采用）。
THINKING_OFF_OBSERVED_KEY = "thinking_off_observed"


def is_official_deepseek_origin(base_url: str | None) -> bool:
    """官方 DeepSeek 源——**方言语义**，含 OpenAI 兼容模式常用的 ``/v1``。

    为什么不复用 :func:`remote.deepseek.is_official_deepseek_api_base_url`：那一个是给
    **带凭据的余额接口**用的信任边界，只认 ``https://api.deepseek.com``（path 必须为空或
    ``/``）。这里判的是"请求体说哪种方言"，而官方文档里 OpenAI 兼容模式就是
    ``https://api.deepseek.com/v1``，语音侧 ``DEEPSEEK_BASE_URL`` 的默认值也正是它。

    两者的宽严差异是**刻意**的，但只允许存在这一处方言判定：`/v1` 一定算官方源。
    判定仍然只认官方域名，不给任意兼容网关开 DeepSeek 原生字段。
    """

    if not base_url:
        return False
    try:
        parsed = urlsplit(str(base_url).strip())
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == "api.deepseek.com"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path.rstrip("/") in {"", "/v1"}
        and not parsed.query
        and not parsed.fragment
    )


def _declared_identity(capabilities: Mapping[str, Any] | None) -> tuple[str, str, str]:
    caps = capabilities if isinstance(capabilities, Mapping) else {}
    family = str(caps.get("protocol_family") or "").strip().casefold()
    brand = str(caps.get("brand_id") or "").strip().casefold()
    model_family = str(caps.get("model_family") or "").strip().casefold()
    return family, brand, model_family


def provider_dialect(
    provider_type: str | None,
    base_url: str | None,
    capabilities: Mapping[str, Any] | None = None,
) -> ProviderDialect:
    """这个 provider 的请求体说哪种方言。

    规则（顺序即优先级，每一条都有事故或既有教训撑着）：

    1. **托管方优先**：DashScope / Model Studio 上也跑 DeepSeek、GLM、Kimi 的权重，
       但请求体必须说 DashScope 方言——所以 ``protocol_family``/``brand_id`` 先于品牌判断。
    2. **显式声明**：``provider_type`` 是 ``deepseek_chat`` 时用户已明确选了原生协议。
    3. **官方源，含 ``/v1``**：线上事故就是这一条被写成"只认空路径"的严格版，
       于是 ``openai_compatible_chat`` + ``https://api.deepseek.com/v1`` 被当成通用网关，
       发出去的关闭字段是 ``enable_thinking``（官方不认）→ 思考照旧开、首字慢 2.6 s。
    4. **官方主机上的未知路径**：靠能力快照里声明的 ``brand_id``/``model_family`` 兜一层
       （厂商改文档、路径变形时不必再改代码）。注意声明**只在官方主机上**才用来放宽路径，
       不改变"只信官方域名"这条信任边界：把 DeepSeek 原生字段发给第三方网关会被判 400、
       整通没有回答，这正是仓库既有注释反复强调的事。
    """

    family, brand, model_family = _declared_identity(capabilities)
    kind = str(provider_type or "").strip().casefold()
    if kind == "qwen" or brand == "qwen" or family == "dashscope":
        return "dashscope"
    if kind in {"deepseek_chat", "deepseek"}:
        return "deepseek"
    if is_official_deepseek_origin(base_url):
        return "deepseek"
    if _is_official_deepseek_host(base_url) and (
        brand == "deepseek" or model_family == "deepseek"
    ):
        return "deepseek"
    return "generic"


def uses_deepseek_native_adapter(
    provider_type: str | None,
    base_url: str | None,
    capabilities: Mapping[str, Any] | None = None,
) -> bool:
    """自研链路是否该选原生 ``DeepSeekChatProvider``。

    与语音侧的方言判定**同源**（同一个 :func:`provider_dialect`）。历史上这条判定在
    ``providers/factory`` 里手写、语音侧又手写一遍，两份宽严相反，于是同一行 provider
    在文字链路走原生字段、在语音链路走通用兜底——"文字关得掉思考，语音关不掉"。
    把它做成一个显式函数，是为了让"两条链路必须同结论"成为可断言的不变量。
    """

    return provider_dialect(provider_type, base_url, capabilities) == "deepseek"


def _is_official_deepseek_host(base_url: str | None) -> bool:
    """只判主机名（不判路径）：给"官方主机 + 没见过的路径"这一格用。"""

    if not base_url:
        return False
    try:
        parsed = urlsplit(str(base_url).strip())
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() == "api.deepseek.com"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _declaration_fields(declaration: Any) -> dict[str, Any]:
    """把一条 ``thinking_off`` 声明折成请求字段。空 = 这条声明不可用。"""

    if not isinstance(declaration, Mapping):
        return {}
    mode = str(declaration.get("mode") or "fields").strip().casefold()
    if mode not in {"", "fields"}:
        return {}
    fields = declaration.get("fields")
    if not isinstance(fields, Mapping):
        return {}
    return {str(key): value for key, value in fields.items() if value is not None}


def thinking_off_declaration(
    capabilities: Mapping[str, Any] | None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """能力快照里显式声明的"关闭思考"字段（模型级声明优先于 provider 级）。"""

    caps = capabilities if isinstance(capabilities, Mapping) else {}
    model = str(model_id or "").strip()
    if model:
        models = caps.get("models")
        if isinstance(models, Mapping):
            selected = models.get(model)
            if isinstance(selected, Mapping):
                fields = _declaration_fields(selected.get("thinking_off"))
                if fields:
                    return fields
    return _declaration_fields(caps.get("thinking_off"))


def thinking_off_observation(
    capabilities: Mapping[str, Any] | None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """上一次**真机实测**的结论（L2）。没有记录时返回空 dict。"""

    caps = capabilities if isinstance(capabilities, Mapping) else {}
    model = str(model_id or "").strip()
    if model:
        models = caps.get("models")
        if isinstance(models, Mapping):
            selected = models.get(model)
            if isinstance(selected, Mapping):
                observed = selected.get(THINKING_OFF_OBSERVED_KEY)
                if isinstance(observed, Mapping):
                    return dict(observed)
    observed = caps.get(THINKING_OFF_OBSERVED_KEY)
    return dict(observed) if isinstance(observed, Mapping) else {}


def observation_marks_ineffective(
    observed: Mapping[str, Any] | None,
    *,
    mechanism: str,
    model_id: str | None = None,
) -> bool:
    """实测结论是否说明**这个机制**在这个模型上关不掉。

    只认同一 mechanism（换过方言/字段之后，旧结论不能继续作数）与同一模型。
    """

    if not isinstance(observed, Mapping) or not observed:
        return False
    if observed.get("verified") is not False:
        return False
    recorded_mechanism = str(observed.get("mechanism") or "")
    if mechanism and recorded_mechanism and recorded_mechanism != mechanism:
        return False
    recorded_model = str(observed.get("model") or "")
    return not (model_id and recorded_model and recorded_model != str(model_id))


@dataclass(frozen=True, slots=True)
class ThinkingOffVerdict:
    """"本轮意图关闭思考"与"实测是否真的没思考"的比对结果。"""

    violated: bool
    detail: str
    reasoning_tokens: int = 0
    thinking_time_ms: float | None = None


def thinking_off_verdict(
    *,
    intent_off: bool,
    reasoning_tokens: int | None = 0,
    thinking_time_ms: float | None = None,
    tolerance_ms: float = THINKING_OFF_TOLERANCE_MS,
) -> ThinkingOffVerdict | None:
    """L3 不变量：**意图 = off，实测却观察到思考 ⇒ 违约**。

    ``intent_off`` 为假时返回 ``None``（本轮没要求关闭思考，没有可判定的东西）。

    两个证据互相独立，任一成立即算违约：

    * ``reasoning_tokens > 0``（厂商在 usage 里自己承认产生了思考 token）；
    * ``thinking_time_ms > tolerance_ms``（首包到首个正文字之间的空隙，pipecat 的
      ``TTFAT - TTFB``；思考 token 不产出任何帧，只能在时间轴上看见）。

    这是 fail-loud 判据，**不是** fail-closed：通话照常进行，只是不再允许它静默。
    """

    if not intent_off:
        return None
    tokens = max(0, int(reasoning_tokens or 0))
    milliseconds = None if thinking_time_ms is None else max(0.0, float(thinking_time_ms))
    if tokens > 0:
        return ThinkingOffVerdict(
            True,
            f"reasoning_tokens={tokens}",
            reasoning_tokens=tokens,
            thinking_time_ms=milliseconds,
        )
    if milliseconds is not None and milliseconds > float(tolerance_ms):
        return ThinkingOffVerdict(
            True,
            f"thinking_time={milliseconds:.0f}ms（容差 {float(tolerance_ms):.0f}ms）",
            reasoning_tokens=tokens,
            thinking_time_ms=milliseconds,
        )
    return ThinkingOffVerdict(
        False, "honored", reasoning_tokens=tokens, thinking_time_ms=milliseconds
    )
