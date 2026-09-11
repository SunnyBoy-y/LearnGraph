"""Runtime discovery for the optional full-duplex voice bridge.

The LearnGraph API owns the session and agent contract.  Audio transport is
provided by a separately deployed Pipecat/SmallWebRTC worker, so a session is
only marked audio-ready when that worker is explicitly configured.  This keeps
the UI from claiming that a microphone is connected on installations that have
only installed the base backend.
"""

from __future__ import annotations

import os
import importlib.util
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VoiceRuntimeInfo:
    ready: bool
    signaling_url: str | None = None
    reason: str | None = None


def runtime_info(settings: object | None = None) -> VoiceRuntimeInfo:
    """Return the configured audio worker without importing optional packages."""

    # Keep this environment driven so source installs and containers can use
    # the same image while choosing their worker at deployment time.
    url = (
        os.getenv("LEARNGRAPH_VOICE_RUNTIME_URL")
        or os.getenv("LEARNGRAPH_SMALLWEBRTC_URL")
        or ""
    ).strip().rstrip("/")
    enabled = os.getenv("LEARNGRAPH_VOICE_RUNTIME_ENABLED", "").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return VoiceRuntimeInfo(False, reason="voice_runtime_disabled")
    # The normal LearnGraph installation owns the SmallWebRTC routes in-process;
    # no second worker URL is required when the optional voice extra is present.
    embedded = os.getenv("LEARNGRAPH_VOICE_RUNTIME_EMBEDDED", "true").strip().lower()
    # Check the modules actually imported by the embedded runner, rather than
    # treating the top-level ``pipecat`` package alone as proof that the
    # runtime can start.  This prevents advertising a usable microphone when
    # the WebRTC/runner extras are only partially installed.
    required = (
        "pipecat",
        # 内嵌运行时实际用到的部分：SmallWebRTC 信令、Workers/Bus 多-agent 拓扑、
        # OpenAI 兼容 LLM 服务，以及自研 STT/TTS 适配层所需的 websockets。
        # 注意：不再检查 pipecat.services.deepseek.llm —— 管线已改为使用
        # 会话当前模型的 OpenAI 兼容通道。
        "pipecat.transports.smallwebrtc.request_handler",
        "pipecat.bus",
        "pipecat.workers.llm",
        "pipecat.workers.runner",
        "pipecat.services.openai.llm",
        "websockets",
    )
    embedded_ready = all(importlib.util.find_spec(name) is not None for name in required)
    if embedded not in {"0", "false", "no", "off"} and embedded_ready:
        return VoiceRuntimeInfo(True, signaling_url="/api/v1/voice/sessions/{session_id}/api/offer")
    if embedded not in {"0", "false", "no", "off"} and not url:
        return VoiceRuntimeInfo(False, reason="voice_runtime_dependency_missing")
    if not url:
        return VoiceRuntimeInfo(False, reason="voice_runtime_not_configured")
    if not url.startswith(("http://", "https://", "ws://", "wss://")):
        return VoiceRuntimeInfo(False, reason="voice_runtime_url_invalid")
    return VoiceRuntimeInfo(True, signaling_url=url)
