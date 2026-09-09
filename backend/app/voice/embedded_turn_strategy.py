"""Adaptive user turn start strategy — backchannel 不打断 + barge-in 分类决策。

替换 Pipecat 默认的 ``[VADUserTurnStartStrategy, TranscriptionUserTurnStartStrategy]``
两个 start 策略，在"VAD 检测到用户开口"与"广播 InterruptionFrame"之间插入一个
**决策窗口**：

  用户说"嗯嗯 / 对 / 啊哈"（backchannel，非意图性发声）
       -> 不打断 Agent，整段发声无痕吞掉（不进入用户回合）

  用户真打断 "等等 / 停 / 不是" 或说出完整句
       -> 立即中断 Agent，进入用户回合

设计要点
--------
- 只有 Agent 正在说话时才启用决策延时；Agent 没说话时（用户在正常开场、AIPM
  主动发问等待回答等）保持原 VAD / 转录立即触发行为，不影响正常对话启动。
- 判定依赖 ASR 的 interim 文本：interim 一出现"确实要打断"的内容就立即中断；
  若整段发声周期结束（VAD stop）时累积文本仍只是背声词，则整段吞掉不打断。
- 决策窗口超时（VAD 后一段时间内既无 interim 也无 VAD stop）按真实开口处理，
  立即打断，避免 ASR 卡顿时关键中断丢失。
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.base_user_turn_start_strategy import (
    BaseUserTurnStartStrategy,
)

# 纯背声词：整段发声仅为这些词 → 不打断。判断为"整段文本去掉空白/标点后
# 恰好是其中一个词"（如 "嗯"、"嗯嗯"、"对"、"是的"、"啊哈"）。
BACKCHANNEL_WORDS = (
    "嗯",
    "嗯嗯",
    "嗯嗯嗯",
    "嗯哼",
    "唔",
    "对",
    "对的",
    "是",
    "是的",
    "啊",
    "哦",
    "哦哦",
    "好",
    "好的",
    "好吧",
    "啊哈",
)

# 立即打断词：interim 一旦出现这些内容即判定为抢占对话权。
INTERRUPT_PATTERNS = (
    "等等",
    "等一下",
    "停",
    "停一下",
    "不是",
    "不对",
    "听我说",
    "打断一下",
    "别说了",
    "先听我",
    "我问",
)

# 决策窗口：VAD 开口后等待 ASR interim / VAD stop 的最长时限（秒）。
# 超时未定 -> 按真实开口打断。
_DECISION_WINDOW = 0.5

# 把文本规整为仅剩核心汉字串（去空白、标点、emoji 等）。
_NON_HAN_SPLIT = re.compile(r"[\s\W_]+", re.UNICODE)  # noqa: RUF016


def _compact(text: str) -> str:
    """去空白与标点，返回紧凑的汉字串，用于整段背声词比对。"""
    return _NON_HAN_SPLIT.sub("", text or "").strip()


def _is_pure_backchannel(text: str) -> bool:
    """整段发声是否为纯背声词（如 '嗯嗯'）。"""
    compact = _compact(text)
    if not compact:
        return False
    return compact in BACKCHANNEL_WORDS


def _contains_interrupt_pattern(text: str) -> bool:
    """文本里是否出现"确实要抢话"的打断词。"""
    compact = _compact(text)
    if not compact:
        return False
    return any(p in compact for p in INTERRUPT_PATTERNS)


class AdaptiveUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """在 VAD 与打断之间做 backchannel / barge-in 决策。"""

    def __init__(
        self,
        *,
        enable_interruptions: bool = True,
        decision_window: float = _DECISION_WINDOW,
        **kwargs,
    ):
        super().__init__(enable_interruptions=enable_interruptions, **kwargs)
        self._decision_window = decision_window
        # Agent 是否正在说话（决定是否启用决策延时）
        self._bot_speaking = False
        # 当前是否处于"待判定"状态
        self._pending = False
        self._pending_text = ""
        self._decision_task: Optional[asyncio.Task] = None

    async def handle_user_turn_started(self):
        """每次回合开始时清掉残留的 pending 状态。"""
        await self._cancel_decision()
        self._pending = False
        self._pending_text = ""

    async def cleanup(self):
        await self._cancel_decision()
        self._pending = False
        await super().cleanup()

    async def _cancel_decision(self):
        if self._decision_task:
            task = self._decision_task
            self._decision_task = None
            if task is not asyncio.current_task():
                await self.cancel_task(task)

    async def _decision_timeout(self):
        """决策窗口超时：既无 interim 也无 VAD stop，按真实开口处理。

        注意：此协程即为 decision task 自身，点到的打断路径不得再取消本 task。
        """
        if not self._pending:
            return
        logger.debug(f"{self}: decision window timeout, treating as real user turn")
        self._pending = False
        self._pending_text = ""
        await self.trigger_user_turn_started(enable_interruptions=True)

    async def _trigger_normal_turn(self):
        """正常开始用户回合（Agent 未说话时的开场/回答，或确认打断后）。"""
        self._pending = False
        self._pending_text = ""
        await self.trigger_user_turn_started(enable_interruptions=self._enable_interruptions)

    async def _trigger_interrupt_turn(self):
        """判定为真实打断：立即中断并开始用户回合。"""
        self._pending = False
        self._pending_text = ""
        await self.trigger_user_turn_started(enable_interruptions=True)

    async def _suppress_backchannel(self):
        """判定为纯背声词：整段吞掉，不打断、不进入用户回合。"""
        self._pending = False
        self._pending_text = ""

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        # 跟踪 Agent 说话状态
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            return ProcessFrameResult.CONTINUE
        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            # Agent 说话的回合结束前若还有残留判定，直接吞掉（不打断）
            if self._pending:
                await self._cancel_decision()
                await self._suppress_backchannel()
            return ProcessFrameResult.CONTINUE

        # 一、VAD 检测到用户开口
        if isinstance(frame, VADUserStartedSpeakingFrame):
            if self._bot_speaking and not self._pending:
                # Agent 正在说话 -> 进入决策窗口，暂不打断
                await self._cancel_decision()
                self._pending = True
                self._pending_text = ""
                self._decision_task = self.create_task(self._decision_timeout())
                return ProcessFrameResult.STOP
            # Agent 未说话 -> 正常开始回合
            await self._cancel_decision()
            await self._trigger_normal_turn()
            return ProcessFrameResult.STOP

        # 二、VAD 检测到用户停止发声 -> 评判整段发声
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            if self._pending:
                await self._cancel_decision()
                if _is_pure_backchannel(self._pending_text):
                    await self._suppress_backchannel()
                else:
                    await self._trigger_interrupt_turn()
            return ProcessFrameResult.STOP

        # 三、ASR 转录结果
        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            text = str(getattr(frame, "text", "") or "").strip()
            if self._pending:
                # 决策窗口内：持续累积文本
                self._pending_text += text if not self._pending_text else (" " + text)
                # 一旦出现"确实要抢话"的内容 -> 立即打断
                if _contains_interrupt_pattern(self._pending_text):
                    await self._cancel_decision()
                    await self._trigger_interrupt_turn()
                # 纯背声词 -> 继续等 VAD stop 确认（不贸然打断）
                return ProcessFrameResult.STOP
            # 非决策窗口（Agent 未说话的正规转录）-> 正常开始回合
            await self._cancel_decision()
            await self._trigger_normal_turn()
            return ProcessFrameResult.STOP

        return ProcessFrameResult.CONTINUE