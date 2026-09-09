"""Volcengine Bidirectional TTS 2.0 provider adapter.

This is the transport-only implementation extracted from the verified
``demo-voice2o2`` shell.  It intentionally does not import Pipecat; a voice
runtime may wrap :class:`VolcengineBidirectionalTTSProvider` and translate
``TTSAudioChunk`` into its own audio frame type.
"""

from __future__ import annotations

import asyncio
import io
import inspect
import json
import struct
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable

from app.providers.ports.tts import TTSAudioChunk

DEFAULT_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
# ``seed-tts-2.0-concurr`` is a Volcengine resource ID, not a selectable
# model.  Keep the model catalog aligned with the model-driven Provider UI.
SUPPORTED_MODELS = frozenset({"seed-tts-2.0-standard"})


class VolcengineTTSError(RuntimeError):
    """Raised when the Volcengine stream cannot be established or completed."""


class MsgType(IntEnum):
    FULL_CLIENT_REQUEST = 0b0001
    FULL_SERVER_RESPONSE = 0b1001
    AUDIO_ONLY_SERVER = 0b1011
    ERROR = 0b1111


class MsgTypeFlagBits(IntEnum):
    NO_SEQ = 0
    WITH_EVENT = 0b0100


class EventType(IntEnum):
    START_CONNECTION = 1
    FINISH_CONNECTION = 2
    CONNECTION_STARTED = 50
    CONNECTION_FAILED = 51
    CONNECTION_FINISHED = 52
    START_SESSION = 100
    CANCEL_SESSION = 101
    FINISH_SESSION = 102
    SESSION_STARTED = 150
    SESSION_CANCELED = 151
    SESSION_FINISHED = 152
    SESSION_FAILED = 153
    TASK_REQUEST = 200
    TTS_ENDED = 359


CONNECTION_EVENTS = {
    EventType.START_CONNECTION,
    EventType.FINISH_CONNECTION,
    EventType.CONNECTION_STARTED,
    EventType.CONNECTION_FAILED,
    EventType.CONNECTION_FINISHED,
}


@dataclass(slots=True)
class _Message:
    type: MsgType = MsgType.FULL_CLIENT_REQUEST
    flag: MsgTypeFlagBits = MsgTypeFlagBits.NO_SEQ
    event: EventType | int = 0
    session_id: str = ""
    connect_id: str = ""
    error_code: int = 0
    payload: bytes = b""

    def marshal(self) -> bytes:
        buffer = io.BytesIO()
        buffer.write(bytes([0x11, (int(self.type) << 4) | int(self.flag), 0x10, 0x00]))
        if self.flag == MsgTypeFlagBits.WITH_EVENT:
            buffer.write(struct.pack(">i", int(self.event)))
            if self.event not in CONNECTION_EVENTS:
                encoded = self.session_id.encode("utf-8")
                buffer.write(struct.pack(">I", len(encoded)))
                buffer.write(encoded)
        buffer.write(struct.pack(">I", len(self.payload)))
        buffer.write(self.payload)
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "_Message":
        if len(data) < 8:
            raise VolcengineTTSError(f"Volcengine TTS message too short: {len(data)}")
        try:
            msg_type = MsgType(data[1] >> 4)
            flag = MsgTypeFlagBits(data[1] & 0x0F)
        except ValueError as exc:
            raise VolcengineTTSError("Volcengine TTS returned an unknown message type") from exc
        msg = cls(type=msg_type, flag=flag)
        offset = 4 * (data[0] & 0x0F)
        if msg.type == MsgType.ERROR:
            if len(data) < offset + 4:
                raise VolcengineTTSError("Volcengine TTS error frame is truncated")
            msg.error_code = struct.unpack(">I", data[offset : offset + 4])[0]
            offset += 4
        if msg.flag == MsgTypeFlagBits.WITH_EVENT:
            if len(data) < offset + 4:
                raise VolcengineTTSError("Volcengine TTS event frame is truncated")
            event_value = struct.unpack(">i", data[offset : offset + 4])[0]
            offset += 4
            try:
                msg.event = EventType(event_value)
            except ValueError:
                msg.event = event_value
            if msg.event not in CONNECTION_EVENTS:
                if len(data) < offset + 4:
                    raise VolcengineTTSError("Volcengine TTS session frame is truncated")
                size = struct.unpack(">I", data[offset : offset + 4])[0]
                offset += 4
                if len(data) < offset + size:
                    raise VolcengineTTSError("Volcengine TTS session id is truncated")
                msg.session_id = data[offset : offset + size].decode("utf-8", "replace")
                offset += size
            if msg.event == EventType.CONNECTION_FINISHED:
                if len(data) < offset + 4:
                    raise VolcengineTTSError("Volcengine TTS connection id is truncated")
                size = struct.unpack(">I", data[offset : offset + 4])[0]
                offset += 4
                msg.connect_id = data[offset : offset + size].decode("utf-8", "replace")
                offset += size
        if len(data) < offset + 4:
            raise VolcengineTTSError("Volcengine TTS payload length is missing")
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        msg.payload = data[offset : offset + size]
        return msg


def _decode_payload(payload: bytes) -> Any:
    if not payload:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return payload.decode("utf-8", "replace")


def _resource_id(endpoint: str, voice_type: str, configured: str) -> str:
    if configured:
        return configured
    voice = str(voice_type or "")
    if "/api/v3/" in endpoint:
        if voice.startswith("S_"):
            return "seed-icl-2.0"
        if "_uranus_" in voice or "_saturn_" in voice:
            return "seed-tts-2.0"
        if "_moon_" in voice or "_mars_" in voice or "_bigtts" in voice:
            return "seed-tts-1.0"
        if voice.startswith("ICL_"):
            return "seed-icl-2.0" if not voice.endswith("_tob") else "seed-tts-1.0"
        return "seed-tts-1.0-concurr"
    return "volc.megatts.default" if voice.startswith("S_") else "volc.service_type.10029"


@dataclass(frozen=True, slots=True)
class TTSRequestOptions:
    voice_type: str = "ICL_uranus_zh_female_heainainai_tob"
    sample_rate: int = 24_000
    emotion: str | None = None
    speech_rate: int | None = None
    model: str | None = "seed-tts-2.0-standard"
    uid: str | None = None

    def payload(self, text: str, *, event: EventType) -> dict[str, Any]:
        audio_params: dict[str, Any] = {"format": "pcm", "sample_rate": self.sample_rate}
        if self.emotion:
            audio_params["emotion"] = self.emotion
        if self.speech_rate:
            audio_params["speech_rate"] = self.speech_rate
        request: dict[str, Any] = {
            "user": {"uid": self.uid or str(uuid.uuid4())},
            "req_params": {"speaker": self.voice_type, "audio_params": audio_params},
            "event": int(event),
            "namespace": "BidirectionalTTS",
        }
        if text:
            request["req_params"]["text"] = text
        if self.model:
            request["req_params"]["model"] = self.model
        return request


class VolcengineBidirectionalTTSProvider:
    """Streaming Huoshan/Volcengine TTS with explicit cancellation semantics."""

    available = True
    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str = DEFAULT_ENDPOINT,
        api_key: str,
        voice_type: str = "ICL_uranus_zh_female_heainainai_tob",
        resource_id: str = "",
        sample_rate: int = 24_000,
        emotion: str | None = None,
        speech_rate: int | None = None,
        websocket_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Volcengine TTS requires an API key")
        if not base_url.startswith(("ws://", "wss://")):
            raise ValueError("Volcengine TTS endpoint must use ws:// or wss://")
        self.provider_id, self.model_id = provider_id, model_id or "seed-tts-2.0-standard"
        self.base_url, self.api_key = base_url.rstrip("/"), api_key
        self.options = TTSRequestOptions(
            voice_type=voice_type,
            sample_rate=max(8_000, min(int(sample_rate), 48_000)),
            emotion=emotion,
            speech_rate=speech_rate,
            model=self.model_id,
        )
        self.resource_id = resource_id
        self._websocket_factory = websocket_factory
        self._ws: Any = None
        self._current_session_id = ""

    async def _connect(self) -> None:
        if self._ws is not None:
            return
        factory = self._websocket_factory
        if factory is None:
            try:
                import websockets
            except ImportError as exc:
                raise VolcengineTTSError(
                    "Install the optional 'voice' extra to use realtime TTS"
                ) from exc
            factory = websockets.connect
        headers = {
            "X-Api-Resource-Id": _resource_id(self.base_url, self.options.voice_type, self.resource_id),
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Key": self.api_key,
        }
        try:
            connection = factory(
                self.base_url,
                additional_headers=headers,
                max_size=10 * 1024 * 1024,
                open_timeout=15,
            )
            self._ws = await connection if inspect.isawaitable(connection) else connection
            await self._send(EventType.START_CONNECTION)
            await self._expect(EventType.CONNECTION_STARTED, EventType.CONNECTION_FAILED)
        except Exception as exc:
            self._ws = None
            if isinstance(exc, VolcengineTTSError):
                raise
            raise VolcengineTTSError("Could not connect to Volcengine TTS") from exc

    async def _send(
        self, event: EventType, session_id: str = "", payload: dict[str, Any] | None = None
    ) -> None:
        if self._ws is None:
            raise VolcengineTTSError("Volcengine TTS is not connected")
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        await self._ws.send(
            _Message(
                flag=MsgTypeFlagBits.WITH_EVENT,
                event=event,
                session_id=session_id,
                payload=body,
            ).marshal()
        )

    async def _expect(self, success: EventType, failure: EventType) -> _Message:
        if self._ws is None:
            raise VolcengineTTSError("Volcengine TTS is not connected")
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
            if not isinstance(raw, bytes):
                raise VolcengineTTSError("Volcengine TTS returned a text frame")
            msg = _Message.from_bytes(raw)
            if msg.type == MsgType.ERROR:
                raise VolcengineTTSError(f"Volcengine TTS error {msg.error_code}: {_decode_payload(msg.payload)}")
            if msg.event == success:
                return msg
            if msg.event == failure:
                raise VolcengineTTSError(f"Volcengine TTS handshake failed: {_decode_payload(msg.payload)}")

    async def stream(
        self, text: str, *, session_id: str | None = None
    ) -> AsyncIterator[TTSAudioChunk]:
        if not text.strip():
            return
        await self._connect()
        sid = session_id or str(uuid.uuid4())
        self._current_session_id = sid
        try:
            await self._send(EventType.START_SESSION, sid, self.options.payload("", event=EventType.START_SESSION))
            await self._expect(EventType.SESSION_STARTED, EventType.SESSION_FAILED)
            await self._send(EventType.TASK_REQUEST, sid, self.options.payload(text, event=EventType.TASK_REQUEST))
            await self._send(EventType.FINISH_SESSION, sid)
            while True:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                if not isinstance(raw, bytes):
                    raise VolcengineTTSError("Volcengine TTS returned a text frame")
                msg = _Message.from_bytes(raw)
                if msg.type == MsgType.ERROR:
                    raise VolcengineTTSError(f"Volcengine TTS error {msg.error_code}: {_decode_payload(msg.payload)}")
                if msg.type == MsgType.AUDIO_ONLY_SERVER:
                    if msg.session_id and msg.session_id != sid:
                        continue
                    if msg.payload and self._current_session_id == sid:
                        yield TTSAudioChunk(msg.payload, self.options.sample_rate, 1, sid)
                elif msg.type == MsgType.FULL_SERVER_RESPONSE:
                    if msg.event == EventType.SESSION_FAILED:
                        raise VolcengineTTSError(f"Volcengine TTS session failed: {_decode_payload(msg.payload)}")
                    if msg.event in {
                        EventType.SESSION_FINISHED,
                        EventType.SESSION_CANCELED,
                        EventType.TTS_ENDED,
                    }:
                        payload = _decode_payload(msg.payload)
                        if isinstance(payload, dict) and payload.get("status_code") not in (None, 20000000):
                            raise VolcengineTTSError(f"Volcengine TTS failed: {payload}")
                        break
        finally:
            if self._current_session_id == sid:
                self._current_session_id = ""

    async def cancel(self, session_id: str | None = None) -> None:
        sid = session_id or self._current_session_id
        if not sid or self._ws is None:
            self._current_session_id = ""
            return
        try:
            await self._send(EventType.CANCEL_SESSION, sid)
        finally:
            self._current_session_id = ""

    async def close(self) -> None:
        ws = self._ws
        self._current_session_id = ""
        if ws is None:
            return
        try:
            await self._send(EventType.FINISH_CONNECTION)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
        self._ws = None
