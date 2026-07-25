from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import Lock
from typing import Any


class InProcessTaskQueue:
    """A deliberately small queue behind a replaceable execution boundary.

    Work identifiers and state are persisted before submission, so a future
    Redis adapter can resume work without changing HTTP or domain contracts.
    """

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="learngraph")
        self._lock = Lock()
        self._closed = False
        self._accepting = True
        self._futures: set[Future[Any]] = set()

    def submit(self, task: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any] | None:
        with self._lock:
            if self._closed or not self._accepting:
                return None
            future = self._executor.submit(task, *args, **kwargs)
            self._futures.add(future)
        future.add_done_callback(self._discard)
        return future

    def _discard(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.discard(future)

    def quiesce(self, timeout_seconds: float = 5.0) -> bool:
        """Stop accepting new work and wait a bounded time for active tasks."""

        with self._lock:
            if self._closed:
                return True
            self._accepting = False
            active = tuple(self._futures)
        if not active:
            return True
        _, pending = wait(active, timeout=max(0.0, timeout_seconds))
        return not pending

    def resume(self) -> None:
        with self._lock:
            if not self._closed:
                self._accepting = True

    def status(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "accepting": self._accepting and not self._closed,
                "active_tasks": sum(not future.done() for future in self._futures),
            }

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=False)


task_queue = InProcessTaskQueue()
