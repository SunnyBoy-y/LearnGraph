"""In-process SmallWebRTC runtime for LearnGraph voice sessions.

This is the demo-voice2o2 runner reduced to a router: the same API process owns
the offer endpoint and starts the Pipecat pipeline per peer connection.
"""
import importlib.util
import uuid
from typing import Any


def install_embedded_runtime(app: Any) -> bool:
    """Install SmallWebRTC offer/ICE routes when the optional voice extra exists."""
    if importlib.util.find_spec("pipecat") is None:
        return False
    try:
        from fastapi import BackgroundTasks, HTTPException, Request, Response
        from pipecat.runner.types import SmallWebRTCRunnerArguments
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
        from pipecat.transports.smallwebrtc.request_handler import (
            IceCandidate, SmallWebRTCPatchRequest, SmallWebRTCRequest,
            SmallWebRTCRequestHandler,
        )
        from app.voice import embedded_bot
    except (ImportError, ModuleNotFoundError):
        return False

    handler = SmallWebRTCRequestHandler(ice_servers=None, host="0.0.0.0")

    @app.post("/api/v1/voice/sessions/{session_id}/api/offer")
    async def voice_offer(session_id: str, request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
        # The offer route is outside the normal APIRouter dependency chain;
        # reject stale or fabricated ids before allocating a WebRTC worker.
        from app.voice.service import VoiceSessionService
        with VoiceSessionService._lock:
            session = VoiceSessionService._sessions.get(session_id)
        if session is None or session.status != "active":
            raise HTTPException(status_code=404, detail="Voice session was not found")

        async def on_connection(connection: SmallWebRTCConnection):
            args = SmallWebRTCRunnerArguments(
                webrtc_connection=connection,
                body=request.request_data,
                session_id=session_id or str(uuid.uuid4()),
            )
            background_tasks.add_task(embedded_bot.bot, args)

        return await handler.handle_web_request(request=request, webrtc_connection_callback=on_connection)

    @app.patch("/api/v1/voice/sessions/{session_id}/api/offer")
    async def voice_ice(session_id: str, request: SmallWebRTCPatchRequest):
        from app.voice.service import VoiceSessionService
        with VoiceSessionService._lock:
            session = VoiceSessionService._sessions.get(session_id)
        if session is None or session.status != "active":
            raise HTTPException(status_code=404, detail="Voice session was not found")
        await handler.handle_patch_request(request)
        return {"status": "success"}

    app.state.learn_graph_voice_runtime = handler
    return True


async def close_embedded_runtime(app: Any) -> None:
    handler = getattr(app.state, "learn_graph_voice_runtime", None)
    if handler is not None:
        await handler.close()
