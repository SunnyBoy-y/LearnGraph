"""句级账本的**因果**触发：让"这一句开始/结束播放"由输出传输放行的帧来证明。

## 为什么需要这一层

字幕已经交给 Pipecat 官方的 ``bot-output``（句级路径：``AggregatedTextFrame`` 的
``new`` + ``TTSTextFrame`` 的 ``completed``），但账本（``journal``）必须继续回答
"用户到底听到了什么"，它不能跟着字幕一起交给框架——官方通道不落库、丢包不可恢复，
而"未听到的文本不落库、不进记忆"是本项目的硬口径。

记账时刻必须是**因果**的：合成远快于播放，若在音频入队时就记账，压在执行队列里
还没播出来的整段回答会提前几秒进账本、进转录、进记忆。所以这里把句首/句尾各做成
一枚普通数据帧，按位置排在那一句音频的前后，让它们**和音频一起排队**。

## 为什么到点保证是免费的

输出传输对帧分两路（``transports/base_output.py``）：

* ``SystemFrame`` → 直接 ``push_frame``，绕过所有队列；
* 其余（``DataFrame``）→ 无 ``pts`` 时进 ``_audio_queue``，与音频块**同队列同序**。

而音频写出是阻塞到真正被取走才返回的（WebRTC 发送端按 10ms 节拍消费）。于是排在
音频后面的数据帧，只有在前序音频真的播完之后才会被放行到下游——**不需要任何时间戳、
任何锚点换算、任何 sleep**。这正是官方句级/词级路径用的机制（S5）。

因此本模块的帧**必须是 DataFrame**：换成 ``SystemFrame`` 就退化成"立即下发 + 只能
靠自算时刻"，那是旧实现"字幕/记账时刻会漂"的根因。

## 前端看不到它们

``VoiceLedgerFrame`` 永不发给浏览器：relay 消费掉它（不再往下推），只把结果交给
账本；字幕显示完全由官方 ``bot-output`` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from pipecat.frames.frames import DataFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class VoiceLedgerFrame(DataFrame):
    """某一句的句首/句尾标记，随音频队列排队（**永不下发前端**）。

    Parameters:
        kind: ``"start"`` = 这一句的第一帧音频即将入队；``"end"`` = 它的音频已全部入队。
        token: 同句的句首/句尾配对键（由 TTS 适配器生成，句尾复用它）。
        text: 这一句的文本（只在 ``start`` 上有意义；账本按它记账）。
        context_id: 所属 audio context（仅用于排障）。
        audio_cursor_ms: 句首在整轮回答音频里的偏移（``start``）。
        audio_end_cursor_ms: 句尾偏移（``end``），句内位置估算的分母。
        generation_id: 音频闸门代次（沿用 TTS 适配器的语义，可为 None）。
    """

    kind: str
    token: str
    text: str = ""
    context_id: str | None = None
    audio_cursor_ms: int | None = None
    audio_end_cursor_ms: int | None = None
    generation_id: int | None = None


class VoiceLedgerRelay(FrameProcessor):
    """把被输出传输放行的账本帧翻成账本写入。

    位置：**紧跟 ``transport_output``**（见 ``build_pipeline_steps``）。放在这里不是
    随便挑的——只有输出传输下游的处理器才会在"前序音频已经写出"之后看到这些帧，
    那正是"这一句开始播放 / 播完"的因果时刻。

    收到账本帧即消费（不再往下推），避免把内部簿记帧漏给 assistant 聚合器。
    """

    def __init__(self, *, journal: Any = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._journal = journal
        # token → journal 分配的句序号；句尾靠它把 ended 关到同一句上。
        self._sequences: dict[str, int] = {}

    @property
    def journal(self) -> Any:
        return self._journal

    async def process_frame(self, frame: Any, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, VoiceLedgerFrame):
            if direction != FrameDirection.DOWNSTREAM:
                return
            await self._release(frame)
            return
        await self.push_frame(frame, direction)

    async def _release(self, frame: VoiceLedgerFrame) -> None:
        journal = self._journal
        if journal is None:
            logger.debug("VoiceLedgerRelay: no journal bound; dropping ledger frame")
            return
        try:
            if frame.kind == "start":
                identity = await journal.sentence_queued(
                    frame.text,
                    audio_cursor_ms=frame.audio_cursor_ms,
                    context_id=frame.context_id,
                    generation_id=frame.generation_id,
                )
                sequence = int((identity or {}).get("sentence_seq") or 0)
                if sequence > 0:
                    self._sequences[frame.token] = sequence
                else:
                    # 账本拒绝（回合已收尾 / 被打断 / 没有未 finalize 的回合）：这一句
                    # 一个字都不该出现，句尾也就无从谈起。
                    logger.debug(
                        f"VoiceLedgerRelay: sentence refused by journal: {frame.text[:20]!r}"
                    )
                return
            sequence = self._sequences.pop(frame.token, None)
            if sequence is None:
                # 句首没落账（从未开始播放）：不能给一句不存在的话写句尾。
                return
            if not journal.sentence_recorded(sequence):
                return
            await journal.sentence_ended(
                sentence_seq=sequence,
                audio_end_cursor_ms=int(frame.audio_end_cursor_ms or 0),
                generation_id=frame.generation_id,
            )
        except Exception:
            logger.warning("VoiceLedgerRelay: ledger release failed", exc_info=True)
