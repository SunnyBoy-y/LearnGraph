"""Backend registry for sandbox runtimes (Phase 1 of the sandboxd migration).

Every persisted sandbox session records the backend that created it
(``SandboxSession.backend_id``). The registry is the single place that maps a
backend id to a ``SandboxBackendPort`` implementation:

- creating NEW sessions uses the configured default backend;
- resuming / stopping / deleting EXISTING sessions MUST use the backend
  recorded on the session — never the current default.

Today only the explicit ``docker`` backend is registered; ``sandboxd`` is
reserved for the daemon migration (see docs/sandboxd-migration-plan.md) and
fails closed until it is implemented and registered here. Unknown backend ids
must never silently fall back to the default backend: that would let a stale
or tampered session resume/delete resources through the wrong runtime.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from app.core.config import Settings
from app.providers.ports.sandbox import SandboxBackendPort
from app.providers.remote.sandbox import SandboxBackendUnavailable

#: Builds a Port adapter for ``(settings, runtime_kind)``.
BackendFactory = Callable[[Settings, str], SandboxBackendPort]


@dataclass(frozen=True, slots=True)
class SandboxBackendProvider:
    """A named backend id plus the factory that constructs its Port adapter."""

    backend_id: str
    factory: BackendFactory


class SandboxBackendRegistry:
    """Maps backend ids to Port adapters.

    Instances are stateless and cheap; the process-wide default is created by
    ``get_sandbox_backend_registry()``. Callers may also build their own
    registry with fake providers for tests.
    """

    def __init__(self) -> None:
        self._providers: dict[str, SandboxBackendProvider] = {}
        self._lock = threading.Lock()

    def register(self, provider: SandboxBackendProvider) -> None:
        if not provider.backend_id or not provider.factory:
            raise ValueError("Sandbox backend provider requires an id and a factory")
        with self._lock:
            self._providers[provider.backend_id.strip().casefold()] = provider

    def default(
        self, settings: Settings, runtime_kind: str = "python-node"
    ) -> SandboxBackendPort:
        """Backend used to create NEW sandbox sessions."""
        return self.for_backend_id(settings.sandbox_backend, settings, runtime_kind)

    def for_backend_id(
        self,
        backend_id: str,
        settings: Settings,
        runtime_kind: str = "python-node",
    ) -> SandboxBackendPort:
        """Backend for an EXISTING session recorded with ``backend_id``.

        Unknown ids fail closed: a session must never be resumed or deleted
        through a different backend than the one that created it.
        """
        provider = self._providers.get((backend_id or "").strip().casefold())
        if provider is None:
            raise SandboxBackendUnavailable(
                f"Sandbox backend {backend_id!r} is not registered; cannot route the session"
            )
        return provider.factory(settings, runtime_kind)

    def backend_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._providers))


class SandboxManager:
    """Thin high-level facade over a registry for business services.

    Kept deliberately free of ORM reads and authorization: services decide the
    session/actor rules, the manager only resolves a backend Port.
    """

    def __init__(self, registry: SandboxBackendRegistry | None = None) -> None:
        self.registry = registry or get_sandbox_backend_registry()

    def default_backend(
        self, settings: Settings, runtime_kind: str = "python-node"
    ) -> SandboxBackendPort:
        return self.registry.default(settings, runtime_kind)

    def backend_for_session(
        self,
        backend_id: str,
        settings: Settings,
        runtime_kind: str = "python-node",
    ) -> SandboxBackendPort:
        return self.registry.for_backend_id(backend_id, settings, runtime_kind)


_registry: SandboxBackendRegistry | None = None
_registry_lock = threading.Lock()


def _docker_backend_for(
    settings: Settings, runtime_kind: str = "python-node"
) -> SandboxBackendPort:
    # Lazy import keeps this module import-cycle-free: sandbox_bootstrap
    # imports providers/remote/sandbox but never this registry.
    from app.services.sandbox_bootstrap import backend_for_settings

    return backend_for_settings(settings, runtime_kind)


def _sandboxd_backend_for(
    settings: Settings, runtime_kind: str = "python-node"
) -> SandboxBackendPort:
    # Lazy import keeps the registry import-cycle-free.
    from app.providers.remote.sandboxd_backend import SandboxdBackend

    return SandboxdBackend(settings, runtime_kind)


def get_sandbox_backend_registry() -> SandboxBackendRegistry:
    """Process-wide registry with the explicit ``docker`` and ``sandboxd`` backends."""
    global _registry
    with _registry_lock:
        if _registry is None:
            registry = SandboxBackendRegistry()
            registry.register(SandboxBackendProvider("docker", _docker_backend_for))
            registry.register(SandboxBackendProvider("sandboxd", _sandboxd_backend_for))
            _registry = registry
        return _registry
