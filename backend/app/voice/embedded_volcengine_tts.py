"""火山引擎 语音合成 2.0（双向流式）— Pipecat TTSService 适配。

复用火山「双向流式语音合成」二进制 WebSocket 协议（与忆伴
Agent/src/voice/tts/volcengine_tts/__init__.py 一致）。
输出 encoding=pcm（16bit mono PCM），sample_rate 与 Pipecat audio_out 对齐。
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import struct
import time
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, AsyncGenerator, Callable, Optional

import websockets
from loguru import logger
from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStartedFrame
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TextAggregationMode, TTSService

from app.voice.tts_alignment import SubtitleAudioJoin
from app.voice.caption_ledger import VoiceLedgerFrame
from app.voice.embedded_timeline import timeline_mark
from app.voice.embedded_tts_aggregator import CommaSentenceAggregator
from app.voice.response_guard import SpeechContentGuard

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
    TTSSubtitle = 364


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
        audio_params: dict = {"format": self.encoding, "sample_rate": self.sample_rate, "enable_subtitle": True}
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
        generation_source: Callable[[], int] | None = None,
        **kwargs,
    ):
        if settings is None:
            settings = self.Settings(model="seed-tts-2.0-standard")
        settings.api_key = api_key
        settings.voice = None
        settings.language = None
        # One provider session per model response. Text chunks do not create
        # audio boundaries; timestamped subtitles own those independently.
        self._streaming_sentences = True
        super().__init__(settings=settings, text_aggregation_mode=TextAggregationMode.SENTENCE, **kwargs)
        self._text_aggregator = CommaSentenceAggregator()
        self._stream_inputs: dict[str, asyncio.Queue] = {}
        self._ws: Any = None
        self._options: TTSRequestOptions | None = None
        self._logid = ""
        # 当前正在合成的这一句的 session_id，用于 interruption 取消与 stale audio 过滤。
        self._current_session_id = ""
        # 本 turn 的 audio context：跨句复用，直到 flush（文本结束）或打断才移除。
        self._streaming_context_id: str | None = None
        # 单点计时用：本 turn 是否已发过第一个 TaskRequest。
        self._first_task_sent = False
        self._audio_cursor_ms = 0
        self._caption_turn_id: str | None = None
        # Response sessions serialize on one socket; text inside each session
        # streams concurrently with its sole receiver.
        self._session_chain: asyncio.Task | None = None
        # 本轮所有句子合成任务。``_session_chain`` 只指向队尾，而每个任务都在
        # ``await gather(previous)`` 上等前一个——取消时必须按集合逐个取消并等它们
        # 真正结束，否则会有 recv 循环活过打断（见 ``_cancel_sentence_tasks``）。
        self._sentence_tasks: set[asyncio.Task] = set()
        # 字幕代次：打断时 +1。一个句子在**入队**时记住当时的代次，之后无论是合成、
        # 收音频还是投递 marker，只要发现代次变了立刻放弃——被打断那一句的文本绝不能
        # 按新回合的身份领序号（否则一句从没播过的旧句子会进字幕、进转录，甚至进记忆）。
        self._caption_epoch = 0
        self._context_turns: dict[str, str | None] = {}
        # Optional durable journal.  When present it **allocates** the sentence
        # identity (``turn_id`` / ``sentence_seq`` / ``segment_id``) and records
        # it in the append-only event log, so a client that missed data-channel
        # frames reconciles by ``(turn_id, sentence_seq)`` instead of by text.
        # One allocator only: the fast-path marker below carries the journal's
        # number, it does not invent one.
        self._journal = journal
        # 音频闸门代次来源（`VoiceGenerationGate.generation`）。句子身份里的
        # `generation_id` 必须与"这一句的音频属于哪一代"同源，否则前端无法用代次
        # 判断一条迟到的 marker 是不是上一轮被打断的残留。没接线时为 None：宁可没
        # 有代次，也不要编一个看起来有效的数字。
        self._generation_source = generation_source
        # Bounded upstream reconnect bookkeeping (TTS websocket).
        self._reconnect_attempt = 0
        self._reconnect_lock = asyncio.Lock()
        # 收尾标志：`cleanup()` 会先置位再 await，而 `_connect_bidirectional` 会检查
        # 它——否则一场正在进行的重连可以在收尾之后建出一条没人负责的连接（ASR 侧
        # 同一类窄窗口泄漏，2026-09-16 一并修掉）。
        self._closed = False

    def _upstream_alive(self) -> bool:
        ws = self._ws
        return ws is not None and not getattr(ws, "closed", False)

    async def _detach_upstream(self, reason: str) -> bool:
        """摘掉并关闭当前上游连接，幂等。必须在重连锁内调用。"""
        ws, self._ws = self._ws, None
        if ws is None:
            return False
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            logger.debug(f"{self}: close upstream failed ({reason})", exc_info=True)
        return True

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
        if self._closed:
            # 收尾已经开始（或已完成）：绝不能在收尾之后再建一条没人负责的连接。
            raise RuntimeError("TTS service is closing")
        if self._ws is not None:
            # 一条连接只能有一个主人：覆盖引用而不关闭，会让旧 WS 一直活着。
            await self._detach_upstream("reconnect")
        ws = await websockets.connect(
            self._settings.endpoint,
            additional_headers=self._headers(),
            max_size=10 * 1024 * 1024,
            open_timeout=15,
        )
        if self._closed:
            # 握手与收尾赛跑：cleanup() 跑的时候看到的 `_ws` 还是 None，于是这条
            # 刚建好的连接没有任何人会关它。就地关掉。
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError("TTS service closing during connect")
        self._ws = ws
        self._logid = self._ws.response.headers.get("x-tt-logid", "")
        try:
            await self._send_event(EventType.StartConnection)
            await self._expect_event(EventType.ConnectionStarted, EventType.ConnectionFailed)
        except Exception:
            # 握手失败不能留成半开连接。
            await self._detach_upstream("handshake_failed")
            raise
        logger.info(f"{self}: connected to {self._settings.endpoint}")

    async def on_audio_context_interrupted(self, context_id: str):
        """打断时向火山发送 CancelSession，主动结束当前合成并释放服务端资源。

        Pipecat 基类收到 InterruptionFrame 会停止 run_tts 的 audio context task，
        但火山服务端仍可能在后台继续合成。若不显式取消，切割后的残余音频会
        继续占用服务端资源、甚至混入下一轮。这里发 CancelSession 让火山立即
        放弃当前 session（收到 SessionCanceled 后服务端释放资源）。
        """
        # 顺序有讲究：先记住正在合成的那一句的 session id，再掐断串联链（含它的
        # 接收循环），最后用记住的 id 通知火山放弃该 session。反过来的话，链被取消
        # 时 `_synthesize_sentence` 的 finally 已经把 `_current_session_id` 清空，
        # CancelSession 就发不出去 —— 服务端会把这一句合成完（白占资源，残余音频还
        # 可能混进下一轮）。
        # 先推进字幕代次：任何还在飞的旧句（合成中、收音频中、等投递）都会在下一次
        # 判断时发现自己已经过期并立刻退出，而不是把旧文本按新回合的身份领一个序号。
        self._caption_epoch += 1
        session_id = self._current_session_id
        chain = self._session_chain
        self._session_chain = None
        # 取消本轮**全部**句子任务并等它们结束。只取消队尾是不够的：实测取消之后仍有
        # recv 在跑，下一句的 recv 一起步就报 "cannot call recv while another coroutine
        # is already running recv or recv_streaming"（见 `_cancel_sentence_tasks`）。
        await self._cancel_sentence_tasks(chain)
        self._stream_inputs.clear()
        await self._cancel_session_id(session_id, "interrupted")
        # 火山账号同时只允许 1 个 session：CancelSession 只是"请求"，服务端释放之前
        # 新的 StartSession 会被拒（55000000 session number limit exceeded: 1），整句
        # 合成失败、用户一个音都听不到（实测）。这里独占 recv 等服务端确认（有超时，
        # 拿不到也不阻塞，调用方还有重试兜底）。
        await self._await_session_release(session_id, timeout=1.5)
        # 剩下的音频永远不会播出来了：还没到播放时刻的账本帧会随输出传输的媒体队列
        # 重置（``_audio_queue.reset()``）一起被丢弃，所以这里不需要额外撤销——被
        # 打断的句子既不会点亮字幕，也不会进账本。
        # 轮次被打断，下一轮从序号 1 重新开始。
        self._begin_turn_captions()

    async def _cancel_current_session(self, reason: str) -> None:
        await self._cancel_session_id(self._current_session_id, reason)

    async def _cancel_sentence_tasks(self, chain: asyncio.Task | None) -> None:
        """取消本轮所有句子合成任务，并等它们真正结束。

        ``_session_chain`` 只指向**队尾**，而每个任务都在 ``await gather(previous)``
        上等前一个，所以"取消队尾"并不能保证前面的 recv 循环已经退出——实测打断之后
        仍有 recv 在跑，下一句的 recv 一起步就报 ``cannot call recv while another
        coroutine is already running recv or recv_streaming``，那一句随即合成失败、
        整轮静默。上游读取的串行化不能建立在"取消应该会传播"的假设上。

        这里逐个取消并 ``gather`` 等干净：``gather(return_exceptions=True)`` 让被取消
        任务的 CancelledError 不向上冒泡（打断路径不能因为一句合成被取消而失败）。
        """
        targets = {task for task in self._sentence_tasks if not task.done()}
        self._sentence_tasks.clear()
        if chain is not None and not chain.done():
            targets.add(chain)
        if not targets:
            return
        for task in targets:
            task.cancel()
        await asyncio.gather(*targets, return_exceptions=True)

    async def _await_session_release(self, session_id: str, *, timeout: float) -> bool:
        """等服务端确认那个 session 已经释放（CancelSession → SessionCanceled）。

        火山账号的并发上限是 1 个 session，而 ``CancelSession`` 只是"请求"：服务端真正
        释放之前再开新 session 会被拒（``session number limit exceeded: 1``），表现为整句
        合成失败、用户听不到任何声音。调用方在打断时已经把本轮所有合成任务取消并等干净，
        所以这里可以独占 recv 而不与任何人抢同一个连接。

        拿不到确认不算失败：返回 False，调用方的 StartSession 重试会兜住。
        """
        if not session_id or not self._upstream_alive():
            return False
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.debug(f"{self}: 等服务端释放 session {session_id} 超时")
                    return False
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
                if not isinstance(raw, bytes):
                    continue
                msg = Message.from_bytes(raw)
                if msg.type != MsgType.FullServerResponse:
                    continue
                if msg.session_id and msg.session_id != session_id:
                    # 别的 session 的响应（不应该出现，出现了也不能当成"已释放"）。
                    continue
                if msg.event in (EventType.SessionCanceled, EventType.SessionFinished):
                    logger.debug(f"{self}: session {session_id} 已释放（{msg.event}）")
                    return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{self}: 等待 session 释放时上游异常: {exc}")
            return False

    async def _cancel_session_id(self, session_id: str, reason: str) -> None:
        if not session_id:
            return
        if not self._ws:
            if self._current_session_id == session_id:
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
        """Feed semantic text into the response's continuous provider session."""
        if not text or not text.strip():
            yield None
            return
        guard = getattr(self, 'content_guard', None)
        if guard is None:
            guard = self.content_guard = SpeechContentGuard()
        policy = getattr(self, 'conversation_policy', None)
        plan = getattr(policy, 'plan', None)
        if not guard.admit(text, (context_id, getattr(policy, 'revision', 0)),
                           no_repeat=bool(getattr(policy, 'no_repeat', False)),
                           allow_repeat=bool(plan and (plan.allow_repeat or re.search(r'两遍|三遍|两次|三次|台词|逐句', plan.request_text)))):
            yield None
            return
        if self._ws is None:
            raise RuntimeError("TTS WebSocket is not connected")
        try:
            if not self.audio_context_available(context_id):
                # 一个 turn 一个 audio context，跨句复用：先建 context 并放行
                # TTSStartedFrame，保证音频不会排到它前面。
                await self.create_audio_context(context_id)
                self._streaming_context_id = context_id
                # 这里**不**重置字幕游标：基类在某些路径上会重建 audio context，
                # 若在此把序号归零，同一轮里后面的句子会因为序号回退而被前端去重
                # 丢掉。游标在轮次结束时重置（flush_audio / 打断）。
                await self.start_ttfb_metrics()
                yield TTSStartedFrame(context_id=context_id)
            submitted = getattr(self._journal, "tts_submitted", None)
            if callable(submitted):
                submitted()
            self._enqueue_sentence(text, context_id)
            await self.start_tts_usage_metrics(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: run_tts 失败: {exc}")
            await self.push_error(error_msg=f"TTS 合成失败: {exc}", exception=exc)
        yield None

    def _enqueue_sentence(self, text: str, context_id: str) -> None:
        queue = self._stream_inputs.get(context_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=256)
            self._stream_inputs[context_id] = queue
            epoch = self._caption_epoch
            turn_id = self._journal.current_turn_id() if self._journal else None
            if turn_id and turn_id != self._caption_turn_id:
                self._begin_turn_captions()
                self._caption_turn_id = turn_id
            self._context_turns.setdefault(context_id, turn_id)
            previous = self._session_chain

            async def run():
                try:
                    if previous is not None:
                        await asyncio.gather(previous, return_exceptions=True)
                    if epoch != self._caption_epoch:
                        return
                    await self._synthesize_stream(queue, context_id, epoch=epoch)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Fail this response explicitly; never skip a sentence and
                    # carry on as though the complete answer had been spoken.
                    await self.push_error(error_msg=f"TTS continuous synthesis failed: {exc}", exception=exc, fatal=True)
                    if self._journal is not None:
                        await self._journal.processor_error("tts", "连续语音合成失败", retryable=False, degraded=True)
                        failed = getattr(self._journal, "tts_failed", None)
                        if callable(failed):
                            await failed(turn_id)
                    raise
                finally:
                    self._stream_inputs.pop(context_id, None)

            self._session_chain = self.create_task(run())
            self._sentence_tasks.add(self._session_chain)
            self._session_chain.add_done_callback(self._sentence_tasks.discard)
        queue.put_nowait(text)

    async def _close_turn_context(
        self, context_id: str | None, chain: asyncio.Task | None, *, turn_final: bool | None = None
    ) -> None:
        """After the last sentence drains: close the context and reset the cursor.

        Runs as its own task so `flush_audio` (called from the frame loop) returns
        immediately; the base class only reports end-of-playback once the context
        is marked for deletion.
        """
        if chain is not None:
            result = await asyncio.gather(chain, return_exceptions=True)
            if isinstance(result[0], BaseException):
                if context_id and self.audio_context_available(context_id):
                    await self.remove_audio_context(context_id)
                return
        if context_id and self.audio_context_available(context_id):
            if turn_final is None:
                # Tool completion and LLM end can race the synthesis drain.
                # Decide at the actual queue boundary, not at flush admission.
                busy = getattr(self._journal, "foreground_busy", None)
                turn_final = not (busy() if callable(busy) else False) and chain is self._session_chain
            await self.append_to_audio_context(context_id, VoiceLedgerFrame(
                kind="turn-end", token=f"end:{context_id}", context_id=context_id,
                turn_id=self._context_turns.get(context_id),
                generation_id=self._current_generation(),
                turn_final=turn_final,
            ))
            await self.remove_audio_context(context_id)
            timeline_mark("tts", "audio context 已收尾（文本结束）")
        # Several model/tool rounds may belong to the same durable user turn.
        # Keep its audio cursor after the acknowledgement context drains.
        if self._journal is None:
            self._begin_turn_captions()

    def _begin_turn_captions(self) -> None:
        """Reset the per-turn caption cursor.

        Called at the *end* of a turn (flush or interruption) rather than when the
        audio context is created, so a context recreated mid-turn cannot restart
        the sequence and make the client drop later sentences as duplicates.  It
        only resets the *fallback* counter and the turn's audio cursor: with a
        journal in place the sequence itself belongs to the turn it numbers, and
        resetting it here must not be visible to the client as a rewind.
        """
        self._audio_cursor_ms = 0
        self._first_task_sent = False
        self._caption_turn_id = None

    async def flush_audio(self, context_id: str | None = None) -> None:
        """Seal input once; receiver drains before the audio ledger closes."""
        target = context_id or self._streaming_context_id
        self._streaming_context_id = None
        chain = self._session_chain
        queue = self._stream_inputs.get(target)
        if queue is not None:
            queue.put_nowait(None)
        # Keep the session chain until drained: the next model/tool round must
        # not open another session on the same socket.
        # Hand off: the remaining sentence still has to drain before the context can
        # be closed, and blocking the frame loop here would delay a barge-in that
        # arrives while the last sentence is still being synthesised.
        self.create_task(self._close_turn_context(target, chain))

    async def _ensure_upstream(self) -> bool:
        """Reuse the live TTS websocket, or rebuild it with bounded backoff.

        An upstream drop used to silently end caption and audio production for
        the rest of the call.  The websocket is rebuildable, so it is retried;
        only an exhausted budget is reported as degraded, and the voice session
        itself stays alive either way.
        """
        if self._closed:
            return False
        if self._upstream_alive():
            return True
        async with self._reconnect_lock:
            if self._closed:
                return False
            if self._upstream_alive():
                return True
            await self._detach_upstream("replaced")
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
                    if self._closed:
                        return False
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
            self._reconnect_attempt = attempts
            if self._journal is not None:
                await self._journal.processor_error(
                    "tts",
                    "TTS 上游连接无法恢复，本轮语音失败",
                    retryable=False,
                    degraded=True,
                )
            return False

    async def _open_sentence_session(self, session_id: str, *, epoch: int) -> None:
        """StartSession → SessionStarted，专为"上一句刚被取消"留退避重试。

        火山账号同时只允许 1 个 session。``CancelSession`` 到达服务端并真正释放之前，
        新的 ``StartSession`` 会被拒（``55000000 session number limit exceeded: 1``）——
        这不是"这一句有问题"，而是上一句的释放还在路上。所以只对这一个错误退避重试；
        重试前再查一次代次，被打断就立刻放弃（不再为一句已经作废的话占资源）。
        """
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                await self._send_event(
                    EventType.StartSession,
                    session_id,
                    self._options.to_v3_start_session_payload("", "BidirectionalTTS"),
                )
                await self._expect_event(EventType.SessionStarted, EventType.SessionFailed)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                retryable = (
                    "session number limit" in str(exc)
                    or "session limit" in str(exc)
                )
                if attempt >= attempts or not retryable or epoch != self._caption_epoch:
                    raise
                delay = 0.2 * attempt
                logger.warning(
                    f"{self}: 上一句的 session 尚未释放，{delay:.1f}s 后重试"
                    f"（{attempt}/{attempts - 1}）: {exc}"
                )
                await asyncio.sleep(delay)

    async def _synthesize_sentence(self, text: str, context_id: str, *, epoch: int | None = None):
        """Single-input driver of the same streaming protocol (diagnostics/tests)."""
        queue = asyncio.Queue()
        queue.put_nowait(text)
        queue.put_nowait(None)
        await self._synthesize_stream(queue, context_id, epoch=self._caption_epoch if epoch is None else epoch)

    async def _synthesize_stream(self, queue: asyncio.Queue, context_id: str, *, epoch: int):
        if self._closed or epoch != self._caption_epoch:
            return
        if not await self._ensure_upstream():
            raise RuntimeError("TTS upstream is unavailable")
        session_id = str(uuid.uuid4())
        self._current_session_id = session_id
        sender = None
        try:
            await self._open_sentence_session(session_id, epoch=epoch)

            async def send_text():
                while True:
                    text = await queue.get()
                    if epoch != self._caption_epoch:
                        return
                    if text is None:
                        await self._send_event(EventType.FinishSession, session_id)
                        return
                    await self._send_event(EventType.TaskRequest, session_id, self._options.to_v3_task_payload(text))

            sender = asyncio.create_task(send_text())
            await self._drain_stream_audio(context_id, session_id, epoch=epoch, sender=sender)
            await sender
        finally:
            if sender is not None and not sender.done():
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
            if self._current_session_id == session_id:
                self._current_session_id = ""

    async def _drain_stream_audio(self, context_id, session_id, *, epoch, sender):
        join = SubtitleAudioJoin(self.sample_rate)
        base_cursor = self._audio_cursor_ms
        emitted_bytes = 0

        async def audio(data):
            nonlocal emitted_bytes
            if data:
                await self.append_to_audio_context(context_id, TTSAudioRawFrame(data, self.sample_rate, 1, context_id=context_id))
                emitted_bytes += len(data)
                self._audio_cursor_ms = base_cursor + round(emitted_bytes / (2 * self.sample_rate) * 1000)

        while epoch == self._caption_epoch:
            if sender.done() and not sender.cancelled() and sender.exception():
                raise sender.exception()
            raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
            if epoch != self._caption_epoch:
                return
            if not isinstance(raw, bytes):
                raise RuntimeError("TTS returned non-binary data")
            msg = Message.from_bytes(raw)
            if msg.session_id and msg.session_id != session_id:
                continue
            if msg.type == MsgType.Error or msg.event == EventType.SessionFailed:
                raise RuntimeError(f"TTS provider error: {msg.error_code} {decode_payload(msg.payload)}")
            if msg.type == MsgType.AudioOnlyServer:
                join.add_audio(msg.payload)
            elif msg.event == EventType.TTSSubtitle:
                join.add_subtitle(decode_payload(msg.payload))
            for span in join.ready():
                await audio(span.prefix)
                identity = await self._enqueue_sentence_start(context_id, span.text, epoch=epoch)
                await audio(span.audio)
                await self._enqueue_sentence_end(context_id, identity)
            if msg.event == EventType.SessionFinished:
                await audio(join.finish())
                return
            if msg.event == EventType.SessionCanceled:
                raise RuntimeError("TTS session cancelled without local interruption")

    def _current_generation(self) -> int | None:
        """本句所属的音频闸门代次；没有接线时为 None。"""
        if self._generation_source is None:
            return None
        return int(self._generation_source())

    async def _enqueue_sentence_start(
        self, context_id: str, sentence_text: str, *, epoch: int
    ) -> dict[str, Any]:
        """把"这一句开始播放"的账本帧排到它第一帧音频**之前**。

        为什么不再是"发一条 marker"：账本必须记在"音频真的被写出去"的因果时刻，而不是
        入队的时刻——合成远快于播放，入队即记账会让压在执行队列里、还没播出来的整段回答
        提前落库并进记忆。这里只把一枚 ``VoiceLedgerFrame`` 放进 audio context 队列，
        让它和音频一起排队；真正写账本的是输出传输下游的 ``VoiceLedgerRelay``。

        由此句身份（``sentence_seq`` / ``segment_id``）也改由账本在**释放时刻**分配：
        序号按播放顺序产生，不再需要"预约"，也就不存在烧掉的序号。
        """
        identity: dict[str, Any] = {
            "token": uuid.uuid4().hex,
            "turn_id": self._context_turns.get(context_id),
            "text": sentence_text,
            "context_id": context_id,
            "audio_cursor_ms": int(self._audio_cursor_ms),
            "generation_id": self._current_generation(),
            "caption_epoch": int(epoch),
        }
        await self.append_to_audio_context(
            context_id,
            VoiceLedgerFrame(
                kind="start",
                token=str(identity["token"]),
                turn_id=identity.get("turn_id"),
                text=sentence_text,
                context_id=context_id,
                audio_cursor_ms=int(identity["audio_cursor_ms"]),
                generation_id=identity["generation_id"],
            ),
        )
        return identity

    async def _enqueue_sentence_end(
        self, context_id: str, identity: dict[str, Any]
    ) -> None:
        """把句尾账本帧排到这一句音频的**后面**（audio context 队列尾）。

        最后一批音频可能还没写出，所以这里同样只入队、不记账；等它在输出传输里被放行
        时，这一句的音频正好播完。
        """
        if int(identity.get("caption_epoch") or 0) != self._caption_epoch:
            # 定身份之后被打断：这一句不会再播完，句尾也就无从谈起。
            return
        await self.append_to_audio_context(
            context_id,
            VoiceLedgerFrame(
                kind="end",
                token=str(identity["token"]),
                turn_id=identity.get("turn_id"),
                context_id=context_id,
                audio_end_cursor_ms=int(self._audio_cursor_ms),
                generation_id=identity.get("generation_id"),
            ),
        )

    async def cleanup(self):
        # Stop the single reader and writer before closing their socket.
        self._closed = True
        await self._cancel_sentence_tasks(self._session_chain)
        self._stream_inputs.clear()
        await super().cleanup()
        try:
            await asyncio.wait_for(self._reconnect_lock.acquire(), timeout=6.0)
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(f"{self}: 重连在 6s 内没有落定，跳过锁直接关连接")
            ws, self._ws = self._ws, None
            if ws is not None:
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
            return
        try:
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
            await self._detach_upstream("cleanup")
        finally:
            self._reconnect_lock.release()
