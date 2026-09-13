"""语音管线单点计时探针 —— 只在排障时启用（``VOICE_TIMELINE_DEBUG=1``）。

问题：用户实测「说完话 → 屏幕上出文字 / 耳朵里出声音」约 3-5s，远远超过
链路本身的应有耗时。要定位瓶颈，必须把这条链拆成可对照的分段：

    VAD 判定停止 → Smart Turn 端点判定 → 用户回合结束（commit 触发点）
    → DashScope final 转录 → LLM 首字 → TTS 首字节 → 机器人开始出声

做法：在管线里插入几个零逻辑的 ``FrameProcessor``，把关键帧以 INFO 打到
日志。时间轴锚点取「本轮 VAD 判定用户开口」的时刻，之后每行都带 ``T+秒``，
因此一眼可以看出各段的绝对间隔；每行还带探针位置（``source``）与帧方向
（``in`` = 下游、``up`` = 上游），用来区分同一帧是被谁看到的。

默认关闭：``VOICE_TIMELINE_DEBUG`` 未开启时 :func:`insert_timeline_probes`
原样返回入参，生产管线里不会多出任何处理器，零额外延迟。
"""

from __future__ import annotations

import os
import time

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# 日志前缀：便于 `docker logs | grep` 一行捞出整条时间线。
TAG = "[VT]"

_OFF_VALUES = ("", "0", "false", "no", "off")

# 时间轴锚点（time.time()）：每轮用户开口时重置。
_anchor: float | None = None
# 本轮是否已经打印过第一个文本帧（文本帧是逐 token 下发的，只留第一行）。
_first_text_logged = False


def timeline_enabled() -> bool:
    """探针开关。逐次读取 env，便于排障时不重启进程即可改配置。"""
    return os.getenv("VOICE_TIMELINE_DEBUG", "0").strip().lower() not in _OFF_VALUES


def reset_timeline() -> None:
    """把时间轴锚点重置为「现在」（每轮用户开口时调用）。"""
    global _anchor, _first_text_logged
    _anchor = time.time()
    _first_text_logged = False


def timeline_mark(source: str, event: str, detail: str = "") -> None:
    """打一行时间线。锚点为空时以本行作为新锚点。"""
    global _anchor
    if not timeline_enabled():
        return
    now = time.time()
    if _anchor is None:
        _anchor = now
    suffix = f" {detail}" if detail else ""
    logger.info(f"{TAG} {source:<12} T+{now - _anchor:6.3f}s  {event}{suffix}")


def timeline_mark_first_text(source: str, detail: str) -> None:
    """本轮第一个文本帧只记一次（文本帧是逐 token 下发的）。"""
    global _first_text_logged
    if _first_text_logged:
        return
    _first_text_logged = True
    timeline_mark(source, "首个文本帧(→TTS)", detail)


class VoiceTimelineProbe(FrameProcessor):
    """只观察、不修改帧的计时探针（``source`` 用于区分探针位置）。"""

    def __init__(self, source: str):
        super().__init__(name=f"timeline::{source}")
        self._source = source

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if timeline_enabled():
            self._observe(frame, direction)
        await self.push_frame(frame, direction)

    def _observe(self, frame: Frame, direction: FrameDirection) -> None:
        where = "in" if direction == FrameDirection.DOWNSTREAM else "up"
        source = f"{self._source}/{where}"
        if isinstance(frame, VADUserStartedSpeakingFrame):
            # 每轮用户开口重置锚点：后续所有 T+ 都以这一刻为原点。
            reset_timeline()
            timeline_mark(source, "VAD 判定用户开口")
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            timeline_mark(
                source,
                "VAD 判定用户停止发声",
                f"(stop_secs={getattr(frame, 'stop_secs', None)})",
            )
        elif isinstance(frame, UserStartedSpeakingFrame):
            timeline_mark(source, "聚合器广播 用户回合开始")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            timeline_mark(source, "聚合器广播 用户回合结束(回合边界)")
        elif isinstance(frame, InterimTranscriptionFrame):
            timeline_mark(source, "ASR interim", f"text_len={len(frame.text or '')}")
        elif isinstance(frame, TranscriptionFrame):
            timeline_mark(
                source,
                "ASR final",
                f"finalized={getattr(frame, 'finalized', None)} text_len={len(frame.text or '')}",
            )
        elif isinstance(frame, LLMFullResponseStartFrame):
            timeline_mark(source, "LLM 响应开始")
        elif isinstance(frame, LLMFullResponseEndFrame):
            timeline_mark(source, "LLM 响应结束")
        elif isinstance(frame, TextFrame):
            # 子 worker 经总线回来的第一段文本（= TTS 的输入）。
            timeline_mark_first_text(source, f"text_len={len(frame.text or '')}")
        elif isinstance(frame, TTSStartedFrame):
            timeline_mark(source, "TTS 开始合成")
        elif isinstance(frame, TTSStoppedFrame):
            timeline_mark(source, "TTS 合成结束")
        elif isinstance(frame, BotStartedSpeakingFrame):
            timeline_mark(source, "机器人开始出声")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            timeline_mark(source, "机器人停止出声")


def insert_timeline_probes(steps: list) -> list:
    """探针关闭时原样返回；开启时在关键截面上插入三个观察点。

    插入位置（相对主 worker 管线）：

        transport.input → stt → [turn] → user_aggregator → bridge
        → [llm] → tts → [out] → latency → transport.output → assistant_aggregator

    - ``turn``：STT 之后，看得到 ASR 输出与上游广播的 VAD/回合帧；
    - ``llm``：TTS 之前，看得到子 worker 回来的文本帧；
    - ``out``：TTS 之后，看得到 TTS 与机器人说话状态帧。
    """
    if not timeline_enabled():
        return steps
    from app.voice.embedded_dashscope_stt import DashScopeSTTService
    from app.voice.embedded_volcengine_tts import VolcengineTTSService

    marked: list = []
    for step in steps:
        # stt 之后插 turn 探针；tts 之前插 llm；tts 之后插 out。
        if isinstance(step, DashScopeSTTService):
            marked.append(step)
            marked.append(VoiceTimelineProbe("turn"))
            continue
        if isinstance(step, VolcengineTTSService):
            marked.append(VoiceTimelineProbe("llm"))
            marked.append(step)
            marked.append(VoiceTimelineProbe("out"))
            continue
        marked.append(step)
    return marked
