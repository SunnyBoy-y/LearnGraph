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
    if embedded not in {"0", "false", "no", "off"} and importlib.util.find_spec("pipecat") is not None:
        return VoiceRuntimeInfo(True, signaling_url="/api/v1/voice/sessions/{session_id}/api/offer")
    if embedded not in {"0", "false", "no", "off"} and not url:
        return VoiceRuntimeInfo(False, reason="voice_runtime_dependency_missing")
    if not url:
        return VoiceRuntimeInfo(False, reason="voice_runtime_not_configured")
    if not url.startswith(("http://", "https://", "ws://", "wss://")):
        return VoiceRuntimeInfo(False, reason="voice_runtime_url_invalid")
    return VoiceRuntimeInfo(True, signaling_url=url)
