"""B1-3: process-local provider resolution cache.

The provider factory used to run 1-3 DB queries plus one secret decryption per
request. This module keeps detached, non-secret configuration and encrypted
secret payloads in a thread-safe TTL-bounded LRU so hot chat requests skip the
DB round-trips entirely. Decrypted api_key values are NEVER cached; provider
instances are still built per request (they hold api_key / OAuth credentials in
the clear and carry mutable per-call state).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import ProviderConfig, ProviderSecret, WorkspaceSetting

_PROVIDER_PLAN_CACHE_TTL_SECONDS = 60.0
_PROVIDER_PLAN_CACHE_MAX_ENTRIES = 1024
_PROVIDER_PLAN_MISS = object()


@dataclass(frozen=True, slots=True)
class ProviderRowSnapshot:
    """Detached ProviderConfig projection, safe across DB sessions."""

    id: str
    provider_type: str
    base_url: str | None
    enabled: bool
    remote_capability: bool
    capabilities: dict[str, Any]
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProviderSecretSnapshot:
    """Detached ProviderSecret projection; encrypted fields only."""

    ciphertext: str
    algorithm: str
    key_provider: str
    key_version: int
    revoked_at: datetime | None
    updated_at: datetime | None


class _ProviderPlanCache:
    """Thread-safe TTL-bounded LRU keyed by ``(kind, workspace_id, ...)``."""

    def __init__(
        self,
        *,
        ttl_seconds: float = _PROVIDER_PLAN_CACHE_TTL_SECONDS,
        max_entries: int = _PROVIDER_PLAN_CACHE_MAX_ENTRIES,
    ) -> None:
        self._lock = threading.Lock()
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[object, ...], tuple[float | None, object]] = (
            OrderedDict()
        )

    def get(self, key: tuple[object, ...]) -> object:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _PROVIDER_PLAN_MISS
            expires_at, value = entry
            if expires_at is not None and time.monotonic() > expires_at:
                del self._entries[key]
                return _PROVIDER_PLAN_MISS
            self._entries.move_to_end(key)
            return value

    def put(self, key: tuple[object, ...], value: object) -> None:
        with self._lock:
            expires_at = (
                time.monotonic() + self._ttl_seconds
                if self._ttl_seconds > 0
                else None
            )
            self._entries[key] = (expires_at, value)
            self._entries.move_to_end(key)
            if len(self._entries) > self._max_entries:
                self._evict()

    def _evict(self) -> None:
        now = time.monotonic()
        for key in [
            key
            for key, (expires_at, _value) in self._entries.items()
            if expires_at is not None and now > expires_at
        ]:
            del self._entries[key]
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def invalidate(
        self,
        workspace_id: str,
        provider_id: str | None = None,
        *,
        setting_key: str | None = None,
    ) -> None:
        with self._lock:
            stale = []
            for key in self._entries:
                kind = key[0]
                if kind == "setting":
                    if key[1] != workspace_id:
                        continue
                    if setting_key is not None:
                        if key[2] == setting_key:
                            stale.append(key)
                    elif provider_id is None:
                        stale.append(key)
                elif kind in {"row", "secret"}:
                    if key[1] != workspace_id or setting_key is not None:
                        continue
                    if provider_id is None:
                        stale.append(key)
                    elif kind == "secret":
                        if key[2] == provider_id:
                            stale.append(key)
                    elif key[3] in {"", provider_id}:
                        stale.append(key)
            for key in stale:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_provider_plan_cache = _ProviderPlanCache()


def invalidate_provider_plan_cache(
    workspace_id: str,
    provider_id: str | None = None,
    *,
    setting_key: str | None = None,
) -> None:
    """Drop cached provider plans for a workspace.

    Called after ProviderConfig / ProviderSecret / WorkspaceSetting mutations.
    ``provider_id=None`` clears the whole workspace.
    """
    _provider_plan_cache.invalidate(
        workspace_id, provider_id=provider_id, setting_key=setting_key
    )


def clear_provider_plan_cache() -> None:
    """Drop every cached provider plan (process-level reset / tests)."""
    _provider_plan_cache.clear()


def cached_workspace_setting_value(
    db: Session, workspace_id: str, key: str
) -> dict | None:
    cache_key = ("setting", workspace_id, key)
    cached = _provider_plan_cache.get(cache_key)
    if cached is not _PROVIDER_PLAN_MISS:
        return cached
    setting = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace_id,
            WorkspaceSetting.key == key,
        )
    )
    value = (
        setting.value
        if setting is not None and isinstance(setting.value, dict)
        else None
    )
    _provider_plan_cache.put(cache_key, value)
    return value


def cached_provider_rows(
    db: Session,
    workspace_id: str,
    provider_types: frozenset[str] | set[str] | None,
    *,
    provider_id: str | None = None,
    remote_capability: bool | None = None,
    priority_order: tuple[Any, ...] = (),
) -> tuple[ProviderRowSnapshot, ...]:
    types_key = tuple(sorted(provider_types)) if provider_types else ()
    rc_key = (
        "rc"
        if remote_capability is True
        else "norc"
        if remote_capability is False
        else ""
    )
    cache_key = ("row", workspace_id, types_key, provider_id or "", rc_key)
    cached = _provider_plan_cache.get(cache_key)
    if cached is not _PROVIDER_PLAN_MISS:
        return cached
    statement = select(ProviderConfig).where(
        ProviderConfig.workspace_id == workspace_id,
        ProviderConfig.enabled.is_(True),
    )
    if provider_types:
        statement = statement.where(ProviderConfig.provider_type.in_(provider_types))
    if provider_id:
        statement = statement.where(ProviderConfig.id == provider_id)
    if remote_capability is True:
        statement = statement.where(ProviderConfig.remote_capability.is_(True))
    elif remote_capability is False:
        statement = statement.where(ProviderConfig.remote_capability.is_(False))
    if priority_order:
        statement = statement.order_by(*priority_order)
    rows = tuple(
        ProviderRowSnapshot(
            id=row.id,
            provider_type=row.provider_type,
            base_url=row.base_url,
            enabled=row.enabled,
            remote_capability=row.remote_capability,
            capabilities=dict(row.capabilities or {}),
            updated_at=row.updated_at,
        )
        for row in db.scalars(statement).all()
    )
    _provider_plan_cache.put(cache_key, rows)
    return rows


def cached_first_provider_row(
    db: Session,
    workspace_id: str,
    provider_types: frozenset[str] | set[str] | None,
    *,
    provider_id: str | None = None,
    remote_capability: bool | None = None,
    priority_order: tuple[Any, ...] = (),
) -> ProviderRowSnapshot | None:
    rows = cached_provider_rows(
        db,
        workspace_id,
        provider_types,
        provider_id=provider_id,
        remote_capability=remote_capability,
        priority_order=priority_order,
    )
    return rows[0] if rows else None


def cached_secret_for_provider(
    db: Session,
    workspace_id: str,
    provider_id: str,
) -> ProviderSecretSnapshot | None:
    cache_key = ("secret", workspace_id, provider_id)
    cached = _provider_plan_cache.get(cache_key)
    if cached is not _PROVIDER_PLAN_MISS:
        return cached
    secret_record = db.scalar(
        select(ProviderSecret).where(
            ProviderSecret.workspace_id == workspace_id,
            ProviderSecret.provider_id == provider_id,
        )
    )
    if secret_record is None:
        _provider_plan_cache.put(cache_key, None)
        return None
    snapshot = ProviderSecretSnapshot(
        ciphertext=secret_record.ciphertext,
        algorithm=secret_record.algorithm,
        key_provider=secret_record.key_provider,
        key_version=secret_record.key_version,
        revoked_at=secret_record.revoked_at,
        updated_at=secret_record.updated_at,
    )
    _provider_plan_cache.put(cache_key, snapshot)
    return snapshot
