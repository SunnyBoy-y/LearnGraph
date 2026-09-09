"""Provider-neutral contracts for streaming speech synthesis.

The port deliberately has no Pipecat dependency.  The realtime voice runtime
can adapt these chunks to ``TTSAudioRawFrame`` while HTTP/API code can keep the
provider manager usable when the optional voice extra is not installed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TTSAudioChunk:
    audio: bytes
    sample_rate: int = 24_000
    channels: int = 1
    session_id: str | None = None


class TTSProviderPort(Protocol):
    provider_id: str
    model_id: str
    available: bool
    remote_capability: bool

    async def stream(
        self, text: str, *, session_id: str | None = None
    ) -> AsyncIterator[TTSAudioChunk]: ...

    async def cancel(self, session_id: str | None = None) -> None: ...

    async def close(self) -> None: ...
