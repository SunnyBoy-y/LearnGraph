"""Release sentence identities after transport audio writes, in media order.

Synthesis only enqueues markers. The start marker is held until the first
successfully written audio chunk; end/turn-end markers follow their audio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from pipecat.frames.frames import DataFrame, InterruptionFrame, TTSAudioRawFrame
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
    turn_id: str | None = None
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
        self._pending_start: VoiceLedgerFrame | None = None

    @property
    def journal(self) -> Any:
        return self._journal

    async def process_frame(self, frame: Any, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            self._pending_start = None
            self._sequences.clear()
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, VoiceLedgerFrame):
                if frame.kind == "start":
                    self._pending_start = frame
                else:
                    await self._release(frame)
                return
            if isinstance(frame, TTSAudioRawFrame) and self._pending_start is not None:
                pending = self._pending_start
                if frame.context_id is None or pending.context_id == frame.context_id:
                    self._pending_start = None
                    await self._release(pending)
        await self.push_frame(frame, direction)

    async def _release(self, frame: VoiceLedgerFrame) -> None:
        journal = self._journal
        if journal is None:
            logger.debug("VoiceLedgerRelay: no journal bound; dropping ledger frame")
            return
        try:
            if frame.turn_id and frame.turn_id != journal.current_turn_id():
                return
            if frame.kind == "turn-end":
                await journal.playback_ended()
                return
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
