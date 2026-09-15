"""ICE / TURN 兼容层与可观测性补丁 —— 语音链路排障的地基。

两个和 aioice 有关的问题在真实通话里反复出现，而且都只能从「现象」倒推：

1. **TURN 通道绑定被拒时的报错完全不可读。**
   aioice 对每个要发包的对端地址都先做一次 ``CHANNEL-BIND``（``send_data``），
   而 Cloudflare 的 TURN 会拒绝**无法路由**的对端地址：实测 ``10.100.61.42``
   （浏览器上报的局域网 host 候选）与 ``1.1.1.1``（Cloudflare 自家 anycast）
   返回裸 401，而 ``104.30.x``（中继候选）与 ``39.144.124.36``（srflx）正常。
   浏览器永远会把自己的 host 候选也发过来，所以这条 401 每次通话都会出现。
   它在 ICE 层面是无害的（那条候选对本来就不可达），但 ``send_data`` 是
   ``asyncio.create_task`` 里跑的 fire-and-forget 协程，异常没人取，于是日志里
   只有一段 ``Task exception was never retrieved`` 的堆栈；更糟的是 aioice 只在
   绑定**成功**后才清理 ``peer_connect_waiters[addr]``，失败时把它留成空列表，
   同一地址后续的发送会永久等在无人 resolve 的 future 上（任务泄漏）。
   本补丁把失败收敛成「一条带地址的 warning + 释放排队者」，其余照旧。

2. **aioice/aiortc 的 ICE 与 TURN 决策在日志里不可见。**
   这两个库用标准库 ``logging``，而本进程只配置了 loguru + uvicorn，
   ``aioice`` 的 INFO（``TURN allocation created`` / ``Check ... -> succeeded`` /
   ``ICE completed`` / ``ICE failed``）全部被丢掉。于是「通话连不上」只能看到
   pipecat 的 ``ICE connection state is checking``，永远停在 checking，
   看不出是哪条候选对失败、中继到底有没有建起来。默认把 ``aioice``/``aiortc``
   的 INFO 接进 loguru（``VOICE_ICE_DEBUG=0`` 关闭，``=debug`` 开包级细节）。

两个补丁都是**幂等**的，且都做了防御：aioice 内部结构一旦变化就整体跳过，
绝不因为补丁装不上而让语音链路起不来。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from loguru import logger

# 打开包级细节（每条 STUN 消息）用 ``VOICE_ICE_DEBUG=debug``。
# 关闭（回到「什么都不打」）用 ``VOICE_ICE_DEBUG=0``/``off``。
OFF_VALUES = {"0", "off", "false", "no"}
DEBUG_VALUES = {"debug", "trace", "2"}


class _LoguruHandler(logging.Handler):
    """把标准库日志转发给 loguru，并保留原本的调用位置。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: Any = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def install_ice_log_bridge() -> str:
    """把 ``aioice``/``aiortc`` 的日志接进 loguru；返回实际生效的级别。"""

    requested = (os.getenv("VOICE_ICE_DEBUG") or "").strip().lower()
    if requested in OFF_VALUES:
        return "off"
    level = logging.DEBUG if requested in DEBUG_VALUES else logging.INFO

    handler = _LoguruHandler()
    for name in ("aioice", "aiortc"):
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers = [handler]
        stdlib_logger.setLevel(level)
        # 不再向上冒泡：根 logger 没有 handler，向上只会落到 lastResort
        # 那条无格式输出上，等于同一条日志打两遍、且其中一遍没有时间戳。
        stdlib_logger.propagate = False
    return logging.getLevelName(level)


def install_turn_channel_bind_guard() -> bool:
    """让 TURN 通道绑定失败变成一条可读日志，并释放排队等待的发送者。"""

    try:
        from aioice.turn import TurnClientMixin
    except Exception:  # pragma: no cover - 语音依赖未安装
        return False

    # 只依赖这两个方法；aioice 换实现时整体跳过，宁可不补也不改坏库。
    # （``peer_connect_waiters`` 是实例属性，不在这里检查，运行时用 getattr 取。）
    if not hasattr(TurnClientMixin, "send_data") or not hasattr(
        TurnClientMixin, "channel_bind"
    ):
        logger.debug("aioice internals changed; TURN channel-bind guard skipped")
        return False
    if getattr(TurnClientMixin, "_learngraph_bind_guard", False):
        return True

    original_send_data = TurnClientMixin.send_data

    async def send_data(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            return await original_send_data(self, data, addr)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 见模块说明：失败本身是预期内的
            # ① 释放排队者：aioice 只在绑定成功后清理这张表，失败时留着会
            #    让同一地址后续的发送永久挂起（每个都是泄漏的任务）。
            waiters_map = getattr(self, "peer_connect_waiters", None)
            if isinstance(waiters_map, dict):
                for waiter in waiters_map.pop(addr, None) or []:
                    if not waiter.done() and isinstance(exc, Exception):
                        waiter.set_exception(exc)
            # ② 同一地址只报一次 warning，重传不再刷屏。
            reported = getattr(self, "_learngraph_bind_failures", None)
            if reported is None:
                reported = set()
                self._learngraph_bind_failures = reported
            if addr not in reported:
                reported.add(addr)
                logger.warning(
                    "TURN 通道绑定被拒，跳过该对端地址（通常是对端上报的内网 host 候选，"
                    "Cloudflare 不允许中继到不可路由地址）：{} —— {}",
                    addr,
                    exc,
                )
            else:
                logger.debug("TURN channel bind still rejected for {}", addr)
            return None

    send_data._learngraph_original = original_send_data  # type: ignore[attr-defined]
    TurnClientMixin.send_data = send_data  # type: ignore[method-assign]
    TurnClientMixin._learngraph_bind_guard = True  # type: ignore[attr-defined]
    return True


def install_ice_compat() -> dict[str, Any]:
    """装好上面两个补丁，返回一份可直接打日志的状态摘要。"""

    status = {
        "log_level": install_ice_log_bridge(),
        "turn_guard": install_turn_channel_bind_guard(),
    }
    if status["turn_guard"]:
        logger.info("Voice ICE instrumentation installed: {}", status)
    else:
        logger.warning("Voice ICE instrumentation partially installed: {}", status)
    return status
