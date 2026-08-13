"""Patch chat.py stream-event commit path with transient-lock retry (v2).

v1 flaw: ``ScopedRepository.add`` flushes immediately, so the lock surfaces on
the INSERT (inside add) as well as on COMMIT; and direct ``self.db.commit()``
calls elsewhere committed buffered events without clearing the snapshot, which
made a later replay re-insert already-persisted rows.

v2 design:
- ``_append_event`` snapshots every buffered event (same id / created_at) and
  treats add + flush + commit as one retryable unit; on a transient lock it
  delegates to ``_recover_pending_events``.
- ``_commit_pending_stream_events`` commits, clearing the snapshot on success;
  on a transient lock it delegates to ``_recover_pending_events``.
- ``_recover_pending_events`` rolls back, re-runs the stream checkpoint
  callback, re-creates each buffered event from the snapshot *skipping ids
  already persisted by an intervening direct commit* (idempotent replay), then
  commits; it retries a bounded number of times and only then re-raises.
- ``_append_event`` returns its SSE envelope from a detached record instance
  so the returned envelope survives rollback + replay.

Applied via exact string replacement; fails loudly if a marker is missing.
"""
from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "app" / "services" / "chat.py"

# 1) __init__: add the snapshot list after _stream_checkpoint_fn
MARKER_INIT = """        self._stream_checkpoint_fn: Callable[[], None] | None = None
"""
REPL_INIT = """        self._stream_checkpoint_fn: Callable[[], None] | None = None
        # In-memory snapshot of every stream event added since the last flush.
        # On a transient SQLite lock the flush rolls back and re-creates these
        # rows (the ORM instances are gone after rollback), so the retried
        # commit writes exactly the same events (same ids / sequences).
        self._pending_event_snapshots: list[dict[str, Any]] = []
        # Caller-supplied redo for ``_append_event_locked_retry``: re-applies
        # ORM mutations (for example tool-record completion fields) after a
        # locked flush rolled the session back. Executed inside
        # ``_recover_pending_events`` so the inner retry honors it too.
        self._stream_redo_fn: Callable[[], None] | None = None
"""

# 2) _flush_event_buffer + new helpers
MARKER_FLUSH = """    def _flush_event_buffer(self) -> None:
        if self._stream_checkpoint_fn is not None:
            self._stream_checkpoint_fn()
        self.db.commit()
        self._event_commit_count = 0
        self._last_event_commit_at = time.monotonic()
"""
REPL_FLUSH = """    def _flush_event_buffer(self) -> None:
        if self._stream_checkpoint_fn is not None:
            self._stream_checkpoint_fn()
        self._commit_pending_stream_events()
        self._event_commit_count = 0
        self._last_event_commit_at = time.monotonic()

    def _commit_pending_stream_events(self) -> None:
        \"\"\"Commit buffered stream events, retrying transient SQLite locks.

        The engine busy timeout already waits before raising, so a lock error
        means another writer (a parallel stream, a background sweep, or a WAL
        autocheckpoint) held the single SQLite write lock for the whole wait.
        On lock contention the transaction is dropped and every buffered event
        is re-created from the in-memory snapshot (``_recover_pending_events``)
        so the retried commit writes the same rows.
        \"\"\"

        from sqlalchemy.exc import OperationalError

        from app.core.database import _is_sqlite_locked_error

        try:
            self.db.commit()
        except OperationalError as exc:
            if not _is_sqlite_locked_error(exc):
                raise
            self._recover_pending_events()
            return
        self._pending_event_snapshots.clear()

    def _recover_pending_events(self) -> None:
        \"\"\"Roll back a locked event write and re-apply every buffered event.

        The repository ``add`` flushes immediately, so the lock can surface on
        the INSERT itself rather than on COMMIT.  Recovery drops the failed
        transaction, re-runs the stream checkpoint callback (it re-applies
        part/message state), re-creates each buffered event from the snapshot
        — skipping ids already persisted by an intervening direct commit so
        the replay stays idempotent — and commits once more.  A bounded number
        of attempts absorbs a multi-second contention window (for example a
        large WAL checkpoint) without killing the stream.
        \"\"\"

        from sqlalchemy.exc import OperationalError

        from app.core.database import _is_sqlite_locked_error

        last_error: OperationalError | None = None
        for attempt in range(1, 5):
            try:
                self.db.rollback()
            except Exception:
                pass
            try:
                if self._stream_checkpoint_fn is not None:
                    self._stream_checkpoint_fn()
                if self._stream_redo_fn is not None:
                    self._stream_redo_fn()
                snapshots = self._pending_event_snapshots
                if snapshots:
                    persisted: set[str] = set()
                    if len(snapshots) < 500:
                        persisted = {
                            str(value)
                            for value in self.db.scalars(
                                select(MessageStreamEvent.id).where(
                                    MessageStreamEvent.workspace_id
                                    == self.workspace_id,
                                    MessageStreamEvent.id.in_(
                                        [snapshot["id"] for snapshot in snapshots]
                                    ),
                                )
                            ).all()
                        }
                    for snapshot in snapshots:
                        if snapshot["id"] in persisted:
                            continue
                        self.stream_events.add(
                            MessageStreamEvent(
                                id=snapshot["id"],
                                workspace_id=snapshot["workspace_id"],
                                session_id=snapshot["session_id"],
                                message_id=snapshot["message_id"],
                                message_version_id=snapshot["message_version_id"],
                                part_id=snapshot["part_id"],
                                sequence=snapshot["sequence"],
                                event_type=snapshot["event_type"],
                                payload=snapshot["payload"],
                                created_at=snapshot["created_at"],
                            )
                        )
                self.db.commit()
                self._pending_event_snapshots.clear()
                return
            except OperationalError as exc:
                if not _is_sqlite_locked_error(exc):
                    raise
                last_error = exc
                if attempt >= 4:
                    # Nothing was persisted by this recovery; drop the
                    # snapshots so a caller retry appends fresh events instead
                    # of re-creating (and duplicating) these rows.
                    self._pending_event_snapshots.clear()
                time.sleep(0.15 * (2 ** (attempt - 1)))
        assert last_error is not None
        logger.warning(
            "SQLite write still locked after stream-event retries: %s",
            str(getattr(last_error, "orig", last_error))[:200],
        )
        raise last_error
"""

# 3) _append_event: snapshot-first + retryable add/flush/commit + detached envelope
MARKER_APPEND = """        event_payload = self._stream_safe_event_payload(payload)
        record = self.stream_events.add(
            MessageStreamEvent(
                workspace_id=self.workspace_id,
                session_id=session_id,
                message_id=message_id,
                message_version_id=message_version_id,
                part_id=part_id,
                sequence=sequence,
                event_type=event_type,
                payload=event_payload,
            )
        )
        if self._event_commit_batching:
            self._maybe_flush_events(event_type)
        else:
            self.db.commit()
        return self._event_envelope(record)
"""
REPL_APPEND = """        event_payload = self._stream_safe_event_payload(payload)
        from sqlalchemy.exc import OperationalError

        # Detached copy for the SSE envelope: a locked-commit retry rolls the
        # session back, which expires the ORM row instance. The snapshot keeps
        # the exact id / created_at so the replayed row and this envelope match.
        envelope_record = MessageStreamEvent(
            id=str(uuid4()),
            workspace_id=self.workspace_id,
            session_id=session_id,
            message_id=message_id,
            message_version_id=message_version_id,
            part_id=part_id,
            sequence=sequence,
            event_type=event_type,
            payload=event_payload,
            created_at=utc_now(),
        )
        self._pending_event_snapshots.append(
            {
                "id": envelope_record.id,
                "workspace_id": self.workspace_id,
                "session_id": session_id,
                "message_id": message_id,
                "message_version_id": message_version_id,
                "part_id": part_id,
                "sequence": sequence,
                "event_type": event_type,
                "payload": event_payload,
                "created_at": envelope_record.created_at,
            }
        )
        try:
            self.stream_events.add(
                MessageStreamEvent(
                    id=envelope_record.id,
                    workspace_id=self.workspace_id,
                    session_id=session_id,
                    message_id=message_id,
                    message_version_id=message_version_id,
                    part_id=part_id,
                    sequence=sequence,
                    event_type=event_type,
                    payload=event_payload,
                    created_at=envelope_record.created_at,
                )
            )
            if self._event_commit_batching:
                self._maybe_flush_events(event_type)
            else:
                self._commit_pending_stream_events()
        except OperationalError as exc:
            from app.core.database import _is_sqlite_locked_error

            if not _is_sqlite_locked_error(exc):
                raise
            self._recover_pending_events()
        return self._event_envelope(envelope_record)
"""


MARKER_LOCKED_RETRY = """        last_error: OperationalError | None = None
        for attempt in range(1, 5):
            try:
                return self._append_event(**event_kwargs)
            except OperationalError as exc:
                if not _is_sqlite_locked_error(exc):
                    raise
                last_error = exc
                try:
                    self.db.rollback()
                except Exception:
                    pass
                if attempt >= 4:
                    break
                if redo is not None:
                    redo()
                time.sleep(0.15 * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error
"""
REPL_LOCKED_RETRY = """        last_error: OperationalError | None = None
        # Register the caller's redo so the inner ``_append_event`` recovery
        # (which may succeed before this outer loop ever runs) also re-applies
        # the ORM mutations after its rollback.
        self._stream_redo_fn = redo
        try:
            for attempt in range(1, 5):
                try:
                    return self._append_event(**event_kwargs)
                except OperationalError as exc:
                    if not _is_sqlite_locked_error(exc):
                        raise
                    last_error = exc
                    try:
                        self.db.rollback()
                    except Exception:
                        pass
                    if attempt >= 4:
                        break
                    if redo is not None:
                        redo()
                    time.sleep(0.15 * (2 ** (attempt - 1)))
            assert last_error is not None
            raise last_error
        finally:
            self._stream_redo_fn = None
"""


def apply() -> None:
    text = TARGET.read_text(encoding="utf-8")
    replacements = [
        ("init snapshot field", MARKER_INIT, REPL_INIT, 1),
        ("flush + commit retry", MARKER_FLUSH, REPL_FLUSH, 1),
        ("append event", MARKER_APPEND, REPL_APPEND, 1),
        ("locked retry redo", MARKER_LOCKED_RETRY, REPL_LOCKED_RETRY, 1),
    ]
    for name, old, new, expected in replacements:
        count = text.count(old)
        if count != expected:
            raise SystemExit(
                f"[FAIL] marker '{name}' found {count} times (expected {expected})"
            )
        text = text.replace(old, new)
        print(f"[OK] patched: {name}")
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print("[OK] wrote", TARGET)


if __name__ == "__main__":
    apply()
