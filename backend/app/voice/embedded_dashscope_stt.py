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
from dataclasses import dataclass
from typing import Any, AsyncGenerator

import websockets
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.settings import STTSettings
from pipecat.services.stt_latency import DEFAULT_TTFS_P99
from pipecat.services.stt_service import STTService
from pipecat.utils.time import time_now_iso8601


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

    def can_generate_metrics(self) -> bool:
        return True

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        await self._connect()

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
        if isinstance(frame, UserStoppedSpeakingFrame):
            await self._send_commit()

    async def _send_commit(self) -> None:
        """向 DashScope 发送 input_audio_buffer.commit，强制立即出 final。"""
        if self._ws is None:
            return
        if not self._commit_on_eou:
            return
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "event_id": self._next_event_id("commit"),
                        "type": "input_audio_buffer.commit",
                    }
                )
            )
            logger.debug(f"{self}: committed audio buffer on EOU")
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
                    if text:
                        await self.push_frame(
                            InterimTranscriptionFrame(
                                text, self._user_id, time_now_iso8601()
                            )
                        )
                elif etype == "conversation.item.input_audio_transcription.completed":
                    text = str(
                        payload.get("transcript") or payload.get("text") or ""
                    ).strip()
                    if text:
                        await self.emit_stt_usage_metrics()
                        await self.push_frame(
                            TranscriptionFrame(
                                text,
                                self._user_id,
                                time_now_iso8601(),
                                finalized=True,
                            )
                        )
                elif etype in ("error", "asr.error"):
                    logger.warning(
                        f"{self}: asr error: {payload.get('message') or payload.get('error')}"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"{self}: reader ended: {exc}")

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
