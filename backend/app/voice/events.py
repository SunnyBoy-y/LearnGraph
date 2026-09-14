"""Durable voice control plane: append-only event journal and control channel.

Everything a full-duplex call needs in order to be *recoverable* lives here:

* :class:`VoiceEventSink` — append-only writer with a monotonic ``event_seq``
  per session, idempotent on ``(request_id, event_type)``, usable from the API
  process *and* from the audio worker (each call opens a short-lived DB session).
* :func:`replay_events` / :func:`read_control_events` — cursor reads.  A client
  that lost data-channel frames asks for ``after_event_seq``; the audio worker
  uses the same mechanism as a control channel (``turn.interrupted``,
  ``session.closed``, ``context.updated``) so an interrupt or a hang-up issued
  on any worker reaches the worker that actually owns the runner.
* :func:`load_session` / :func:`is_session_active` — the worker's authority on
  whether the call is still allowed to run.

No process-local state is kept here: two workers and a restarted process all see
one cursor, which is what makes ``after_event_seq`` replay and multi-worker
reconnect correct.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.domain.models import VoiceEventRecord, VoiceSessionRecord

logger = logging.getLogger(__name__)

# Event types the worker treats as control input rather than user-visible output.
CONTROL_EVENT_TYPES: tuple[str, ...] = (
    "turn.interrupted",
    "session.closed",
    "context.updated",
)

# Every event type the foundation guarantees.  Kept in sync with
# ``app.voice.schemas.VOICE_EVENT_TYPES``; validated on emit so a typo in the
# worker fails loudly instead of writing an unreplayable row.
VOICE_EVENT_TYPES: tuple[str, ...] = (
    "session.created",
    "session.ready",
    "session.reconnecting",
    "session.closed",
    "user.started",
    "user.interim",
    "user.final",
    "turn.accepted",
    "turn.finalized",
    "turn.interrupted",
    "assistant.llm.delta",
    "assistant.sentence.queued",
    "assistant.sentence.playback_started",
    "assistant.sentence.playback_ended",
    "processor.error",
    "processor.retry_scheduled",
    "context.updated",
    "session.ice",
)

PHASE_SPECULATIVE = "speculative"
PHASE_AUTHORITATIVE = "authoritative"

_SEQ_ALLOCATION_ATTEMPTS = 4


class VoiceSessionMissing(RuntimeError):
    """Raised when a voice session row disappeared mid-call."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def json_safe(value: Any) -> Any:
    """Round-trip a value through JSON so it can be stored in a JSON column.

    Dataclasses, datetimes and enums are stringified; the column contract is
    "transport safe", not "lossless Python".
    """
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


@dataclass(slots=True)
class VoiceSessionHandle:
    """Detached read-only view of a durable voice session.

    The worker holds this instead of an ORM instance so nothing keeps a DB
    session or a transaction open across the audio pipeline's lifetime.
    """

    id: str
    chat_session_id: str
    workspace_id: str
    tenant_id: str
    owner_user_id: str
    status: str
    event_seq: int
    session_epoch: int
    peer_generation: int
    model_id: str | None
    provider_id: str | None
    max_thinking_mode: str
    runtime_ready: bool
    signaling_url: str | None
    context_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.status == "active"


def load_session(voice_session_id: str) -> VoiceSessionHandle | None:
    """Read the current durable session state (fresh session, no caching)."""
    if not voice_session_id:
        return None
    with SessionLocal() as db:
        row = db.get(VoiceSessionRecord, voice_session_id)
        if row is None:
            return None
        return _to_handle(row)


def is_session_active(voice_session_id: str) -> bool:
    handle = load_session(voice_session_id)
    return bool(handle and handle.active)


def _to_handle(row: VoiceSessionRecord) -> VoiceSessionHandle:
    return VoiceSessionHandle(
        id=row.id,
        chat_session_id=row.chat_session_id,
        workspace_id=row.workspace_id,
        tenant_id=row.tenant_id,
        owner_user_id=row.owner_user_id,
        status=row.status,
        event_seq=int(row.event_seq or 0),
        session_epoch=int(row.session_epoch or 1),
        peer_generation=int(row.peer_generation or 0),
        model_id=row.model_id,
        provider_id=row.provider_id,
        max_thinking_mode=row.max_thinking_mode or "high",
        runtime_ready=bool(row.runtime_ready),
        signaling_url=row.signaling_url,
        context_snapshot=dict(row.context_snapshot or {}),
    )


def envelope_from_record(record: VoiceEventRecord) -> dict[str, Any]:
    """Serialize a durable event into the transport envelope the client reads."""
    return {
        "event_id": record.event_id,
        "type": record.event_type,
        "seq": int(record.event_seq),
        "event_seq": int(record.event_seq),
        "session_id": record.voice_session_id,
        "session_epoch": int(record.session_epoch or 1),
        "turn_id": record.turn_id,
        "request_id": record.request_id,
        "phase": record.phase,
        "causality": dict(record.causality or {}),
        "audio_cursor_ms": record.audio_cursor_ms,
        "timestamp": record.created_at.isoformat() if record.created_at else None,
        "payload": dict(record.payload or {}),
    }


class VoiceEventSink:
    """Append-only event writer for one voice session.

    Each ``emit`` opens its own short-lived session, allocates the next
    ``event_seq`` with an atomic ``UPDATE ... SET event_seq = event_seq + 1`` and
    commits one row.  That is deliberately *not* a long-lived transaction: the
    audio worker must be able to journal an event while an unrelated request is
    writing to the same database.
    """

    def __init__(self, voice_session_id: str) -> None:
        self.voice_session_id = voice_session_id

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        turn_id: str | None = None,
        phase: str = PHASE_AUTHORITATIVE,
        causality: dict[str, Any] | None = None,
        audio_cursor_ms: int | None = None,
        request_id: str | None = None,
        session_epoch: int | None = None,
    ) -> dict[str, Any]:
        """Append one event and return its transport envelope.

        Idempotent when ``request_id`` is supplied: replaying the same
        ``(request_id, event_type)`` returns the stored row instead of appending
        a second event with a new sequence number.  That is what lets a retried
        processor or a re-delivered HTTP call converge on one event.
        """
        if event_type not in VOICE_EVENT_TYPES:
            raise ValueError(f"unknown voice event type: {event_type}")
        safe_payload = json_safe(payload or {})
        safe_causality = json_safe(causality or {})
        rid = request_id or f"req_{uuid4().hex[:20]}"
        event_id = f"ve_{uuid4().hex[:24]}"

        last_error: Exception | None = None
        for _attempt in range(_SEQ_ALLOCATION_ATTEMPTS):
            with SessionLocal() as db:
                if request_id:
                    previous = db.scalar(
                        select(VoiceEventRecord).where(
                            VoiceEventRecord.voice_session_id == self.voice_session_id,
                            VoiceEventRecord.request_id == request_id,
                            VoiceEventRecord.event_type == event_type,
                        )
                    )
                    if previous is not None:
                        return envelope_from_record(previous)
                try:
                    seq, epoch = _allocate_seq(db, self.voice_session_id)
                except VoiceSessionMissing:
                    raise
                except IntegrityError as exc:  # pragma: no cover - lock contention
                    db.rollback()
                    last_error = exc
                    continue
                workspace_id, tenant_id = _session_scope(db, self.voice_session_id)
                record = VoiceEventRecord(
                    event_id=event_id,
                    voice_session_id=self.voice_session_id,
                    workspace_id=workspace_id,
                    tenant_id=tenant_id,
                    session_epoch=session_epoch or epoch,
                    turn_id=turn_id,
                    event_seq=seq,
                    request_id=rid,
                    event_type=event_type,
                    phase=phase,
                    causality=safe_causality,
                    audio_cursor_ms=audio_cursor_ms,
                    payload=safe_payload,
                )
                db.add(record)
                try:
                    db.commit()
                except IntegrityError as exc:
                    # A racing writer took this sequence number.  Retry with a
                    # fresh allocation rather than losing the event.
                    db.rollback()
                    last_error = exc
                    continue
                db.refresh(record)
                return envelope_from_record(record)
        raise RuntimeError(
            f"could not allocate an event sequence for {self.voice_session_id}: {last_error}"
        )


def emit_event(voice_session_id: str, event_type: str, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for one-off emits (control plane / worker)."""
    return VoiceEventSink(voice_session_id).emit(event_type, **kwargs)


def _session_scope(db: Any, voice_session_id: str) -> tuple[str, str]:
    row = db.scalars(
        select(VoiceSessionRecord)
        .where(VoiceSessionRecord.id == voice_session_id)
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise VoiceSessionMissing(voice_session_id)
    return row.workspace_id, row.tenant_id


def _allocate_seq(db: Any, voice_session_id: str) -> tuple[int, int]:
    """Atomically claim the next ``event_seq`` for a session.

    The increment happens in the database so two workers can never hand out the
    same sequence number; ``uq_voice_event_seq`` is the final backstop if the
    two transactions somehow interleave across processes.
    """
    result = db.execute(
        update(VoiceSessionRecord)
        .where(VoiceSessionRecord.id == voice_session_id)
        .values(event_seq=VoiceSessionRecord.event_seq + 1, updated_at=utc_now())
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:
        raise VoiceSessionMissing(voice_session_id)
    row = db.scalars(
        select(VoiceSessionRecord)
        .where(VoiceSessionRecord.id == voice_session_id)
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise VoiceSessionMissing(voice_session_id)
    return int(row.event_seq), int(row.session_epoch or 1)


def replay_events(
    voice_session_id: str,
    after_event_seq: int = 0,
    *,
    types: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Cursor read of durable events, ordered by the monotonic ``event_seq``."""
    with SessionLocal() as db:
        statement = select(VoiceEventRecord).where(
            VoiceEventRecord.voice_session_id == voice_session_id,
            VoiceEventRecord.event_seq > int(after_event_seq or 0),
        )
        if types:
            statement = statement.where(VoiceEventRecord.event_type.in_(tuple(types)))
        statement = statement.order_by(VoiceEventRecord.event_seq)
        if limit:
            statement = statement.limit(int(limit))
        rows = db.scalars(statement).all()
        return [envelope_from_record(row) for row in rows]


def read_control_events(
    voice_session_id: str, after_event_seq: int
) -> list[dict[str, Any]]:
    """Control-channel read used by the audio worker's watchdog.

    Returns only the events that ask the *worker* to do something, so a long
    spoken turn does not make the watchdog page through caption events.
    """
    return replay_events(
        voice_session_id, after_event_seq, types=CONTROL_EVENT_TYPES
    )


def last_event_seq(voice_session_id: str) -> int:
    handle = load_session(voice_session_id)
    return handle.event_seq if handle else 0


def close_session_record(voice_session_id: str) -> bool:
    """Mark a session ended; returns True only on the transition.

    Idempotent by construction: a second close observes ``status == "ended"``
    and reports ``False`` so the caller emits exactly one ``session.closed``.
    """
    with SessionLocal() as db:
        row = db.get(VoiceSessionRecord, voice_session_id)
        if row is None:
            raise VoiceSessionMissing(voice_session_id)
        if row.status == "ended":
            return False
        row.status = "ended"
        row.closed_at = utc_now()
        row.session_epoch = int(row.session_epoch or 1) + 1
        db.commit()
        return True


def count_events(voice_session_id: str, event_type: str) -> int:
    """How many events of one type a session has recorded."""
    with SessionLocal() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(VoiceEventRecord)
                .where(
                    VoiceEventRecord.voice_session_id == voice_session_id,
                    VoiceEventRecord.event_type == event_type,
                )
            )
            or 0
        )


def is_reconnect(voice_session_id: str) -> bool:
    """True when a pipeline has already been ready for this session.

    A second ``session.ready`` is only ever produced by a reconnect (a rebuilt
    transport re-dialling the same durable session) or by a runner taking over
    from another one, which is exactly when the client needs to be told that the
    call is re-establishing rather than starting fresh.
    """
    return count_events(voice_session_id, "session.ready") > 0


def mark_session_reconnecting(voice_session_id: str) -> int:
    """Bump the epoch when a transport is torn down and will be rebuilt."""
    with SessionLocal() as db:
        row = db.get(VoiceSessionRecord, voice_session_id)
        if row is None:
            raise VoiceSessionMissing(voice_session_id)
        row.session_epoch = int(row.session_epoch or 1) + 1
        db.commit()
        return int(row.session_epoch)


def iter_session_ids() -> Iterable[str]:
    """All known voice session ids; used by diagnostics, never by hot paths."""
    with SessionLocal() as db:
        return list(db.scalars(select(VoiceSessionRecord.id)).all())
