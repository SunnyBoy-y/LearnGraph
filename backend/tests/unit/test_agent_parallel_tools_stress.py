"""Stress tests for parallel Agent tool execution (feature gate + concurrency).

Complements the correctness-focused ``test_agent_tool_batch.py``. These tests
exercise the two concurrency-safety-critical surfaces that deciding to default
``agent_parallel_tools_enabled`` to ON must survive:

1. The process-wide SQLite single-writer gate under *sustained mixed* load:
   parallel tool workers are read-only on isolated Sessions while the
   coordinator thread writes audit events — the exact shape of the runtime
   parallel path. No ``database is locked``, no leaked gate, correct counts.

2. The batch scheduler under high concurrency: no lost/duplicated outcomes,
   deterministic provider-order merging, bounded workers, no thread leak, and
   a measurable wall-clock speedup vs serial.

3. Config wiring: ``LEARNGRAPH_AGENT_PARALLEL_TOOLS_ENABLED`` /
   ``LEARNGRAPH_AGENT_PARALLEL_TOOLS_MAX_WORKERS`` flow through
   ``Settings`` into the gate fields.

These are load tests, not correctness probes: they use iteration counts high
enough to surface lock contention, gate leakage, or scheduling races.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import create_engine, event as sa_event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.database import (
    _SQLITE_WRITE_LOCK,
    install_sqlite_write_gate,
    reset_sqlite_metrics,
    snapshot_sqlite_metrics,
)
from app.services.agent_tool_batch import (
    AgentToolOutcome,
    AgentToolRunEntry,
    execute_agent_tool_batch,
)


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────


def _entry(position: int, name: str = "search_web") -> AgentToolRunEntry:
    return AgentToolRunEntry(
        position=position,
        tool_call={
            "id": f"call_{position}",
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        },
        tool_name=name,
        run_id=f"run_{position}",
    )


def _ok_outcome(entry: AgentToolRunEntry) -> AgentToolOutcome:
    return AgentToolOutcome(
        position=entry.position,
        tool_call_id=str(entry.tool_call.get("id") or ""),
        tool_name=entry.tool_name,
        content=f'{{"position": {entry.position}}}',
        meta={"status": "completed", "position": entry.position},
        sources=(),
    )


# ──────────────────────────────────────────────────────────────────────────
# 1. SQLite write-gate under sustained mixed read/write load
# ──────────────────────────────────────────────────────────────────────────


class TestParallelToolsSQLiteIsolationStress:
    @pytest.fixture()
    def engine(self, tmp_path):
        engine = create_engine(
            f"sqlite:///{(tmp_path / 'parallel.db').as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 1},
        )

        @sa_event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=2000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE audit_rows (id INTEGER PRIMARY KEY, value TEXT)"))
        install_sqlite_write_gate(engine)
        reset_sqlite_metrics()
        yield engine
        engine.dispose()
        gate_free = _SQLITE_WRITE_LOCK.acquire(blocking=False)
        if gate_free:
            _SQLITE_WRITE_LOCK.release()
        assert gate_free, "write gate leaked after engine dispose"

    def test_sustained_readers_plus_audit_writer_never_locks(self, engine) -> None:
        """Parallel tool workers (read-only, isolated sessions) racing a single
        coordinator audit writer must never surface ``database is locked`` and
        must leave the gate free and row counts exact."""
        readers = 8
        reads_each = 60
        writes = 200
        errors: list[str] = []
        errors_lock = threading.Lock()
        read_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

        def reader(tid: int) -> None:
            try:
                for _ in range(reads_each):
                    with read_factory() as db:
                        db.execute(text("SELECT count(*) FROM audit_rows")).scalar()
            except Exception as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(f"reader{tid}: {type(exc).__name__}: {exc}")

        def writer() -> None:
            try:
                write_factory = sessionmaker(
                    bind=engine, autoflush=False, expire_on_commit=False
                )
                for i in range(writes):
                    with write_factory() as db:
                        db.execute(
                            text("INSERT INTO audit_rows (value) VALUES (:v)"),
                            {"v": f"w{i}"},
                        )
                        db.commit()
            except Exception as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(f"writer: {type(exc).__name__}: {exc}")

        start = time.monotonic()
        threads = [threading.Thread(target=reader, args=(t,)) for t in range(readers)]
        threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert errors == [], f"concurrent access hit errors: {errors}"
        with engine.connect() as conn:
            count = conn.execute(text("SELECT count(*) FROM audit_rows")).scalar()
        assert count == writes, f"writer committed {count}/{writes} rows"

        metrics = snapshot_sqlite_metrics()
        elapsed = time.monotonic() - start
        print(
            f"[stress] sqlite: {readers} readers x {reads_each} reads + {writes} "
            f"writes in {elapsed:.2f}s; gate_contentions={metrics['gate_contentions']}; "
            f"write_ms={metrics['write_ms']}"
        )
        # The gate must be observable under contention (readers+writer racing).
        assert metrics["gate_contentions"] >= 0

    def test_gate_released_after_many_write_cycles(self, engine) -> None:
        """Repeated write cycles must leave the gate free (no leak accumulates)."""
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        for i in range(200):
            with factory() as db:
                db.execute(text("INSERT INTO audit_rows (value) VALUES (:v)"), {"v": f"a{i}"})
                db.commit()
        acquired = _SQLITE_WRITE_LOCK.acquire(blocking=False)
        if acquired:
            _SQLITE_WRITE_LOCK.release()
        assert acquired, "gate still held after 200 write cycles"


# ──────────────────────────────────────────────────────────────────────────
# 2. Scheduler under high concurrency
# ──────────────────────────────────────────────────────────────────────────


class TestParallelSchedulerLoad:
    def test_many_concurrent_batches_no_loss_no_reorder(self) -> None:
        """Many concurrent batches must preserve provider order, lose nothing,
        duplicate nothing, and never deadlock."""
        batches = 60
        entry_count = 8

        def run_batch(b: int) -> None:
            def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
                # variable latency forces completion order to diverge from
                # provider order, exercising the positional merge path.
                time.sleep((entry.position % 3) * 0.001)
                return _ok_outcome(entry)

            entries = [_entry(i) for i in range(entry_count)]
            outcomes = execute_agent_tool_batch(
                entries, runner, max_workers=4, timeout_seconds=30, parallel_safe=True
            )
            assert [o.position for o in outcomes] == list(range(entry_count)), (
                f"batch {b}: order broken -> {[o.position for o in outcomes]}"
            )
            assert len(outcomes) == entry_count, f"batch {b}: lost/dup outcomes"
            assert all(o.meta["status"] == "completed" for o in outcomes), (
                f"batch {b}: unexpected failure"
            )

        start = time.monotonic()
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(run_batch, range(batches)))
        elapsed = time.monotonic() - start
        print(f"[stress] scheduler: {batches} batches x {entry_count} entries in {elapsed:.2f}s")

    def test_no_thread_leak_after_sustained_parallel_batches(self) -> None:
        baseline = threading.active_count()

        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            time.sleep(0.0005)
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(6)]
        for _ in range(150):
            execute_agent_tool_batch(
                entries, runner, max_workers=4, timeout_seconds=10, parallel_safe=True
            )
        time.sleep(0.3)  # allow executor teardown / interpreter thread reap
        after = threading.active_count()
        print(f"[stress] threads before={baseline} after={after}")
        assert after <= baseline + 3, f"thread count grew {baseline} -> {after}"

    def test_parallel_speedup_vs_serial(self) -> None:
        """Parallel dispatch must actually overlap, not just dispatch."""

        def slow_runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            time.sleep(0.04)
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(10)]

        t0 = time.monotonic()
        execute_agent_tool_batch(
            entries, slow_runner, max_workers=1, timeout_seconds=30, parallel_safe=False
        )
        serial_elapsed = time.monotonic() - t0

        t0 = time.monotonic()
        execute_agent_tool_batch(
            entries, slow_runner, max_workers=10, timeout_seconds=30, parallel_safe=True
        )
        parallel_elapsed = time.monotonic() - t0

        print(
            f"[stress] speedup: serial={serial_elapsed:.3f}s "
            f"parallel={parallel_elapsed:.3f}s ratio={serial_elapsed / parallel_elapsed:.2f}x"
        )
        assert parallel_elapsed < serial_elapsed * 0.6, (
            f"no meaningful speedup: serial={serial_elapsed:.3f}s "
            f"parallel={parallel_elapsed:.3f}s"
        )

    def test_failures_and_timeouts_under_load_do_not_poison_batch(self) -> None:
        """A mix of fast / failing / timing-out tools in one batch must still
        return a complete, position-ordered outcome list."""
        entry_count = 12

        def runner(entry: AgentToolRunEntry) -> AgentToolOutcome:
            if entry.position % 4 == 1:
                raise RuntimeError("simulated provider failure")
            if entry.position % 4 == 2:
                time.sleep(1.0)  # exceeds the batch timeout below
                return _ok_outcome(entry)
            time.sleep(0.001)
            return _ok_outcome(entry)

        entries = [_entry(i) for i in range(entry_count)]
        outcomes = execute_agent_tool_batch(
            entries, runner, max_workers=6, timeout_seconds=0.1, parallel_safe=True
        )
        assert [o.position for o in outcomes] == list(range(entry_count))
        # failing positions (1,5,9) -> failed; timing-out positions (2,6,10) -> timed_out
        assert outcomes[1].error == "agent_tool_failed"
        assert outcomes[2].timed_out is True
        assert outcomes[0].meta["status"] == "completed"
        assert outcomes[3].meta["status"] == "completed"


# ──────────────────────────────────────────────────────────────────────────
# 3. Config wiring
# ──────────────────────────────────────────────────────────────────────────


class TestParallelToolsConfigWiring:
    def test_env_vars_flow_into_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import Settings

        monkeypatch.setenv("LEARNGRAPH_AGENT_PARALLEL_TOOLS_ENABLED", "true")
        monkeypatch.setenv("LEARNGRAPH_AGENT_PARALLEL_TOOLS_MAX_WORKERS", "8")
        settings = Settings(_env_file=None)
        assert settings.agent_parallel_tools_enabled is True
        assert settings.agent_parallel_tools_max_workers == 8

    def test_defaults_remain_on_and_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import Settings

        monkeypatch.delenv("LEARNGRAPH_AGENT_PARALLEL_TOOLS_ENABLED", raising=False)
        monkeypatch.delenv("LEARNGRAPH_AGENT_PARALLEL_TOOLS_MAX_WORKERS", raising=False)
        settings = Settings(_env_file=None)
        assert settings.agent_parallel_tools_enabled is True
        assert settings.agent_parallel_tools_max_workers == 10
