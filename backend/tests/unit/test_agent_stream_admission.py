"""Agent-stream admission control (P0-4).

Exercises concurrency and cleanup invariants in the regular CI suite.
"""

from __future__ import annotations

import gc
import threading
import time

import pytest

from app.core.admission import (
    AgentStreamAdmissionGate,
    AdmissionTicket,
    resolve_agent_stream_limit,
    snapshot_admission_metrics,
)


# ── gate semantics ────────────────────────────────────────────────────────


def test_gate_admits_up_to_limit_then_rejects_without_blocking():
    gate = AgentStreamAdmissionGate(3)
    tickets = [gate.try_acquire() for _ in range(3)]
    assert all(isinstance(t, AdmissionTicket) for t in tickets)
    assert gate.in_flight() == 3

    started = time.monotonic()
    rejected = gate.try_acquire()
    elapsed = time.monotonic() - started

    assert rejected is None
    # The whole point: refusal is immediate. Blocking here is what produced the
    # 10s stall followed by an unreadable QueuePool error.
    assert elapsed < 0.05
    assert gate.snapshot()["rejected"] == 1


def test_release_is_idempotent_across_threads():
    gate = AgentStreamAdmissionGate(2)
    ticket = gate.try_acquire()
    assert ticket is not None

    barrier = threading.Barrier(4)

    def release_many() -> None:
        barrier.wait()
        for _ in range(50):
            ticket.release()

    threads = [threading.Thread(target=release_many) for _ in range(4)]
    for t in threads:
        t.start()
    # Exactly the 4 worker threads rendezvous on the barrier (main must NOT
    # wait on it, or it would be a 5th party and deadlock).
    for t in threads:
        t.join()

    # A double release must not free two slots, or the gate would admit more
    # streams than the pool can serve — the exact failure it exists to prevent.
    assert gate.in_flight() == 0
    assert gate.snapshot()["released"] == 1


def test_slot_becomes_available_again_after_release():
    gate = AgentStreamAdmissionGate(1)
    first = gate.try_acquire()
    assert first is not None
    assert gate.try_acquire() is None

    first.release()
    second = gate.try_acquire()
    assert second is not None
    assert gate.snapshot()["peak_in_flight"] == 1


def test_disabled_gate_is_unbounded_not_limit_one():
    gate = AgentStreamAdmissionGate(None, enabled=False)
    tickets = [gate.try_acquire() for _ in range(500)]
    assert all(t is not None for t in tickets)

    snap = gate.snapshot()
    # Disabled must be reported as an explicit null, never as a huge number
    # that could be misread as real capacity.
    assert snap["limit"] is None
    assert snap["available"] is None
    assert snap["enabled"] is False


def test_reset_metrics_does_not_disturb_occupancy():
    gate = AgentStreamAdmissionGate(4)
    held = [gate.try_acquire() for _ in range(3)]
    assert gate.in_flight() == 3

    gate.reset_metrics()

    snap = gate.snapshot()
    assert snap["in_flight"] == 3
    assert snap["acquired"] == 0
    assert snap["rejected"] == 0
    assert all(t is not None for t in held)


# ── limit derivation ──────────────────────────────────────────────────────


def test_derived_limit_matches_pool_arithmetic():
    from app.core.config import get_settings

    settings = get_settings()
    capacity = settings.sqlite_pool_size + settings.sqlite_pool_max_overflow
    expected = max(
        1,
        (capacity - settings.agent_stream_pool_reserve)
        // settings.agent_stream_connections_per_stream,
    )
    assert resolve_agent_stream_limit() == expected


def test_derived_limit_leaves_headroom_below_pool_capacity():
    """The ceiling must never claim more connections than the pool has."""
    from app.core.config import get_settings

    settings = get_settings()
    capacity = settings.sqlite_pool_size + settings.sqlite_pool_max_overflow
    limit = resolve_agent_stream_limit()
    assert limit is not None
    # Streams may pin at most (capacity - reserve) connections.
    assert limit * settings.agent_stream_connections_per_stream <= capacity


def test_explicit_limit_overrides_derivation(monkeypatch):
    from app.core import admission
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_stream_max_concurrent", 7, raising=False)
    assert admission.resolve_agent_stream_limit() == 7


def test_snapshot_reports_pool_capacity():
    snap = snapshot_admission_metrics()
    from app.core.config import get_settings

    settings = get_settings()
    assert snap["pool_capacity"] == (
        settings.sqlite_pool_size + settings.sqlite_pool_max_overflow
    )


# ── transport integration: the slot really comes back ─────────────────────


def _drain(stream) -> list[str]:
    return [chunk for chunk in stream]


def test_transport_releases_slot_when_generation_completes():
    from app.api.routers.chat import _detached_sse_transport

    gate = AgentStreamAdmissionGate(1)
    lease = gate.try_acquire()
    assert lease is not None

    stream = _detached_sse_transport(
        lambda: iter(["a", "b"]),
        session_id="s1",
        thread_name="test-transport-ok",
        lease=lease,
    )
    chunks = _drain(stream)

    assert "a" in chunks and "b" in chunks
    # Release happens before the end marker, so draining the stream fully is
    # enough — no sleep-and-hope.
    assert gate.in_flight() == 0
    assert lease.released


def test_transport_releases_slot_when_producer_raises():
    from app.api.routers.chat import _detached_sse_transport

    gate = AgentStreamAdmissionGate(1)
    lease = gate.try_acquire()

    def boom():
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover - makes this a generator

    stream = _detached_sse_transport(
        boom,
        session_id="s2",
        thread_name="test-transport-fail",
        lease=lease,
    )
    chunks = _drain(stream)

    assert any("message.failed" in c for c in chunks)
    assert gate.in_flight() == 0


def test_transport_releases_slot_when_client_never_iterates():
    """The leak path that no ``finally`` covers.

    Closing or collecting an *unstarted* generator runs no code, so if the
    response is built and then dropped (client aborted before the body
    iterator began) neither the worker nor the generator can hand the slot
    back. The GC finalizer must cover it.
    """
    from app.api.routers.chat import _detached_sse_transport

    gate = AgentStreamAdmissionGate(1)
    lease = gate.try_acquire()
    assert gate.in_flight() == 1

    stream = _detached_sse_transport(
        lambda: iter(["never consumed"]),
        session_id="s3",
        thread_name="test-transport-abandoned",
        lease=lease,
    )
    # Explicitly mirror Starlette dropping an unstarted body iterator.
    stream.close()
    del stream
    gc.collect()

    assert gate.in_flight() == 0


def test_transport_keeps_slot_while_worker_runs_after_disconnect():
    """Disconnect must NOT free the slot: the worker still holds a session."""
    from app.api.routers.chat import _detached_sse_transport

    gate = AgentStreamAdmissionGate(1)
    lease = gate.try_acquire()
    release_worker = threading.Event()
    worker_started = threading.Event()

    def slow_producer():
        worker_started.set()
        yield "first"
        release_worker.wait(timeout=5)
        yield "second"

    stream = _detached_sse_transport(
        slow_producer,
        session_id="s4",
        thread_name="test-transport-disconnect",
        lease=lease,
    )
    next(stream)  # ready comment -> starts the worker
    assert worker_started.wait(timeout=2)
    next(stream)  # "first"
    stream.close()  # subscriber leaves

    # The generation is still running, so the slot must still be held.
    assert gate.in_flight() == 1

    release_worker.set()
    deadline = time.monotonic() + 5
    while gate.in_flight() == 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gate.in_flight() == 0


# ── route-level contract ─────────────────────────────────────────────────


def test_route_raises_429_when_saturated(monkeypatch):
    from app.api.routers import chat as chat_router
    from app.core.errors import AppError

    gate = AgentStreamAdmissionGate(1)
    holder = gate.try_acquire()
    assert holder is not None

    monkeypatch.setattr(
        chat_router, "acquire_agent_stream_slot", gate.try_acquire
    )
    monkeypatch.setattr(
        chat_router, "snapshot_admission_metrics", gate.snapshot
    )

    with pytest.raises(AppError) as excinfo:
        # The admission unit is a generator dependency, so its body runs when
        # iterated — which is exactly what FastAPI does when solving it.
        next(iter(chat_router._admit_agent_generation()))

    assert excinfo.value.status_code == 429
    assert excinfo.value.code == "agent_stream_capacity_exceeded"
    # The caller gets the numbers needed to explain the rejection.
    assert excinfo.value.details["limit"] == 1
    assert excinfo.value.details["in_flight"] == 1
    # A refused admission must not consume anything.
    assert gate.in_flight() == 1


def test_real_derived_limit_holds_that_many_concurrent_generations():
    """Concurrency at the *configured* ceiling, not a toy limit.

    Every other test runs with limit 1-4 or a disabled gate; this one drives
    the real derived limit with real threads and real transports, so the
    arithmetic the deployment actually uses is exercised end to end.
    """
    from app.api.routers.chat import _detached_sse_transport

    limit = resolve_agent_stream_limit()
    assert limit is not None and limit > 1

    gate = AgentStreamAdmissionGate(limit)
    first_chunk = threading.Semaphore(0)
    finish = threading.Event()
    failures: list[str] = []

    def run_generation(idx: int) -> None:
        lease = gate.try_acquire()
        if lease is None:
            failures.append(f"thread {idx}: unexpectedly rejected")
            return

        def producer():
            yield f"chunk-{idx}"
            finish.wait(timeout=20)

        stream = _detached_sse_transport(
            producer,
            session_id=f"real-{idx}",
            thread_name=f"admission-real-{idx}",
            lease=lease,
        )
        try:
            next(stream)  # ready comment -> starts the worker
            next(stream)  # first chunk -> worker is now parked in the producer
            first_chunk.release()
            finish.wait(timeout=20)
            for _ in stream:
                pass
        except Exception as exc:  # pragma: no cover - diagnostic only
            failures.append(f"thread {idx}: {exc!r}")

    threads = [
        threading.Thread(target=run_generation, args=(i,)) for i in range(limit)
    ]
    for t in threads:
        t.start()
    for i in range(limit):
        assert first_chunk.acquire(timeout=20), f"worker {i} never produced a chunk"

    assert failures == []
    assert gate.in_flight() == limit, gate.snapshot()

    # The (limit+1)-th stream is refused immediately, at the real ceiling too.
    started = time.monotonic()
    assert gate.try_acquire() is None
    assert time.monotonic() - started < 0.05

    finish.set()
    for t in threads:
        t.join(timeout=30)
    assert gate.in_flight() == 0, "slots leaked at the real limit"


# ── request-lifecycle ownership ───────────────────────────────────────────
#
# These two encode the regression that made the gate wedge: a plain function
# dependency can only release inside the handler, and the handler never runs
# when a LATER dependency (auth / workspace) rejects the request — so every
# 403/404 raised after admission leaked one slot.


def test_dependency_releases_when_request_fails_before_handoff(monkeypatch):
    from app.api.routers import chat as chat_router

    gate = AgentStreamAdmissionGate(4)
    monkeypatch.setattr(
        chat_router, "acquire_agent_stream_slot", gate.try_acquire
    )

    for _ in range(6):
        dep = chat_router._admit_agent_generation()
        lease = next(dep)
        assert gate.in_flight() == 1
        # FastAPI unwinds the yield dependency when the request ends, including
        # when a later dependency or the handler raised.
        dep.close()
        assert gate.in_flight() == 0, "a failed request leaked its slot"
        assert lease.released

    assert gate.snapshot()["rejected"] == 0


def test_dependency_leaves_the_slot_to_the_transport_once_claimed(monkeypatch):
    from app.api.routers import chat as chat_router

    gate = AgentStreamAdmissionGate(1)
    monkeypatch.setattr(
        chat_router, "acquire_agent_stream_slot", gate.try_acquire
    )

    dep = chat_router._admit_agent_generation()
    lease = next(dep)

    # The transport takes ownership as soon as the response is built: the
    # detached generation may outlive the HTTP request (it keeps persisting
    # after a client disconnect), so request teardown must not free the slot.
    lease.claim()
    dep.close()
    assert gate.in_flight() == 1, (
        "request teardown freed a slot that a still-running generation owns"
    )

    # The generation ends later and releases it itself.
    lease.release()
    assert gate.in_flight() == 0
