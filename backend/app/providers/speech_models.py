"""Model-driven speech provider catalog.

This catalog is intentionally separate from the general provider catalog:
speech models select a transport and a purpose (realtime, stored,
stored_async, or tts), while a provider row remains the credential and
endpoint container.  The defaults describe code paths implemented locally;
they are not a remote model discovery snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Literal


SpeechPurpose = Literal["realtime", "stored", "stored_async", "tts"]


@dataclass(frozen=True, slots=True)
class SpeechModelSpec:
    id: str
    label: str
    purpose: SpeechPurpose
    provider_type: str
    default_base_url: str
    default_capabilities: dict[str, Any]
    description: str

    def view(self) -> dict[str, Any]:
        # ``asdict`` recursively copies the dataclass, and deepcopy protects
        # callers from mutating the process-level defaults.
        return deepcopy(asdict(self))


SPEECH_MODEL_SPECS: tuple[SpeechModelSpec, ...] = (
    SpeechModelSpec(
        id="qwen3-asr-flash-realtime",
        label="通义千问 Qwen3 ASR Flash Realtime",
        purpose="realtime",
        provider_type="openai_compatible_transcription",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_capabilities={
            "speech_model_id": "qwen3-asr-flash-realtime",
            "default_realtime_transcription_model_id": "qwen3-asr-flash-realtime",
            "realtime_ws_url": "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
            "realtime_sample_rate": 16_000,
            "realtime_silence_ms": 400,
            "realtime_language": "zh",
        },
        description="DashScope Realtime WebSocket ASR with partial/final events for the full-duplex voice path.",
    ),
    SpeechModelSpec(
        id="qwen3-asr-flash",
        label="通义千问 Qwen3 ASR Flash",
        purpose="stored",
        provider_type="openai_compatible_transcription",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_capabilities={
            "speech_model_id": "qwen3-asr-flash",
            "default_transcription_model_id": "qwen3-asr-flash",
        },
        description="DashScope compatible input_audio transcription for stored recordings and dictation segments.",
    ),
    SpeechModelSpec(
        id="whisper-1",
        label="OpenAI Whisper-1",
        purpose="stored",
        provider_type="openai_compatible_transcription",
        default_base_url="https://api.openai.com/v1",
        default_capabilities={
            "speech_model_id": "whisper-1",
            "default_transcription_model_id": "whisper-1",
        },
        description="OpenAI-compatible /audio/transcriptions multipart ASR for stored audio.",
    ),
    SpeechModelSpec(
        id="paraformer-v2",
        label="通义千问 Paraformer V2",
        purpose="stored_async",
        provider_type="openai_compatible_transcription",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_capabilities={
            "speech_model_id": "paraformer-v2",
            "default_async_transcription_model_id": "paraformer-v2",
        },
        description="DashScope asynchronous file transcription submitted by public audio URL.",
    ),
    SpeechModelSpec(
        id="sensevoice-v1",
        label="通义千问 SenseVoice V1",
        purpose="stored_async",
        provider_type="openai_compatible_transcription",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_capabilities={
            "speech_model_id": "sensevoice-v1",
            "default_async_transcription_model_id": "sensevoice-v1",
        },
        description="DashScope asynchronous file transcription submitted by public audio URL.",
    ),
    SpeechModelSpec(
        id="seed-tts-2.0-standard",
        label="火山引擎 Seed TTS 2.0 Standard",
        purpose="tts",
        provider_type="volcengine_tts",
        default_base_url="wss://openspeech.bytedance.com/api/v3/tts/bidirection",
        default_capabilities={
            "speech_model_id": "seed-tts-2.0-standard",
            "default_tts_model_id": "seed-tts-2.0-standard",
            "voice_type": "ICL_uranus_zh_female_heainainai_tob",
            # X-Api-Resource-Id for the v3 bidirectional endpoint. Prefilled so the
            # settings form never asks the operator to guess it: the seeded voice and
            # every ICL_* clone voice belong to seed-tts-2.0 (seed-icl-2.0 is rejected
            # by the server as "resource ID is mismatched with speaker related resource").
            "resource_id": "seed-tts-2.0",
            "sample_rate": 24_000,
            "emotion": "",
            "speech_rate": 0,
        },
        description="Volcengine Bidirectional TTS 2.0 PCM WebSocket output with cancellation support.",
    ),
)

SPEECH_MODEL_BY_ID: dict[str, SpeechModelSpec] = {
    item.id: item for item in SPEECH_MODEL_SPECS
}


def speech_models_for_provider(provider_type: str) -> tuple[SpeechModelSpec, ...]:
    return tuple(item for item in SPEECH_MODEL_SPECS if item.provider_type == provider_type)


def speech_model_spec(model_id: str | None) -> SpeechModelSpec | None:
    return SPEECH_MODEL_BY_ID.get(str(model_id or "").strip())

