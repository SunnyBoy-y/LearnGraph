"""把**真机实测结论**写回 provider 的能力快照（L2：身份与方言的"声明 + 实测"闭环）。

## 为什么需要这一步

"这个 provider 能不能关掉思考"以前是**纯代码推断**：域名像不像官方、provider_type 是
什么、能力快照里 ``thinking_mapping.off`` 是不是 ``null``。三处推断各写一半，于是线上
出现了"字段发了、语义没关掉、还静默"的组合（见 ``providers/dialects.py`` 的事故记录）。

``dialects`` 负责把"声明"变成字段，本模块负责把"实测"变成数据：一次真机通话里
上游自己回的 ``reasoning_tokens`` / ``TTFAT.thinking_time`` 就是最硬的证据。
结论回写后，下一次解析（``thinking_policy.resolve_thinking_off``）会优先采用它，
并把"已验证无效"的机制标成可疑，让会话一开始就提醒用户代价。

## 写入门槛（宁可少写，不可乱写）

* 结论**没变**就不写：能力快照是每个 provider 一行的大 JSON，每通电话都写只会把 WAL 撑爆；
  进程内 :data:`_last_verdict` 做去抖（多进程部署下退化为"每个进程各写一次"，可接受）。
* 写库失败**绝不**影响通话：整体 try/except，只留一条 warning。
* 没有 provider 行（纯环境变量兜底）时不写——没有可写的对象。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.database import SessionLocal
from app.domain.models import ProviderConfig
from app.providers.dialects import THINKING_OFF_OBSERVED_KEY

logger = logging.getLogger(__name__)

#: 进程内去抖：(provider_id, model_id, mechanism) → 上一次写入的结论。
_last_verdict: dict[tuple[str, str, str], bool] = {}


def record_thinking_off_observation(
    *,
    provider_id: str | None,
    model_id: str | None,
    verified: bool,
    mechanism: str,
    surface: str,
    reasoning_tokens: int = 0,
    thinking_time_ms: float | None = None,
    detail: str = "",
) -> bool:
    """记录一次"关闭思考到底生效没有"的实测结论。返回是否真的写了库。

    ``verified=False`` 表示**意图关闭思考，上游却仍在思考**（违约）。
    """

    provider = str(provider_id or "").strip()
    model = str(model_id or "").strip()
    if not provider or not model:
        return False
    mechanism_key = str(mechanism or "")
    key = (provider, model, mechanism_key)
    if _last_verdict.get(key) is verified:
        return False
    observation: dict[str, Any] = {
        "verified": bool(verified),
        "mechanism": mechanism_key,
        "model": model,
        "surface": str(surface or ""),
        "reasoning_tokens": max(0, int(reasoning_tokens or 0)),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    if thinking_time_ms is not None:
        observation["thinking_time_ms"] = round(float(thinking_time_ms), 1)
    if detail:
        observation["detail"] = str(detail)[:200]
    try:
        with SessionLocal() as db:
            row = db.get(ProviderConfig, provider)
            if row is None:
                return False
            capabilities = dict(row.capabilities or {})
            capabilities[THINKING_OFF_OBSERVED_KEY] = observation
            # 整份赋新 dict：SQLAlchemy 的 JSON 列靠"对象变了"来判定 dirty。
            row.capabilities = capabilities
            db.commit()
    except Exception:
        logger.warning(
            "Failed to record thinking-off observation for provider %s", provider,
            exc_info=True,
        )
        return False
    _last_verdict[key] = verified
    logger.info(
        "Thinking-off observation recorded: provider={} model={} verified={} mechanism={}",
        provider,
        model,
        verified,
        mechanism_key or "-",
    )
    return True
