"""Session-level idle reaper for voice calls (the "session TTL" hardening).

Why this exists
---------------
A voice call is one WebRTC peer plus one audio pipeline, and the pipeline owns two
paid upstream sessions: the realtime ASR websocket and the bidirectional TTS
stream.  Nothing in the call path ends those on behalf of a client that simply
stopped being there -- a closed laptop lid, a phone in a pocket, a forgotten tab.
``VoiceControlWatchdog`` only reacts to explicit control (interrupt / close), and
``on_client_disconnected`` needs aiortc to notice the peer is gone, which it never
does while the peer keeps the connection alive.

So the durable row is the only thing that can notice, and it only notices if
somebody looks at it: this sweep.  It is deliberately the *last* resort rather
than a primary mechanism:

* It requires real activity evidence (a durable event, a turn write, or a session
  row write) to be older than ``voice_session_idle_timeout_seconds``, so a call
  whose transcript is still being written is never touched.  The client's event
  polling is read-only precisely so it cannot keep a dead call alive.
* When the pipeline happens to run in this process, the local audio activity that
  never reaches the database (an utterance the ASR has not finalised yet) also
  counts; see ``_local_pipeline_idle_seconds``.
* Closing is idempotent and shared with the normal hang-up path, so a reaper that
  races a user who finally hangs up themselves is harmless.

Activity definition
-------------------
``max(voice_sessions.updated_at, MAX(voice_events.created_at), MAX(voice_turns.updated_at))``

All three are written by the audio worker for anything the user can perceive
(speech, an answer, a task result, a context switch), which is what makes "45
minutes of none of them" a safe signal that nobody is on the call.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.domain.models import VoiceEventRecord, VoiceSessionRecord, VoiceTurnRecord

logger = logging.getLogger(__name__)

# Statuses that still own a pipeline (and therefore upstream sessions).
ACTIVE_STATUS = "active"


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a stored timestamp to an aware UTC datetime.

    Rows written by SQLite come back naive (the column type drops the offset), so
    comparing them against an aware ``now`` would raise instead of expiring a
    session -- i.e. the reaper would silently never fire.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_pipeline_idle_seconds(voice_session_id: str) -> float | None:
    """Seconds since this process's pipeline for the session last saw audio.

    ``None`` means "no local pipeline, no opinion".  This is what keeps the sweep
    from ending a live call during a long silence that the database cannot see:
    while the peer is connected the STT keeps receiving audio frames, and that
    timestamp lives only in memory.
    """
    try:
        from app.voice.runner_registry import get_runner

        handle = get_runner(voice_session_id)
    except Exception:
        return None
    if handle is None:
        return None
    newest: float | None = None
    for processor in list(handle.processors):
        getter = getattr(processor, "last_audio_activity_at", None)
        if getter is None:
            continue
        try:
            stamp = getter()
        except Exception:
            continue
        if stamp is None:
            continue
        newest = stamp if newest is None else max(newest, float(stamp))
    if newest is None:
        return None
    return max(0.0, time.monotonic() - newest)


def idle_voice_sessions(
    *,
    idle_timeout_seconds: float,
    now: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Active sessions with no activity for ``idle_timeout_seconds``.

    One aggregate query per table over the active session ids, so the cost does
    not grow with event volume.
    """
    if idle_timeout_seconds <= 0:
        return []
    current = now or datetime.now(timezone.utc)
    with SessionLocal() as db:
        sessions = [
            (str(row[0]), _as_utc(row[1]))
            for row in db.execute(
                select(VoiceSessionRecord.id, VoiceSessionRecord.updated_at)
                .where(VoiceSessionRecord.status == ACTIVE_STATUS)
                .limit(max(1, int(limit)))
            ).all()
        ]
        if not sessions:
            return []
        ids = [session_id for session_id, _ in sessions]
        last_event: dict[str, datetime] = {}
        for session_id, stamp in db.execute(
            select(
                VoiceEventRecord.voice_session_id,
                func.max(VoiceEventRecord.created_at),
            )
            .where(VoiceEventRecord.voice_session_id.in_(ids))
            .group_by(VoiceEventRecord.voice_session_id)
        ).all():
            normalized = _as_utc(stamp)
            if normalized is not None:
                last_event[str(session_id)] = normalized
        last_turn: dict[str, datetime] = {}
        for session_id, stamp in db.execute(
            select(
                VoiceTurnRecord.voice_session_id,
                func.max(VoiceTurnRecord.updated_at),
            )
            .where(VoiceTurnRecord.voice_session_id.in_(ids))
            .group_by(VoiceTurnRecord.voice_session_id)
        ).all():
            normalized = _as_utc(stamp)
            if normalized is not None:
                last_turn[str(session_id)] = normalized

    idle: list[dict[str, Any]] = []
    for session_id, updated_at in sessions:
        candidates = [stamp for stamp in (updated_at, last_event.get(session_id), last_turn.get(session_id)) if stamp]
        if candidates:
            last_activity = max(candidates)
            idle_seconds = (current - last_activity).total_seconds()
        else:
            # Every row missing means we cannot prove the session is dead; saying
            # "idle for ever" here would end a call on a query hiccup.
            continue
        if idle_seconds < idle_timeout_seconds:
            continue
        idle.append(
            {
                "voice_session_id": session_id,
                "idle_seconds": idle_seconds,
                "last_activity_at": last_activity.isoformat(),
            }
        )
    idle.sort(key=lambda item: item["idle_seconds"], reverse=True)
    return idle


def run_voice_session_idle_sweep(
    *,
    idle_timeout_seconds: float | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Close every active session that has been idle past the ceiling.

    Returns ``{"sessions", "idle", "closed", "skipped_local"}``.  Best effort: a
    single session that fails to close must not stop the sweep, because the next
    round will try again and the durable row is still the source of truth.
    """
    from app.core.config import get_settings

    settings = get_settings()
    ceiling = (
        float(settings.voice_session_idle_timeout_seconds)
        if idle_timeout_seconds is None
        else float(idle_timeout_seconds)
    )
    if ceiling <= 0:
        return {"sessions": 0, "idle": 0, "closed": 0, "skipped_local": 0}

    candidates = idle_voice_sessions(idle_timeout_seconds=ceiling, limit=limit)
    closed = 0
    skipped = 0
    for candidate in candidates:
        session_id = str(candidate["voice_session_id"])
        local_idle = _local_pipeline_idle_seconds(session_id)
        if local_idle is not None and local_idle < ceiling:
            # The pipeline in this process is still receiving audio: the user is
            # on the call even though nothing durable has been written yet.
            skipped += 1
            logger.debug(
                "voice session %s looks idle in the journal but its pipeline is "
                "still active (%.0fs since last audio); keeping it",
                session_id,
                local_idle,
            )
            continue
        try:
            from app.voice.turns import close_voice_session

            result = close_voice_session(session_id, reason="idle_timeout")
        except Exception:
            logger.warning(
                "voice session %s could not be closed by the idle reaper",
                session_id,
                exc_info=True,
            )
            continue
        if result.get("already_closed"):
            continue
        closed += 1
        logger.warning(
            "voice session %s closed after %.0fs of inactivity (session TTL)",
            session_id,
            float(candidate["idle_seconds"]),
        )
    return {
        "sessions": len(candidates),
        "idle": len(candidates),
        "closed": closed,
        "skipped_local": skipped,
    }
