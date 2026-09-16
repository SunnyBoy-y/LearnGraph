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
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, AsyncGenerator, Callable, Optional

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
        generation_source: Callable[[], int] | None = None,
        **kwargs,
    ):
        if settings is None:
            settings = self.Settings(model="seed-tts-2.0-standard")
        settings.api_key = api_key
        settings.voice = None
        settings.language = None
        # 逐句合成开关：
        #   1（默认）= Pipecat 以句为单位聚合 LLM 增量，**每句开一个独立的火山
        #              session**；该句第一批音频到达即下发字幕游标，字幕边界与
        #              音频边界严格一致。
        #   0        = 旧的“一次 run_tts 一个 session、整段文本一次投喂”。
        #
        # 为什么不再是“一个 session 连续 TaskRequest”：实测（一个 session 里发 3 个
        # TaskRequest）火山 v3 只回 1 个 TTSSentenceStart（text 为空）、0 个
        # TTSSentenceEnd，而三句音频全部合成了。于是“靠 TTSSentenceEnd 推进游标”的
        # 写法永远只能标出第一句 —— 前端就表现为整段回答一次蹦出来。改成一句一
        # session 后，每句的首帧音频本身就是精确边界，不再依赖供应商的句子事件。
        self._streaming_sentences = os.getenv("VOLC_TTS_STREAMING", "1").lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        super().__init__(
            settings=settings,
            # 句级聚合：每确认一句就调用一次 run_tts，而一次 run_tts 就是"这一句的
            # session"。单句回答在 LLMFullResponseEndFrame 到达时 flush。
            text_aggregation_mode=(
                TextAggregationMode.SENTENCE if self._streaming_sentences else None
            ),
            **kwargs,
        )
        self._ws: Any = None
        self._options: TTSRequestOptions | None = None
        self._logid = ""
        # 当前正在合成的这一句的 session_id，用于 interruption 取消与 stale audio 过滤。
        self._current_session_id = ""
        # 本 turn 的 audio context：跨句复用，直到 flush（文本结束）或打断才移除。
        self._streaming_context_id: str | None = None
        # 单点计时用：本 turn 是否已发过第一个 TaskRequest。
        self._first_task_sent = False
        # 逐句字幕游标：**仅在 journal 没有给出句身份时**（无 journal / 没有未
        # finalize 的 turn）作为兜底计数器使用；有 journal 时序号由它分配。
        self._sentence_sequence = 0
        self._audio_cursor_ms = 0
        # 句子合成按序串联：后一句等前一句收完音频再开自己的 session。任务放在
        # 后台（不阻塞 process_frame），否则打断帧会被"正在合成的那一句"挡住。
        self._session_chain: asyncio.Task | None = None
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
        session_id = self._current_session_id
        chain = self._session_chain
        self._session_chain = None
        if chain is not None and not chain.done():
            await self.cancel_task(chain)
        await self._cancel_session_id(session_id, "interrupted")
        # 轮次被打断，下一轮从序号 1 重新开始。
        self._begin_turn_captions()

    async def _cancel_current_session(self, reason: str) -> None:
        await self._cancel_session_id(self._current_session_id, reason)

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
        """把一句文本投喂给火山并产出音频。

        流式模式（默认，`VOLC_TTS_STREAMING=1`）：**一句一个 session**。Pipecat 每
        确认一个句子就调用一次本方法，这里为它开一个 session、发一个 TaskRequest、
        立刻 FinishSession，然后把这一句的音频按到达顺序写进本 turn 的 audio
        context；该句首批音频入队的那一刻下发字幕游标（精确的音频边界）。audio
        context 跨句复用，直到文本结束（`flush_audio`）或被打断才移除，所以整段
        回答的播放是连续的。

        非流式模式（`VOLC_TTS_STREAMING=0`）：每次调用都 StartSession → 整段文本
        一次 TaskRequest → FinishSession → 同步收音频（回退开关）。
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
                # 一个 turn 一个 audio context，跨句复用：先建 context 并放行
                # TTSStartedFrame，保证音频不会排到它前面。
                await self.create_audio_context(context_id)
                self._streaming_context_id = context_id
                # 这里**不**重置字幕游标：基类在某些路径上会重建 audio context，
                # 若在此把序号归零，同一轮里后面的句子会因为序号回退而被前端去重
                # 丢掉。游标在轮次结束时重置（flush_audio / 打断）。
                await self.start_ttfb_metrics()
                yield TTSStartedFrame(context_id=context_id)
            self._enqueue_sentence(text, context_id)
            await self.start_tts_usage_metrics(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: run_tts 失败: {exc}")
            await self.push_error(error_msg=f"TTS 合成失败: {exc}", exception=exc)
        yield None

    def _enqueue_sentence(self, text: str, context_id: str) -> None:
        """Queue one sentence behind the previous one, without blocking the frame loop.

        Ordering matters twice over: the audio all lands in one context, so sessions
        must not interleave, and the caption sequence must increase monotonically or
        the client drops later sentences as duplicates.
        """
        previous = self._session_chain

        async def _run_after_previous() -> None:
            if previous is not None and not previous.done():
                await asyncio.gather(previous, return_exceptions=True)
            try:
                await self._synthesize_sentence(text, context_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # run_tts does not await synthesis any more, so a failure here would
                # otherwise disappear into a task nobody reads -- the sentence just
                # never gets spoken and nothing says why.
                logger.warning(f"{self}: 这一句合成失败（已跳过）: {exc}")
                if self._journal is not None:
                    await self._journal.processor_error(
                        "tts",
                        f"这一句合成失败，已跳过：{exc}",
                        retryable=True,
                        attempt=self._reconnect_attempt,
                    )

        self._session_chain = self.create_task(_run_after_previous())

    async def _close_turn_context(
        self, context_id: str | None, chain: asyncio.Task | None
    ) -> None:
        """After the last sentence drains: close the context and reset the cursor.

        Runs as its own task so `flush_audio` (called from the frame loop) returns
        immediately; the base class only reports end-of-playback once the context
        is marked for deletion.
        """
        if chain is not None:
            await asyncio.gather(chain, return_exceptions=True)
        if context_id and self.audio_context_available(context_id):
            await self.remove_audio_context(context_id)
            timeline_mark("tts", "audio context 已收尾（文本结束）")
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
        self._sentence_sequence = 0
        self._audio_cursor_ms = 0
        self._first_task_sent = False

    async def flush_audio(self, context_id: str | None = None) -> None:
        """文本结束（基类在 LLMFullResponseEndFrame 后调用）：收尾本 turn 的 context。

        每句的 session 在开出去时就已经 `FinishSession` 了，所以这里没有"长期
        session"要结束；剩下的事情是把本 turn 的 audio context 标记为结束，让基类
        把已排队的音频放完并报出播放结束（BotStoppedSpeaking）。这正是"逐句 session"
        与"一句一个 context"的分界：session 逐句结束，context 整轮结束。
        """
        if not self._streaming_sentences:
            return
        target = context_id or self._streaming_context_id
        self._streaming_context_id = None
        chain = self._session_chain
        self._session_chain = None
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
                    "TTS 上游连接无法恢复，已降级为文本模式",
                    retryable=False,
                    degraded=True,
                )
            return False

    async def _synthesize_sentence(self, text: str, context_id: str) -> None:
        """合成**一句**：独立 session，首帧音频即该句的字幕边界。

        这是整个逐句字幕的地基。火山 v3 在一个长 session 里不会逐句回
        TTSSentenceStart/End（实测 3 个 TaskRequest → 1 个 SentenceStart、0 个
        SentenceEnd），所以"等 SentenceEnd 再推进游标"永远只标出第一句。换成一句
        一个 session 后，本句的首帧音频就是我们需要的精确边界，且不依赖供应商事件。

        session 开出去即 `FinishSession`：该句文本已经完整，让火山把这一句合成完
        并结束 session；音频由本方法按到达顺序写进本 turn 的 audio context。
        """
        if self._closed:
            # 收尾期间不要再合成：`_ensure_upstream` 会拒绝建连，抛异常只会把一次
            # 正常挂断刷成一条错误。
            return
        if not await self._ensure_upstream():
            raise RuntimeError("TTS upstream is unavailable")
        session_id = str(uuid.uuid4())
        options = self._options
        self._current_session_id = session_id
        try:
            await self._send_event(
                EventType.StartSession,
                session_id,
                options.to_v3_start_session_payload("", "BidirectionalTTS"),
            )
            await self._expect_event(EventType.SessionStarted, EventType.SessionFailed)
            timeline_mark("tts", "session 握手完成(StartSession→SessionStarted)")
            await self._send_event(
                EventType.TaskRequest, session_id, options.to_v3_task_payload(text)
            )
            if not self._first_task_sent:
                self._first_task_sent = True
                timeline_mark("tts", "首个 TaskRequest 已发出")
            await self._send_event(EventType.FinishSession, session_id)
            identity = await self._drain_sentence_audio(context_id, session_id, text)
            if identity is not None:
                await self._emit_sentence_end_marker(context_id, identity)
        finally:
            if self._current_session_id == session_id:
                self._current_session_id = ""

    async def _drain_sentence_audio(
        self, context_id: str, session_id: str, sentence_text: str
    ) -> dict[str, Any] | None:
        """收完这一句的音频；首帧入队时下发句起点 marker。

        注意这里**不**移除 audio context：后面的句子还要往同一个 context 里排音频，
        context 由 `flush_audio`（整轮文本结束）或打断来收尾。

        正常收完（`SessionFinished/SessionCanceled/TTSEnded`）时把句身份交回调用方，
        由它补发句结束 marker：句结束偏移只有在音频收干之后才存在，而本方法在收到
        结束事件的那一刻就要 `return` 了。被打断或出错时返回 None —— 那一句既没有
        结束偏移，也不该让前端认为它播完了。
        """
        marker_sent = False
        identity: dict[str, Any] | None = None
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
                    await self.append_to_audio_context(
                        context_id,
                        TTSAudioRawFrame(
                            msg.payload, self.sample_rate, 1, context_id=context_id
                        ),
                    )
                    if not marker_sent:
                        # 首批音频已入队：这就是这一句开始被朗读的时刻。先入队、
                        # 再发游标，前端才不会先看到字后听到声。
                        marker_sent = True
                        timeline_mark("tts", "火山首个音频包")
                        identity = await self._emit_sentence_marker(
                            context_id, sentence_text
                        )
                    self._audio_cursor_ms += int(
                        len(msg.payload) / 2 / self.sample_rate * 1000
                    )
                elif msg.type == MsgType.FullServerResponse:
                    data = decode_payload(msg.payload)
                    if msg.event == EventType.SessionFailed:
                        raise RuntimeError(f"TTS session failed: {data}")
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
                        return identity
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
        return None

    def _current_generation(self) -> int | None:
        """本句所属的音频闸门代次；没有接线时为 None。"""
        if self._generation_source is None:
            return None
        return int(self._generation_source())

    async def _queue_sentence_identity(self, text: str, context_id: str) -> dict[str, Any]:
        """句身份的唯一分配入口：先落持久事件，再把身份交给快通道 marker。

        为什么必须由 journal 分配：适配器以前自己数 `_sentence_sequence`，journal 内部
        另数一套，同一条流上的快通道 marker 与持久事件因此带着两套互不相等的序号，
        前端按 `(turn_id, sentence_seq)` 归并时必然错位（重复定稿或丢句）。现在只有
        一个分配者，快通道的 `sequence` 只是它的向后兼容别名。
        """
        generation_id = self._current_generation()
        audio_cursor_ms = int(self._audio_cursor_ms)
        if self._journal is not None:
            identity = await self._journal.sentence_queued(
                text,
                audio_cursor_ms=audio_cursor_ms,
                context_id=context_id,
                generation_id=generation_id,
            )
            if identity:
                # 本地计数器跟着 journal 走：万一它下一句没有可用 turn（返回 {}）而
                # 退回本地兜底，序号也不会倒退。
                self._sentence_sequence = int(identity["sentence_seq"])
                return {
                    "turn_id": identity.get("turn_id"),
                    "sentence_seq": int(identity["sentence_seq"]),
                    "segment_id": identity.get("segment_id"),
                    "generation_id": identity.get("generation_id", generation_id),
                    "audio_cursor_ms": int(
                        identity.get("audio_cursor_ms") or audio_cursor_ms
                    ),
                }
        # 没有 journal（老部署）或 journal 里没有未 finalize 的 turn：序号只能本地
        # 兜底，`turn_id`/`segment_id` 留空由前端按 unattributed 处理——编一个假的回合
        # id 会把这句话并进一个并不存在的回合。
        self._sentence_sequence += 1
        return {
            "turn_id": None,
            "sentence_seq": self._sentence_sequence,
            "segment_id": None,
            "generation_id": generation_id,
            "audio_cursor_ms": audio_cursor_ms,
        }

    async def _emit_sentence_marker(
        self, context_id: str, sentence_text: str
    ) -> dict[str, Any]:
        """下发"这一句正在被朗读"的起点 marker，并写进持久事件流。

        返回句身份，供句结束时补发 `voice-sentence-end`。
        """
        identity = await self._queue_sentence_identity(sentence_text, context_id)
        sentence_seq = int(identity["sentence_seq"])
        await self.append_to_audio_context(
            context_id,
            RTVIServerMessageFrame(
                data={
                    "type": "voice-sentence-start",
                    "event_id": f"tts_{uuid.uuid4().hex[:20]}",
                    "text": sentence_text,
                    "sequence": sentence_seq,
                    "sentence_seq": sentence_seq,
                    "segment_id": identity["segment_id"],
                    "audio_cursor_ms": int(identity["audio_cursor_ms"]),
                    "turn_id": identity["turn_id"],
                    "context_id": context_id,
                    "generation_id": identity["generation_id"],
                }
            ),
        )
        return identity

    async def _emit_sentence_end_marker(
        self, context_id: str, identity: dict[str, Any]
    ) -> None:
        """这一句的音频已全部入队：补发句结束 marker，并落一条持久事件。

        只有起点偏移的话，前端无法知道"这一句播到哪里算完"，句内进度就只能用字速
        猜。补上终点偏移后，句内位置是**在有界窗口里插值**，而不是承诺逐字对齐
        （§6 明确不取供应商词级时间戳、不承诺毫秒级对齐）。
        """
        await self.append_to_audio_context(
            context_id,
            RTVIServerMessageFrame(
                data={
                    "type": "voice-sentence-end",
                    "event_id": f"ttse_{uuid.uuid4().hex[:20]}",
                    "sentence_seq": int(identity["sentence_seq"]),
                    "segment_id": identity["segment_id"],
                    "audio_end_cursor_ms": int(self._audio_cursor_ms),
                    "turn_id": identity["turn_id"],
                    "context_id": context_id,
                    "generation_id": identity["generation_id"],
                }
            ),
        )
        if self._journal is not None:
            await self._journal.sentence_ended(
                sentence_seq=int(identity["sentence_seq"]),
                audio_end_cursor_ms=int(self._audio_cursor_ms),
                segment_id=identity["segment_id"],
                generation_id=identity["generation_id"],
            )

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
        # 一句一 session：每句的接收循环都在 run_tts 里被 await 完，没有后台任务要在
        # WS 关闭前先停掉。
        # ``_closed`` 先置位（挡住并发重连建新连接），拆卸走重连锁：旧实现直接读写
        # ``_ws``，一场正在进行的重连可以在收尾窗口里建出一条没人关得掉的连接。
        self._closed = True
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
