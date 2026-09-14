"""Durable journal for the audio pipeline, plus the worker's control channel.

The pipeline owns two things the control plane cannot know: what the user
actually said and what the assistant actually produced.  This module turns those
into durable events without letting caption bookkeeping leak into the TTS/ASR
adapters.

Design rules:

* **Text is never the identity.**  A turn is identified by ``turn_id`` and an
  assistant caption by ``(turn_id, sentence_seq)``; ordering uses
  ``event_seq`` and a monotonic ``audio_cursor_ms``.  Two sentences with equal
  text stay two sentences, and a duplicated or out-of-order TTS marker collapses
  instead of appending.
* **The authoritative assistant text comes from the LLM stream, not from TTS
  markers.**  A lost, duplicated or reordered marker can therefore never change,
  duplicate or drop the final message: markers only drive the speculative
  layer.
* **Journal writes never block the audio loop.**  Every database call is
  dispatched to a worker thread.
* **A processor failure degrades, it does not end the call.**  Errors are
  classified and retried; only a transport that cannot be rebuilt falls back to
  text mode, and the transcript is preserved.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMMessagesAppendFrame,
    TextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.voice import turns as turn_api
from app.voice.events import emit_event, last_event_seq, load_session

logger = logging.getLogger(__name__)

# Assistant stage names used in ``processor.error`` payloads.  The UI renders a
# per-stage banner ("麦克风 / 信令 / ASR / LLM / TTS / 网络") from this value.
STAGE_ASR = "asr"
STAGE_LLM = "llm"
STAGE_TTS = "tts"
STAGE_NETWORK = "network"

# A turn with no audio at all (TTS down) must still be finalized, otherwise the
# user's utterance would never reach the transcript or memory.
FINALIZE_GRACE_SECS = 6.0
# Idle ceiling for the assistant half of a turn. A provider error or a hang
# produces no text, no TTS and therefore no speaking frames, so nothing else
# would ever close the turn.
DEFAULT_IDLE_TIMEOUT_SECS = 20.0
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY = 1.0
DEFAULT_RETRY_MAX_DELAY = 8.0
# ``assistant.llm.delta`` is a caption stream, not an audit log: coalesce writes.
LLM_DELTA_MIN_INTERVAL_SECS = 0.4


def _jitter(base: float) -> float:
    return base * (0.7 + random.random() * 0.6)


def backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 30.0) -> float:
    """Exponential backoff with jitter, as used by every retry in the layer.

    Shared with the client contract (0.5/1/2/4/8s) so a reconnect storm from the
    browser and a processor retry inside the worker do not synchronise.
    """
    return min(cap, _jitter(base * (2 ** max(0, attempt - 1))))


@dataclass
class _TurnState:
    turn_id: str
    user_text: str = ""
    assistant_text: str = ""
    sentence_seq: int = 0
    audio_cursor_ms: int = 0
    llm_closed: bool = False
    finalized: bool = False
    retry_attempt: int = 0


class VoiceTurnJournal:
    """Turns pipeline observations into durable voice events.

    One instance per voice session; safe to call from the pipeline's event loop
    and from the watchdog.
    """

    def __init__(
        self,
        voice_session_id: str,
        *,
        publish: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        grace_secs: float = FINALIZE_GRACE_SECS,
        idle_timeout_secs: float = DEFAULT_IDLE_TIMEOUT_SECS,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY,
        retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY,
        retry_hook: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.voice_session_id = voice_session_id
        self._publish = publish
        self._grace_secs = grace_secs
        self._turn: _TurnState | None = None
        self._last_delta_at = 0.0
        self._last_delta_text = ""
        self._finalize_handle: asyncio.TimerHandle | None = None
        # Turn recovery. ``_deadline_handle`` bounds how long a turn may stay
        # open without progress; ``retry_hook`` re-runs a generation that
        # produced no text at all.
        self._deadline_handle: asyncio.TimerHandle | None = None
        self._retry_handle: asyncio.TimerHandle | None = None
        self._idle_timeout_secs = max(1.0, float(idle_timeout_secs))
        self._retry_attempts = max(0, int(retry_attempts))
        self._retry_base_delay = max(0.1, float(retry_base_delay))
        self._retry_max_delay = max(self._retry_base_delay, float(retry_max_delay))
        self._retry_hook = retry_hook
        self._lock = asyncio.Lock()
        self.degraded = False
        self.degraded_reason = ""

    # ------------------------------------------------------------------ utils

    async def _emit(self, event_type: str, **kwargs: Any) -> dict[str, Any] | None:
        try:
            envelope = await asyncio.to_thread(
                emit_event, self.voice_session_id, event_type, **kwargs
            )
        except Exception:
            # A journal write must never break audio.  The client can still
            # recover the turn from the next successful event or from replay.
            logger.warning(
                "voice journal emit failed (%s) for session %s",
                event_type,
                self.voice_session_id,
                exc_info=True,
            )
            return None
        if self._publish is not None:
            try:
                await self._publish(envelope)
            except Exception:
                logger.debug("voice journal publish failed", exc_info=True)
        return envelope

    def _cancel_finalize_timer(self) -> None:
        if self._finalize_handle is not None:
            self._finalize_handle.cancel()
            self._finalize_handle = None

    def _cancel_recovery_timers(self) -> None:
        self._cancel_finalize_timer()
        if self._deadline_handle is not None:
            self._deadline_handle.cancel()
            self._deadline_handle = None
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None

    def _arm_turn_deadline(self) -> None:
        """(Re)arm the idle ceiling for the open turn.

        Refreshed by every sign of progress, so it measures "stuck" rather than
        "slow": a long answer that keeps streaming or playing never trips it,
        while a provider error or a hang -- which produce no text frame, no TTS,
        and therefore no ``BotStoppedSpeakingFrame`` and no
        ``LLMFullResponseEndFrame`` -- is guaranteed to reach a terminal state.
        """
        if self._turn is None or self._turn.finalized:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._deadline_handle is not None:
            self._deadline_handle.cancel()
        self._deadline_handle = loop.call_later(
            self._idle_timeout_secs,
            lambda: loop.create_task(self._on_turn_idle()),
        )

    async def _on_turn_idle(self) -> None:
        self._deadline_handle = None
        state = self._turn
        if state is None or state.finalized:
            return
        # Progress happened after the timer was armed: re-arm instead of
        # finalizing a turn that is merely long.
        logger.warning(
            "voice turn %s went idle for %.0fs; finalizing",
            state.turn_id,
            self._idle_timeout_secs,
        )
        if state.assistant_text.strip():
            await self.assistant_completed(degraded_reason="turn_idle_timeout")
        else:
            await self.assistant_completed(
                outcome=turn_api.TURN_FAILED,
                failure_reason="turn_idle_timeout",
                degraded_reason="turn_idle_timeout",
            )

    def _touch_turn(self) -> None:
        """Progress signal: keep the open turn alive."""
        if self._turn is not None and not self._turn.finalized:
            self._arm_turn_deadline()

    def _cancel_retry(self) -> None:
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None

    # ------------------------------------------------------------- user side

    def _begin_turn(self, state: _TurnState) -> None:
        """Install the open turn and start bounding its lifetime.

        Every turn must reach a terminal state; arming here means the guarantee
        does not depend on which code path opened the turn.
        """
        self._turn = state
        self._arm_turn_deadline()

    async def user_started(self) -> None:
        # Barge-in: the user talking over the bot interrupts the open turn.
        if self._turn is not None and not self._turn.finalized:
            await self.turn_interrupted(reason="user_started_speaking")
        await self._emit(
            "user.started",
            payload={},
            turn_id=self._turn.turn_id if self._turn else None,
            phase="speculative",
        )

    async def user_stopped(self) -> None:
        """VAD says the user paused; not a turn boundary by itself.

        The authoritative boundary is the ASR final (``user_final``), because
        Smart Turn may still extend the turn.
        """
        return None

    async def user_interim(self, text: str) -> None:
        if not str(text or "").strip():
            return
        await self._emit(
            "user.interim",
            payload={"text": text},
            turn_id=self._turn.turn_id if self._turn else None,
            phase="speculative",
        )

    async def user_final(self, text: str, *, client_message_id: str | None = None) -> None:
        """A finalized ASR segment: merge into the open turn, never duplicate.

        Consecutive finals inside one turn are concatenated (the user paused
        mid-sentence); a final after the turn was finalized opens a new turn.
        """
        segment = str(text or "").strip()
        if not segment:
            return
        async with self._lock:
            if self._turn is not None and not self._turn.finalized:
                # Multi-segment ASR merge: one turn, one user message.
                state = self._turn
                state.user_text = (
                    f"{state.user_text}{segment}" if state.user_text else segment
                )
                await self._emit(
                    "user.final",
                    payload={
                        "text": state.user_text,
                        "segment": segment,
                        "merged": True,
                        "client_message_id": client_message_id,
                    },
                    turn_id=state.turn_id,
                    request_id=f"user.final:{state.turn_id}:{len(state.user_text)}",
                )
                return
            existing = await asyncio.to_thread(
                turn_api.open_turn, self.voice_session_id
            )
            if existing is not None:
                # The client pre-opened this turn (typed input).  Attach to it
                # instead of creating a parallel one.
                state = _TurnState(turn_id=str(existing["turn_id"]))
                state.user_text = str(existing.get("user_text") or segment)
                self._begin_turn(state)
                await self._emit(
                    "user.final",
                    payload={
                        "text": state.user_text,
                        "segment": segment,
                        "client_message_id": existing.get("client_message_id"),
                    },
                    turn_id=state.turn_id,
                    request_id=f"user.final:{state.turn_id}",
                )
                return
            try:
                accepted = await asyncio.to_thread(
                    turn_api.accept_turn,
                    self.voice_session_id,
                    segment,
                    client_message_id=client_message_id,
                )
            except Exception:
                logger.warning("voice turn accept failed", exc_info=True)
                return
        self._begin_turn(
            _TurnState(turn_id=str(accepted["turn_id"]), user_text=segment)
        )

    async def user_typed(self, text: str) -> None:
        """A typed message entered the pipeline directly (no ASR).

        Pipecat's ``send-text`` appends the message to the LLM context instead of
        running it through STT, so it must be journaled from the append frame.
        The turn is usually already open because the client posts an idempotent
        ``turns/accept`` first; when it is not, the worker opens one so the
        exchange still reaches the transcript.
        """
        content = str(text or "").strip()
        if not content:
            return
        async with self._lock:
            if self._turn is not None and not self._turn.finalized:
                if self._turn.user_text:
                    return
                self._turn.user_text = content
                return
            existing = await asyncio.to_thread(turn_api.open_turn, self.voice_session_id)
            if existing is not None:
                state = _TurnState(turn_id=str(existing["turn_id"]))
                state.user_text = str(existing.get("user_text") or content)
                self._begin_turn(state)
                return
            try:
                accepted = await asyncio.to_thread(
                    turn_api.accept_turn, self.voice_session_id, content
                )
            except Exception:
                logger.warning("typed voice turn accept failed", exc_info=True)
                return
        self._begin_turn(
            _TurnState(turn_id=str(accepted["turn_id"]), user_text=content)
        )

    # -------------------------------------------------------- assistant side

    def note_assistant_text(self, text: str) -> None:
        """Accumulate the authoritative assistant text (sync; no IO).

        This is the single source of the final message.  It is fed by the LLM's
        text frames, so it is independent of TTS markers: if the browser never
        receives a single caption marker, the finalized message is still
        complete and unique.
        """
        if not text:
            return
        if self._turn is None or self._turn.finalized:
            return
        self._turn.assistant_text += text
        self._touch_turn()

    async def assistant_delta(self, text: str, *, force: bool = False) -> None:
        """Coalesced ``assistant.llm.delta`` for the speculative caption layer."""
        if not text:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        if (
            not force
            and now - self._last_delta_at < LLM_DELTA_MIN_INTERVAL_SECS
        ):
            return
        self._last_delta_at = now
        self._last_delta_text = text
        await self._emit(
            "assistant.llm.delta",
            payload={"text": text},
            turn_id=self._turn.turn_id if self._turn else None,
            phase="speculative",
        )

    async def sentence_queued(self, text: str, *, audio_cursor_ms: int | None = None) -> None:
        """A sentence entered the TTS output queue (speculative caption)."""
        if self._turn is None or self._turn.finalized:
            return
        state = self._turn
        state.sentence_seq += 1
        if audio_cursor_ms is not None:
            state.audio_cursor_ms = max(state.audio_cursor_ms, int(audio_cursor_ms))
        self._touch_turn()
        await self._emit(
            "assistant.sentence.queued",
            payload={"text": text, "sentence_seq": state.sentence_seq},
            turn_id=state.turn_id,
            phase="speculative",
            audio_cursor_ms=state.audio_cursor_ms or None,
        )

    async def playback_started(self) -> None:
        self._touch_turn()
        await self._emit(
            "assistant.sentence.playback_started",
            payload={"sentence_seq": self._turn.sentence_seq if self._turn else 0},
            turn_id=self._turn.turn_id if self._turn else None,
            phase="speculative",
            audio_cursor_ms=self._turn.audio_cursor_ms if self._turn else None,
        )

    async def playback_ended(self) -> None:
        await self._emit(
            "assistant.sentence.playback_ended",
            payload={"sentence_seq": self._turn.sentence_seq if self._turn else 0},
            turn_id=self._turn.turn_id if self._turn else None,
            phase="speculative",
            audio_cursor_ms=self._turn.audio_cursor_ms if self._turn else None,
        )
        await self.assistant_completed()

    def llm_closed(self) -> None:
        """The LLM stopped producing text; schedule a finalize fallback.

        Normally ``BotStoppedSpeakingFrame`` finalizes the turn.  If TTS is down
        there will never be one, so the turn would hang forever and the user's
        utterance would be lost from the transcript.
        """
        if self._turn is not None:
            self._turn.llm_closed = True
        self._schedule_finalize_fallback()

    def _schedule_finalize_fallback(self) -> None:
        if self._finalize_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._finalize_handle = loop.call_later(
            self._grace_secs, lambda: loop.create_task(self._fallback_finalize())
        )

    async def _fallback_finalize(self) -> None:
        self._finalize_handle = None
        if self._turn is None or self._turn.finalized:
            return
        if not self._turn.llm_closed:
            return
        logger.info(
            "voice turn %s finalized by grace timer (no playback frames)",
            self._turn.turn_id,
        )
        await self.assistant_completed(
            degraded_reason="no_playback_frames"
        )

    async def assistant_completed(
        self,
        *,
        outcome: str = turn_api.TURN_FINALIZED,
        failure_reason: str | None = None,
        degraded_reason: str | None = None,
    ) -> None:
        """Finalize the open turn exactly once, from the authoritative text.

        ``outcome="failed"`` is the "no answer" path: the provider errored or the
        turn went idle with nothing to say. The user's question is still
        persisted (an unanswered turn must not vanish on refresh), no assistant
        message is written, and nothing reaches long-term memory.
        """
        async with self._lock:
            state = self._turn
            if state is None or state.finalized:
                return
            state.finalized = True
            self._cancel_recovery_timers()
        if degraded_reason:
            await self._emit(
                "processor.error",
                payload={
                    "stage": STAGE_LLM if outcome != turn_api.TURN_FINALIZED else STAGE_TTS,
                    "message": (
                        "模型未能生成回答，已保留你的问题，可以重试"
                        if outcome != turn_api.TURN_FINALIZED
                        else "音频未播报，已按文本收尾"
                    ),
                    "degraded": True,
                    "reason": degraded_reason,
                    "retryable": outcome != turn_api.TURN_FINALIZED,
                },
                turn_id=state.turn_id,
            )
        try:
            await asyncio.to_thread(
                turn_api.finalize_turn,
                self.voice_session_id,
                state.turn_id,
                state.assistant_text,
                audio_cursor_ms=state.audio_cursor_ms or None,
                outcome=outcome,
                failure_reason=failure_reason,
                # A failed turn has no answer: nothing to remember, and the user
                # never heard the text that was never produced.
                memory=outcome == turn_api.TURN_FINALIZED,
            )
        except Exception:
            logger.warning("voice turn finalize failed", exc_info=True)

    async def llm_failed(self, message: str) -> None:
        """Handle an LLM-stage failure for the open turn.

        Retrying is only safe when the failed generation produced **no** text:
        there is then no partial answer to duplicate, and the voice pipeline runs
        no tools, so re-running the request has no external side effect. When text
        did arrive, the answer is kept and the retry is skipped -- re-asking would
        duplicate what the user already heard.
        """
        state = self._turn
        if state is None or state.finalized:
            return
        if state.assistant_text.strip():
            await self.processor_error(
                STAGE_LLM,
                f"模型中断，已保留已生成内容：{message}",
                retryable=False,
            )
            self._touch_turn()
            return
        if self._retry_handle is not None:
            # The same failure can be reported twice: once by the pipeline tap
            # that sees the error frame travel past it, and again by the worker's
            # ``on_pipeline_error`` when that frame reaches the source. A retry is
            # already armed for this turn, so this report is a duplicate -- it
            # must not consume a second attempt.
            logger.debug(
                "duplicate llm failure report for turn %s ignored", state.turn_id
            )
            return
        state.retry_attempt += 1
        if state.retry_attempt > self._retry_attempts or self._retry_hook is None:
            await self.assistant_completed(
                outcome=turn_api.TURN_FAILED,
                failure_reason="llm_error",
                degraded_reason="llm_error",
            )
            return
        delay = backoff_delay(
            state.retry_attempt, base=self._retry_base_delay, cap=self._retry_max_delay
        )
        await self.retry_scheduled(
            STAGE_LLM,
            attempt=state.retry_attempt,
            delay_ms=int(delay * 1000),
            reason=message[:200],
        )
        # Re-arm the idle ceiling: the retry itself must also be bounded, so a
        # provider that hangs on every attempt still ends in a failed turn.
        self._touch_turn()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._cancel_retry()

        def _fire() -> None:
            self._retry_handle = None
            loop.create_task(self._run_retry())

        self._retry_handle = loop.call_later(delay, _fire)

    async def _run_retry(self) -> None:
        state = self._turn
        if state is None or state.finalized or self._retry_hook is None:
            return
        logger.info(
            "retrying voice generation for turn %s (attempt %d)",
            state.turn_id,
            state.retry_attempt,
        )
        try:
            await self._retry_hook()
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice generation retry failed: %s", exc)
            await self.assistant_completed(
                outcome=turn_api.TURN_FAILED,
                failure_reason="llm_error",
                degraded_reason="llm_error",
            )

    async def turn_interrupted(self, *, reason: str = "barge_in") -> None:
        """Abandon the open turn, keeping only what the user actually heard.

        The partial assistant text is written to the transcript so a refresh
        still shows what was said, but it is explicitly *not* offered to
        long-term memory: the user never heard the rest of it.
        """
        async with self._lock:
            state = self._turn
            if state is None or state.finalized:
                return
            state.finalized = True
            self._cancel_recovery_timers()
        await asyncio.to_thread(
            turn_api.interrupt_turn,
            self.voice_session_id,
            turn_id=state.turn_id,
            reason=reason,
        )
        if state.assistant_text.strip():
            try:
                await asyncio.to_thread(
                    turn_api.finalize_turn,
                    self.voice_session_id,
                    state.turn_id,
                    state.assistant_text,
                    memory=False,
                    audio_cursor_ms=state.audio_cursor_ms or None,
                )
            except Exception:
                logger.warning("interrupted voice turn persist failed", exc_info=True)

    # ------------------------------------------------------- errors/lifecycle

    async def processor_error(
        self,
        stage: str,
        message: str,
        *,
        retryable: bool,
        attempt: int = 0,
        degraded: bool = False,
    ) -> None:
        if degraded:
            self.degraded = True
            self.degraded_reason = message
        await self._emit(
            "processor.error",
            payload={
                "stage": stage,
                "message": message,
                "retryable": retryable,
                "attempt": attempt,
                "degraded": degraded,
            },
            turn_id=self._turn.turn_id if self._turn else None,
        )

    async def retry_scheduled(
        self, stage: str, *, attempt: int, delay_ms: int, reason: str
    ) -> None:
        await self._emit(
            "processor.retry_scheduled",
            payload={
                "stage": stage,
                "attempt": attempt,
                "delay_ms": delay_ms,
                "reason": reason,
            },
            turn_id=self._turn.turn_id if self._turn else None,
        )

    async def session_ready(self, *, epoch: int | None = None) -> None:
        await self._emit(
            "session.ready",
            payload={"epoch": epoch},
            session_epoch=epoch,
        )

    async def session_reconnecting(
        self,
        *,
        reason: str = "transport_rebuilt",
        attempt: int = 0,
        delay_ms: int = 0,
    ) -> None:
        """Announce that the call is being re-established.

        Emitted by the *worker* (server side) because it is the side that knows a
        pipeline is being rebuilt for an existing durable session. Without it the
        durable log shows a gap with no explanation, and a client that reloaded
        silently cannot tell a fresh dial from a recovery.
        """
        await self._emit(
            "session.reconnecting",
            payload={"reason": reason, "attempt": attempt, "delay_ms": delay_ms},
        )

    async def session_closed(self, *, reason: str) -> None:
        await self._emit(
            "session.closed",
            payload={"reason": reason},
            request_id=f"session.closed:{self.voice_session_id}",
        )

    async def context_updated(self, payload: dict[str, Any]) -> None:
        await self._emit("context.updated", payload=payload)


async def run_with_retry(
    journal: VoiceTurnJournal,
    stage: str,
    operation: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 4,
    base_delay: float = 0.5,
    cap: float = 8.0,
) -> tuple[bool, Any]:
    """Stage-scoped retry with backoff and jitter.

    Each failed attempt is journaled as ``processor.retry_scheduled`` so the UI
    can explain the pause instead of appearing frozen.  Exhausting the attempts
    reports ``processor.error`` and lets the caller decide between degradation
    and a fatal close; it never raises.
    """
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return True, await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            delay = backoff_delay(attempt, base=base_delay, cap=cap)
            await journal.retry_scheduled(
                stage,
                attempt=attempt,
                delay_ms=int(delay * 1000),
                reason=str(exc)[:200],
            )
            await asyncio.sleep(delay)
    await journal.processor_error(
        stage,
        f"{stage} 处理失败：{last_error}",
        retryable=True,
        attempt=attempts,
    )
    return False, last_error


class VoiceJournalProcessor(FrameProcessor):
    """Observation tap for one side of the pipeline.

    Insert two instances: one between STT and the user aggregator (``role="user"``)
    and one between the bus bridge and TTS (``role="assistant"``).  Both are pure
    pass-through — they only read frames — so inserting them cannot change the
    audio path, and the frame ordering the pipeline already guarantees decides
    what each tap sees.
    """

    def __init__(self, journal: VoiceTurnJournal, *, role: str = "assistant") -> None:
        super().__init__()
        self._journal = journal
        self._role = role

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        try:
            if self._role == "user":
                await self._observe_user(frame, direction)
            else:
                await self._observe_assistant(frame, direction)
        except Exception:
            logger.debug("voice journal observation failed", exc_info=True)
        await self.push_frame(frame, direction)

    async def _observe_user(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, InterimTranscriptionFrame):
            await self._journal.user_interim(frame.text)
        elif isinstance(frame, TranscriptionFrame):
            await self._journal.user_final(frame.text)
        elif isinstance(frame, UserStartedSpeakingFrame):
            await self._journal.user_started()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            await self._journal.user_stopped()

    async def _observe_assistant(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, LLMMessagesAppendFrame):
            for message in frame.messages or ():
                if isinstance(message, dict) and message.get("role") == "user":
                    await self._journal.user_typed(str(message.get("content") or ""))
            return
        if isinstance(frame, TextFrame) and not isinstance(
            frame, (TranscriptionFrame, InterimTranscriptionFrame)
        ):
            # Downstream only: the upstream copy is the echo of a spoken frame.
            if direction == FrameDirection.DOWNSTREAM:
                self._journal.note_assistant_text(frame.text)
                await self._journal.assistant_delta(frame.text)
            return
        if isinstance(frame, LLMFullResponseEndFrame):
            self._journal.llm_closed()
            return
        if isinstance(frame, BotStartedSpeakingFrame):
            await self._journal.playback_started()
            return
        if isinstance(frame, BotStoppedSpeakingFrame):
            await self._journal.playback_ended()
            return
        if isinstance(frame, InterruptionFrame):
            await self._journal.turn_interrupted(reason="barge_in")
            return
        if isinstance(frame, ErrorFrame):
            processor = getattr(frame, "processor", None)
            stage = _stage_for_processor(processor)
            message = str(getattr(frame, "error", "") or "processor error")
            if stage == STAGE_LLM:
                # Route LLM failures through the retry/recovery path instead of
                # only reporting them: an error that produced no text would
                # otherwise leave the turn open forever and lose the question.
                await self._journal.llm_failed(message)
                return
            await self._journal.processor_error(
                stage,
                message,
                retryable=not bool(getattr(frame, "fatal", False)),
            )


def _stage_for_processor(processor: Any) -> str:
    name = type(processor).__name__.lower() if processor is not None else ""
    if "stt" in name or "asr" in name:
        return STAGE_ASR
    if "tts" in name:
        return STAGE_TTS
    if "llm" in name:
        return STAGE_LLM
    return STAGE_NETWORK


@dataclass
class VoiceControlWatchdog:
    """Polls the durable control channel on behalf of the audio worker.

    This is what makes an interrupt, a model switch or a hang-up *effective*
    even when the HTTP request lands on a different worker than the one running
    the pipeline.  It is also the durability check: a session that reaches
    ``ended`` for any reason is closed here, so no worker is left synthesising
    audio for a call nobody is on.
    """

    voice_session_id: str
    on_interrupt: Callable[[dict[str, Any]], Awaitable[None]]
    on_model_changed: Callable[[dict[str, Any]], Awaitable[None]]
    on_close: Callable[[str], Awaitable[None]]
    interval_secs: float = 1.0
    _cursor: int = 0
    _task: asyncio.Task[Any] | None = field(default=None, init=False)
    _stopped: bool = field(default=False, init=False)

    async def _initial_cursor(self) -> None:
        try:
            self._cursor = await asyncio.to_thread(last_event_seq, self.voice_session_id)
        except Exception:
            self._cursor = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def run(self) -> None:
        await self._initial_cursor()
        while not self._stopped:
            try:
                await asyncio.sleep(self.interval_secs)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("voice control watchdog tick failed", exc_info=True)

    async def tick(self) -> None:
        events = await asyncio.to_thread(
            _read_control, self.voice_session_id, self._cursor
        )
        for event in events:
            self._cursor = max(self._cursor, int(event.get("event_seq") or 0))
            event_type = event.get("type")
            if event_type == "turn.interrupted":
                await self.on_interrupt(event)
            elif event_type == "context.updated":
                await self.on_model_changed(event)
            elif event_type == "session.closed":
                await self.on_close("session.closed")
                return
        session = await asyncio.to_thread(load_session, self.voice_session_id)
        if session is None:
            await self.on_close("session_missing")
            return
        if not session.active:
            # Another worker (or a reaper) ended the call: close locally.
            await self.on_close("session_ended_elsewhere")


def _read_control(voice_session_id: str, cursor: int) -> list[dict[str, Any]]:
    from app.voice.events import read_control_events

    return read_control_events(voice_session_id, cursor)


def journal_observers(
    journal: VoiceTurnJournal,
) -> Iterable[tuple[str, VoiceJournalProcessor]]:
    """The two taps to insert into the pipeline, with their roles."""
    return (
        ("user", VoiceJournalProcessor(journal, role="user")),
        ("assistant", VoiceJournalProcessor(journal, role="assistant")),
    )
