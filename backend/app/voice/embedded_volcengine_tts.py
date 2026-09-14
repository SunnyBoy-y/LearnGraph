"""火山引擎 语音合成 2.0（双向流式）— Pipecat TTSService 适配。

复用火山「双向流式语音合成」二进制 WebSocket 协议（与忆伴
Agent/src/voice/tts/volcengine_tts/__init__.py 一致）。
输出 encoding=pcm（16bit mono PCM），sample_rate 与 Pipecat audio_out 对齐。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import uuid
from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, AsyncGenerator, Optional

import websockets
from loguru import logger
from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStartedFrame
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TextAggregationMode, TTSService

from app.voice.embedded_timeline import timeline_mark

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
        journal: Any = None,
        **kwargs,
    ):
        if settings is None:
            settings = self.Settings(model="seed-tts-2.0-standard")
        settings.api_key = api_key
        settings.voice = None
        settings.language = None
        # 真流式开关：
        #   1（默认）= Pipecat 以句为单位聚合 LLM 增量，同一个火山 session
        #              连续 TaskRequest；第一句开始合成时就下发音频，后续句子
        #              不需要等待整段 LLM 完成。
        #   0        = 旧的“一次 run_tts 一个 session、整段文本一次投喂”。
        # 句级聚合是有意的：火山协议提供 TTSSentenceStart，能够把字幕游标
        # 绑定到对应句子的第一批音频，而不是把整段 bot-output 提前发到前端。
        self._streaming_sentences = os.getenv("VOLC_TTS_STREAMING", "1").lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        super().__init__(
            settings=settings,
            # 句级聚合会在下一个句子的首字符到达时确认边界；单句回答则在
            # LLMFullResponseEndFrame 到达时 flush。每个句子仍复用同一火山 session。
            text_aggregation_mode=(
                TextAggregationMode.SENTENCE if self._streaming_sentences else None
            ),
            **kwargs,
        )
        self._ws: Any = None
        self._options: TTSRequestOptions | None = None
        self._logid = ""
        # 当前正在合成的 session_id，用于 interruption 取消与 stale audio 过滤。
        self._current_session_id = ""
        # 流式模式下这个 session 服务的 audio context，以及它的音频接收任务。
        self._streaming_context_id: str | None = None
        self._receiver_task: asyncio.Task | None = None
        # 单点计时用：本 session 是否已发过第一个 TaskRequest。
        self._first_task_sent = False
        # 火山服务端的 TTSSentenceStart/End 与 TaskRequest 保持顺序；把待合成
        # 的句子排队，等对应句子的第一批音频到达后再发 RTVI 字幕游标。
        self._pending_sentence_texts: deque[str] = deque()
        self._active_sentence_text = ""
        self._active_sentence_marker_sent = False
        self._sentence_sequence = 0
        self._audio_cursor_ms = 0
        # Optional durable journal.  When present, every caption marker is also
        # written to the append-only event log, which is what lets a client that
        # missed data-channel frames reconcile by ``(turn_id, sentence_seq)``
        # instead of by text.
        self._journal = journal
        # Bounded upstream reconnect bookkeeping (TTS websocket).
        self._reconnect_attempt = 0
        self._reconnect_lock = asyncio.Lock()

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
        # 先停接收任务再取消 session，避免接收循环把 SessionCanceled 当成
        # "本会话正常结束"后去动已经被基类移除的 audio context。
        await self._stop_streaming_session("interrupted")
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
        """把 LLM 的文本增量投喂给火山并产出音频。

        流式模式（默认，`VOLC_TTS_STREAMING=1`）：同一个 audio context 内只开
        一个火山 session，Pipecat 每确认一个句子就发送一个 `TaskRequest`；音频
        由后台接收任务写进 audio context。文本结束（`LLMFullResponseEndFrame`
        → `flush_audio`）时才发 `FinishSession`，所以第一句无需等待完整回答。

        非流式模式（`VOLC_TTS_STREAMING=0`）：保持旧行为——每次调用都 StartSession
        → 整段文本一次 TaskRequest → FinishSession → 同步收音频。
        """
        if not self._streaming_sentences:
            async for frame in self._run_tts_one_shot(text, context_id):
                yield frame
            return

        if not text or not text.strip():
            yield None
            return
        if self._ws is None:
            logger.warning(f"{self}: TTS WebSocket 未连接，跳过本轮合成")
            yield None
            return
        try:
            if not self.audio_context_available(context_id):
                # 一个 turn 一个 audio context：这里同时是"开新 session"的时机。
                # 先建 context 并放行 TTSStartedFrame，再握手 + 起接收任务，保证
                # 音频不会排到 TTSStartedFrame 前面。
                await self.create_audio_context(context_id)
                await self.start_ttfb_metrics()
                yield TTSStartedFrame(context_id=context_id)
                await self._start_streaming_session(context_id)
            await self._send_text(text)
            await self.start_tts_usage_metrics(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: run_tts 失败: {exc}")
            await self.push_error(error_msg=f"TTS 合成失败: {exc}", exception=exc)
        yield None

    async def flush_audio(self, context_id: str | None = None) -> None:
        """文本结束（基类在 LLMFullResponseEndFrame 后调用）：FinishSession 收尾。

        `FinishSession` 不是"立刻切断"，而是告诉火山"文本发完了，把缓冲的音频
        合成完并结束本 session"。收到 SessionFinished/TTSEnded 后接收任务才
        把 audio context 标记为结束。
        """
        if not self._streaming_sentences:
            return
        session_id = self._current_session_id
        if not session_id or self._ws is None:
            return
        try:
            await self._send_event(EventType.FinishSession, session_id)
            timeline_mark("tts", "FinishSession 已发出（文本结束）")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: FinishSession 发送失败: {exc}")

    async def _ensure_upstream(self) -> bool:
        """Reuse the live TTS websocket, or rebuild it with bounded backoff.

        An upstream drop used to silently end caption and audio production for
        the rest of the call.  The websocket is rebuildable, so it is retried;
        only an exhausted budget is reported as degraded, and the voice session
        itself stays alive either way.
        """
        ws = self._ws
        if ws is not None and not getattr(ws, "closed", False):
            return True
        async with self._reconnect_lock:
            ws = self._ws
            if ws is not None and not getattr(ws, "closed", False):
                return True
            from app.voice.journal import backoff_delay

            attempts = 4
            for attempt in range(1, attempts + 1):
                try:
                    await self._connect_bidirectional()
                    self._reconnect_attempt = 0
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    delay = backoff_delay(attempt, base=0.5, cap=8.0)
                    self._reconnect_attempt = attempt
                    if self._journal is not None:
                        await self._journal.retry_scheduled(
                            "tts",
                            attempt=attempt,
                            delay_ms=int(delay * 1000),
                            reason=str(exc)[:200],
                        )
                    logger.warning(
                        f"{self}: TTS 上游重连第 {attempt} 次失败: {exc}"
                    )
                    await asyncio.sleep(delay)
            self._ws = None
            if self._journal is not None:
                await self._journal.processor_error(
                    "tts",
                    "TTS 上游连接无法恢复，已降级为文本模式",
                    retryable=False,
                    degraded=True,
                )
            return False

    async def _start_streaming_session(self, context_id: str) -> None:
        """开一个火山 session 并把它的音频接收任务挂起来。"""
        await self._stop_streaming_session("restart")
        if not await self._ensure_upstream():
            raise RuntimeError("TTS upstream is unavailable")
        session_id = str(uuid.uuid4())
        options = self._options
        self._streaming_context_id = context_id
        self._current_session_id = session_id
        self._first_task_sent = False
        self._pending_sentence_texts.clear()
        self._active_sentence_text = ""
        self._active_sentence_marker_sent = False
        self._sentence_sequence = 0
        self._audio_cursor_ms = 0
        await self._send_event(
            EventType.StartSession,
            session_id,
            options.to_v3_start_session_payload("", "BidirectionalTTS"),
        )
        await self._expect_event(EventType.SessionStarted, EventType.SessionFailed)
        timeline_mark("tts", "session 握手完成(StartSession→SessionStarted)")
        self._receiver_task = self.create_task(
            self._receive_streaming_audio(context_id, session_id)
        )

    async def _send_text(self, text: str) -> None:
        """把一段增量文本追加进当前 session。"""
        session_id = self._current_session_id
        if not session_id or self._ws is None:
            return
        if not self._first_task_sent:
            self._first_task_sent = True
            timeline_mark("tts", "首个 TaskRequest 已发出")
        self._pending_sentence_texts.append(text)
        await self._send_event(
            EventType.TaskRequest,
            session_id,
            self._options.to_v3_task_payload(text),
        )

    async def _receive_streaming_audio(self, context_id: str, session_id: str) -> None:
        """后台读循环：把本 session 的音频写进 audio context，直到 session 结束。"""
        first_audio = True
        try:
            while True:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=60)
                if not isinstance(raw, bytes):
                    raise RuntimeError(f"unexpected text frame: {raw!r}")
                msg = Message.from_bytes(raw)
                if msg.type == MsgType.AudioOnlyServer:
                    # 只收当前 session 的音频，过滤打断残留的 stale audio。
                    if msg.session_id and msg.session_id != session_id:
                        continue
                    if not msg.payload:
                        continue
                    if first_audio:
                        first_audio = False
                        timeline_mark("tts", "火山首个音频包")
                    audio_frame = TTSAudioRawFrame(
                        msg.payload, self.sample_rate, 1, context_id=context_id
                    )
                    # TTSSentenceStart 通常先于音频到达。若供应商省略该事件，
                    # 则按 TaskRequest 顺序回退；两种路径都只在第一批音频入队
                    # 后发字幕游标，避免前端先收到整段文字。
                    if not self._active_sentence_text and self._pending_sentence_texts:
                        self._active_sentence_text = self._pending_sentence_texts.popleft()
                        self._active_sentence_marker_sent = False
                    await self.append_to_audio_context(context_id, audio_frame)
                    if self._active_sentence_text and not self._active_sentence_marker_sent:
                        self._sentence_sequence += 1
                        await self.append_to_audio_context(
                            context_id,
                            RTVIServerMessageFrame(
                                data={
                                    "type": "voice-sentence-start",
                                    "text": self._active_sentence_text,
                                    "sequence": self._sentence_sequence,
                                    "sentence_seq": self._sentence_sequence,
                                    "audio_cursor_ms": int(self._audio_cursor_ms),
                                    "event_id": f"tts_{uuid.uuid4().hex[:20]}",
                                    "turn_id": context_id,
                                }
                            ),
                        )
                        self._active_sentence_marker_sent = True
                        if self._journal is not None:
                            await self._journal.sentence_queued(
                                self._active_sentence_text,
                                audio_cursor_ms=int(self._audio_cursor_ms),
                            )
                    self._audio_cursor_ms += int(len(msg.payload) / 2 / self.sample_rate * 1000)
                elif msg.type == MsgType.FullServerResponse:
                    data = decode_payload(msg.payload)
                    if msg.event == EventType.SessionFailed:
                        raise RuntimeError(f"TTS session failed: {data}")
                    if msg.event == EventType.TTSSentenceStart:
                        # The protocol event has no stable text field in all API
                        # versions, so use the ordered TaskRequest queue as source
                        # of truth and only fall back to a payload text when present.
                        payload_text = data.get("text") if isinstance(data, dict) else None
                        # A provider can repeat TTSSentenceStart or deliver it just
                        # after the first audio packet. In that case the fallback
                        # queue already identifies the active sentence; consuming
                        # another entry here would shift every later caption by one.
                        if not payload_text and self._active_sentence_text:
                            continue
                        self._active_sentence_text = str(
                            payload_text or (
                                self._pending_sentence_texts.popleft()
                                if self._pending_sentence_texts
                                else ""
                            )
                        )
                        self._active_sentence_marker_sent = False
                        continue
                    if msg.event == EventType.TTSSentenceEnd:
                        self._active_sentence_text = ""
                        self._active_sentence_marker_sent = False
                        continue
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
                                f"TTS session failed: {data.get('status_code')} "
                                f"{data.get('message', '')}"
                            )
                        break
                elif msg.type == MsgType.Error:
                    raise RuntimeError(
                        f"TTS failed: {msg.error_code} {decode_payload(msg.payload)}"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: TTS 接收循环结束: {exc}")
            if self._journal is not None:
                # Report the stage failure but do not end the call: the next
                # sentence will rebuild the upstream session.
                await self._journal.processor_error(
                    "tts",
                    f"TTS 接收循环中断: {exc}",
                    retryable=True,
                    attempt=self._reconnect_attempt,
                )
        finally:
            self._pending_sentence_texts.clear()
            self._active_sentence_text = ""
            self._active_sentence_marker_sent = False
            # 只有还在基类手里的 context 才由我们收尾；被打断时基类已移除它。
            if self.audio_context_available(context_id):
                await self.remove_audio_context(context_id)

    async def _stop_streaming_session(self, reason: str) -> None:
        """停掉接收任务（session 本身由 FinishSession / CancelSession 结束）。"""
        task = self._receiver_task
        self._receiver_task = None
        self._streaming_context_id = None
        if task is not None and not task.done():
            await self.cancel_task(task)

    async def _run_tts_one_shot(
        self, text: str, context_id: str
    ) -> AsyncGenerator[Frame | None, None]:
        """旧行为：一次调用一个 session、整段文本一次投喂（回退开关用）。"""
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
        # 先把接收任务停掉，否则它会在 WS 关闭后继续等 recv（并可能报错刷日志）。
        await self._stop_streaming_session("cleanup")
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
