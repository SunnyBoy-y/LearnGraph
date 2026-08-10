from __future__ import annotations

import time
import threading
from collections import defaultdict, deque

from fastapi import Request

from app.core.errors import AppError


class SlidingWindowRateLimiter:
    """In-process sliding-window rate limiter keyed by client IP.

    Intended for anonymous auth endpoints (login/register/demo-login) where a
    per-account lockout is not enough: an attacker can otherwise lock out any
    account with 5 wrong passwords (15-minute DoS) or flood registrations.
    The limiter is in-memory and per-process; that is acceptable for the
    single-process desktop/self-hosted deployment this codebase targets, and
    a deployment behind multiple workers should move it to shared storage.
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(now)
            # Opportunistic cleanup so a flood of distinct IPs cannot grow the
            # map without bound.
            if len(self._hits) > 10_000:
                for k in [k for k, b in self._hits.items() if not b or b[-1] < cutoff]:
                    del self._hits[k]
            return True


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def enforce_auth_rate_limit(request: Request, limiter: SlidingWindowRateLimiter) -> None:
    if not limiter.allow(client_ip(request)):
        raise AppError(
            429,
            "auth_rate_limited",
            "Too many authentication attempts from this address; please try again later",
        )
