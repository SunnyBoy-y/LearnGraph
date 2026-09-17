"""Voice turn lifecycle on top of the durable event journal.

One turn is the unit of truth for a full-duplex call.  The rules implemented
here are the ones that keep captions, transcripts and memory from drifting:

* A turn is created by :func:`accept_turn` and is idempotent on
  ``client_message_id`` (typed input) or ``turn_id`` (audio input).  Re-sending
  the same idempotency key never opens a second turn.
* Only a *finalized* turn reaches ``Message``/``MessagePart`` and the memory
  extraction queue.  Interim ASR, provisional drafts and text the user never
  heard because of a barge-in are recorded as events, never as turns.
* Interrupting a turn is a status transition, not a deletion: the partial
  assistant text stays auditable in the event journal.

Both the HTTP control plane and the audio worker call these functions, so there
is exactly one persistence path and both can retry safely.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.models import VoiceEventRecord, VoiceTurnRecord
from app.voice.events import (
    PHASE_AUTHORITATIVE,
    VoiceEventSink,
    VoiceSessionMissing,
    emit_event,
    load_session,
)

logger = logging.getLogger(__name__)

TURN_ACCEPTED = "accepted"
TURN_FINALIZED = "finalized"
TURN_INTERRUPTED = "interrupted"
# Terminal *without* an answer: the model errored or the turn went idle. The
# user's question is still persisted -- dropping it is what made a transient
# provider blip look like the user never spoke at all.
TURN_FAILED = "failed"

OPEN_TURN_STATUSES = (TURN_ACCEPTED,)
# Every settled status, i.e. everything that can be rendered as transcript.
# ``list_turns`` uses this so a refresh recovers failed/interrupted turns too,
# not just successful ones.
TERMINAL_TURN_STATUSES = (TURN_FINALIZED, TURN_INTERRUPTED, TURN_FAILED)

# Why a turn that nobody ever answered was closed by the aging sweep. It is a
# failure, not a deletion: the question stays in the transcript with a retry
# affordance, exactly like ``llm_error`` / ``turn_idle_timeout``.
STALE_TURN_REASON = "turn_stale"
# The call ended with this turn still open (hung up mid-answer / mid-typing).
SESSION_CLOSED_TURN_REASON = "session_closed"
# Events that prove the worker picked the turn up and is producing an answer
# under *its own* turn id. Used by the sweep as the "somebody is working on this
# turn" evidence, so a long but live answer is never mistaken for a hang.
_TURN_PROGRESS_EVENT_PREFIX = "assistant."


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def turn_to_dict(turn: VoiceTurnRecord) -> dict[str, Any]:
    return {
        "turn_id": turn.id,
        "voice_session_id": turn.voice_session_id,
        "chat_session_id": turn.chat_session_id,
        "client_message_id": turn.client_message_id,
        "status": turn.status,
        "user_text": turn.user_text,
        "assistant_text": turn.assistant_text,
        "failure_reason": turn.failure_reason,
        "context_version": turn.context_version,
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "finalized_at": turn.finalized_at.isoformat() if turn.finalized_at else None,
    }


def accept_turn(
    voice_session_id: str,
    user_text: str,
    *,
    client_message_id: str | None = None,
    turn_id: str | None = None,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
    phase: str = PHASE_AUTHORITATIVE,
) -> dict[str, Any]:
    """Open (or return) the durable turn for one user utterance.

    Returns the turn as a dict plus an ``created`` flag, so the caller can tell
    a genuine accept from a replayed one and skip duplicate side effects.
    """
    text = str(user_text or "").strip()
    session = load_session(voice_session_id)
    if session is None:
        raise VoiceSessionMissing(voice_session_id)

    with SessionLocal() as db:
        existing = _find_turn(
            db,
            voice_session_id=voice_session_id,
            turn_id=turn_id,
            client_message_id=client_message_id,
        )
        if existing is not None:
            # A replayed accept must not reopen or duplicate the turn, and must
            # not emit a second ``turn.accepted`` for the same sequence.
            emit_event(
                voice_session_id,
                "turn.accepted",
                payload={
                    "client_message_id": existing.client_message_id,
                    "turn_id": existing.id,
                    "replayed": True,
                    **(payload or {}),
                },
                turn_id=existing.id,
                request_id=f"turn.accepted:{existing.id}",
                phase=phase,
            )
            return {**turn_to_dict(existing), "created": False}

        if not text:
            raise ValueError("a voice turn requires non-empty user_text")
        turn = VoiceTurnRecord(
            id=turn_id or f"turn_{uuid4().hex[:24]}",
            voice_session_id=voice_session_id,
            workspace_id=session.workspace_id,
            tenant_id=session.tenant_id,
            chat_session_id=session.chat_session_id,
            client_message_id=client_message_id,
            user_text=text,
            status=TURN_ACCEPTED,
            started_at=utc_now(),
            context_version=(session.context_snapshot or {}).get("version")
            or (session.context_snapshot or {}).get("context_build_id"),
        )
        db.add(turn)
        db.commit()
        db.refresh(turn)
        snapshot = turn_to_dict(turn)

    # ``user.final`` (what the user is understood to have said) and
    # ``turn.accepted`` (what the system committed to answering) are separate
    # events on purpose: the client promotes a caption to authoritative on
    # ``turn.accepted`` and can still show a corrected ``user.final``.
    emit_event(
        voice_session_id,
        "user.final",
        payload={"text": text, "client_message_id": client_message_id},
        turn_id=snapshot["turn_id"],
        request_id=request_id or f"user.final:{snapshot['turn_id']}",
        phase=phase,
    )
    sink = VoiceEventSink(voice_session_id)
    sink.emit(
        "turn.accepted",
        {
            "client_message_id": client_message_id,
            "turn_id": snapshot["turn_id"],
            **(payload or {}),
        },
        turn_id=snapshot["turn_id"],
        request_id=f"turn.accepted:{snapshot['turn_id']}",
        phase=phase,
    )
    return {**snapshot, "created": True}


def _find_turn(
    db: Any,
    *,
    voice_session_id: str,
    turn_id: str | None,
    client_message_id: str | None,
) -> VoiceTurnRecord | None:
    if turn_id:
        found = db.scalar(
            select(VoiceTurnRecord).where(
                VoiceTurnRecord.id == turn_id,
                VoiceTurnRecord.voice_session_id == voice_session_id,
            )
        )
        if found is not None:
            return found
    if client_message_id:
        return db.scalar(
            select(VoiceTurnRecord).where(
                VoiceTurnRecord.voice_session_id == voice_session_id,
                VoiceTurnRecord.client_message_id == client_message_id,
            )
        )
    return None


def find_turn(voice_session_id: str, turn_id: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.scalar(
            select(VoiceTurnRecord).where(
                VoiceTurnRecord.id == turn_id,
                VoiceTurnRecord.voice_session_id == voice_session_id,
            )
        )
        return turn_to_dict(row) if row else None


def open_turn(voice_session_id: str) -> dict[str, Any] | None:
    """The most recent turn that is accepted but not yet finalized.

    The worker uses this to attach an ASR final to the typed turn that already
    claimed the utterance, instead of opening a second one.
    """
    with SessionLocal() as db:
        row = db.scalar(
            select(VoiceTurnRecord)
            .where(
                VoiceTurnRecord.voice_session_id == voice_session_id,
                VoiceTurnRecord.status.in_(OPEN_TURN_STATUSES),
            )
            .order_by(VoiceTurnRecord.created_at.desc())
        )
        return turn_to_dict(row) if row else None


def _turn_age_secs(started_at: datetime | None, *, now: datetime) -> float | None:
    """Age of a turn in seconds; ``None`` when the row carries no start time.

    ``started_at`` is written as an aware UTC value, but SQLite hands it back
    naive, so both shapes have to be accepted here.
    """
    if started_at is None:
        return None
    moment = (
        started_at
        if started_at.tzinfo is not None
        else started_at.replace(tzinfo=timezone.utc)
    )
    return (now - moment).total_seconds()


def list_stale_open_turns(
    voice_session_id: str,
    *,
    max_age_secs: float,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Turns of this session still ``accepted`` past ``max_age_secs``, oldest first.

    The age is compared in Python on purpose: the column is
    ``DateTime(timezone=True)`` while SQLite stores it without an offset, so a
    bound aware parameter compares as a *string* and silently misorders.
    """
    current = now or utc_now()
    with SessionLocal() as db:
        rows = db.scalars(
            select(VoiceTurnRecord)
            .where(
                VoiceTurnRecord.voice_session_id == voice_session_id,
                VoiceTurnRecord.status.in_(OPEN_TURN_STATUSES),
            )
            .order_by(VoiceTurnRecord.created_at.asc())
        ).all()
    stale: list[dict[str, Any]] = []
    for row in rows:
        age = _turn_age_secs(row.started_at, now=current)
        if age is None or age < max(0.0, float(max_age_secs)):
            continue
        stale.append(turn_to_dict(row))
    return stale


def turn_has_worker_progress(voice_session_id: str, turn_id: str) -> bool:
    """True when the worker produced assistant output under this turn's own id.

    A turn the control plane opened and then nobody acknowledged carries only the
    transcript/accept events of ``accept_turn`` (and, in the defect this guards
    against, not even under its own id); a turn the worker is actually answering
    carries ``assistant.*`` events. That difference is what lets the sweep close a
    hung turn without ever cutting a live answer short.
    """
    if not turn_id:
        return False
    with SessionLocal() as db:
        found = db.scalar(
            select(VoiceEventRecord.event_id)
            .where(
                VoiceEventRecord.voice_session_id == voice_session_id,
                VoiceEventRecord.turn_id == turn_id,
                VoiceEventRecord.event_type.like(f"{_TURN_PROGRESS_EVENT_PREFIX}%"),
            )
            .limit(1)
        )
    return found is not None


def finalize_stale_turns(
    voice_session_id: str,
    *,
    max_age_secs: float,
    skip_turn_ids: Iterable[str] = (),
    reason: str = STALE_TURN_REASON,
    now: datetime | None = None,
) -> list[str]:
    """Close stuck open turns of one session; returns the ids it closed.

    Three guards, in order:

    * ``skip_turn_ids`` — turns the caller (the audio worker) owns. Its own turn
      has its own idle ceiling and closing it from the outside would bypass the
      state that holds the answer.
    * ``turn_has_worker_progress`` — a turn whose id shows up in ``assistant.*``
      events is being answered right now, however old it is.
    * the age ceiling itself.

    Everything closed here is closed as ``failed``: the question is kept in the
    transcript with a retry affordance, no empty assistant message is written, and
    nothing reaches long-term memory.
    """
    skip = {str(item) for item in skip_turn_ids if item}
    closed: list[str] = []
    for row in list_stale_open_turns(
        voice_session_id, max_age_secs=max_age_secs, now=now
    ):
        turn_id = str(row.get("turn_id") or "")
        if not turn_id or turn_id in skip:
            continue
        if turn_has_worker_progress(voice_session_id, turn_id):
            continue
        try:
            finalize_turn(
                voice_session_id,
                turn_id,
                "",
                user_text=str(row.get("user_text") or "") or None,
                outcome=TURN_FAILED,
                failure_reason=reason,
            )
        except Exception:
            logger.warning(
                "stale voice turn %s could not be closed", turn_id, exc_info=True
            )
            continue
        closed.append(turn_id)
        logger.warning(
            "voice turn %s was never answered (age >= %.0fs); closed as %s",
            turn_id,
            max(0.0, float(max_age_secs)),
            reason,
        )
    return closed


def run_stale_turn_sweep(
    *,
    max_age_secs: float | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Process-wide aging sweep over every voice session that still has open turns.

    This is the durable backstop for the case the per-call worker cannot cover:
    a worker that died, a pipeline that was rebuilt mid-turn, or a turn the audio
    path never acknowledged. Without it such a row stays ``accepted`` forever --
    the page waits for a ``turn.finalized`` that never arrives, and the question
    never reaches the transcript or memory.
    """
    from app.core.config import get_settings

    settings = get_settings()
    ceiling = (
        float(settings.voice_turn_stale_timeout)
        if max_age_secs is None
        else float(max_age_secs)
    )
    if ceiling <= 0:
        return {"sessions": 0, "closed": 0}
    current = utc_now()
    with SessionLocal() as db:
        session_ids = [
            str(item)
            for item in db.scalars(
                select(VoiceTurnRecord.voice_session_id)
                .where(VoiceTurnRecord.status.in_(OPEN_TURN_STATUSES))
                .distinct()
                .limit(max(1, int(limit)))
            ).all()
        ]
    closed = 0
    for session_id in session_ids:
        closed += len(
            finalize_stale_turns(session_id, max_age_secs=ceiling, now=current)
        )
    return {"sessions": len(session_ids), "closed": closed}


def list_turns(
    voice_session_id: str,
    *,
    limit: int = 50,
    include_open: bool = False,
) -> list[dict[str, Any]]:
    """Authoritative transcript, oldest first.

    Includes every settled status, not just ``finalized``: a failed or
    interrupted turn still holds the user's question, and hiding it here is what
    used to make a dropped turn vanish on refresh.

    ``include_open`` additionally appends the turn that is still running. A page
    reload during an answer used to lose that turn *entirely* -- it has no
    persisted chat messages yet, and the live caption row lives only in the
    canvas -- so the user's own question disappeared until the turn settled.
    Reconciliation keeps such a turn transient (the stale-turn sweep and the
    pipeline's own finalize both settle it), and long-term memory still only
    ever takes finalized turns, so exposing it cannot leak an unfinished answer.
    """
    with SessionLocal() as db:
        rows = db.scalars(
            select(VoiceTurnRecord)
            .where(
                VoiceTurnRecord.voice_session_id == voice_session_id,
                VoiceTurnRecord.status.in_(TERMINAL_TURN_STATUSES),
            )
            .order_by(VoiceTurnRecord.finalized_at.asc(), VoiceTurnRecord.created_at.asc())
            .limit(int(limit))
        ).all()
        settled = [turn_to_dict(row) for row in rows]
        if not include_open:
            return settled
        open_rows = db.scalars(
            select(VoiceTurnRecord)
            .where(
                VoiceTurnRecord.voice_session_id == voice_session_id,
                VoiceTurnRecord.status.in_(OPEN_TURN_STATUSES),
            )
            .order_by(VoiceTurnRecord.created_at.asc())
        ).all()
        return settled + [turn_to_dict(row) for row in open_rows]


def finalize_turn(
    voice_session_id: str,
    turn_id: str,
    assistant_text: str = "",
    *,
    user_text: str | None = None,
    request_id: str | None = None,
    audio_cursor_ms: int | None = None,
    context_epoch: int | None = None,
    memory: bool = True,
    outcome: str = TURN_FINALIZED,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """Close a turn and persist it as an ordinary chat exchange.

    Idempotent: the status transition happens once, and the chat persistence is
    keyed on the turn id so a retry after a crash converges on the same
    messages.  Only this function writes long-term transcript/memory.

    ``user_text`` is the caller's authoritative question whenever it holds more
    than the row does.  The row only carries the segment that *opened* the turn
    (see ``accept_turn``), while the caller merges consecutive ASR finals into one
    question -- without this override the transcript keeps the first segment and
    the page shows a truncated question beside an answer to the whole thing.

    ``outcome`` is the settled status: ``finalized`` (an answer exists),
    ``interrupted`` (barged in) or ``failed`` (no answer: provider error, or the
    turn went idle).  ``failed`` and ``interrupted`` both keep the user's
    question in the transcript -- dropping it is what made a transient provider
    blip look like the user never spoke -- but neither reaches long-term memory,
    because there is no answer to remember and the user never heard one.
    """
    if outcome not in TERMINAL_TURN_STATUSES:
        raise ValueError(f"unknown voice turn outcome: {outcome}")
    created = False
    with SessionLocal() as db:
        turn = db.scalar(
            select(VoiceTurnRecord).where(
                VoiceTurnRecord.id == turn_id,
                VoiceTurnRecord.voice_session_id == voice_session_id,
            )
        )
        if turn is None:
            raise ValueError(f"voice turn {turn_id} was not found")
        if user_text is not None and user_text.strip():
            merged = user_text.strip()
            if turn.user_text != merged:
                turn.user_text = merged
                # Commit immediately: the branches below only commit when they
                # change the status or fill in a missing answer, so an already
                # settled turn (typically ``interrupted``) would otherwise drop
                # this correction when the session closes.
                db.commit()
                db.refresh(turn)
        if turn.status not in TERMINAL_TURN_STATUSES:
            turn.assistant_text = assistant_text or turn.assistant_text
            turn.status = outcome
            turn.failure_reason = failure_reason
            turn.finalized_at = utc_now()
            db.commit()
            db.refresh(turn)
            created = True
        elif assistant_text and not str(turn.assistant_text or "").strip():
            # The turn already settled (typically ``interrupted``, marked by the
            # barge-in path) and the caller holds the partial answer the user
            # actually heard. Record the text for audit without rewriting the
            # settled status, which stays authoritative.
            turn.assistant_text = assistant_text
            db.commit()
            db.refresh(turn)
        snapshot = turn_to_dict(turn)

    if created:
        emit_event(
            voice_session_id,
            "turn.finalized",
            payload={
                "text": snapshot["assistant_text"],
                "user_text": snapshot["user_text"],
                "outcome": snapshot["status"],
                "failed": snapshot["status"] == TURN_FAILED,
                "retryable": snapshot["status"] != TURN_FINALIZED,
                "failure_reason": snapshot["failure_reason"],
                "audio_cursor_ms": audio_cursor_ms,
                "context_epoch": context_epoch,
            },
            turn_id=snapshot["turn_id"],
            request_id=request_id or f"turn.finalized:{snapshot['turn_id']}",
            audio_cursor_ms=audio_cursor_ms,
        )

    # A turn that produced no answer has nothing to remember, and text the user
    # never heard must not reach long-term memory.
    memory_allowed = (
        memory
        and snapshot["status"] == TURN_FINALIZED
        and bool(str(snapshot["assistant_text"] or "").strip())
    )
    persisted = persist_turn_messages(
        voice_session_id, snapshot, memory=memory_allowed
    )
    return {**snapshot, "persisted": persisted}


def persist_turn_messages(
    voice_session_id: str, turn: dict[str, Any], *, memory: bool = True
) -> bool:
    """Write the authoritative exchange into the ordinary chat transcript.

    Failure is reported, never swallowed silently: the worker retries it on the
    next control tick and the caller can surface a degraded state.  The
    underlying call is idempotent on the turn id.
    """
    user_text = str(turn.get("user_text") or "").strip()
    if not user_text:
        return False
    assistant_text = str(turn.get("assistant_text") or "")
    # A failed turn has no answer to write. Persisting an empty assistant message
    # would render an empty bubble; the failure is carried by the turn status and
    # the ``turn.finalized`` payload instead, so the client can offer a retry.
    include_assistant = bool(assistant_text.strip())
    session = load_session(voice_session_id)
    if session is None:
        return False
    try:
        from app.core.config import get_settings
        from app.services.chat_service_factory import build_voice_chat_service

        settings = get_settings()
        with SessionLocal() as db:
            chat_service = build_voice_chat_service(
                db,
                workspace_id=session.workspace_id,
                actor_id=session.owner_user_id,
                settings=settings,
                model_id=session.model_id,
                provider_id=session.provider_id,
                thinking_mode=session.max_thinking_mode,
            )
            chat_service.persist_voice_turn(
                session.chat_session_id,
                turn_id=str(turn["turn_id"]),
                user_text=user_text,
                assistant_text=assistant_text,
                client_message_id=turn.get("client_message_id"),
                commit=True,
                memory=memory,
                include_assistant=include_assistant,
                assistant_status="completed" if include_assistant else "failed",
            )
        return True
    except Exception:
        logger.warning(
            "voice turn %s could not be written to the chat transcript",
            turn.get("turn_id"),
            exc_info=True,
        )
        return False


def interrupt_turn(
    voice_session_id: str,
    *,
    turn_id: str | None = None,
    reason: str = "barge_in",
    origin: str = "control",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Mark the open turn interrupted and publish the interrupt event.

    The event is what the audio worker observes (possibly via another worker),
    so an interrupt issued over HTTP still stops the audio that is being
    produced in a different process.

    ``origin`` records who caused the interrupt, and it exists to stop a
    self-inflicted loop: when the *pipeline itself* barges in (VAD, or the
    client's RTVI barge-in) it has already stopped its own audio, yet the
    interrupt is still journalled for the transcript. If that record looked
    like an externally requested interrupt, the worker's own control watchdog
    would read it one tick later and barge in again -- and the second barge-in
    lands on whatever the *next* turn is already generating (a typed turn starts
    within milliseconds), aborting it and leaving the user with a silently
    dropped question. ``"pipeline"`` therefore means "already applied where it
    happened; do not re-apply"; anything else means "apply it".
    """
    target: str | None = turn_id
    if target is None:
        current = open_turn(voice_session_id)
        target = current["turn_id"] if current else None
    snapshot: dict[str, Any] | None = None
    if target:
        with SessionLocal() as db:
            turn = db.scalar(
                select(VoiceTurnRecord).where(
                    VoiceTurnRecord.id == target,
                    VoiceTurnRecord.voice_session_id == voice_session_id,
                )
            )
            if turn is not None and turn.status == TURN_ACCEPTED:
                turn.status = TURN_INTERRUPTED
                db.commit()
                db.refresh(turn)
            snapshot = turn_to_dict(turn) if turn is not None else None
    emit_event(
        voice_session_id,
        "turn.interrupted",
        payload={
            "reason": reason,
            "turn_id": target,
            "origin": origin,
            "heard_text": (snapshot or {}).get("assistant_text") or "",
        },
        turn_id=target,
        request_id=request_id or f"turn.interrupted:{uuid4().hex[:12]}",
    )
    return snapshot or {"turn_id": target, "status": TURN_INTERRUPTED}


def record_assistant_draft(
    voice_session_id: str,
    turn_id: str | None,
    text: str,
    *,
    sentence_seq: int | None = None,
    audio_cursor_ms: int | None = None,
    event_type: str = "assistant.llm.delta",
    request_id: str | None = None,
    phase: str = "speculative",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Journal a speculative assistant fragment (draft or queued sentence).

    Speculative events carry ``sentence_seq`` and ``audio_cursor_ms`` so the
    client can order captions by the server's cursor instead of by content, and
    so a lost or duplicated marker can be reconciled on replay.
    """
    return emit_event(
        voice_session_id,
        event_type,
        payload={
            "text": text,
            "sentence_seq": sentence_seq,
            **(extra or {}),
        },
        turn_id=turn_id,
        phase=phase,
        audio_cursor_ms=audio_cursor_ms,
        request_id=request_id,
    )


def close_voice_session(
    voice_session_id: str,
    *,
    reason: str = "client_request",
    close_runtime: bool = True,
) -> dict[str, Any]:
    """End a session: terminal event once, then tear the runner down.

    Returns ``{"closed": bool, "already_closed": bool}``.  Idempotent: repeated
    closes emit exactly one terminal event and never fail.
    """
    from app.voice.events import close_session_record

    try:
        transitioned = close_session_record(voice_session_id)
    except VoiceSessionMissing:
        return {"closed": False, "already_closed": True}

    if transitioned:
        emit_event(
            voice_session_id,
            "session.closed",
            payload={"reason": reason},
            request_id=f"session.closed:{voice_session_id}",
        )
    # The runner may live in this worker or another one.  A local close is a
    # fast path; every worker's watchdog also observes the ended status through
    # the durable row, which is what makes the cross-worker case correct.
    try:
        if not close_runtime:
            return {"closed": True, "already_closed": not transitioned}
        from app.voice.runner_registry import close_local_runner

        close_local_runner(voice_session_id, reason=reason)
    except Exception:
        logger.debug("local voice runner close failed", exc_info=True)
    return {"closed": True, "already_closed": not transitioned}
