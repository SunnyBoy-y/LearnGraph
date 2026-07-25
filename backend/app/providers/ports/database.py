from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderProbe:
    provider_kind: str
    capability: str
    status: str
    configured: bool
    driver_available: bool
    connection_verified: bool
    details: dict[str, Any] = field(default_factory=dict)
