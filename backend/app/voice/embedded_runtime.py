"""In-process SmallWebRTC runtime for LearnGraph voice sessions.

This is the demo-voice2o2 runner reduced to a router: the same API process owns
the offer endpoint and starts the Pipecat pipeline per peer connection.
"""

import asyncio
import importlib.util
import json
import logging
import uuid
from typing import Annotated, Any

logger = logging.getLogger(__name__)


def install_embedded_runtime(app: Any) -> bool:
    """Install SmallWebRTC offer/ICE routes when the optional voice extra exists."""
    if importlib.util.find_spec("pipecat") is None:
        return False
    try:
        from fastapi import BackgroundTasks, Body, HTTPException
        from pipecat.runner.types import SmallWebRTCRunnerArguments
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
        from pipecat.transports.smallwebrtc.request_handler import (
            IceCandidate,
            SmallWebRTCPatchRequest,
            SmallWebRTCRequest,
            SmallWebRTCRequestHandler,
        )

        from app.api.deps import AppSettings, DB, VoiceWorkspaceContext
        from app.voice import embedded_bot
        from app.voice.embedded_ice import install_ice_compat
        from app.voice.service import VoiceSessionService
    except (ImportError, ModuleNotFoundError):
        return False

    # ICE/TURN 的可观测性与兼容层：aioice 的 TURN 绑定失败要变成一条带地址的
    # warning（而不是无人取用的任务堆栈），且 aioice/aiortc 的 ICE 决策要能进
    # 日志 —— 否则「通话一直停在 checking」在日志里没有任何可用线索。
    try:
        install_ice_compat()
    except Exception:
        logger.debug("voice ICE instrumentation failed", exc_info=True)

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

        # Deployment-wide TURN/STUN, configured by the instance administrator.
        # It is applied to the *handler*, not to this connection, because pipecat
        # reads it while building each new peer connection: a saved change takes
        # effect on the next call with no restart, and peers already up are left
        # alone.  Resolution never raises -- a relay that is genuinely off or
        # broken degrades to host-only ICE, i.e. exactly the behaviour of a
        # deployment with no relay at all.
        #
        # A *transient* upstream failure is not "off": the resolver keeps serving
        # the last minted credential while it is still valid, so a credential
        # refresh that times out no longer clears a working relay for the next
        # peer connection (that used to leave the browser with relay candidates
        # and the server with none, i.e. a call that can never connect).
        try:
            from app.services.voice_relay import resolve_for_runtime

            resolved = await asyncio.to_thread(resolve_for_runtime)
            if resolved.configured:
                handler.update_ice_servers(resolved.as_aiortc_servers())
                if resolved.source == "stale":
                    logger.warning(
                        "Voice relay credential is stale (%s); reusing the last one",
                        resolved.detail,
                    )
                else:
                    logger.debug(
                        "Voice relay applied (%s URLs, source=%s)",
                        len(resolved.urls),
                        resolved.source,
                    )
            else:
                handler.update_ice_servers([])
                logger.debug("Voice relay not configured (%s)", resolved.detail)
        except Exception:
            logger.debug("voice relay injection failed", exc_info=True)

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
            # ``SmallWebRTCPatchRequest`` and ``IceCandidate`` are plain dataclasses
            # in pipecat 1.9 (see ``pipecat/runner/run.py``), so unpacking the JSON
            # body directly would leave ``candidates`` as raw dicts and
            # ``handle_patch_request`` would blow up on ``candidate.candidate``
            # with a 500.  That failure is fatal, not cosmetic: the candidates in
            # this PATCH are the *only* place the server learns the browser's
            # addresses (the offer SDP carries none), so without them ICE never
            # completes and every call dies on "Timeout establishing the
            # connection to the remote peer" while the pipeline itself looks
            # healthy.  Map them explicitly, exactly like the reference runner.
            candidates = [
                IceCandidate(
                    candidate=str(item.get("candidate") or ""),
                    sdp_mid=str(item.get("sdp_mid") or "0"),
                    sdp_mline_index=int(item.get("sdp_mline_index") or 0),
                )
                for item in (request.get("candidates") or [])
                if isinstance(item, dict)
            ]
            parsed_request = SmallWebRTCPatchRequest(
                pc_id=str(request.get("pc_id") or ""),
                candidates=candidates,
            )
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
