"""火山引擎 语音合成 2.0（双向流式）— Pipecat TTSService 适配。

复用火山「双向流式语音合成」二进制 WebSocket 协议（与忆伴
Agent/src/voice/tts/volcengine_tts/__init__.py 一致）。
输出 encoding=pcm（16bit mono PCM），sample_rate 与 Pipecat audio_out 对齐。
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, AsyncGenerator, Optional

import websockets
from loguru import logger
from pipecat.frames.frames import Frame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

DEFAULT_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"


class MsgType(IntEnum):
    FullClientRequest = 0b0001
    FullServerResponse = 0b1001
    AudioOnlyServer = 0b1011
    Error = 0b1111


class MsgTypeFlagBits(IntEnum):
    NoSeq = 0
    WithEvent = 0b0100


class EventType(IntEnum):
    StartConnection = 1
    FinishConnection = 2
    ConnectionStarted = 50
    ConnectionFailed = 51
    ConnectionFinished = 52
    StartSession = 100
    CancelSession = 101
    FinishSession = 102
    SessionStarted = 150
    SessionCanceled = 151
    SessionFinished = 152
    SessionFailed = 153
    TaskRequest = 200
    TTSSentenceStart = 350
    TTSSentenceEnd = 351
    TTSResponse = 352
    TTSEnded = 359


CONNECTION_EVENTS = {
    EventType.StartConnection,
    EventType.FinishConnection,
    EventType.ConnectionStarted,
    EventType.ConnectionFailed,
    EventType.ConnectionFinished,
}


@dataclass
class Message:
    type: MsgType = MsgType.FullClientRequest
    flag: MsgTypeFlagBits = MsgTypeFlagBits.NoSeq
    event: Any = 0
    session_id: str = ""
    connect_id: str = ""
    error_code: int = 0
    payload: bytes = b""

    def marshal(self) -> bytes:
        buffer = io.BytesIO()
        buffer.write(bytes([0x11, (self.type << 4) | self.flag, 0x10, 0x00]))
        if self.flag == MsgTypeFlagBits.WithEvent:
            buffer.write(struct.pack(">i", int(self.event)))
            if self.event not in CONNECTION_EVENTS:
                sid = self.session_id.encode("utf-8")
                buffer.write(struct.pack(">I", len(sid)))
                buffer.write(sid)
        buffer.write(struct.pack(">I", len(self.payload)))
        buffer.write(self.payload)
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Message":
        if len(data) < 8:
            raise ValueError(f"message too short: {len(data)} bytes")
        msg_type = MsgType(data[1] >> 4)
        flag = MsgTypeFlagBits(data[1] & 0x0F)
        msg = cls(type=msg_type, flag=flag)
        offset = 4 * (data[0] & 0x0F)

        if msg.type == MsgType.Error:
            msg.error_code = struct.unpack(">I", data[offset : offset + 4])[0]
            offset += 4

        if msg.flag == MsgTypeFlagBits.WithEvent:
            event_value = struct.unpack(">i", data[offset : offset + 4])[0]
            offset += 4
            try:
                msg.event = EventType(event_value)
            except ValueError:
                msg.event = event_value

            if msg.event not in CONNECTION_EVENTS:
                size = struct.unpack(">I", data[offset : offset + 4])[0]
                offset += 4
                msg.session_id = data[offset : offset + size].decode("utf-8")
                offset += size

            if msg.event == EventType.ConnectionFinished:
                size = struct.unpack(">I", data[offset : offset + 4])[0]
                offset += 4
                msg.connect_id = data[offset : offset + size].decode("utf-8")
                offset += size

        size = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        msg.payload = data[offset : offset + size]
        return msg


def get_legacy_resource_id(voice: str) -> str:
    if voice.startswith("S_"):
        return "volc.megatts.default"
    return "volc.service_type.10029"


def get_v3_resource_id(voice: str) -> str:
    voice = str(voice or "")
    if voice.startswith("S_"):
        return "seed-icl-2.0"
    if "_uranus_" in voice or "_saturn_" in voice:
        return "seed-tts-2.0"
    if "_moon_" in voice or "_mars_" in voice or "_bigtts" in voice:
        return "seed-tts-1.0"
    if voice.startswith("ICL_") and voice.endswith("_tob"):
        return "seed-tts-1.0"
    if voice.startswith("ICL_"):
        return "seed-icl-2.0"
    return "seed-tts-1.0-concurr"


def resolve_resource_id(endpoint: str, resource_id: str, api_key: str, voice: str) -> str:
    if resource_id:
        return resource_id
    if "/api/v3/" in endpoint:
        return get_v3_resource_id(voice)
    return get_legacy_resource_id(voice)


@dataclass
class TTSRequestOptions:
    voice_type: str
    encoding: str = "pcm"
    sample_rate: int = 24000
    emotion: Optional[str] = None
    speech_rate: Optional[int] = None
    model: Optional[str] = None
    uid: Optional[str] = None

    def to_payload(self, text: str) -> dict:
        audio_params: dict = {"format": self.encoding, "sample_rate": self.sample_rate}
        if self.emotion is not None:
            audio_params["emotion"] = self.emotion
        if self.speech_rate is not None:
            audio_params["speech_rate"] = self.speech_rate
        req_params: dict = {"speaker": self.voice_type, "audio_params": audio_params}
        if text:
            req_params["text"] = text
        if self.model:
            req_params["model"] = self.model
        return {"user": {"uid": self.uid or str(uuid.uuid4())}, "req_params": req_params}

    def to_v3_start_session_payload(self, text: str, namespace: str) -> dict:
        payload = self.to_payload(text)
        payload["event"] = int(EventType.StartSession)
        payload["namespace"] = namespace
        return payload

    def to_v3_task_payload(self, text: str, namespace: str = "BidirectionalTTS") -> dict:
        payload = self.to_payload(text)
        payload["event"] = int(EventType.TaskRequest)
        payload["namespace"] = namespace
        return payload


@dataclass
class TTSStreamEvent:
    kind: str
    event: Any
    audio: bytes = b""
    data: Any = None
    session_id: str = ""


def decode_payload(payload: bytes) -> Any:
    if not payload:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return payload.decode("utf-8", "ignore")


@dataclass
class VolcengineTTSSettings(TTSSettings):
    api_key: str = ""
    voice_type: str = "ICL_uranus_zh_female_heainainai_tob"
    emotion: str = ""
    speech_rate: int = 0
    endpoint: str = DEFAULT_ENDPOINT
    resource_id: str = ""


class VolcengineTTSService(TTSService):
    """火山引擎语音合成 2.0（双向流式）服务。"""

    Settings = VolcengineTTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str = "",
        settings: VolcengineTTSSettings | None = None,
        **kwargs,
    ):
        if settings is None:
            settings = self.Settings(model="seed-tts-2.0-standard")
        settings.api_key = api_key
        settings.voice = None
        settings.language = None
        super().__init__(settings=settings, **kwargs)
        self._ws: Any = None
        self._options: TTSRequestOptions | None = None
        self._logid = ""
        # 当前正在合成的 session_id，用于 interruption 取消与 stale audio 过滤。
        self._current_session_id = ""

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        s = self._settings
        self._options = TTSRequestOptions(
            voice_type=s.voice_type,
            encoding="pcm",
            sample_rate=self.sample_rate,
            emotion=s.emotion or None,
            speech_rate=s.speech_rate or None,
            model=s.model,
        )
        await self._connect_bidirectional()

    def _headers(self) -> dict:
        s = self._settings
        resource_id = resolve_resource_id(
            s.endpoint, s.resource_id, s.api_key, self._options.voice_type
        )
        return {
            "X-Api-Resource-Id": resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Key": s.api_key,
        }

    async def _connect_bidirectional(self) -> None:
        self._ws = await websockets.connect(
            self._settings.endpoint,
            additional_headers=self._headers(),
            max_size=10 * 1024 * 1024,
            open_timeout=15,
        )
        self._logid = self._ws.response.headers.get("x-tt-logid", "")
        await self._send_event(EventType.StartConnection)
        await self._expect_event(EventType.ConnectionStarted, EventType.ConnectionFailed)
        logger.info(f"{self}: connected to {self._settings.endpoint}")

    async def on_audio_context_interrupted(self, context_id: str):
        """打断时向火山发送 CancelSession，主动结束当前合成并释放服务端资源。

        Pipecat 基类收到 InterruptionFrame 会停止 run_tts 的 audio context task，
        但火山服务端仍可能在后台继续合成。若不显式取消，切割后的残余音频会
        继续占用服务端资源、甚至混入下一轮。这里发 CancelSession 让火山立即
        放弃当前 session（收到 SessionCanceled 后服务端释放资源）。
        """
        await self._cancel_current_session("interrupted")

    async def _cancel_current_session(self, reason: str) -> None:
        session_id = self._current_session_id
        if not session_id:
            return
        if not self._ws:
            self._current_session_id = ""
            return
        try:
            await self._send_event(EventType.CancelSession, session_id)
            logger.debug(f"{self}: CancelSession({reason}) for session {session_id}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: cancel session({reason}) failed: {exc}")
        finally:
            self._current_session_id = ""

    async def _send_event(
        self, event: EventType, session_id: str = "", payload: Optional[dict] = None
    ) -> None:
        body = json.dumps(payload if payload is not None else {}, ensure_ascii=False).encode("utf-8")
        msg = Message(
            type=MsgType.FullClientRequest,
            flag=MsgTypeFlagBits.WithEvent,
            event=event,
            session_id=session_id,
            payload=body,
        )
        await self._ws.send(msg.marshal())

    async def _expect_event(self, success: EventType, failure: EventType) -> Message:
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
            if not isinstance(raw, bytes):
                raise RuntimeError(f"unexpected text frame: {raw!r}")
            msg = Message.from_bytes(raw)
            if msg.type == MsgType.Error:
                raise RuntimeError(f"TTS failed: {msg.error_code} {decode_payload(msg.payload)}")
            if msg.type == MsgType.FullServerResponse and msg.event == success:
                return msg
            if msg.type == MsgType.FullServerResponse and msg.event == failure:
                raise RuntimeError(f"TTS handshake failed: {failure} {decode_payload(msg.payload)}")

    async def run_tts(
        self, text: str, context_id: str
    ) -> AsyncGenerator[Frame | None, None]:
        if not text.strip():
            yield None
            return
        session_id = str(uuid.uuid4())
        self._current_session_id = session_id
        options = self._options
        try:
            await self._send_event(
                EventType.StartSession,
                session_id,
                options.to_v3_start_session_payload("", "BidirectionalTTS"),
            )
            await self._expect_event(EventType.SessionStarted, EventType.SessionFailed)
            await self._send_event(
                EventType.TaskRequest,
                session_id,
                options.to_v3_task_payload(text),
            )
            await self._send_event(EventType.FinishSession, session_id)

            while True:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                if not isinstance(raw, bytes):
                    raise RuntimeError(f"unexpected text frame: {raw!r}")
                msg = Message.from_bytes(raw)
                if msg.type == MsgType.AudioOnlyServer:
                    # 仅收当前 session 的音频，过滤打断残留的 stale audio。
                    # 音频帧一定带 session_id；带但非当前 -> stale 丢弃。
                    if msg.session_id and msg.session_id != self._current_session_id:
                        continue
                    if msg.payload:
                        yield TTSAudioRawFrame(
                            msg.payload, self.sample_rate, 1, context_id=context_id
                        )
                elif msg.type == MsgType.FullServerResponse:
                    data = decode_payload(msg.payload)
                    if msg.event in (
                        EventType.SessionFinished,
                        EventType.SessionCanceled,
                        EventType.TTSEnded,
                    ):
                        if (
                            isinstance(data, dict)
                            and data.get("status_code") not in (None, 20000000)
                        ):
                            raise RuntimeError(
                                f"TTS session failed: {data.get('status_code')} {data.get('message', '')}"
                            )
                        break
                    if msg.event == EventType.SessionFailed:
                        raise RuntimeError(f"TTS session failed: {data}")
                elif msg.type == MsgType.Error:
                    raise RuntimeError(
                        f"TTS failed: {msg.error_code} {decode_payload(msg.payload)}"
                    )
        finally:
            # 仅当 session 尚未被 interruption 取消时清空；被取消时保留取消标记由调用方判定
            if self._current_session_id == session_id:
                self._current_session_id = ""

        yield None

    async def cleanup(self):
        await super().cleanup()
        if self._ws:
            try:
                msg = Message(
                    type=MsgType.FullClientRequest,
                    flag=MsgTypeFlagBits.WithEvent,
                    event=EventType.FinishConnection,
                    payload=b"{}",
                )
                await self._ws.send(msg.marshal())
                await asyncio.wait_for(self._ws.recv(), timeout=1)
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
