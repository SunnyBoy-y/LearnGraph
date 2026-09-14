"""In-process SmallWebRTC runtime for LearnGraph voice sessions.

This is the demo-voice2o2 runner reduced to a router: the same API process owns
the offer endpoint and starts the Pipecat pipeline per peer connection.
"""

import importlib.util
import json
import uuid
from typing import Annotated, Any


def install_embedded_runtime(app: Any) -> bool:
    """Install SmallWebRTC offer/ICE routes when the optional voice extra exists."""
    if importlib.util.find_spec("pipecat") is None:
        return False
    try:
        from fastapi import BackgroundTasks, Body, HTTPException
        from pipecat.runner.types import SmallWebRTCRunnerArguments
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
        from pipecat.transports.smallwebrtc.request_handler import (
            SmallWebRTCPatchRequest,
            SmallWebRTCRequest,
            SmallWebRTCRequestHandler,
        )

        from app.api.deps import AppSettings, DB, VoiceWorkspaceContext
        from app.voice import embedded_bot
        from app.voice.service import VoiceSessionService
    except (ImportError, ModuleNotFoundError):
        return False

    handler = SmallWebRTCRequestHandler(ice_servers=None, host="0.0.0.0")

    def _response_pc_id(response: Any) -> str | None:
        payload: Any = response
        body = getattr(response, "body", None)
        if isinstance(body, (bytes, bytearray)):
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                payload = None
        if not isinstance(payload, dict):
            return None
        value = payload.get("pc_id")
        return str(value) if value else None

    @app.post("/api/v1/voice/sessions/{session_id}/api/offer")
    async def voice_offer(
        session_id: str,
        request: Annotated[dict[str, Any], Body()],
        background_tasks: BackgroundTasks,
        db: DB,
        context: VoiceWorkspaceContext,
        settings: AppSettings,
    ):
        # This route is installed outside the normal APIRouter chain, so it
        # repeats the same bearer + workspace + owner + tenant checks before
        # allocating a peer or audio pipeline.
        service = VoiceSessionService(db, context, settings)
        service.require_runtime_access(session_id)
        try:
            parsed_request = SmallWebRTCRequest.from_dict(dict(request))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid WebRTC offer") from exc
        request_data = (
            parsed_request.request_data
            if isinstance(parsed_request.request_data, dict)
            else {}
        )
        request_voice_session_id = str(request_data.get("voice_session_id") or "")
        if request_voice_session_id and request_voice_session_id != session_id:
            raise HTTPException(status_code=409, detail="Voice session mismatch")

        async def on_connection(connection: SmallWebRTCConnection):
            args = SmallWebRTCRunnerArguments(
                webrtc_connection=connection,
                body=parsed_request.request_data,
                session_id=session_id or str(uuid.uuid4()),
            )
            # The bot registers itself in app.voice.runner_registry, which is what
            # makes DELETE able to cancel this pipeline (and a reconnect able to
            # replace it) instead of leaving an orphan worker behind.
            background_tasks.add_task(embedded_bot.bot, args)

        response = await handler.handle_web_request(
            request=parsed_request, webrtc_connection_callback=on_connection
        )
        pc_id = _response_pc_id(response)
        if not pc_id:
            raise HTTPException(status_code=502, detail="Voice peer id was not allocated")
        service.bind_peer(session_id, pc_id)
        return response

    @app.patch("/api/v1/voice/sessions/{session_id}/api/offer")
    async def voice_ice(
        session_id: str,
        request: Annotated[dict[str, Any], Body()],
        db: DB,
        context: VoiceWorkspaceContext,
        settings: AppSettings,
    ):
        try:
            parsed_request = SmallWebRTCPatchRequest(**dict(request))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid ICE patch") from exc
        VoiceSessionService(db, context, settings).require_runtime_access(
            session_id,
            peer_connection_id=parsed_request.pc_id,
        )
        await handler.handle_patch_request(parsed_request)
        return {"status": "success"}

    app.state.learn_graph_voice_runtime = handler
    return True


async def close_embedded_runtime(app: Any) -> None:
    """Shutdown hook: stop every local voice pipeline and its upstream sockets."""
    from app.voice.runner_registry import close_all_runners

    try:
        await close_all_runners(reason="app_shutdown")
    except Exception:
        pass
    handler = getattr(app.state, "learn_graph_voice_runtime", None)
    if handler is not None:
        await handler.close()
