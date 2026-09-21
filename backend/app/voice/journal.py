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
import time
from dataclasses import dataclass, field
from uuid import uuid4
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
# How many consecutive idle ceilings may be skipped while audio is still playing.
# Six re-arms is two minutes of continuous speech, which is far past any real
# answer; past that we assume the end-of-playback signal was lost and finalize.
MAX_PLAYBACK_REARMS = 6
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY = 1.0
DEFAULT_RETRY_MAX_DELAY = 8.0
# ``assistant.llm.delta`` is a caption stream, not an audit log: coalesce writes.
LLM_DELTA_MIN_INTERVAL_SECS = 0.4
# A turn opened by the control plane that no worker ever acknowledged is closed
# by the aging sweep after this long (see ``reconcile_stale_turns``). Generous on
# purpose: it must never race a slow-but-live provider round trip.
DEFAULT_STALE_TURN_SECS = 180.0
# How often the audio worker re-runs that sweep for its own session.
DEFAULT_RECONCILE_INTERVAL_SECS = 15.0
# How long a client-announced typed idempotency key stays usable. The client
# sends it immediately before ``send-text``; the window only has to absorb the
# RTVI message task hand-off, not a network round trip.
TYPED_KEY_TTL_SECS = 20.0
# RTVI custom client message: ``{t: VOICE_TYPED_TURN_MESSAGE, d: {...}}``.
# ``send-text`` itself cannot carry the client's idempotency key (Pipecat's
# ``SendTextData`` has no such field and the append frame it produces only holds
# role/content), so the client announces it on this channel first.
VOICE_TYPED_TURN_MESSAGE = "learngraph-typed-turn"
# 客户端播放确认的 phase 取值（契约 §2.5）。``ended`` 表示该句已经播完，剩下的
# 都是"播到哪里"的位置报告。
PLAYBACK_PHASE_PROGRESS = "progress"
PLAYBACK_PHASE_ENDED = "ended"


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
    # Provider final identity (DashScope item_id/commit_id). Re-delivered
    # terminal ASR events must not append the same text a second time.
    seen_user_final_ids: set[str] = field(default_factory=set)
    assistant_text: str = ""
    sentence_seq: int = 0
    sentence_texts: dict[int, str] = field(default_factory=dict)
    # 每句的媒体窗口（句首入队游标 / 句尾结束游标）与它所属的 audio context。
    # 保留它们是为了把客户端 playback_ack 的媒体位置换算成"这一句听到了第几个
    # 字"：句内位置只能是估算（见 ``_played_chars``），但没有窗口就完全无法定位。
    sentence_start_cursor_ms: dict[int, int] = field(default_factory=dict)
    sentence_end_cursor_ms: dict[int, int] = field(default_factory=dict)
    sentence_contexts: dict[int, str] = field(default_factory=dict)
    # 由 playback_ack 填充：key 为 sentence_seq，value 为该句已播报的字符数。
    # 缺失的 key 表示"从未收到过确认"，那一句按已入队全文计（老客户端兜底）。
    sentence_heard_chars: dict[int, int] = field(default_factory=dict)
    # 是否至少收到过一条 playback_ack；只用于在 turn 载荷里标注诚实程度。
    acknowledged: bool = False
    playback_observed: bool = False
    # True between BotStartedSpeaking and BotStoppedSpeaking. Distinct from
    # ``playback_observed`` (which latches): this one says audio is being heard
    # *right now*, which is what stops the idle ceiling from firing mid-answer.
    playback_active: bool = False
    # Consecutive idle re-arms granted while audio was still playing, so a
    # playback that never reports its end cannot hold the turn open forever.
    playback_rearms: int = 0
    # 本回合的播放锚点（``time.monotonic()`` 秒）：第一个 BotStartedSpeakingFrame
    # 到达的时刻，也就是输出传输开始按实时节奏写音频、媒体时钟起走的原点。
    # 逐句文本的投递排期全靠它：第 N 句的音频在 ``anchor + sentence_start_cursor_ms``
    # 出声，文本也只能到那时才下发——"未读文本不进前端、不入库"由此成为结构保证
    # （见 ``reserve_sentence_seq`` / ``playback_anchor_at``）。None = 还没开始播放。
    playback_anchor_at: float | None = None
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
        stale_turn_secs: float = DEFAULT_STALE_TURN_SECS,
        reconcile_interval_secs: float = DEFAULT_RECONCILE_INTERVAL_SECS,
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
        # Aging sweep for turns this worker never picked up (see
        # ``reconcile_stale_turns``): the ceiling, the throttle between rounds,
        # and the idempotency keys the client announced for typed utterances.
        self._stale_turn_secs = max(0.0, float(stale_turn_secs))
        self._reconcile_interval_secs = max(1.0, float(reconcile_interval_secs))
        self._last_reconcile_at = 0.0
        self._pending_typed: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        # Transcript/message writes are offloaded from the audio event loop and
        # serialized per journal.  The gate prevents an interrupt finalization
        # racing a normal playback finalization for the same turn while the
        # actual synchronous ChatService/SQL work stays off-loop.
        self._persistence_gate = asyncio.Semaphore(1)
        # Pipecat's assistant aggregator can append the complete LLM draft to
        # its in-memory context while handling InterruptionFrame.  The
        # downstream context guard consumes this tuple and replaces that draft
        # with the audio prefix that actually crossed the TTS start marker.
        self._playback_context_hook: Callable[[str, str], None] | None = None
        self._write_tail: asyncio.Task | None = None
        self._user_text_hook: Callable[[str], None] | None = None
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

    async def _persist_offloop(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run synchronous turn/message persistence off the audio event loop."""

        async with self._persistence_gate:
            return await asyncio.to_thread(lambda: func(*args, **kwargs))

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
        if state.playback_active:
            # Audio is still being played, so the turn IS making progress even
            # though no event has been emitted for a while -- a single sentence
            # can take longer than the idle ceiling to speak, and the only
            # progress signals we emit are per sentence.  Finalizing here marked
            # a perfectly good answer ``turn_idle_timeout``/degraded about a
            # second before its audio actually finished, which also dropped the
            # exchange from long-term memory.
            #
            # Bounded, so a playback that never reports its end still reaches a
            # terminal state instead of hanging the turn forever.
            if state.playback_rearms < MAX_PLAYBACK_REARMS:
                state.playback_rearms += 1
                logger.debug(
                    "voice turn %s idle for %.0fs but still playing; re-arming (%d/%d)",
                    state.turn_id,
                    self._idle_timeout_secs,
                    state.playback_rearms,
                    MAX_PLAYBACK_REARMS,
                )
                self._arm_turn_deadline()
                return
            logger.warning(
                "voice turn %s still reports playback after %d idle re-arms; finalizing",
                state.turn_id,
                state.playback_rearms,
            )
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
        # Single funnel for "the user has said this": whichever path opened the
        # turn (ASR, typed input, attaching to a client-opened turn), this is the
        # earliest moment the words exist. The audio worker starts the turn's
        # memory recall from here so the retrieval overlaps the turn decision
        # instead of running after it.
        self._note_user_text(state.user_text)

    def set_user_text_hook(self, hook: Callable[[str], None] | None) -> None:
        """Observe what the user has said so far, as soon as it is known.

        A hook must not block and must not raise: it runs inside the journal's
        lock on the audio event loop. Used for per-turn memory recall
        (``app/voice/embedded_memory.py``).
        """
        self._user_text_hook = hook

    def _note_user_text(self, text: str) -> None:
        hook = self._user_text_hook
        value = str(text or "").strip()
        if hook is None or not value:
            return
        try:
            hook(value)
        except Exception:
            logger.warning("voice journal user-text hook failed", exc_info=True)

    async def user_started(self) -> None:
        """VAD signals that the user started speaking."""
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
        await self._playback_event(
            "user.interim",
            payload={"text": text},
            turn_id=(self._turn.turn_id if self._turn and not self._turn.finalized
                     and not self._turn.assistant_text and not self._turn.sentence_texts else None),
            phase="speculative",
        )

    async def user_final(
        self,
        text: str,
        *,
        client_message_id: str | None = None,
        source_id: str | None = None,
    ) -> None:
        """A finalized ASR segment: merge into the open turn, never duplicate.

        Consecutive finals inside one turn are concatenated (the user paused
        mid-sentence); a final after the turn was finalized opens a new turn.
        """
        segment = str(text or "").strip()
        if not segment:
            return
        barge_in = False
        identity = str(source_id or "").strip()
        async with self._lock:
            if self._turn is not None and not self._turn.finalized:
                if identity and identity in self._turn.seen_user_final_ids:
                    return
                if identity:
                    self._turn.seen_user_final_ids.add(identity)
                if (
                    not self._turn.assistant_text
                    and not self._turn.sentence_seq
                    and not self._turn.playback_observed
                ):
                    # Multi-segment ASR merge: one turn, one user message (user paused mid-sentence).
                    state = self._turn
                    state.user_text = (state.user_text + (" " if state.user_text[-1:].isascii() and state.user_text[-1:].isalnum() and segment[:1].isascii() and segment[:1].isalnum() else "") + segment)
                    # The recall started for the first segment; restart it for the
                    # longer utterance so the query is the whole question.
                    self._note_user_text(state.user_text)
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
                    await self._publish_live(
                        "user.final", turn_id=state.turn_id,
                        payload={"text": state.user_text, "client_message_id": client_message_id},
                    )
                    return
                # The assistant has already begun generating or speaking: this new
                # utterance is a new turn (barge-in), NOT a continuation of the
                # previous question.  Concat here was the root cause of
                # "ASR混入上一句已经结束的显示".
                #
                # The finalize itself runs *below*, outside the lock.  It has to:
                # ``turn_interrupted`` takes this very lock, ``asyncio.Lock`` is not
                # reentrant, and calling it from inside a locked section wedges the
                # journal permanently -- no exception, no warning, and every later
                # event (user and assistant alike) is silently lost while the audio
                # pipeline keeps running normally.  That is the "用一会儿语音就又
                # 没反应了" bug: ASR keeps producing finals, the ledger stops dead.
                barge_in = True
        if barge_in:
            await self.turn_interrupted(reason="barge_in")
        await self._open_turn_for_final(
            segment,
            client_message_id=client_message_id,
            source_id=identity,
        )

    async def _open_turn_for_final(
        self,
        segment: str,
        *,
        client_message_id: str | None = None,
        source_id: str | None = None,
    ) -> None:
        """Open (or attach to) the turn that owns this ASR segment.

        Split out of ``user_final`` so the barge-in finalize can run without
        holding the lock (see there).  Because the lock is released in between,
        the open-turn check is repeated here: a concurrent final must be merged
        into the turn it belongs to rather than overwritten by ``_begin_turn``.
        """
        async with self._lock:
            if self._turn is not None and not self._turn.finalized:
                state = self._turn
                identity = str(source_id or "").strip()
                if identity and identity in state.seen_user_final_ids:
                    return
                if identity:
                    state.seen_user_final_ids.add(identity)
                state.user_text = (state.user_text + (" " if state.user_text[-1:].isascii() and state.user_text[-1:].isalnum() and segment[:1].isascii() and segment[:1].isalnum() else "") + segment)
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
                await self._publish_live(
                    "user.final", turn_id=state.turn_id,
                    payload={"text": state.user_text, "client_message_id": client_message_id},
                )
                return
            existing = await asyncio.to_thread(
                turn_api.open_turn, self.voice_session_id
            )
            if (existing is not None and self._turn is not None
                    and self._turn.finalized and existing["turn_id"] == self._turn.turn_id):
                existing = None
            if existing is not None:
                # The client pre-opened this turn (typed input).  Attach to it
                # instead of creating a parallel one.
                state = _TurnState(turn_id=str(existing["turn_id"]))
                state.user_text = str(existing.get("user_text") or segment)
                identity = str(source_id or "").strip()
                if identity:
                    state.seen_user_final_ids.add(identity)
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
        identity = str(source_id or "").strip()
        state = _TurnState(turn_id=str(accepted["turn_id"]), user_text=segment)
        if identity:
            state.seen_user_final_ids.add(identity)
        self._begin_turn(state)
        await self._publish_live(
            "user.final", turn_id=state.turn_id,
            payload={"text": state.user_text, "client_message_id": client_message_id},
        )

    async def user_typed(self, text: str) -> None:
        """A typed message entered the pipeline directly (no ASR).

        Pipecat's ``send-text`` appends the message to the LLM context instead of
        running it through STT, so it must be journaled from the append frame.

        Identity comes first: when the client told us which idempotency key it
        used (``expect_typed_turn``, carried over RTVI ahead of ``send-text``),
        ``accept_turn`` is called with that key, so the row is the *same* one the
        control plane's ``turns/accept`` created -- whichever of the two lands
        first, one utterance can never become two turns (and two chat messages).
        Without a key the worker attaches to the turn the control plane already
        opened, and only opens one itself as a last resort.
        """
        content = str(text or "").strip()
        if not content:
            return
        async with self._lock:
            if self._turn is not None and not self._turn.finalized:
                if self._turn.user_text:
                    return
                self._turn.user_text = content
                # Typed input recalls memory too, and this branch bypasses
                # ``_begin_turn`` -- it is the one place a turn's words are set
                # without going through the funnel.
                self._note_user_text(content)
                return
            key = self._take_typed_client_message_id(content)
            if key:
                try:
                    accepted = await asyncio.to_thread(
                        turn_api.accept_turn,
                        self.voice_session_id,
                        content,
                        client_message_id=key,
                    )
                except Exception:
                    logger.warning("typed voice turn accept failed", exc_info=True)
                    return
                self._begin_turn(
                    _TurnState(turn_id=str(accepted["turn_id"]), user_text=content)
                )
                return
            existing = await asyncio.to_thread(turn_api.open_turn, self.voice_session_id)
            if (existing is not None and self._turn is not None
                    and self._turn.finalized and existing["turn_id"] == self._turn.turn_id):
                existing = None
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

    def expect_typed_turn(self, client_message_id: str, text: str = "") -> None:
        """Remember the idempotency key the client is about to type with.

        Called from the RTVI client-message channel, which the browser writes
        *before* ``send-text`` on the same ordered data channel. The key is what
        makes the worker's ``accept_turn`` land on the row the control plane's
        ``turns/accept`` created instead of opening a second turn for one
        utterance.

        A hint, never an identity: the turn is identified by the key itself (and
        by the database's per-key uniqueness), and the text is only used to pair
        the announcement with the append frame that follows it -- the two arrive
        on the same channel but are handled by separate tasks, so their order is
        not something to depend on.
        """
        key = str(client_message_id or "").strip()
        if not key:
            return
        fingerprint = str(text or "").strip()
        self._pending_typed[fingerprint] = (key, asyncio.get_running_loop().time())
        # Bounded: a client that announces keys it never uses must not grow this.
        if len(self._pending_typed) > 8:
            oldest = sorted(self._pending_typed.items(), key=lambda item: item[1][1])
            for stale_key, _ in oldest[: len(self._pending_typed) - 8]:
                self._pending_typed.pop(stale_key, None)

    def _take_typed_client_message_id(self, content: str) -> str | None:
        """Consume the announced key for this typed text, if one is still fresh."""
        now = asyncio.get_running_loop().time()
        for fingerprint in (content, ""):
            entry = self._pending_typed.pop(fingerprint, None)
            if entry is not None:
                key, announced_at = entry
                if now - announced_at <= TYPED_KEY_TTL_SECS:
                    return key
                return None
        # No announcement for this text: drop anything that has expired so a
        # later, unrelated utterance cannot inherit a stale key.
        for fingerprint, (_, announced_at) in list(self._pending_typed.items()):
            if now - announced_at > TYPED_KEY_TTL_SECS:
                self._pending_typed.pop(fingerprint, None)
        return None

    async def reconcile_stale_turns(self, *, force: bool = False) -> int:
        """Close turns of this session that nobody ever picked up.

        The control plane creates the turn *before* the worker sees the
        utterance, so the row exists even when nothing else in the pipeline ever
        acknowledges it. Without this sweep such a row stays ``accepted`` forever:
        the page waits for a ``turn.finalized`` that never comes, and the question
        is never persisted or remembered.

        Throttled because it runs from the 1 Hz control watchdog, and the worker's
        own open turn is skipped -- its idle ceiling is the right closer, and
        closing it here would bypass the state that holds the answer.
        """
        if self._stale_turn_secs <= 0:
            return 0
        loop = asyncio.get_running_loop()
        now = loop.time()
        if not force and now - self._last_reconcile_at < self._reconcile_interval_secs:
            return 0
        self._last_reconcile_at = now
        skip = [self._turn.turn_id] if self._turn is not None else []
        try:
            closed = await self._persist_offloop(
                turn_api.finalize_stale_turns,
                self.voice_session_id,
                max_age_secs=self._stale_turn_secs,
                skip_turn_ids=skip,
            )
        except Exception:
            logger.warning("voice stale turn sweep failed", exc_info=True)
            return 0
        return len(closed)

    async def close_open_turns_on_session_end(self) -> int:
        """Settle everything still open when the call ends.

        The worker's own turn is finalized through the normal path (what was
        spoken is the answer; a turn with nothing spoken is a ``failed`` turn that
        keeps the question). Any other open row belongs to a turn this worker
        never picked up, so it is closed as a failure too. Called *before* the
        runner is cancelled -- afterwards there is no loop left to emit from.
        """
        state = self._turn
        spoken = self._spoken_assistant_text(state) if state is not None else ""
        await self.assistant_completed(
            outcome=turn_api.TURN_FINALIZED if spoken else turn_api.TURN_FAILED,
            failure_reason=None if spoken else turn_api.SESSION_CLOSED_TURN_REASON,
        )
        await self.drain_persistence()
        try:
            closed = await asyncio.to_thread(
                turn_api.finalize_stale_turns,
                self.voice_session_id,
                max_age_secs=0.0,
                reason=turn_api.SESSION_CLOSED_TURN_REASON,
            )
        except Exception:
            logger.warning("voice session-end turn sweep failed", exc_info=True)
            return 0
        return len(closed)

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

    def current_turn_id(self) -> str | None:
        """当前未 finalize 的持久 turn id，没有则 None。同步、无 IO。

        TTS 适配器需要用这个 id 给快通道 marker 打身份：Pipecat 的
        ``context_id`` 只是音频 context 的 uuid，把 marker 的 ``turn_id`` 填成它
        会让字幕落到另一个回合（F03/F04 的根因）。这里读的就是账本自己认的那条
        turn，因此 marker 与持久事件永远指向同一个回合。
        """
        state = self._turn
        if state is None or state.finalized:
            return None
        return state.turn_id

    async def sentence_queued(
        self,
        text: str,
        *,
        audio_cursor_ms: int | None = None,
        context_id: str | None = None,
        generation_id: int | None = None,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        """A sentence's audio entered playback; it is being read aloud now.

        This is the marker the page draws a sentence from, and the journal's only
        record of what the user actually heard -- see ``_spoken_assistant_text``.

        句身份（``sentence_seq`` / ``segment_id``）由账本统一分配并原样返回，
        快通道 marker 必须用这一份：以前 TTS 适配器自己数一套、账本另数一套，
        于是同一句话在两个通道里序号不同，字幕与持久事件永远对不上（F02/F04/F05
        的共同根因）。

        **调用时刻就是"这一句开始播放"的时刻**（逐句字幕方案 B）：TTS 适配器先用
        :meth:`reserve_sentence_seq` 预约序号（句尾 marker 要复用它），等这一句真的
        开始出声时才带 ``sequence=`` 调这里记账。于是"没被读到的文字不进前端、不进
        数据库"是结构保证而不是事后裁切：没走到这一步的句子根本不在账本里。
        """
        if self._turn is None or self._turn.finalized:
            return {}
        state = self._turn
        if sequence is None:
            state.sentence_seq += 1
            sequence = state.sentence_seq
        else:
            sequence = int(sequence)
            if sequence <= 0:
                return {}
            if sequence in state.sentence_texts:
                # 同序号重复投递（重试 / 快通道与持久通道都到）：身份原样返回，
                # 不重复记账、不重复发事件。
                return {
                    "turn_id": state.turn_id,
                    "sentence_seq": sequence,
                    "segment_id": f"{state.turn_id}:s{sequence}",
                    "generation_id": generation_id,
                    "audio_cursor_ms": state.audio_cursor_ms,
                }
            state.sentence_seq = max(state.sentence_seq, sequence)
        # Kept verbatim: two adjacent sentences have to concatenate back into the
        # text that was spoken, including any separator whitespace the provider
        # put between them (English answers would lose it otherwise).
        state.sentence_texts[sequence] = str(text or "")
        segment_id = f"{state.turn_id}:s{sequence}"
        if audio_cursor_ms is not None:
            cursor = int(audio_cursor_ms)
            state.sentence_start_cursor_ms[sequence] = cursor
            # 信封沿用"回合内累计最大值"语义：乱序/重复的 marker 不得让游标倒退。
            state.audio_cursor_ms = max(state.audio_cursor_ms, cursor)
        if context_id is not None:
            state.sentence_contexts[sequence] = str(context_id)
        self._update_playback_context(state, self._spoken_assistant_text(state))
        self._touch_turn()
        await self._playback_event(
            "assistant.sentence.queued",
            payload={
                "text": text,
                "sentence_seq": sequence,
                "segment_id": segment_id,
                "generation_id": generation_id,
                "context_id": context_id,
            },
            turn_id=state.turn_id,
            phase="speculative",
            audio_cursor_ms=state.audio_cursor_ms or None,
            generation_id=generation_id,
            segment_id=segment_id,
            context_id=context_id,
        )
        return {
            "turn_id": state.turn_id,
            "sentence_seq": sequence,
            "segment_id": segment_id,
            "generation_id": generation_id,
            "audio_cursor_ms": state.audio_cursor_ms,
        }

    async def sentence_ended(
        self,
        *,
        sentence_seq: int,
        audio_end_cursor_ms: int,
        segment_id: str | None = None,
        generation_id: int | None = None,
    ) -> None:
        """落一条 ``assistant.sentence.ended``：这一句的音频已经收尾。

        句尾游标是句内位置估算的分母（见 ``_played_chars``），也是"这一句到底说
        完了没有"的唯一服务端证据；它必须落进账本，否则打断时只能靠"已入队即已
        听到"这个偏乐观的假设。
        """
        state = self._turn
        if state is None or state.finalized:
            return
        sequence = int(sentence_seq)
        cursor = max(0, int(audio_end_cursor_ms))
        state.sentence_end_cursor_ms[sequence] = cursor
        resolved_segment = segment_id or f"{state.turn_id}:s{sequence}"
        # 信封的 ``audio_cursor_ms`` 保持"句首入队游标的回合内最大值"语义，句尾
        # 游标只出现在 payload 里：混进同一个字段会让回放游标在两个意义上跳。
        await self._playback_event(
            "assistant.sentence.ended",
            payload={
                "text": state.sentence_texts.get(sequence, ""),
                "sentence_seq": sequence,
                "segment_id": resolved_segment,
                "generation_id": generation_id,
                "context_id": state.sentence_contexts.get(sequence),
                "audio_end_cursor_ms": cursor,
            },
            turn_id=state.turn_id,
            phase="speculative",
            audio_cursor_ms=state.audio_cursor_ms or None,
            generation_id=generation_id,
            segment_id=resolved_segment,
            context_id=state.sentence_contexts.get(sequence),
        )

    async def playback_ack(
        self,
        *,
        sentence_seq: int | None,
        played_ms: int,
        phase: str,
        generation_id: int | None = None,
    ) -> None:
        """浏览器报告"已经出声播到哪里了"。

        只用于回放位置、逐句展示精度与审计：**它绝不参与回合的终态判定**（既不
        收尾也不续命）。客户端时钟会受设备、后台标签页与浏览器省电策略影响，
        把它当成完成判据会把"用户没听到"变成"系统认为说完了"。

        记录下来的字符数只用于把已听到的句子截成前缀，缺 ack 的句子仍按已入队
        全文计，所以一条丢失或迟到的确认不会让整段回答消失。
        """
        state = self._turn
        if state is None or state.finalized:
            return
        sequence = None if sentence_seq is None else int(sentence_seq)
        played = max(0, int(played_ms))
        if sequence is not None:
            chars = self._played_chars(state, sequence, played, str(phase or ""))
            if chars is not None:
                state.sentence_heard_chars[sequence] = max(
                    state.sentence_heard_chars.get(sequence, 0), chars
                )
        state.acknowledged = True
        await self._emit(
            "assistant.playback.ack",
            payload={
                "sentence_seq": sequence,
                "played_ms": played,
                "phase": str(phase or ""),
            },
            turn_id=state.turn_id,
            phase="speculative",
            generation_id=generation_id,
        )

    def heard_text(self) -> str:
        """当前回合计为"用户已听到"的文本。

        与 ``_spoken_assistant_text`` 同源，供 worker 在 finalize 之前对外说明
        "到底说出去多少"（例如折叠未播完的余量）。
        """
        state = self._turn
        if state is None:
            return ""
        return self._spoken_assistant_text(state)

    def set_playback_context_hook(self, hook: Callable[[str, str], None]) -> None:
        self._playback_context_hook = hook

    def _update_playback_context(self, state: _TurnState, text: str) -> None:
        if self._playback_context_hook is not None:
            self._playback_context_hook(state.turn_id, text)

    def _enqueue_write(self, operation: Callable[[], Awaitable[Any]]) -> None:
        """One FIFO writer per call; audio callbacks never wait on SQL or locks."""
        previous = self._write_tail

        async def write() -> None:
            if previous is not None:
                await asyncio.shield(previous)
            try:
                await operation()
            except Exception:
                logger.exception("voice persistence failed for %s", self.voice_session_id)

        self._write_tail = asyncio.create_task(write(), name="voice-journal-write")

    async def drain_persistence(self) -> None:
        """Await pending writes at teardown/testing, never in the audio path."""
        if self._write_tail is not None:
            await asyncio.shield(self._write_tail)

    async def _publish_live(self, event_type: str, **kwargs: Any) -> None:
        if self._publish is not None:
            try:
                await self._publish({
                    "type": event_type, "delivery": "live",
                    "session_id": self.voice_session_id,
                    **kwargs,
                })
            except Exception:
                logger.exception("voice live event publish failed")

    async def _playback_event(self, event_type: str, **kwargs: Any) -> None:
        # Semantic identity is (turn_id, sentence_seq). The live and durable
        # copies fold into the same UI segment; event_seq remains DB-owned.
        kwargs["payload"] = {**kwargs.get("payload", {}), "live_event_id": uuid4().hex}
        await self._publish_live(event_type, **kwargs)
        self._enqueue_write(lambda: self._emit(event_type, **kwargs))

    def assistant_reply_in_flight(self) -> bool:
        """用户这句话已经有归属、助手还欠一个回答时为真（= 本回合已开且未终结）。

        给背声词门闩（``DashScopeSTTService._is_backchannel_filler``）用。只要助手
        还在作答——刚收到问题、正在生成、已经在播——用户的「嗯。」就是在回应，不是
        一次发言；反过来，回合落定之后的「嗯」很可能**就是**回答（"听懂了吗？"→
        "嗯"），必须放行。

        实现只看"回合是否已开且未终结"，而不是"音频是否在播"：从 LLM 出第一个字到
        第一帧音频真的写出去之间有一到几秒（实测 1~3s），而那正是用户最容易应一声
        「嗯」的空档；只盯 ``BotStartedSpeakingFrame`` 会把它整段漏掉。

        本方法只读一个快照、不加锁：判据是"回合开着吗"，偶发的一帧偏差只会让门闩
        宽/窄一点点，而为了它去抢账本锁会把 ASR 读循环拖进死锁风险。
        """
        state = self._turn
        return state is not None and not state.finalized

    async def playback_started(self) -> None:
        state = self._turn
        if state is not None and not state.finalized:
            state.playback_observed = True
            state.playback_active = True
            state.playback_rearms = 0
            if state.playback_anchor_at is None:
                # 本回合第一次"开始说话"的沿 = 媒体时钟原点。之后的重复沿（同一
                # 回合里输出队列排空又续上）不得覆盖它，否则排期会整体前移。
                state.playback_anchor_at = time.monotonic()
        self._touch_turn()
        await self._emit(
            "assistant.sentence.playback_started",
            payload={"sentence_seq": self._turn.sentence_seq if self._turn else 0},
            turn_id=self._turn.turn_id if self._turn else None,
            phase="speculative",
            audio_cursor_ms=self._turn.audio_cursor_ms if self._turn else None,
        )

    def playback_anchor_at(self) -> float | None:
        """本回合播放锚点（``time.monotonic()`` 秒），None = 音频还没开始播放。

        TTS 适配器用它把「句首/句尾媒体游标」换算成"应当在什么时刻出声"，从而把
        文本投递排期到那一刻（逐句字幕方案 B）。
        """
        state = self._turn
        if state is None or state.finalized:
            return None
        return state.playback_anchor_at

    def open_turn_id(self) -> str | None:
        """当前未 finalize 的回合 id；没有可用回合时返回 None。"""
        state = self._turn
        if state is None or state.finalized:
            return None
        return state.turn_id

    def sentence_recorded(self, sequence: int) -> bool:
        """这一句是否已经进入账本（= 它真的开始播放过）。

        句尾 marker 的投递用它做前置条件：起点都被账本拒绝的句子（回合已收尾 / 从未
        开始播放），句尾也就无从谈起——否则一条"未读句子的结束标记"仍会跑到前端去。
        """
        state = self._turn
        if state is None:
            return False
        return int(sequence) in state.sentence_texts

    def reserve_sentence_seq(self) -> int | None:
        """预分配句序号，但**不记账**（不写 ``sentence_texts``、不发事件）。

        逐句字幕方案 B 要求把「句身份分配」与「记账」分开：

        * 序号必须在句音频入队时就定下来——句尾 marker 要复用它，而且序号必须与
          播放顺序一致；
        * 但句子的**文本**只允许在它**开始播放**的那一刻进入账本 / 事件流 / 上下文，
          否则用户没听到的文字就已经落库了。

        所以这里只递增计数器；真正的 ``sentence_queued`` 由 TTS 适配器在投递时刻带
        ``sequence=`` 调用。序号被烧掉（那一句最终没播）是无害的——它只是排序键。
        分配器始终是账本：适配器自己数的那一套只是"没有账本"时的兜底。
        """
        state = self._turn
        if state is None or state.finalized:
            return None
        state.sentence_seq += 1
        return state.sentence_seq

    async def playback_ended(self, *, turn_final: bool = True) -> None:
        if not turn_final:
            if self._turn is not None:
                self._turn.playback_active = False
            return
        if self._turn is not None:
            self._turn.playback_active = False
        await self._playback_event(
            "assistant.sentence.playback_ended",
            payload={
                "sentence_seq": self._turn.sentence_seq if self._turn else 0,
                # 输出队列已排空 = 这就是本回合的最后一次播报。前端只认这个标记
                # 收尾（再叠加 llm 已结束），而不是自己用 RMS 静音去猜：静音判完成
                # 会在句间停顿处把回答切成两半（F03）。
                "turn_final": True,
            },
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
        if self._turn.playback_observed:
            # Playback was observed, so TTS is demonstrably alive and this timer
            # is not allowed to conclude "no playback frames".  What it is really
            # looking at is a sentence that is still being spoken: the grace is
            # measured from LLM close, while ``BotStoppedSpeakingFrame`` only
            # arrives when the *last* sentence finishes -- and a spoken answer is
            # routinely longer than the grace.  Finalizing here marked a
            # perfectly normal turn degraded, which latched a false "voice
            # playback unavailable" banner for the rest of the call and dropped
            # the exchange from long-term memory (``memory`` is False whenever a
            # degraded reason is set).  Declining is safe: the idle deadline is
            # refreshed by playback progress and still bounds the turn, so a
            # genuine stall reaches a terminal state by itself.
            logger.debug(
                "voice turn %s skipped the grace finalize: playback already observed",
                self._turn.turn_id,
            )
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
        memory: bool | None = None,
    ) -> None:
        """Finalize the open turn exactly once, from the text that was read aloud.

        ``outcome="failed"`` is the "no answer" path: the provider errored or the
        turn went idle with nothing to say. The user's question is still
        persisted (an unanswered turn must not vanish on refresh), no assistant
        message is written, and nothing reaches long-term memory. ``memory`` may
        be passed explicitly; by default only a turn whose answer was actually
        spoken is memory-eligible.
        """
        async with self._lock:
            state = self._turn
            if state is None or state.finalized:
                return
            state.finalized = True
            self._cancel_recovery_timers()
        spoken = self._spoken_assistant_text(state)
        # What the user heard is the whole answer. The LLM's output is only used
        # when no sentence was ever spoken at all *and* the turn still succeeded
        # -- the degraded "audio never played, fall back to text" path, where the
        # page hands the answer over as text on purpose. A failed or interrupted
        # turn with nothing spoken has nothing the user heard, so it stays empty.
        # A generated LLM draft is not an answer the user heard. The media
        # ledger is the sole authority for transcript, context and statistics.
        assistant_text = spoken
        if memory is None:
            memory = (
                outcome == turn_api.TURN_FINALIZED
                and bool(spoken)
                and degraded_reason is None
            )
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
        self._update_playback_context(state, assistant_text)
        await self._publish_live(
            "turn.finalized", turn_id=state.turn_id,
            payload={"role": "assistant", "text": assistant_text,
                     "user_text": state.user_text, "outcome": outcome,
                     "interrupted": outcome == turn_api.TURN_INTERRUPTED,
                     "failed": outcome == turn_api.TURN_FAILED},
        )

        async def persist() -> None:
            await self._emit_turn_finalized(
                state,
                outcome=outcome,
                failure_reason=failure_reason,
                spoken=spoken,
                assistant_text=assistant_text,
            )
            try:
                await self._persist_offloop(
                    turn_api.finalize_turn,
                    self.voice_session_id,
                    state.turn_id,
                    assistant_text,
                    # The journal is the authority on the user's question: it merges
                    # consecutive ASR finals into one turn, while the row still holds
                    # only the first segment that opened it.  Passing it here is what
                    # keeps the transcript (and the page) from showing a truncated
                    # question next to an answer to the whole thing.
                    user_text=state.user_text or None,
                    audio_cursor_ms=state.audio_cursor_ms or None,
                    outcome=outcome,
                    failure_reason=failure_reason,
                    # Only what was read aloud may be remembered; text that stayed in
                    # the queue or was lost with TTS must not become long-term memory.
                    memory=memory,
                )
            except Exception:
                logger.warning("voice turn finalize failed", exc_info=True)
        self._enqueue_write(persist)

    async def _emit_turn_finalized(
        self,
        state: _TurnState,
        *,
        outcome: str,
        failure_reason: str | None,
        spoken: str,
        assistant_text: str,
    ) -> None:
        """落一条只带"真的播出去过"那一侧的 ``turn.finalized``。

        载荷里**没有**未播出的余量：生成出来但没进播放的字既不上屏也不落库。
        前端拿到的 ``text`` 就是用户实际听到的内容，``heard_text`` 是同一份播报
        记录的显式命名（正常回合里两者相等，被打断时是听到的那个前缀）。以前
        一个 ``text`` 字段同时承担"转录内容"和"实际播报内容"两个意思，于是要么
        把没读出来的字写进转录（把没说过的话当成说过了），要么把听到的内容截断。

        这里**先**用 ``turn.finalized:{turn_id}`` 这个 request_id 落事件，
        ``turn_api.finalize_turn`` 内部的同名事件会命中 emit 的幂等分支，因此账本
        里始终只有一条 ``turn.finalized``，而载荷带的是这份播报记录。反过来做
        不行：幂等分支只返回已存在的行，不会把后来的载荷合并进去。
        """
        await self._emit(
            "turn.finalized",
            payload={
                "role": "assistant",
                # 与落库的助手文本一致（正常回合里它就等于 spoken）。
                "text": assistant_text,
                "user_text": state.user_text,
                "outcome": outcome,
                "failure_reason": failure_reason,
                "failed": outcome == turn_api.TURN_FAILED,
                "retryable": outcome != turn_api.TURN_FINALIZED,
                "heard_text": spoken,
                "acknowledged": state.acknowledged,
                "interrupted": outcome == turn_api.TURN_INTERRUPTED,
                "audio_cursor_ms": state.audio_cursor_ms or None,
            },
            turn_id=state.turn_id,
            request_id=f"turn.finalized:{state.turn_id}",
            audio_cursor_ms=state.audio_cursor_ms or None,
        )


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

        The sentences whose playback had started are written to the transcript so
        a refresh still shows what was said, but they are explicitly *not*
        offered to long-term memory: the rest of the answer never reached the
        user, and cutting an exchange off mid-answer is not something to learn
        from.
        """
        async with self._lock:
            state = self._turn
            if state is None or state.finalized:
                return
            state.finalized = True
            self._cancel_recovery_timers()
        heard_text = self._spoken_assistant_text(state)
        self._update_playback_context(state, heard_text)
        await self._publish_live(
            "turn.interrupted", turn_id=state.turn_id,
            payload={"reason": reason, "origin": "pipeline", "heard_text": heard_text},
        )

        async def persist() -> None:
            interrupt_kwargs = {
                "turn_id": state.turn_id,
                "reason": reason,
                "origin": "pipeline",
            }
            if heard_text:
                interrupt_kwargs["heard_text"] = heard_text
            await self._persist_offloop(
                turn_api.interrupt_turn, self.voice_session_id, **interrupt_kwargs
            )
            await self._emit_turn_finalized(
                state, outcome=turn_api.TURN_INTERRUPTED, failure_reason=None,
                spoken=heard_text, assistant_text=heard_text,
            )
            await self._persist_offloop(
                turn_api.finalize_turn, self.voice_session_id, state.turn_id,
                heard_text, user_text=state.user_text or None, memory=False,
                outcome=turn_api.TURN_INTERRUPTED,
                audio_cursor_ms=state.audio_cursor_ms or None,
            )

        self._enqueue_write(persist)

    @staticmethod
    def _spoken_assistant_text(state: _TurnState) -> str:
        """Exactly the sentences whose audio entered playback, in order.

        A sentence lands in ``sentence_texts`` when the TTS adapter emits its
        marker -- the moment that sentence's first audio frame is queued, which
        is also the moment the page draws it. *Every* such sentence counts: an
        interrupted turn keeps all of them (the old version stopped at the first
        one, so an answer the user heard in full was stored as its opening
        sentence), and a sentence that never reached playback is not in this
        dict at all, so it can never be shown, persisted or remembered.

        The LLM's own output is not a substitute: it can run well past what was
        spoken, and writing it is what put words on the page (and into memory)
        that were never read aloud.

        曾经收到过播放确认的句子只算确认到的那个前缀：一个"说了三个字就被打断"
        的句子必须落成那三个字，而不是整句（F02）。没有确认记录的句子仍然按已
        入队全文计——老客户端不发确认，把缺失当成"一个字都没听到"会让整段回答
        消失，那是更坏的错误。句内位置本身是估算（见 :meth:`_played_chars`），
        所以 ``turn.finalized`` 用 ``acknowledged`` 标注哪些回合有真凭据。
        """
        parts: list[str] = []
        for sequence in sorted(state.sentence_texts):
            text = state.sentence_texts[sequence]
            heard = state.sentence_heard_chars.get(sequence)
            if heard is None:
                parts.append(text)
            else:
                parts.append(text[: max(0, min(len(text), heard))])
        # 拼接后统一清尾部空白：截断到句中的前缀常以空格结尾，而句间分隔空白
        # 属于下一句的开头，不该残留在这里。
        return "".join(parts).strip()

    @staticmethod
    def _played_chars(
        state: _TurnState, sentence_seq: int, played_ms: int, phase: str
    ) -> int | None:
        """把客户端的媒体位置换算成"这一句听到了第几个字"。

        ``played_ms`` 是**整轮回答已出声的媒体位置**（客户端音量累加器的读数，
        与服务端 marker 的句首/句尾游标同一条时间线）；只有拿它减去本句的句首
        游标，才知道这一句播了多久。

        返回 ``None`` 表示无法定位：句首游标缺失或没有句尾游标时，一个位置报告
        不能被折算成字符数——按 0 记会把"其实已经说了半句"的句子清空，比保守地
        不记录更糟。句内位置永远是估算（线性插值，精度受音量检测周期限制），
        所以它只用于展示，不用于任何终态判定。
        """
        text = state.sentence_texts.get(sentence_seq)
        if text is None:
            return None
        if phase == PLAYBACK_PHASE_ENDED:
            return len(text)
        start = state.sentence_start_cursor_ms.get(sentence_seq)
        end = state.sentence_end_cursor_ms.get(sentence_seq)
        if start is None or end is None or end <= start:
            return None
        elapsed = min(max(played_ms - start, 0), end - start)
        return min(len(text), int(len(text) * elapsed / (end - start)))


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

    async def notice(self, stage: str, code: str, message: str) -> None:
        """Record a stage note that is deliberately **not** an error.

        Same durable channel as ``processor.error``, but the client only degrades a
        call for errors carrying ``degraded=true`` (and only then forces text input
        and mutes the microphone).  Some of the most consequential voice events are
        invisible to the user and would otherwise leave no trace at all -- "the
        upstream session closed, next speech reconnects", "the local VAD produced
        no frames, PCM energy fallback is carrying the turn".  They belong in the
        durable log, not in a red banner, so they are emitted as notices.
        """
        await self._emit(
            "processor.notice",
            payload={"stage": stage, "code": code, "message": message},
            turn_id=self._turn.turn_id if self._turn else None,
        )

    async def session_ice(self, payload: dict[str, Any]) -> None:
        """Report which ICE path this call actually settled on.

        Recorded once per connection (and again if the path changes), because
        "did this call go direct, through a NAT hole, or through the relay?" is
        the first question whenever voice quality or connectivity is disputed --
        and it is invisible from both the browser console and the server log
        once the call is up.
        """

        await self._emit("session.ice", payload=dict(payload))

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

    async def context_updated(
        self, payload: dict[str, Any], *, request_id: str | None = None
    ) -> None:
        """Report a context/model change on the durable channel.

        A worker-authored report must carry ``origin="pipeline"`` in its payload
        and a ``request_id`` derived from the event it answers.  The payload
        marker is what ``VoiceControlWatchdog`` uses to tell "do this" from "this
        was done"; the request id makes the report idempotent per triggering
        event, so even a re-read cannot append a second row.
        """
        await self._emit("context.updated", payload=payload, request_id=request_id)


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
        # A frame has to be observed *upstream of whoever consumes it*.  This is
        # why this tap sits between STT and the user aggregator, and why the
        # typed-message branch below lives here rather than on the assistant tap:
        # ``LLMMessagesAppendFrame`` (what RTVI's ``send-text`` pushes from the top
        # of the pipeline) is consumed by ``LLMUserAggregator`` -- it calls
        # ``add_messages`` and never re-pushes the frame -- and the assistant tap
        # sits *downstream* of that aggregator.  Observing it there never fired,
        # so every typed utterance went un-journaled: no turn was opened (the
        # ``assistant.*`` events were emitted with ``turn_id=None``), the idle
        # ceiling was never armed and ``finalize_turn`` was never reached, which
        # left the page waiting on an answer that could not be closed, persisted
        # or remembered.
        if isinstance(frame, LLMMessagesAppendFrame):
            for message in frame.messages or ():
                if isinstance(message, dict) and message.get("role") == "user":
                    await self._journal.user_typed(str(message.get("content") or ""))
            return
        if isinstance(frame, InterimTranscriptionFrame):
            await self._journal.user_interim(frame.text)
        elif isinstance(frame, TranscriptionFrame):
            result = getattr(frame, "result", None)
            source_id = ""
            if isinstance(result, dict):
                source_id = str(
                    result.get("item_id")
                    or result.get("commit_id")
                    or result.get("event_id")
                    or ""
                )
            await self._journal.user_final(frame.text, source_id=source_id or None)
        elif isinstance(frame, UserStartedSpeakingFrame):
            await self._journal.user_started()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            await self._journal.user_stopped()

    async def _observe_assistant(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, LLMMessagesAppendFrame):
            # Fallback only: the user tap owns this frame today (see
            # ``_observe_user``, which is upstream of the aggregator that consumes
            # it).  Kept so a Pipecat version that starts forwarding the frame
            # downstream still journals typed input -- ``user_typed`` is
            # idempotent per turn, so a second observation cannot open a second
            # turn.
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
            await self._journal.playback_ended(turn_final=False)
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
    # Aging sweep for turns nobody acknowledged. Optional so the watchdog stays
    # usable (and testable) without a journal bound to a session.
    on_reconcile: Callable[[], Awaitable[int]] | None = None
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
                # 只执行"别处请求的"打断。``origin="pipeline"`` 的打断是本管线自己
                # 造成的（VAD / 客户端 RTVI barge-in），音频那时已经停了；再打断一次
                # 会落在下一轮刚起跑的 generation 上并把它掐掉（实测：打字回合的
                # LLM 请求 TTFB 0.3s 之后被这一击 abort，零文本、用户的问题静默丢失）。
                payload = event.get("payload")
                origin = payload.get("origin") if isinstance(payload, dict) else None
                if origin == "pipeline":
                    logger.debug(
                        "Skipping self-inflicted interrupt (turn %s)",
                        event.get("turn_id"),
                    )
                    continue
                await self.on_interrupt(event)
            elif event_type == "context.updated":
                # 同样的自触发回路，落在模型上：``origin="pipeline"`` 的
                # context.updated 是**回执**——本管线对别处请求的答复（"已把 LLM
                # 服务指向 X 了"），不是指令。watchdog 若再执行一次
                # ``on_model_changed``，那条回执又会写出一条新的 context.updated，
                # 于是每秒一条、持续到挂断，而前端的模型 pin 也就永远停在
                # "将在下次接通后生效"上。
                payload = event.get("payload")
                origin = payload.get("origin") if isinstance(payload, dict) else None
                if origin == "pipeline":
                    logger.debug(
                        "Skipping self-authored context.updated (seq %s)",
                        event.get("event_seq"),
                    )
                    continue
                await self.on_model_changed(event)
            elif event_type == "session.closed":
                await self.on_close("session.closed")
                return
        # Turns the control plane opened and no worker ever acknowledged are
        # invisible to every other recovery path (no idle ceiling is armed for a
        # turn this journal never opened, and no provider error is ever reported
        # for it). The sweep is throttled internally, so running it on every tick
        # stays cheap.
        if self.on_reconcile is not None:
            await self.on_reconcile()
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
