"""DashScope 实时语音识别（qwen3-asr-flash-realtime）— Pipecat STTService 适配。

协议（阿里云百炼 Qwen-ASR-Realtime，与忆伴 Agent/src/voice/stt.py 同源）：
  -> session.update（turn_detection=null，即 Manual 模式）
  -> input_audio_buffer.append(base64 16k PCM)
  -> input_audio_buffer.commit（由本机回合结束信号触发）
  <- conversation.item.input_audio_transcription.text（partial，两种模式均下发）
  <- conversation.item.input_audio_transcription.completed（final）
  -> session.finish（关闭前；服务端回 session.finished 后才断开）

**Manual 模式是必须的**：官方文档对 ``input_audio_buffer.commit`` 明确标注
"禁用场景：VAD 模式"。旧实现把 session 配成 ``server_vad`` 却又在回合结束
时发 commit —— 该事件会被忽略，本机回合结束信号永远无法提前 finalize。
因此通话链路必须关闭云端 VAD，回合边界完全交给本机 Silero VAD + Smart Turn v3。

**由此 commit 成为硬依赖**：Manual 模式没有任何自动断句，必须由
``UserStoppedSpeakingFrame``（Pipecat 用户聚合器在停策略触发时广播）驱动
commit，否则永远不会产生 final 转录。``DASHSCOPE_ASR_COMMIT_ON_EOU=0``
仅用于排障，开启状态下会让本服务不产生任何 final。

本适配层把该 WebSocket 客户端包成 Pipecat 的 STTService：
  run_stt(audio) 仅喂入音频；后台 read loop 收到结果后 push TranscriptionFrame。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import websockets
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.settings import STTSettings
from pipecat.services.stt_latency import DEFAULT_TTFS_P99
from pipecat.services.stt_service import STTService
from pipecat.utils.time import time_now_iso8601

from app.voice.embedded_timeline import timeline_mark


@dataclass
class DashScopeSTTSettings(STTSettings):
    ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    # DashScope 侧语言代码（ISO 639-1，如 zh / yue / en）。
    # 单独建字段而不复用基类的 ``language``：基类会把字符串转成 ``Language``
    # 枚举再映射为服务侧代码，而 DashScope 只认原始 ISO 代码（v1.1 已修过
    # zh-CN→zh 同类问题），直接下发可避免二次转换。
    asr_language: str = "zh"
    # 保留字段：Manual 模式下云端 VAD 已关闭，该值不再被使用，
    # 仅为兼容既有调用方（embedded_bot.py 仍会传 silence_ms）。
    silence_ms: int = 400
    api_key: str = ""


@dataclass(slots=True)
class _PendingCommit:
    """One client commit awaiting exactly one provider final."""

    event_id: str
    trigger: str
    sent_at: float
    connection_generation: int
    utterance_generation: int


class DashScopeSTTService(STTService):
    """DashScope Qwen 实时 ASR（qwen3-asr-flash-realtime）服务。"""

    Settings = DashScopeSTTSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str = "",
        settings: DashScopeSTTSettings | None = None,
        ttfs_p99_latency: float | None = DEFAULT_TTFS_P99,
        journal: Any = None,
        **kwargs,
    ):
        if settings is None:
            settings = self.Settings(model="qwen3-asr-flash-realtime")
        settings.api_key = api_key
        # 基类会把 str 语言转成 Language 枚举再映射；DashScope 只认原始 ISO
        # 代码，故仍清空基类字段，改由 settings.asr_language 直接下发。
        settings.language = None
        # 显式给出 TTFS（"说完话 → 收到 final"的 P99 延迟），供
        # TurnAnalyzerUserTurnStopStrategy 的 wait_for_transcript 安全网使用。
        # 不传时基类会退回 DEFAULT_TTFS_P99 并打 warning；0 表示"不再等待"，
        # 会导致 Smart Turn 一判完就放行、句中文本被截断，故绝不设 0。
        super().__init__(
            settings=settings,
            ttfs_p99_latency=ttfs_p99_latency,
            **kwargs,
        )
        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._session_finished = asyncio.Event()
        self._event_id = 0
        # 回合结束主动 commit：Manual 模式下这是唯一的 finalize 触发手段
        # （云端 VAD 已关闭）。关闭它等价于让本服务不产生任何 final 转录。
        self._commit_on_eou = os.getenv("DASHSCOPE_ASR_COMMIT_ON_EOU", "1").lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        if not self._commit_on_eou:
            logger.warning(
                f"{self}: DASHSCOPE_ASR_COMMIT_ON_EOU 已关闭，但 Manual 模式下"
                " commit 是唯一的 finalize 触发手段——本服务将不会产生 final 转录。"
            )
        # 本机 VAD 自上次 commit 以来是否真的听到过语音。DashScope 在
        # Manual 模式下对"纯静音缓冲"的 commit 会稳定返回幻觉 final（实测
        # 连续 5 次静音 commit 均返回「嗯。」），该幻觉会被回合层当作真实
        # 用户发言，形成「EOU→commit→幻觉→新回合→EOU」自激循环。因此
        # commit 与 final 都必须以"本机确实听到语音"为前提。
        self._speech_seen = False
        # 一个布尔量无法表达两个合法 commit 同时在途，也无法区分迟到 final
        # 属于哪一个连接/utterance。 使用 FIFO 队列保存 commit 身份。
        self._pending_commits: deque[_PendingCommit] = deque()
        self._connection_generation = 0
        self._utterance_generation = 0
        # commit 的触发点（2026-09-12 单点计时定位后修正）：
        #   "1"（默认）→ 本机 VAD 判定停止（VADUserStoppedSpeakingFrame）即 commit；
        #   "0"        → 等聚合器广播 UserStoppedSpeakingFrame 再 commit（旧行为）。
        #
        # 旧行为把 DashScope 的 commit→final 往返串行压在「回合已经结束」之后，
        # 而聚合器的 stop 策略（Smart Turn v3）在 wait_for_transcript=True 下
        # 必须"有文本 + （final 已到 或 p99 安全网超时）"才放行，于是构成
        # 「策略等文本、文本等策略」的循环依赖，只能靠 p99 安全网超时兜底：
        # 实测每轮固定多等 ttfs_p99 − stop_secs = 1.0 − 0.2 ≈ 0.8s，之后才
        # commit，再串行等 commit 往返。VAD 停止即 commit 让这段往返与
        # Smart Turn 推理并行，final 一到就能立刻结束回合。
        self._commit_on_vad_stop = os.getenv(
            "DASHSCOPE_ASR_COMMIT_ON_VAD_STOP", "1"
        ).lower() not in ("0", "false", "no", "off")
        # Optional durable journal (ASR-stage errors are reported through it) and
        # bounded reconnect bookkeeping for the upstream websocket.
        self._journal = journal
        self._reconnect_attempt = 0
        self._reconnect_lock = asyncio.Lock()
        self._closed = False

    def can_generate_metrics(self) -> bool:
        return True

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        await self._connect()

    async def _ensure_upstream(self) -> bool:
        """Reuse the live ASR websocket, or rebuild it with bounded backoff.

        In Manual mode ``commit`` is the only way to obtain a final transcript,
        so a dead socket does not merely lose audio: the current turn can never
        close.  The socket is therefore rebuilt rather than abandoned, and the
        session keeps running if the rebuild fails.
        """
        if self._closed:
            return False
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
                    await self._connect()
                    self._reconnect_attempt = 0
                    logger.info(f"{self}: ASR upstream reconnected")
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    delay = backoff_delay(attempt, base=0.5, cap=8.0)
                    self._reconnect_attempt = attempt
                    if self._journal is not None:
                        await self._journal.retry_scheduled(
                            "asr",
                            attempt=attempt,
                            delay_ms=int(delay * 1000),
                            reason=str(exc)[:200],
                        )
                    logger.warning(f"{self}: ASR 上游重连第 {attempt} 次失败: {exc}")
                    await asyncio.sleep(delay)
            self._ws = None
            if self._journal is not None:
                await self._journal.processor_error(
                    "asr",
                    "ASR 上游连接无法恢复，已降级为文本模式",
                    retryable=False,
                    degraded=True,
                )
            return False

    async def _connect(self) -> None:
        url = f"{self._settings.ws_url}?model={self._settings.model}"
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            max_size=16 * 1024 * 1024,
            open_timeout=15,
        )
        self._event_id = 0
        self._connection_generation += 1
        self._session_finished.clear()
        await self._ws.send(json.dumps(self._build_session_update(), ensure_ascii=False))
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info(f"{self}: connected to {self._settings.ws_url}")

    def _build_session_update(self) -> dict:
        """构建 session.update —— **Manual 模式**（``turn_detection=null``）。

        官方语义：``turn_detection`` 是 VAD 模式的开关；设为 ``null`` 即关闭
        云端 VAD 并启用 Manual 模式。Manual 模式下服务端不做断句，回合边界
        完全由本机 Silero VAD + Smart Turn v3 决定，并由
        ``input_audio_buffer.commit`` 手动触发识别——这正是双工通话要的语义。

        （旧实现发的是 ``server_vad``，而该模式下 commit 属"禁用场景"，
        导致回合结束时的 commit 被忽略、无法提前 finalize。）
        """
        return {
            "event_id": self._next_event_id("session"),
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "input_audio_transcription": {
                    "model": self._settings.model,
                    "language": self._settings.asr_language or "zh",
                },
                # None = 关闭云端 VAD，进入 Manual 模式（commit 才合法）。
                "turn_detection": None,
            },
        }

    def _next_event_id(self, kind: str) -> str:
        self._event_id += 1
        return f"evt_{kind}_{self._event_id}"

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        if self._ws is None:
            # A dropped socket must not silently stop transcription: the current
            # turn could never be finalized.  Rebuild in the background.
            await self._ensure_upstream()
        if self._ws is not None:
            try:
                await self._ws.send(
                    json.dumps(
                        {
                            "event_id": self._next_event_id("audio"),
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(audio).decode("ascii"),
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{self}: send audio failed: {exc}")
                self._ws = None
        yield None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """在基类 STT 逻辑之上，监听本机 EOU 信号主动 commit。

        ``UserStoppedSpeakingFrame`` 是本机 Silero VAD + Smart Turn v3 判定
        "用户说完"的信号（由 LLMUserAggregator 广播）。Manual 模式下服务端
        不会自行断句，因此这个 commit 是**产生 final 转录的唯一触发点**：
        此时语音音频必然已全部 append 到 DashScope，commit 后服务端立即返回
        ``conversation.item.input_audio_transcription.completed``。
        """
        await super().process_frame(frame, direction)
        # 本机 VAD 判定用户开口：标记"这一轮确实有语音"，供 commit 门闩使用。
        if isinstance(frame, VADUserStartedSpeakingFrame):
            # A new VAD onset starts a new utterance. Any old commit whose
            # final has not arrived is late evidence and must not be attached
            # to the new turn.
            self._utterance_generation += 1
            self._pending_commits.clear()
            self._speech_seen = True
        # commit 触发点：默认在 VAD 判定停止时（最早的可提交时刻），旧行为则
        # 等聚合器广播 UserStoppedSpeakingFrame（回合已经结束之后）。
        if isinstance(frame, VADUserStoppedSpeakingFrame) and self._commit_on_vad_stop:
            timeline_mark("stt", "收到 VAD 停止帧")
            await self._send_commit("vad-stop")
        if isinstance(frame, UserStoppedSpeakingFrame) and not self._commit_on_vad_stop:
            timeline_mark("stt", "收到回合结束帧")
            await self._send_commit("turn-stopped")

    async def _send_commit(self, trigger: str) -> None:
        """向 DashScope 发送 input_audio_buffer.commit，强制立即出 final。

        只有在本机 VAD 本回合确实听到过语音时才 commit：静音缓冲的 commit
        会拿到幻觉 final（见 ``__init__`` 的说明），进而自激出无限空回合。
        ``trigger`` 仅用于单点计时日志，标明这次 commit 是被谁触发的。
        """
        if self._ws is None or self._closed:
            return
        if not self._commit_on_eou:
            return
        if not self._speech_seen:
            logger.debug(f"{self}: skipped commit on EOU (no local speech this turn)")
            return
        event_id = self._next_event_id("commit")
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "event_id": event_id,
                        "type": "input_audio_buffer.commit",
                    }
                )
            )
            self._pending_commits.append(
                _PendingCommit(
                    event_id=event_id,
                    trigger=trigger,
                    sent_at=time.time(),
                    connection_generation=self._connection_generation,
                    utterance_generation=self._utterance_generation,
                )
            )
            logger.debug(f"{self}: committed audio buffer on EOU")
            timeline_mark("stt", "commit 已发出", f"trigger={trigger}")
            self._speech_seen = False
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: commit failed: {exc}")

    async def _read_loop(self) -> None:
        seen_types: set = set()
        try:
            async for raw in self._ws:
                payload = self._parse(raw)
                etype = str(payload.get("type") or "")
                if etype not in seen_types:
                    seen_types.add(etype)
                    logger.info("[DashScopeEvt] 首次见 type={!r}", etype)
                if etype == "session.finished":
                    # 收尾握手：cleanup() 等这个事件到齐后再断开连线。
                    self._session_finished.set()
                if "transcription" in etype:
                    # 只记事件类型与文本长度，不记转录正文（日志脱敏）。
                    logger.debug(
                        "[DashScopeEvt] type={!r} text_len={}",
                        etype,
                        len(str(payload.get("text") or payload.get("transcript") or "")),
                    )
                if etype == "conversation.item.input_audio_transcription.text":
                    text = str(payload.get("text") or "").strip()
                    # 仅在"本回合确实有语音"时上报 partial，避免静音期间的
                    # 服务端幻觉 partial 触发一次空回合。
                    if text and self._speech_seen:
                        await self.push_frame(
                            InterimTranscriptionFrame(
                                text, self._user_id, time_now_iso8601()
                            )
                        )
                elif etype == "conversation.item.input_audio_transcription.completed":
                    await self._handle_final(payload)
                elif etype in ("error", "asr.error"):
                    message = str(
                        payload.get("message") or payload.get("error") or "asr error"
                    )
                    logger.warning(f"{self}: asr error: {message}")
                    if self._journal is not None:
                        # Surface the stage failure instead of logging only: a
                        # silent ASR error is indistinguishable from "the user
                        # said nothing", which is the worst failure mode for a
                        # voice UI.
                        await self._journal.processor_error(
                            "asr",
                            message,
                            retryable=True,
                            attempt=self._reconnect_attempt,
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: reader ended: {exc}")
            self._ws = None
            if self._journal is not None:
                await self._journal.processor_error(
                    "asr",
                    f"ASR 读取循环中断: {exc}",
                    retryable=True,
                    attempt=self._reconnect_attempt,
                )

    async def _handle_final(self, payload: dict) -> None:
        """Accept one final per outstanding commit, in FIFO order.

        A boolean in-flight flag loses the second of two legal commits and can
        attach a late final to a newer utterance. Pending commits therefore
        carry both connection and utterance generations.
        """
        text = str(payload.get("transcript") or payload.get("text") or "").strip()
        if not self._pending_commits:
            logger.debug(
                "[DashScopeEvt] dropping unsolicited final (text_len={})",
                len(text),
            )
            return
        commit = self._pending_commits.popleft()
        if (
            commit.connection_generation != self._connection_generation
            or commit.utterance_generation != self._utterance_generation
        ):
            logger.debug(
                "[DashScopeEvt] dropping stale final (connection={}, utterance={})",
                commit.connection_generation,
                commit.utterance_generation,
            )
            return
        timeline_mark(
            "stt",
            "commit→final 往返",
            f"{time.time() - commit.sent_at:.3f}s text_len={len(text)}",
        )
        if not text:
            return
        await self.emit_stt_usage_metrics()
        await self.push_frame(
            TranscriptionFrame(
                text,
                self._user_id,
                time_now_iso8601(),
                result={
                    "commit_id": commit.event_id,
                    "commit_trigger": commit.trigger,
                    "connection_generation": commit.connection_generation,
                    "utterance_generation": commit.utterance_generation,
                },
                finalized=True,
            )
        )

    @staticmethod
    def _parse(raw: Any) -> dict:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    async def cleanup(self):
        # 优雅收尾：先发 session.finish 并等尾部 final（此刻管线仍在运行，
        # 最后一条 TranscriptionFrame 还能正常推送出去），再拆 reader 与连接，
        # 最后交给基类收尾。顺序颠倒会丢掉尾句的 final。
        self._closed = True
        self._connection_generation += 1
        self._pending_commits.clear()
        await self._send_session_finish()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except BaseException:  # noqa: BLE001
                pass
            self._reader_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
        await super().cleanup()

    async def _send_session_finish(self, timeout: float = 1.5) -> None:
        """发送 session.finish 并等待 session.finished。

        官方流程：若已检测到语音，服务端先发
        ``conversation.item.input_audio_transcription.completed``（尾句 final），
        再发 ``session.finished``；客户端收到后须主动断开。
        """
        if self._ws is None:
            return
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "event_id": self._next_event_id("finish"),
                        "type": "session.finish",
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{self}: session.finish 发送失败: {exc}")
            return
        try:
            await asyncio.wait_for(self._session_finished.wait(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{self}: {timeout}s 内未收到 session.finished: {exc}")
