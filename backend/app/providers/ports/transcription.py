from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    duration_seconds: float | None = None
    request_id: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class TranscriptionProviderPort(Protocol):
    provider_id: str
    model_id: str
    available: bool
    remote_capability: bool

    def transcribe(
        self,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        language: str | None = None,
    ) -> TranscriptionResult: ...
