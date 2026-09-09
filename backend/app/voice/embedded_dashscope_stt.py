"""DashScope 实时语音识别（qwen3-asr-flash-realtime）— Pipecat STTService 适配。

复用阿里云百炼 DashScope Realtime 协议（与忆伴 Agent/src/voice/stt.py 一致）：
  -> session.update（server_vad）-> input_audio_buffer.append(base64 16k PCM)
  <- conversation.item.input_audio_transcription.text（partial）
  <- conversation.item.input_audio_transcription.completed（final）

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
from pipecat.services.stt_service import STTService
from pipecat.utils.time import time_now_iso8601


@dataclass
class DashScopeSTTSettings(STTSettings):
    ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
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
        **kwargs,
    ):
        if settings is None:
            settings = self.Settings(model="qwen3-asr-flash-realtime")
        settings.api_key = api_key
        settings.language = None
        super().__init__(settings=settings, **kwargs)
        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._event_id = 0
        # EOU 主动 commit 开关：收到 UserStoppedSpeakingFrame 时向 DashScope
        # 发 input_audio_buffer.commit，强制立即 finalize，消除云端静音窗口延迟。
        self._commit_on_eou = os.getenv("DASHSCOPE_ASR_COMMIT_ON_EOU", "1").lower() not in (
            "0",
            "false",
            "no",
            "off",
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
        return {
            "event_id": self._next_event_id("session"),
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "input_audio_transcription": {
                    "model": self._settings.model,
                    "language": "zh",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.0,
                    "silence_duration_ms": self._settings.silence_ms,
                },
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

        ``UserStoppedSpeakingFrame`` 是本机 Smart Turn v3 判定"用户说完"
        的权威信号（由 LLMUserAggregator 双向广播，默认开启）。此时所有
        语音音频帧必然已 append 到 DashScope，立即发 ``input_audio_buffer.commit``
        强制云端马上 finalize，消除"等服务端 VAD 静音窗口（silence_duration_ms）
        才出 final"的延迟。
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
                if "transcription" in etype:
                    logger.info(
                        "[DashScopeEvt] type={!r} text={!r} transcript={!r}",
                        etype,
                        payload.get("text"),
                        payload.get("transcript"),
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
        await super().cleanup()
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
