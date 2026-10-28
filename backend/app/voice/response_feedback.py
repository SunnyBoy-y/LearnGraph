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
from app.voice.low_latency import complex_request_acknowledgement, tool_acknowledgement


class VoiceResponseFeedback(FrameProcessor):
    """Keep acknowledgement audio in the ordinary TTS/ledger/generation path.

    A short grace skips fillers for immediate answers. Tool rounds share the
    same durable turn, so neither retries nor parallel tools repeat the cue.
    """

    def __init__(self, journal: Any, *, delay: float = 0.35, policy=None, floor=None) -> None:
        super().__init__(name="VoiceResponseFeedback")
        self._journal = journal
        self._policy = policy
        self._floor = floor
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
        self._progress_text: str | None = "让我思考一下。"
        self._blocked_turn_id: str | None = None
        self._revision: int | None = None

    def cancel_pending(self) -> None:
        """Disabling the preference also cancels an already scheduled cue."""
        self._cancel_timer()

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    def _adopt_turn(self) -> bool:
        turn_id = self._journal.current_turn_id()
        origin = getattr(self._journal, "current_turn_origin", lambda: None)()
        if not turn_id or origin == "delivery" or turn_id == self._blocked_turn_id:
            return False
        if turn_id != self._turn_id:
            self._cancel_timer()
            self._generation += 1
            self._turn_id = turn_id
            self._revision = getattr(self._policy, "revision", None)
            self._acknowledged = self._answer_started = False
            self._model_active = self._round_has_tools = False
            self._tools.clear()
            self._status = "idle"
            self._progress_text = (
                complex_request_acknowledgement(self._policy.request_text)
                if self._policy is not None else "让我思考一下。"
            )
        return True

    async def _activity(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        await self._journal.assistant_activity(status, turn_id=self._turn_id)

    async def _acknowledge(self, generation: int) -> None:
        if self._floor is not None and self._floor.user_speaking:
            return
        if self._policy is not None and not self._policy.feedback_allowed:
            return
        if self._policy is not None and self._policy.revision != self._revision:
            return
        if (not self._progress_text or generation != self._generation or self._acknowledged or self._answer_started
                or self._status == "idle" or self._turn_id != self._journal.current_turn_id()):
            return
        self._acknowledged = True
        frame = TextFrame(self._progress_text)
        frame.metadata["voice_acknowledgement"] = True
        if self._policy is not None:
            frame.metadata["voice_revision"] = self._policy.revision
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _delayed_acknowledgement(self, generation: int) -> None:
        try:
            await asyncio.sleep(self._delay)
            await self._acknowledge(generation)
        except asyncio.CancelledError:
            pass
        finally:
            if self._timer is asyncio.current_task():
                self._timer = None

    def _schedule(self) -> None:
        if (self._progress_text and (self._policy is None or self._policy.feedback_allowed)
                and not self._acknowledged and not self._answer_started and self._timer is None):
            self._timer = asyncio.create_task(self._delayed_acknowledgement(self._generation))

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
            self._blocked_turn_id = self._turn_id
            self._cancel_timer()
            self._generation += 1
            self._model_active = False
            self._acknowledged = self._answer_started = False
            self._tools.clear()
            await self._activity("idle")
        elif direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, LLMFullResponseStartFrame) and self._adopt_turn():
                self._model_active, self._round_has_tools = True, False
                await self._activity("thinking")
                self._schedule()
            elif isinstance(frame, FunctionCallsStartedFrame) and self._adopt_turn():
                self._round_has_tools = True
                self._tools.update(call.tool_call_id for call in frame.function_calls)
                delegating = any(call.function_name.startswith("delegate_") for call in frame.function_calls)
                await self._activity("delegating" if delegating else "thinking")
                for call in frame.function_calls:
                    if self._policy is not None:
                        hint = tool_acknowledgement(call.function_name)
                        if hint:
                            self._progress_text = hint
                    elif call.function_name == "search_web":
                        query = str(call.arguments.get("query") or "").strip() if isinstance(call.arguments, dict) else ""
                        self._progress_text = f"我正在查询{query[:40]}。" if query else "我正在查询相关资料。"
                    elif call.function_name == "get_weather":
                        self._progress_text = "我正在查询天气数据。"
                    elif call.function_name == "get_news":
                        self._progress_text = "我正在读取新闻来源。"
                    elif call.function_name == "get_current_time":
                        self._progress_text = "我正在核对当前时间。"
                    elif call.function_name.startswith("delegate_"):
                        self._progress_text = "正在提交后台任务。"
                self._schedule()
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
                if not self._tools and not self._round_has_tools:
                    self._cancel_timer()
                    await self._activity("idle")
        await self.push_frame(frame, direction)
