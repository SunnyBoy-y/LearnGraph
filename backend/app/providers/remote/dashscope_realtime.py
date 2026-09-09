"""DashScope qwen3-asr-flash-realtime WebSocket adapter.

The adapter exposes normalized partial/final events and is independent of
Pipecat.  A Pipecat STT service can feed ``send_audio`` and publish events from
``events`` while the existing HTTP dictation proxy may continue using its
legacy bridge.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class RealtimeTranscriptionEvent:
    type: str
    text: str = ""
    final: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class DashScopeRealtimeASRError(RuntimeError):
    pass


def realtime_ws_url(base_url: str, model_id: str) -> str:
    parsed = urlsplit(base_url.strip())
    if not parsed.hostname:
        raise ValueError("DashScope ASR base URL has no hostname")
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    # Provider capabilities may store either the DashScope compatible API base
    # or the fully qualified realtime endpoint.  Do not append the realtime
    # path twice when the latter is supplied by the model catalog/UI.
    path = parsed.path.rstrip("/") or "/"
    if path != "/api-ws/v1/realtime":
        path = "/api-ws/v1/realtime"
    return urlunsplit((scheme, parsed.netloc, path, "model=" + model_id, ""))


class DashScopeRealtimeASRProvider:
    available = True
    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str = "qwen3-asr-flash-realtime",
        base_url: str,
        api_key: str,
        sample_rate: int = 16_000,
        silence_ms: int = 400,
        language: str = "zh",
        websocket_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DashScope realtime ASR requires an API key")
        self.provider_id, self.model_id = provider_id, model_id
        self.base_url, self.api_key = base_url.rstrip("/"), api_key
        self.sample_rate = max(8_000, min(int(sample_rate), 48_000))
        self.silence_ms = max(100, min(int(silence_ms), 3_000))
        self.language = language or "zh"
        self._factory = websocket_factory
        self._ws: Any = None
        self._event_id = 0

    async def connect(self) -> None:
        if self._ws is not None:
            return
        factory = self._factory
        if factory is None:
            try:
                import websockets
            except ImportError as exc:
                raise DashScopeRealtimeASRError(
                    "Install the optional 'voice' extra to use realtime ASR"
                ) from exc
            factory = websockets.connect
        try:
            connection = factory(
                realtime_ws_url(self.base_url, self.model_id),
                additional_headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "OpenAI-Beta": "realtime=v1",
                },
                max_size=16 * 1024 * 1024,
                open_timeout=15,
            )
            self._ws = await connection if inspect.isawaitable(connection) else connection
            await self._send(
                "session.update",
                {
                    "session": {
                        "modalities": ["text"],
                        "input_audio_format": "pcm",
                        "sample_rate": self.sample_rate,
                        "input_audio_transcription": {
                            "model": self.model_id,
                            "language": self.language,
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.0,
                            "silence_duration_ms": self.silence_ms,
                        },
                    }
                },
            )
        except Exception as exc:
            self._ws = None
            if isinstance(exc, DashScopeRealtimeASRError):
                raise
            raise DashScopeRealtimeASRError("Could not connect to DashScope realtime ASR") from exc

    def _next_id(self, kind: str) -> str:
        self._event_id += 1
        return f"evt_{kind}_{self._event_id}"

    async def _send(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        if self._ws is None:
            raise DashScopeRealtimeASRError("DashScope realtime ASR is not connected")
        body = {"event_id": self._next_id(event_type.replace(".", "_")), "type": event_type}
        if payload:
            body.update(payload)
        await self._ws.send(json.dumps(body, ensure_ascii=False))

    async def send_audio(self, audio: bytes) -> None:
        if not audio:
            return
        await self.connect()
        await self._send(
            "input_audio_buffer.append",
            {"audio": base64.b64encode(audio).decode("ascii")},
        )

    async def commit(self) -> None:
        if self._ws is not None:
            await self._send("input_audio_buffer.commit")

    async def events(self) -> AsyncIterator[RealtimeTranscriptionEvent]:
        await self.connect()
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                event_type = str(payload.get("type") or "")
                if event_type.endswith(".text"):
                    text = str(payload.get("text") or "").strip()
                    if text:
                        yield RealtimeTranscriptionEvent(event_type, text=text, raw=payload)
                elif event_type.endswith(".completed"):
                    text = str(payload.get("transcript") or payload.get("text") or "").strip()
                    usage_raw = payload.get("usage")
                    usage = {
                        str(k): int(v)
                        for k, v in usage_raw.items()
                        if isinstance(k, str) and isinstance(v, int)
                    } if isinstance(usage_raw, dict) else {}
                    if text:
                        yield RealtimeTranscriptionEvent(event_type, text=text, final=True, usage=usage, raw=payload)
                elif event_type in {"error", "asr.error"}:
                    raise DashScopeRealtimeASRError(str(payload.get("message") or payload.get("error") or "ASR error"))
        except asyncio.CancelledError:
            raise
        except DashScopeRealtimeASRError:
            raise
        except Exception as exc:
            raise DashScopeRealtimeASRError("DashScope realtime ASR stream ended") from exc

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
