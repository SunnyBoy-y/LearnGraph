from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.voice.schemas import (
    VoiceEventResponse, VoiceSession, VoiceSessionCreateRequest, VoiceTaskStartRequest,
    VoiceTaskView, VoiceTurn, VoiceTurnRequest,
)
from app.voice.service import VoiceSessionService

router = APIRouter(prefix="/voice", tags=["voice"])

def svc(db: DB, context: CurrentWorkspace, settings: AppSettings) -> VoiceSessionService:
    return VoiceSessionService(db, context, settings)

@router.post("/sessions", response_model=VoiceSession)
def create_voice_session(payload: VoiceSessionCreateRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).create_session(payload.chat_session_id, payload.max_thinking_mode)

@router.get("/sessions/{voice_session_id}", response_model=VoiceSession)
def get_voice_session(voice_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).get_session(voice_session_id)

@router.delete("/sessions/{voice_session_id}", response_model=VoiceSession)
def end_voice_session(voice_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    session = svc(db, context, settings).get_session(voice_session_id, write=True)
    session.status = "ended"
    return session

@router.post("/sessions/{voice_session_id}/turns", response_model=VoiceTurn)
def append_voice_turn(voice_session_id: str, payload: VoiceTurnRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).append_turn(voice_session_id, payload)

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
