"""Process-wide warm-container pool for sandbox-isolated web fetch.

Each ``fetch()`` used to pay the full container lifecycle (create container,
materialize workspace, run, delete container, remove workspace) — roughly
1.5-3s of host-side overhead per URL before any network I/O. This module
replaces that with a bounded pool of long-lived ``web_fetch`` containers that
are reused across requests:

* **Speed** — a warm container skips create/delete per fetch, so a fetch starts
  immediately and the per-fetch overhead drops to one ``put_archive``, one
  ``exec`` and one ``get_archive``.
* **Concurrency** — up to ``max_size`` fetches run in parallel across the
  process (the provider is thread-safe), while each container still executes
  at most one runner at a time (a timeout/truncation kills the container, so
  parallel execs in the same container are never safe).

Security posture is unchanged and still fails closed: every pooled container
is created with the same egress envelope (policy digest, internal network,
proxy URL) derived from the workspace fetch allowlist, joins the internal
egress network only, and still runs only the fixed ``web_fetch`` runner with
an immutable hash-bound spec. The pool never touches the reviewed generic
Agent egress channel.

Lifecycle: containers are created lazily up to ``max_size``, reused while
warm, evicted on any round-trip failure or timeout, and pruned after
``idle_ttl`` seconds of inactivity (lazily on the next acquire and
periodically by the sandbox cleanup sweep). ``atexit`` provides best-effort
shutdown cleanup.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.domain.models import new_id
from app.providers.ports.sandbox import SandboxCreateSpec, SandboxSessionHandle
from app.providers.remote.sandbox import (
    SandboxBackendError,
    SandboxBackendUnavailable,
)
from app.services.sandbox import (
    _initialize_workspace,
    _sandbox_workspace_path,
    web_fetch_egress_envelope,
)
from app.services.sandbox_runtime import resolve_sandbox_image

logger = logging.getLogger(__name__)

_FETCH_RUNTIME_KIND = "python-node"
# Hard safety cap: an operator mistake must never let the pool balloon the
# host with containers. The deployer can pick anything up to this bound.
_MAX_POOL_SIZE = 8


class FetchPoolUnavailable(RuntimeError):
    """The pool could not materialize a fetch container (backend/egress down)."""


class FetchPoolSaturated(RuntimeError):
    """All pooled containers are busy and the wait budget was exhausted."""


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _settings_fingerprint(
    settings: Settings, image_ref: str, backend_id: str
) -> str:
    """Stable fingerprint of everything that changes a fetch container.

    When any of these change (settings reload, image rebuild, egress
    reconfiguration), a new pool key is derived and the old pool is left to be
    pruned by its idle TTL — stale containers are never served to new fetches.
    """
    payload = {
        "backend": backend_id,
        "image": image_ref,
        "memory_bytes": settings.sandbox_memory_bytes,
        "memory_swap_bytes": settings.sandbox_memory_swap_bytes,
        "cpu_count": settings.sandbox_cpu_count,
        "pids_max": settings.sandbox_pids_max,
        "disk_bytes": settings.sandbox_disk_bytes,
        "workspace_root": str(settings.resolved_sandbox_workspace_root),
        "workspace_uid": settings.sandbox_workspace_uid,
        "runtime_kind": _FETCH_RUNTIME_KIND,
        "egress_enabled": settings.sandbox_egress_enabled,
        "egress_network": settings.sandbox_egress_network,
        "egress_proxy_url": settings.sandbox_egress_proxy_url,
        "egress_policy_dir": settings.sandbox_egress_policy_dir,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class FetchPoolKey:
    namespace: str
    workspace_id: str
    domains: tuple[str, ...]
    fingerprint: str


@dataclass(eq=False, slots=True)
class _PoolEntry:
    # Identity-based equality/hash: entries are unique objects tracked in sets
    # and deques; fields (last_used) mutate over their lifetime.
    handle: SandboxSessionHandle
    workspace_relative: str
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)


class WebFetchContainerPool:
    """A bounded set of warm ``web_fetch`` containers for one pool key."""

    def __init__(
        self,
        *,
        backend: Any,
        settings: Settings,
        workspace_id: str,
        allowed_domains: frozenset[str],
        image_ref: str,
        max_size: int,
        idle_ttl: int,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.workspace_id = workspace_id
        self.allowed_domains = allowed_domains
        self.image_ref = image_ref
        self.max_size = max(1, min(int(max_size), _MAX_POOL_SIZE))
        self.idle_ttl = max(1, int(idle_ttl))
        self._cond = threading.Condition()
        self._entries: list[_PoolEntry] = []
        self._idle: deque[_PoolEntry] = deque()
        self._busy: set[_PoolEntry] = set()
        self._reserving = 0
        # Cold-start container creation is serialized: it touches shared host
        # state (workspace root mkdir, egress policy file, Docker Engine) that
        # is not safe under concurrent first-use on Windows.
        self._create_lock = threading.Lock()

    # --- public API ---------------------------------------------------------

    def acquire(self, *, timeout_seconds: float) -> _PoolEntry:
        """Return an exclusive entry, creating a container when needed.

        Waits up to ``timeout_seconds`` for an idle slot when the pool is
        saturated. Raises ``FetchPoolSaturated`` when the wait budget is
        exhausted, and ``FetchPoolUnavailable`` / backend errors when a new
        container cannot be created.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            self._prune_expired()
            with self._cond:
                if self._idle:
                    entry = self._idle.popleft()
                    self._busy.add(entry)
                    entry.last_used = time.monotonic()
                    return entry
                if len(self._entries) + self._reserving < self.max_size:
                    # Reserve a slot so concurrent acquirers cannot overshoot
                    # max_size while we create the container outside the lock.
                    self._reserving += 1
                    create = True
                else:
                    create = False
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise FetchPoolSaturated(
                            "Sandbox web fetch concurrency limit reached"
                        )
                    self._cond.wait(timeout=min(remaining, 10.0))
            if create:
                try:
                    with self._create_lock:
                        entry = self._create_entry()
                except Exception:
                    with self._cond:
                        self._reserving -= 1
                        self._cond.notify_all()
                    raise
                with self._cond:
                    self._reserving -= 1
                    self._entries.append(entry)
                    self._busy.add(entry)
                    self._cond.notify_all()
                return entry

    def release(self, entry: _PoolEntry) -> None:
        with self._cond:
            if entry in self._busy:
                self._busy.discard(entry)
                entry.last_used = time.monotonic()
                self._idle.append(entry)
            self._cond.notify_all()

    def evict(self, entry: _PoolEntry) -> None:
        """Permanently remove an entry (failed/timed-out container)."""
        with self._cond:
            self._busy.discard(entry)
            try:
                self._idle.remove(entry)
            except ValueError:
                pass
            try:
                self._entries.remove(entry)
            except ValueError:
                pass
            self._cond.notify_all()
        self._destroy_entry(entry)

    def prune_idle(self) -> int:
        """Evict every idle entry past its idle TTL. Returns evicted count."""
        return self._prune_expired()

    def close(self) -> int:
        """Evict every entry (used by the sweep / atexit). Returns count."""
        with self._cond:
            entries = list(self._entries)
            self._entries.clear()
            self._idle.clear()
            self._busy.clear()
            self._cond.notify_all()
        for entry in entries:
            self._destroy_entry(entry)
        return len(entries)

    def size(self) -> int:
        with self._cond:
            return len(self._entries)

    # --- internals ----------------------------------------------------------

    def _prune_expired(self) -> int:
        now = time.monotonic()
        with self._cond:
            expired: list[_PoolEntry] = []
            kept: deque[_PoolEntry] = deque()
            for entry in self._idle:
                (expired if entry.last_used + self.idle_ttl < now else kept).append(entry)
            self._idle = kept
            for entry in expired:
                try:
                    self._entries.remove(entry)
                except ValueError:
                    pass
        for entry in expired:
            self._destroy_entry(entry)
        return len(expired)

    def _create_entry(self) -> _PoolEntry:
        capability = self.backend.probe()
        if not capability.available:
            raise FetchPoolUnavailable(
                capability.reason or "The sandbox runtime is unavailable"
            )
        egress = web_fetch_egress_envelope(
            self.settings, self.workspace_id, self.allowed_domains
        )
        if egress is None:
            raise FetchPoolUnavailable(
                "Sandbox web fetch requires enabled egress and a non-empty allowlist"
            )
        session_id = f"fetchpool-{new_id()}"
        relative = _initialize_workspace(
            self.settings, self._owner_token(), session_id
        )
        workspace_path = _sandbox_workspace_path(self.settings, relative)
        try:
            handle = self.backend.create(
                SandboxCreateSpec(
                    session_id=session_id,
                    image_ref=self.image_ref,
                    memory_bytes=self.settings.sandbox_memory_bytes,
                    memory_swap_bytes=self.settings.sandbox_memory_swap_bytes,
                    cpu_count=self.settings.sandbox_cpu_count,
                    pids_max=self.settings.sandbox_pids_max,
                    disk_bytes=self.settings.sandbox_disk_bytes,
                    workspace_path=str(workspace_path),
                    runtime_kind=_FETCH_RUNTIME_KIND,
                    egress=egress,
                )
            )
        except Exception:
            shutil.rmtree(workspace_path, ignore_errors=True)
            raise
        return _PoolEntry(handle=handle, workspace_relative=relative)

    def _owner_token(self) -> str:
        # Ops-friendly owner prefix: identifies pool workspaces under the
        # sandbox root without leaking domains into the filesystem path.
        digest = hashlib.sha256(
            _canonical_json(
                {
                    "workspace_id": self.workspace_id,
                    "domains": sorted(self.allowed_domains),
                }
            )
        ).hexdigest()[:12]
        return f"fetchpool-{digest}"

    def _destroy_entry(self, entry: _PoolEntry) -> None:
        try:
            self.backend.delete(entry.handle)
        except Exception:
            logger.exception(
                "Failed to clean pooled web fetch container %s",
                entry.handle.session_id,
            )
        shutil.rmtree(
            _sandbox_workspace_path(self.settings, entry.workspace_relative),
            ignore_errors=True,
        )


# --- process-wide registry ---------------------------------------------------

_registry: dict[FetchPoolKey, WebFetchContainerPool] = {}
_registry_lock = threading.Lock()


def pool_key(
    *,
    namespace: str,
    workspace_id: str,
    allowed_domains: frozenset[str],
    fingerprint: str,
) -> FetchPoolKey:
    return FetchPoolKey(
        namespace=namespace,
        workspace_id=workspace_id,
        domains=tuple(sorted(allowed_domains)),
        fingerprint=fingerprint,
    )


def get_fetch_pool(
    *,
    namespace: str,
    backend: Any,
    settings: Settings,
    workspace_id: str,
    allowed_domains: frozenset[str],
    max_size: int,
    idle_ttl: int,
) -> WebFetchContainerPool:
    """Return the process-wide pool for this workspace/domain/settings tuple.

    Provider instances are constructed per request; the registry lets them all
    share one warm pool so containers survive across requests.
    """
    image_ref = resolve_sandbox_image(settings) or ""
    fingerprint = _settings_fingerprint(settings, image_ref, getattr(backend, "backend_id", "docker"))
    key = pool_key(
        namespace=namespace,
        workspace_id=workspace_id,
        allowed_domains=allowed_domains,
        fingerprint=fingerprint,
    )
    with _registry_lock:
        pool = _registry.get(key)
        if pool is None:
            pool = WebFetchContainerPool(
                backend=backend,
                settings=settings,
                workspace_id=workspace_id,
                allowed_domains=allowed_domains,
                image_ref=image_ref,
                max_size=max_size,
                idle_ttl=idle_ttl,
            )
            _registry[key] = pool
        return pool


def prune_fetch_pools() -> dict[str, int]:
    """Evict idle containers across every registered pool.

    Called by the periodic sandbox cleanup sweep so a long-idle process can
    never leak warm containers; also safe to call from tests.
    """
    with _registry_lock:
        pools = list(_registry.values())
    totals = {"evicted": 0, "pools": len(pools)}
    for pool in pools:
        totals["evicted"] += pool.prune_idle()
    return totals


def close_fetch_pools() -> dict[str, int]:
    """Evict every pooled container (best-effort, registered via atexit)."""
    with _registry_lock:
        pools = list(_registry.values())
        _registry.clear()
    totals = {"closed": 0, "pools": len(pools)}
    for pool in pools:
        try:
            totals["closed"] += pool.close()
        except Exception:
            logger.exception("Web fetch pool shutdown cleanup failed")
    return totals


atexit.register(close_fetch_pools)
