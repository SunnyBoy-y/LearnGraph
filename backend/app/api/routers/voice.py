from __future__ import annotations

from fastapi import APIRouter, Header, Query

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.voice.schemas import (
    VoiceEventResponse, VoiceSession, VoiceSessionCreateRequest, VoiceTaskStartRequest,
    VoiceTaskView, VoiceEventReplayResponse,
    VoiceResultDeliveryRequest, VoiceResultDeliveryView, VoiceResultListResponse,
    VoiceTaskCancelRequest, VoiceTaskRevisionRequest,
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
    service = svc(db, context, settings)
    session = service.create_session(
        payload.chat_session_id,
        payload.max_thinking_mode,
        payload.model_id,
        payload.provider_id,
    )
    # Snapshot ordinary ChatService context before the first audio turn. This
    # is best-effort so a provider outage cannot prevent session recovery.
    try:
        from app.services.chat_service_factory import build_chat_service
        from app.services.voice_context import VoiceContextService
        chat = build_chat_service(db, workspace_context=context, settings=settings,
            model_id=payload.model_id, provider_id=payload.provider_id,
            thinking_mode=payload.max_thinking_mode, agent_mode=False)
        snapshot = VoiceContextService(chat).load_snapshot(payload.chat_session_id)
        session = service.update_context_snapshot(session.id, snapshot.to_dict())
    except Exception:
        pass
    return session

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
async def end_voice_session(voice_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    """End a call: terminal event, runner teardown and idempotent re-entry.

    A repeated DELETE returns the same ended session and does not emit a second
    ``session.closed``.  ``service.close`` additionally closes a runner owned by
    this process; a runner in another worker observes the ended status through
    its control watchdog.
    """
    return await svc(db, context, settings).close(voice_session_id)

@router.post("/sessions/{voice_session_id}/events/{event_type}", response_model=VoiceEventResponse)
def emit_voice_event(voice_session_id: str, event_type: str, payload: dict | None = None, db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    body = dict(payload or {})
    phase = str(body.pop("phase", "authoritative"))
    causality = body.pop("causality", None)
    turn_id = body.pop("turn_id", body.pop("turnId", None))
    cursor = body.pop("audio_cursor_ms", body.pop("audioCursorMs", None))
    request_id = body.pop("request_id", body.pop("requestId", None))
    return VoiceEventResponse(event=svc(db, context, settings).envelope(
        voice_session_id, event_type, body, request_id=request_id,
        turn_id=turn_id, phase=phase, causality=causality,
        audio_cursor_ms=cursor,
    ))

@router.get("/sessions/{voice_session_id}/events", response_model=VoiceEventReplayResponse)
def replay_voice_events(voice_session_id: str, after_event_seq: int = Query(default=0, ge=0), db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    service = svc(db, context, settings)
    events = service.replay_events(voice_session_id, after_event_seq)
    current = service.get_session(voice_session_id).event_seq
    return VoiceEventReplayResponse(events=events, last_event_seq=max(current, events[-1].seq if events else after_event_seq))

@router.post("/sessions/{voice_session_id}/turns/accept")
def accept_voice_turn(voice_session_id: str, payload: dict, db: DB, context: CurrentWorkspace, settings: AppSettings):
    turn = svc(db, context, settings).accept_turn(
        voice_session_id, str(payload.get("text") or payload.get("user_text") or ""),
        str(payload.get("client_message_id") or "") or None,
        str(payload.get("turn_id") or "") or None,
    )
    return {"turn_id": turn.id, "status": turn.status, "client_message_id": turn.client_message_id}

@router.post("/sessions/{voice_session_id}/turns/{turn_id}/finalize")
def finalize_voice_turn(voice_session_id: str, turn_id: str, payload: dict | None = None, db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    turn = svc(db, context, settings).finalize_turn(voice_session_id, turn_id, str((payload or {}).get("assistant_text") or ""))
    return {"turn_id": turn.id, "status": turn.status, "assistant_text": turn.assistant_text}

@router.post("/sessions/{voice_session_id}/tasks", response_model=VoiceTaskView)
def start_voice_task(voice_session_id: str, payload: VoiceTaskStartRequest, db: DB, context: CurrentWorkspace, settings: AppSettings, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    payload = payload.model_copy(update={"idempotency_key": idempotency_key or payload.idempotency_key})
    return svc(db, context, settings).start_task(voice_session_id, payload)

@router.get("/sessions/{voice_session_id}/tasks/{subagent_id}", response_model=VoiceTaskView)
def get_voice_task(voice_session_id: str, subagent_id: str, after_event_seq: int | None = Query(default=None, ge=0), db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    return svc(db, context, settings).get_task(voice_session_id, subagent_id, after_event_seq)

@router.post("/sessions/{voice_session_id}/tasks/{subagent_id}/cancel", response_model=VoiceTaskView)
def cancel_voice_task(voice_session_id: str, subagent_id: str, payload: VoiceTaskCancelRequest | None = None, db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    return svc(db, context, settings).cancel_task(
        voice_session_id,
        subagent_id,
        reason=(payload.reason if payload else "user_requested"),
    )

@router.post("/sessions/{voice_session_id}/tasks/{subagent_id}/revise", response_model=VoiceTaskView)
def revise_voice_task(voice_session_id: str, subagent_id: str, payload: VoiceTaskRevisionRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).revise_task(
        voice_session_id,
        subagent_id,
        prompt=payload.prompt,
        title=payload.title,
        note=payload.note,
        idempotency_key=payload.idempotency_key,
    )

@router.get("/sessions/{voice_session_id}/results", response_model=VoiceResultListResponse)
def list_voice_results(
    voice_session_id: str,
    include_terminal: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    db: DB = None,
    context: CurrentWorkspace = None,
    settings: AppSettings = None,
):
    results = svc(db, context, settings).list_results(
        voice_session_id,
        include_terminal=include_terminal,
        limit=limit,
    )
    return VoiceResultListResponse(
        voice_session_id=voice_session_id,
        results=results,
        ready_count=sum(1 for item in results if item.status == "ready"),
        auto_speak=False,
    )

@router.post(
    "/sessions/{voice_session_id}/results/{result_id}/deliver",
    response_model=VoiceResultDeliveryView,
)
def deliver_voice_result(
    voice_session_id: str,
    result_id: str,
    payload: VoiceResultDeliveryRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
):
    return svc(db, context, settings).deliver_result(
        voice_session_id, result_id, request_id=payload.request_id
    )

@router.post("/sessions/{voice_session_id}/results/{result_id}/dismiss")
def dismiss_voice_result(voice_session_id: str, result_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return svc(db, context, settings).dismiss_result(voice_session_id, result_id)

@router.post("/sessions/{voice_session_id}/interrupt", response_model=VoiceSession)
def interrupt_voice_session(voice_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    """Stop the current spoken response while keeping the logical session alive.

    The interrupt is written to the durable journal; the audio worker (in this
    process or another) observes it on its control channel and stops the audio.
    The turn is marked interrupted, so an unheard partial answer is never
    written to the transcript or to long-term memory.
    """
    return svc(db, context, settings).interrupt(voice_session_id)


@router.get("/sessions/{voice_session_id}/transcript")
def get_voice_transcript(voice_session_id: str, limit: int = Query(default=50, ge=1, le=500),
                         db: DB = None, context: CurrentWorkspace = None, settings: AppSettings = None):
    """Authoritative transcript (finalized turns only) for reload recovery."""
    return {
        "voice_session_id": voice_session_id,
        "turns": svc(db, context, settings).transcript(voice_session_id, limit=limit),
    }
