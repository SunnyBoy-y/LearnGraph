"""One brief, interruptible acknowledgement while the foreground actually works."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from pipecat.frames.frames import (
    CancelFrame, EndFrame, ErrorFrame, Frame, FunctionCallCancelFrame,
    FunctionCallResultFrame, FunctionCallsStartedFrame, InterruptionFrame,
    LLMFullResponseEndFrame, LLMFullResponseStartFrame, TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class VoiceResponseFeedback(FrameProcessor):
    """Keep acknowledgement audio in the ordinary TTS/ledger/generation path.

    A short grace skips fillers for immediate answers. Tool rounds share the
    same durable turn, so neither retries nor parallel tools repeat the cue.
    """

    def __init__(self, journal: Any, *, delay: float = .25) -> None:
        super().__init__(name="VoiceResponseFeedback")
        self._journal = journal
        self._delay = delay
        self._timer: asyncio.Task | None = None
        self._turn_id: str | None = None
        self._acknowledged = False
        self._answer_started = False
        self._model_active = False
        self._round_has_tools = False
        self._tools: set[str] = set()
        self._status = "idle"
        self._generation = 0
        self._cue_index = 0

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    def _adopt_turn(self) -> bool:
        turn_id = self._journal.current_turn_id()
        if not turn_id:
            return False
        if turn_id != self._turn_id:
            self._cancel_timer()
            self._generation += 1
            self._turn_id = turn_id
            self._acknowledged = self._answer_started = False
            self._model_active = self._round_has_tools = False
            self._tools.clear()
            self._status = "idle"
        return True

    async def _activity(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        await self._journal.assistant_activity(status, turn_id=self._turn_id)

    async def _acknowledge(self, generation: int) -> None:
        if (generation != self._generation or self._acknowledged or self._answer_started
                or self._status == "idle" or self._turn_id != self._journal.current_turn_id()):
            return
        self._acknowledged = True
        phrases = ("嗯，让我想一想。", "嗯，我看看。", "嗯。")
        phrase = "嗯，我看看。" if self._status == "delegating" else phrases[self._cue_index % len(phrases)]
        self._cue_index += 1
        frame = TextFrame(phrase)
        frame.metadata["voice_acknowledgement"] = True
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _delayed_acknowledgement(self, generation: int) -> None:
        try:
            await asyncio.sleep(self._delay)
            await self._acknowledge(generation)
        except asyncio.CancelledError:
            pass

    async def cleanup(self) -> None:
        timer = self._timer
        self._cancel_timer()
        if timer is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await timer
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, (InterruptionFrame, EndFrame, CancelFrame, ErrorFrame)):
            self._cancel_timer()
            self._generation += 1
            self._model_active = False
            self._tools.clear()
            await self._activity("idle")
        elif direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, LLMFullResponseStartFrame) and self._adopt_turn():
                self._model_active, self._round_has_tools = True, False
                await self._activity("thinking")
                if not self._acknowledged and not self._answer_started and self._timer is None:
                    self._timer = asyncio.create_task(self._delayed_acknowledgement(self._generation))
            elif isinstance(frame, FunctionCallsStartedFrame) and self._adopt_turn():
                self._round_has_tools = True
                self._tools.update(call.tool_call_id for call in frame.function_calls)
                delegating = any(call.function_name.startswith("delegate_") for call in frame.function_calls)
                await self._activity("delegating" if delegating else "thinking")
                self._cancel_timer()
                await self._acknowledge(self._generation)
            elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
                self._tools.discard(frame.tool_call_id)
                # A result normally starts another model round. Keep its wait
                # visible, but don't follow callbacks belonging to an old turn.
                if self._turn_id == self._journal.current_turn_id() and not self._tools:
                    await self._activity("thinking" if frame.run_llm is not False else "idle")
            elif isinstance(frame, TextFrame) and frame.text.strip():
                self._answer_started = True
                self._cancel_timer()
            elif isinstance(frame, LLMFullResponseEndFrame):
                self._model_active = False
                self._cancel_timer()
                if not self._tools and not self._round_has_tools:
                    await self._activity("idle")
        await self.push_frame(frame, direction)
