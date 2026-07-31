from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from app.domain.memory_event_types import CURRENT_EVENT_SCHEMA_VERSIONS

Upcaster = Callable[[dict[str, Any]], dict[str, Any]]


class EventUpcasterRegistry:
    """Pure in-memory version chain; old readers stay supported indefinitely."""

    def __init__(self) -> None:
        self._upcasters: dict[tuple[str, int], Upcaster] = {}

    def register(self, event_type: str, from_version: int, upcaster: Upcaster) -> None:
        key = (event_type, from_version)
        if key in self._upcasters:
            raise ValueError(f"upcaster already registered for {event_type} v{from_version}")
        self._upcasters[key] = upcaster

    def upcast(
        self, event_type: str, schema_version: int, payload: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        current = CURRENT_EVENT_SCHEMA_VERSIONS.get(event_type, schema_version)
        version = schema_version
        value = deepcopy(payload)
        while version < current:
            upcaster = self._upcasters.get((event_type, version))
            if upcaster is None:
                raise ValueError(f"missing upcaster for {event_type} v{version} -> v{version + 1}")
            value = upcaster(value)
            version += 1
        if version > current:
            raise ValueError(f"event {event_type} v{version} is newer than reader v{current}")
        return version, value


upcasters = EventUpcasterRegistry()
