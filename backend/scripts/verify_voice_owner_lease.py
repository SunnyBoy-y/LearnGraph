"""Exercise the voice runtime owner CAS with two real Python processes.

This script intentionally uses a tiny SQLite database instead of starting the
API or any media provider.  It is a deployment check for the shared-database
coordination primitive: concurrent offers must have one winner, an unexpired
lease must not be force-stolen, and an expired lease must be claimable.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import tempfile
from pathlib import Path


def _worker(db_path: str, owner: str, barrier: mp.Barrier, out: mp.Queue) -> None:
    os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["LEARNGRAPH_ENV"] = "test"
    from app.voice.events import claim_runtime_owner

    barrier.wait()
    out.put((owner, claim_runtime_owner("voice-check", owner, lease_seconds=5)))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lg-owner-") as root:
        db_path = str(Path(root) / "owner.db")
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.executescript(
            """
            CREATE TABLE voice_sessions (
              id VARCHAR(64) PRIMARY KEY,
              chat_session_id VARCHAR(36),
              workspace_id VARCHAR(64) NOT NULL,
              tenant_id VARCHAR(64) NOT NULL,
              owner_user_id VARCHAR(64) NOT NULL,
              status VARCHAR(24) NOT NULL,
              peer_connection_id VARCHAR(160),
              peer_generation INTEGER NOT NULL DEFAULT 0,
              runtime_owner_id VARCHAR(160),
              runtime_owner_heartbeat_at DATETIME,
              runtime_owner_lease_until DATETIME,
              closed_at DATETIME,
              max_thinking_mode VARCHAR(16),
              event_seq INTEGER NOT NULL DEFAULT 0,
              session_epoch INTEGER NOT NULL DEFAULT 1,
              model_id VARCHAR(160), provider_id VARCHAR(80),
              runtime_ready BOOLEAN NOT NULL DEFAULT 0,
              signaling_url VARCHAR(500), context_snapshot JSON,
              created_at DATETIME, updated_at DATETIME
            );
            INSERT INTO voice_sessions
              (id, workspace_id, tenant_id, owner_user_id, status,
               session_epoch, peer_generation, context_snapshot)
            VALUES ('voice-check', 'workspace', 'tenant', 'user', 'active',
                    7, 3, '{}');
            """
        )
        connection.commit()
        connection.close()

        ctx = mp.get_context("spawn")
        barrier = ctx.Barrier(2)
        queue = ctx.Queue()
        processes = [
            ctx.Process(target=_worker, args=(db_path, owner, barrier, queue))
            for owner in ("worker-a", "worker-b")
        ]
        for process in processes:
            process.start()
        results = [queue.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)
        winners = [owner for owner, claimed in results if claimed]
        assert len(winners) == 1, results

        # ``force=True`` describes a reconnect but cannot take a live lease.
        # The parent imports the app only after the temporary path is known;
        # otherwise pydantic-settings may bind the helper to a developer's
        # .env database rather than the database used by the child processes.
        os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["LEARNGRAPH_ENV"] = "test"
        from app.voice.events import (
            claim_runtime_owner,
            heartbeat_runtime_owner,
            load_session,
            mark_session_reconnecting,
            release_runtime_owner,
        )

        loser = "worker-b" if winners[0] == "worker-a" else "worker-a"
        assert not claim_runtime_owner(
            "voice-check", loser, lease_seconds=5, force=True
        )

        # Expire the row without waiting for the five-second minimum lease.
        connection = sqlite3.connect(db_path)
        connection.execute(
            "UPDATE voice_sessions SET runtime_owner_lease_until = "
            "'2000-01-01 00:00:00'"
        )
        connection.commit()
        connection.close()
        assert claim_runtime_owner("voice-check", loser, lease_seconds=5, force=True)
        assert not heartbeat_runtime_owner("voice-check", winners[0], lease_seconds=5)
        # A stale disconnect cannot invalidate the replacement epoch.  The
        # current owner can advance the epoch and release its own lease.
        assert mark_session_reconnecting("voice-check", winners[0]) == 7
        assert load_session("voice-check").runtime_owner_id == loser
        assert mark_session_reconnecting("voice-check", loser) == 8
        assert load_session("voice-check").runtime_owner_id is None
        # Reclaim the now-free session for the release CAS check below.
        assert claim_runtime_owner("voice-check", loser, lease_seconds=5)
        # A stale finally block from the old runner cannot clear the replacement
        # lease when it carries the old reconnect epoch.
        assert not release_runtime_owner("voice-check", loser, expected_session_epoch=7)
        assert release_runtime_owner("voice-check", loser, expected_session_epoch=8)
        from app.core.database import engine

        engine.dispose()

    print("PASS: two-process voice owner CAS is exclusive and expiry-safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
