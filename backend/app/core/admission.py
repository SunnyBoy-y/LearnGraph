"""Bounded admission control for long-lived agent streams.

Why this exists
---------------
An agent SSE generation is expensive in *database connections*, not in
requests. Two sessions pin a connection for the same wall-clock window:

1. the request-scoped session from ``get_db`` (``database.py``) stays open
   until the ``StreamingResponse`` body finishes, and
2. the detached worker opens its own session for the whole generation.

So each active stream holds ~2 of the pool's connections. Without a gate the
(capacity + 1)-th stream blocks inside SQLAlchemy pool checkout for
``pool_timeout_seconds`` and then dies with an opaque
``QueuePool limit ... reached`` failure. The concurrency load test reproduced
exactly that (``doc/LearnGraph_并发压测报告_v1.0.md`` §5.1: 20 concurrent
streams, 19/20 succeeded, 1 died on pool checkout).

This gate turns that into an immediate, legible rejection at the HTTP edge and
keeps ``agent_stream_pool_reserve`` connections free for scheduler sweeps,
background workers and the admitting request's own short queries.

Scope
-----
The counter is per-process, which matches the deployment contract: the Compose
file explicitly forbids raising ``uvicorn --workers`` above 1 because the
SQLite single-writer gate and the embedded schedulers assume one process. This
module deliberately does not become a distributed limiter; see
``doc/LearnGraph_SQLite全量迁移PostgreSQL升级方案_v1.0.md`` for the multi-process
design if that contract ever changes.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.core.config import get_settings


class AdmissionTicket:
    """One admitted agent-stream slot.

    ``release()`` is idempotent and thread-agnostic on purpose. The slot is
    opened by the HTTP handler thread but closed by whichever of these
    observes the generation end first:

    * the detached worker thread (normal completion, provider error), or
    * the transport generator (the client disconnected before the worker ever
      started, so the worker's ``finally`` will never run).

    A double release must not free two slots, otherwise the gate would admit
    more streams than the pool can serve — the exact failure this module
    exists to prevent.
    """

    __slots__ = ("_gate", "_consumes_slot", "_released", "_claimed", "acquired_at")

    def __init__(self, gate: "AgentStreamAdmissionGate", *, consumes_slot: bool) -> None:
        self._gate = gate
        self._consumes_slot = consumes_slot
        # Must be initialised here, not only in release(): these attributes are
        # __slots__, and reading an unassigned slot raises AttributeError.
        self._released = False
        self._claimed = False
        # Monotonic: a long-lived stream can be reported as such without
        # leaking wall-clock assumptions into metrics.
        self.acquired_at = time.monotonic()

    @property
    def released(self) -> bool:
        return self._released

    @property
    def claimed(self) -> bool:
        return self._claimed

    def claim(self) -> None:
        """Hand release ownership to the detached stream transport.

        The generation can legally outlive the HTTP request (the worker keeps
        persisting after a client disconnect), so once a transport owns the
        slot the request-lifecycle owner must stop touching it.
        """
        self._claimed = True

    def release_if_unclaimed(self) -> None:
        """Release only when no transport ever took ownership.

        Used by the request-lifecycle owner, i.e. the admission dependency's
        ``finally``. Ordering makes this race-free: ``claim()`` runs on the
        handler thread before the response starts, while this runs only after
        the response (or its failure) has finished.
        """
        if self._claimed:
            return
        self.release()

    def release(self) -> None:
        """Return the slot. Safe to call more than once, from any thread."""
        if not self._consumes_slot:
            self._released = True
            return
        self._gate._release(self)


class AgentStreamAdmissionGate:
    """Bounded gate admitting at most ``limit`` concurrent agent streams.

    ``limit=None`` means unbounded: every acquisition succeeds and nothing is
    counted. That is the explicit "admission control disabled" mode, kept
    distinct from a large numeric limit so metrics can never be misread.
    """

    def __init__(
        self,
        limit: int | None,
        *,
        connections_per_stream: int = 2,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.limit = max(1, limit) if limit is not None else None
        self.connections_per_stream = max(1, connections_per_stream)
        self._lock = threading.Lock()
        self._in_flight = 0
        # Cumulative counters, only ever reset by reset_metrics().
        self._acquired = 0
        self._rejected = 0
        self._released = 0
        self._peak_in_flight = 0

    # ── admission ─────────────────────────────────────────────────────────

    def try_acquire(self) -> AdmissionTicket | None:
        """Take a slot, or return None immediately when the gate is full.

        Never blocks: blocking is what produced the 10s stall followed by an
        unreadable pool error. Callers turn None into an explicit busy
        response so the client can retry with a clear reason.
        """
        if not self.enabled or self.limit is None:
            return AdmissionTicket(self, consumes_slot=False)
        with self._lock:
            if self._in_flight >= self.limit:
                self._rejected += 1
                return None
            self._in_flight += 1
            self._acquired += 1
            if self._in_flight > self._peak_in_flight:
                self._peak_in_flight = self._in_flight
        return AdmissionTicket(self, consumes_slot=True)

    def _release(self, ticket: AdmissionTicket) -> None:
        with self._lock:
            # Check-and-set under the gate lock so two threads racing on the
            # same ticket cannot each decrement.
            if ticket._released:
                return
            ticket._released = True
            self._in_flight = max(0, self._in_flight - 1)
            self._released += 1

    # ── introspection ─────────────────────────────────────────────────────

    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {
                "enabled": self.enabled,
                "limit": self.limit,
                "in_flight": self._in_flight,
                "available": (
                    None if self.limit is None else max(0, self.limit - self._in_flight)
                ),
                "peak_in_flight": self._peak_in_flight,
                "acquired": self._acquired,
                "released": self._released,
                "rejected": self._rejected,
                "connections_per_stream": self.connections_per_stream,
            }
        return out

    def reset_metrics(self) -> None:
        """Reset cumulative counters only; never touches live occupancy."""
        with self._lock:
            self._acquired = 0
            self._released = 0
            self._rejected = 0
            self._peak_in_flight = self._in_flight


def resolve_agent_stream_limit() -> int | None:
    """Resolve the configured admission limit, deriving it when unset.

    Derivation keeps the gate honest against the pool instead of hardcoding a
    number that silently drifts when an operator tunes
    ``LEARNGRAPH_SQLITE_POOL_SIZE`` / ``LEARNGRAPH_SQLITE_POOL_MAX_OVERFLOW``:
    the pool is what actually runs out, so the ceiling is
    ``(capacity - reserve) // connections_per_stream``.
    """
    settings = get_settings()
    explicit = settings.agent_stream_max_concurrent
    if explicit > 0:
        return explicit
    capacity = settings.sqlite_pool_size + settings.sqlite_pool_max_overflow
    reserve = max(0, settings.agent_stream_pool_reserve)
    usable = max(1, capacity - reserve)
    per_stream = max(1, settings.agent_stream_connections_per_stream)
    return max(1, usable // per_stream)


def build_agent_stream_gate() -> AgentStreamAdmissionGate:
    settings = get_settings()
    enabled = settings.agent_stream_admission_enabled
    return AgentStreamAdmissionGate(
        resolve_agent_stream_limit() if enabled else None,
        connections_per_stream=settings.agent_stream_connections_per_stream,
        enabled=enabled,
    )


_gate_lock = threading.Lock()
_gate = build_agent_stream_gate()


def agent_stream_gate() -> AgentStreamAdmissionGate:
    return _gate


def configure_agent_stream_gate(gate: AgentStreamAdmissionGate) -> None:
    """Swap the process gate. For tests that need a deterministic ceiling."""
    global _gate
    with _gate_lock:
        _gate = gate


def acquire_agent_stream_slot() -> AdmissionTicket | None:
    """Admit one agent stream, or return None when the deployment is busy."""
    return _gate.try_acquire()


def snapshot_admission_metrics() -> dict[str, Any]:
    out = _gate.snapshot()
    capacity = 0
    try:
        settings = get_settings()
        capacity = settings.sqlite_pool_size + settings.sqlite_pool_max_overflow
    except Exception:  # pragma: no cover - settings are required at import
        capacity = 0
    out["pool_capacity"] = capacity
    return out
