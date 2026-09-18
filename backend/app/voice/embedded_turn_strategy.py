"""Adaptive user turn start strategy with multilingual barge-in handling.

The strategy inserts a short decision window only while the tutor is speaking.
Explicit interruption words stop output immediately; short backchannels are
swallowed so an acknowledgement does not cut off the tutor.  The actual
classifier lives in ``embedded_turn_intent`` and supports additional language
profiles without changing this state machine.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

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

from app.voice.embedded_turn_intent import (
    DEFAULT_TURN_INTENT_CLASSIFIER,
    TurnIntent,
    TurnIntentClassifier,
    merge_transcript_segments,
)

_DECISION_WINDOW = 0.5
InterruptCallback = Callable[[], Awaitable[None]]


class AdaptiveUserTurnStartStrategy(BaseUserTurnStartStrategy):
    """Classify VAD and ASR evidence before yielding the speaking turn."""

    def __init__(
        self,
        *,
        enable_interruptions: bool = True,
        decision_window: float = _DECISION_WINDOW,
        classifier: TurnIntentClassifier | None = None,
        interrupt_callback: InterruptCallback | None = None,
        **kwargs,
    ):
        super().__init__(enable_interruptions=enable_interruptions, **kwargs)
        self._decision_window = max(0.0, float(decision_window))
        self._classifier = classifier or DEFAULT_TURN_INTENT_CLASSIFIER
        self._interrupt_callback = interrupt_callback
        self._bot_speaking = False
        self._pending = False
        self._pending_text = ""
        self._decision_token = 0
        self._decision_task: asyncio.Task | None = None

    def set_interrupt_callback(self, callback: InterruptCallback | None) -> None:
        """Install the fast TTS-stop hook after the RTVI processor is available."""
        self._interrupt_callback = callback

    async def handle_user_turn_started(self):
        await self._cancel_decision()
        self._clear_pending()
        await super().handle_user_turn_started()

    async def cleanup(self):
        await self._cancel_decision()
        self._clear_pending()
        await super().cleanup()

    def _clear_pending(self) -> None:
        self._pending = False
        self._pending_text = ""

    async def _cancel_decision(self) -> None:
        self._decision_token += 1
        task, self._decision_task = self._decision_task, None
        if task is not None and task is not asyncio.current_task():
            await self.cancel_task(task)

    async def _decision_timeout(self, token: int) -> None:
        """Wait for the full window, then classify the evidence accumulated."""
        if not self._pending:
            return
        # This is the actual wait that the previous implementation advertised
        # but never performed.  Cancellation by VAD-stop/ASR is intentional.
        await asyncio.sleep(self._decision_window)
        if not self._pending or self._decision_token != token:
            return

        intent = self._classifier.classify(self._pending_text)
        if intent is TurnIntent.BACKCHANNEL:
            await self._suppress_backchannel()
            return
        if not self._pending_text.strip():
            # A window that expired without a single recognised word is noise --
            # a cough, a chair, or the tutor's own audio bleeding into the mic --
            # not a barge-in.  Interrupting on it cut the turn off *before* it had
            # answered anything, which is how an answer the user did hear ended up
            # recorded against whatever they said next.  An explicit stop is still
            # acted on immediately, because that path needs the word recognised.
            await self._suppress_backchannel()
            return
        await self._trigger_interrupt_turn()

    def _is_backchannel(self, text: str) -> bool:
        """整句都是背声词/填充词（「嗯。」「哦。」「对。」…）时才为真。"""
        return self._classifier.classify(text) is TurnIntent.BACKCHANNEL

    async def _trigger_normal_turn(self) -> None:
        self._clear_pending()
        await self.trigger_user_turn_started(
            enable_interruptions=self._enable_interruptions
        )

    async def _trigger_interrupt_turn(self) -> None:
        self._clear_pending()
        if self._bot_speaking and self._interrupt_callback is not None:
            self._decision_task = None
            try:
                await self._interrupt_callback()
            except Exception:
                logger.debug("fast voice interrupt callback failed", exc_info=True)
        await self.trigger_user_turn_started(enable_interruptions=True)

    async def _suppress_backchannel(self) -> None:
        self._clear_pending()

    async def _evaluate_pending(self) -> None:
        intent = self._classifier.classify(self._pending_text)
        if intent is TurnIntent.INTERRUPTION:
            await self._trigger_interrupt_turn()
        elif intent is TurnIntent.BACKCHANNEL:
            await self._suppress_backchannel()
        elif not self._pending_text.strip():
            # Speech ended before anything was recognised: same rule as the
            # decision window -- noise is not a barge-in, so the turn stands.
            await self._suppress_backchannel()
        else:
            await self._trigger_interrupt_turn()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            return ProcessFrameResult.CONTINUE

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._pending:
                await self._cancel_decision()
                intent = self._classifier.classify(self._pending_text)
                if intent is TurnIntent.BACKCHANNEL:
                    await self._suppress_backchannel()
                else:
                    await self._trigger_normal_turn()
            return ProcessFrameResult.CONTINUE

        if isinstance(frame, VADUserStartedSpeakingFrame):
            if self._bot_speaking and not self._pending:
                await self._cancel_decision()
                self._pending = True
                self._pending_text = ""
                token = self._decision_token
                self._decision_task = self.create_task(
                    self._decision_timeout(token)
                )
                return ProcessFrameResult.STOP
            if not self._pending:
                await self._cancel_decision()
                await self._trigger_normal_turn()
            return ProcessFrameResult.STOP

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            if self._pending:
                await self._cancel_decision()
                await self._evaluate_pending()
            return ProcessFrameResult.STOP

        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            text = str(getattr(frame, "text", "") or "").strip()
            if self._pending:
                self._pending_text = merge_transcript_segments(
                    self._pending_text, text
                )
                # Explicit interruption words are acted on immediately; no need
                # to wait out the decision window.
                if self._classifier.classify(self._pending_text) is TurnIntent.INTERRUPTION:
                    await self._cancel_decision()
                    await self._trigger_interrupt_turn()
                return ProcessFrameResult.STOP
            # 决策窗口之外的转录。两条路都会走到这里：
            #   * 窗口已按"什么都没听清"关掉（决策超时 / VAD 停止），随后 ASR
            #     的 final 才回来；
            #   * ASR 的 partial 比本机 VAD 帧先到——实测「嗯」的 partial 比
            #     ``VADUserStartedSpeakingFrame`` 早 7ms，于是窗口根本还没开。
            # 旧实现在这里**无条件**起一个回合，而回合默认带打断：任何一次背声词
            # 都会在音频里切掉正在播的回答，随后这句背声词还被当成一个问题回答
            # （实测 ``嗯。`` → 13ms 后 turn.interrupted → bot 回「嗯，我在这儿。」）。
            # 分类器在窗口内才是权威判据，窗口外同样必须先问它。
            await self._cancel_decision()
            if self._bot_speaking:
                # 机器人正在出声。这一刻的「嗯。」是"我在听"，不是一次发言：既不
                # 起回合，也不打断；换成带实义的文本则照旧抢断。
                if self._is_backchannel(text):
                    await self._suppress_backchannel()
                    return ProcessFrameResult.STOP
                await self._trigger_interrupt_turn()
                return ProcessFrameResult.STOP
            # 机器人没出声：孤零零一个「嗯」很可能就是在回答（"听懂了吗？"→"嗯"），
            # 这种发言照旧起回合；它打断不了任何音频，因为本来就没人在说话。
            # （"回答在途但还没出声"的那几秒由 STT 层的背声词门闩负责，见
            # ``DashScopeSTTService._is_backchannel_filler``。）
            await self._trigger_normal_turn()
            return ProcessFrameResult.STOP

        return ProcessFrameResult.CONTINUE
