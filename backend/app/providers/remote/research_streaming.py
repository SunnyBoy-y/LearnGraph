"""A dedicated executor for research vendors that only stream.

Some vendors (DashScope's qwen-deep-research, Jina DeepSearch) have no job
API: the entire multi-minute run arrives over one streamed HTTP response.
Running those on the shared research worker pool would pin one of its two
workers for the whole run and starve every other job's polling.

So streaming runs get their own bounded pool plus an in-process result
registry, which lets a streaming vendor present the same
create/poll/cancel contract as a native job API.

The registry is deliberately in-process and therefore does not survive a
restart.  A poll for an unknown run reports a terminal failure rather than
hanging, so a job can never wait forever on a run that no longer exists.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

import httpx

# Streaming runs are long and mostly idle on the network, but each still holds
# a thread. Cap them well below the vendor rate limits.
_MAX_CONCURRENT_RUNS = 4
# Completed results stay readable long enough for the poller to collect them.
_RESULT_TTL_SECONDS = 3_600.0
_MAX_TRACKED_RUNS = 64


@dataclass
class _StreamingRun:
    task_id: str
    started_at: float
    future: Future[dict[str, Any]] | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    finished_at: float | None = None


class StreamingResearchRunner:
    """Runs streaming research calls off the polling worker pool."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=_MAX_CONCURRENT_RUNS,
            thread_name_prefix="learngraph-research",
        )
        self._lock = threading.Lock()
        self._runs: dict[str, _StreamingRun] = {}

    def submit(self, work: Callable[[threading.Event], dict[str, Any]]) -> str:
        """Start ``work`` in the background and return its poll handle."""

        self._evict_expired()
        task_id = f"lgstream_{uuid.uuid4().hex}"
        run = _StreamingRun(task_id=task_id, started_at=time.monotonic())
        with self._lock:
            if (
                sum(
                    1
                    for item in self._runs.values()
                    if item.future is not None and not item.future.done()
                )
                >= _MAX_CONCURRENT_RUNS
            ):
                raise RuntimeError("streaming research capacity reached")
            self._runs[task_id] = run
        run.future = self._executor.submit(work, run.cancelled)
        run.future.add_done_callback(lambda _: self._mark_finished(task_id))
        return task_id

    def _mark_finished(self, task_id: str) -> None:
        with self._lock:
            run = self._runs.get(task_id)
            if run is not None:
                run.finished_at = time.monotonic()

    def poll(self, task_id: str) -> dict[str, Any] | None:
        """Return the run's state, or ``None`` when the id is unknown."""

        self._evict_expired()
        with self._lock:
            run = self._runs.get(task_id)
        if run is None or run.future is None:
            return None
        if run.cancelled.is_set() and not run.future.done():
            return {"status": "cancel_requested"}
        if not run.future.done():
            return {"status": "running"}
        try:
            return run.future.result()
        except Exception as exc:  # noqa: BLE001 - normalized for the caller
            return {"status": "failed", "error": str(exc)[:2_000]}

    def cancel(self, task_id: str) -> None:
        """Ask a run to stop; the worker checks the flag between chunks."""

        with self._lock:
            run = self._runs.get(task_id)
        if run is None:
            return
        run.cancelled.set()
        if run.future is not None:
            run.future.cancel()

    def _evict_expired(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [
                task_id
                for task_id, run in self._runs.items()
                if run.finished_at is not None
                and now - run.finished_at > _RESULT_TTL_SECONDS
            ]
            for task_id in expired:
                self._runs.pop(task_id, None)
            # A hard cap keeps a runaway caller from growing the registry
            # without bound; the oldest finished runs go first.
            if len(self._runs) > _MAX_TRACKED_RUNS:
                finished = sorted(
                    (
                        (run.finished_at, task_id)
                        for task_id, run in self._runs.items()
                        if run.finished_at is not None
                    ),
                )
                for _, task_id in finished[: len(self._runs) - _MAX_TRACKED_RUNS]:
                    self._runs.pop(task_id, None)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


streaming_research_runner = StreamingResearchRunner()


class StreamingCancelled(RuntimeError):
    """Raised inside a worker when the caller asked the run to stop."""


def iter_sse_json(
    response: httpx.Response,
    *,
    cancelled: threading.Event | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield decoded JSON objects from an SSE ``data:`` stream.

    Non-JSON payloads and the terminal ``[DONE]`` sentinel are skipped rather
    than raised: vendors interleave keep-alive and comment lines that carry no
    business content.
    """

    for raw_line in response.iter_lines():
        if cancelled is not None and cancelled.is_set():
            raise StreamingCancelled("Research run was cancelled")
        line = raw_line.strip() if isinstance(raw_line, str) else raw_line.decode().strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            yield decoded
