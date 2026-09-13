from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.voice.schemas import (
    VoiceEventResponse, VoiceSession, VoiceSessionCreateRequest, VoiceTaskStartRequest,
    VoiceTaskView,
)
from app.voice.service import VoiceSessionService
from app.voice.runtime import runtime_info

router = APIRouter(prefix="/voice", tags=["voice"])

@router.get("/capabilities")
def voice_capabilities(settings: AppSettings):
    """Describe whether an external full-duplex audio worker is configured."""
    info = runtime_info(settings)
    return {
        "runtime_ready": info.ready,
        "signaling_url": info.signaling_url,
        "reason": info.reason,
        "transcription_model": "qwen3-asr-flash-realtime",
        "tts_provider": "huoshan_bidirectional_streaming",
    }

def svc(db: DB, context: CurrentWorkspace, settings: AppSettings) -> VoiceSessionService:
    return VoiceSessionService(db, context, settings)

@router.post("/sessions", response_model=VoiceSession)
def create_voice_session(payload: VoiceSessionCreateRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).create_session(
        payload.chat_session_id,
        payload.max_thinking_mode,
        payload.model_id,
        payload.provider_id,
    )

@router.get("/sessions/{voice_session_id}", response_model=VoiceSession)
def get_voice_session(voice_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).get_session(voice_session_id)

@router.get("/sessions/{voice_session_id}/runtime-config")
def get_voice_runtime_config(voice_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).runtime_config(voice_session_id)

@router.patch("/sessions/{voice_session_id}/model", response_model=VoiceSession)
def update_voice_model(voice_session_id: str, payload: dict, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).update_model(
        voice_session_id,
        str(payload.get("model_id") or "") or None,
        str(payload.get("provider_id") or "") or None,
    )

@router.delete("/sessions/{voice_session_id}", response_model=VoiceSession)
def end_voice_session(voice_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    session = svc(db, context, settings).get_session(voice_session_id, write=True)
    session.status = "ended"
    return session

@router.post("/sessions/{voice_session_id}/events/{event_type}", response_model=VoiceEventResponse)
def emit_voice_event(voice_session_id: str, event_type: str, payload: dict | None = None, db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    return VoiceEventResponse(event=svc(db, context, settings).envelope(voice_session_id, event_type, payload))

@router.post("/sessions/{voice_session_id}/tasks", response_model=VoiceTaskView)
def start_voice_task(voice_session_id: str, payload: VoiceTaskStartRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).start_task(voice_session_id, payload)

@router.get("/sessions/{voice_session_id}/tasks/{subagent_id}", response_model=VoiceTaskView)
def get_voice_task(voice_session_id: str, subagent_id: str, after_event_seq: int | None = Query(default=None, ge=0), db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    return svc(db, context, settings).get_task(voice_session_id, subagent_id, after_event_seq)

@router.post("/sessions/{voice_session_id}/tasks/{subagent_id}/cancel", response_model=VoiceTaskView)
def cancel_voice_task(voice_session_id: str, subagent_id: str, db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    return svc(db, context, settings).cancel_task(voice_session_id, subagent_id)

@router.post("/sessions/{voice_session_id}/interrupt", response_model=VoiceSession)
def interrupt_voice_session(voice_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    """Stop the current spoken response while keeping the logical session alive."""
    session = svc(db, context, settings).get_session(voice_session_id, write=True)
    # The audio worker consumes this event over its RTVI bridge.  Persisting the
    # sequence here also gives reconnecting clients a deterministic cursor.
    svc(db, context, settings).envelope(voice_session_id, "voice.interrupt")
    return session
