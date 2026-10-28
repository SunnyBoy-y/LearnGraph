"""Authenticated, same-origin duplex audio without an ICE dependency."""

import asyncio
import contextlib
import json
import logging
import uuid
from urllib.parse import urlsplit

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.api.deps import AppSettings, DB, current_principal, workspace_context
from app.core.errors import AppError
from app.voice.websocket_socket import BoundedVoiceSocket

logger = logging.getLogger(__name__)


def authorize_voice_socket(db, websocket, settings, session_id, start):
    """Reuse HTTP identity, workspace policy, and voice owner checks.

    The path id names a *voice* session, not a chat session. Resource ownership
    is therefore enforced by require_runtime_access, as in the offer route.
    """
    from app.voice.runner_registry import runtime_owner_id
    from app.voice.service import VoiceSessionService

    token = start.get("token")
    workspace_id = start.get("workspace_id")
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise AppError(401, "unauthorized", "A valid session token is required")
    if not isinstance(workspace_id, str) or not 1 <= len(workspace_id) <= 64:
        raise AppError(401, "unauthorized", "A workspace is required")
    principal = current_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
    scope = dict(websocket.scope, type="http", method="POST", path_params={})
    context = workspace_context(Request(scope), workspace_id, principal, db)
    service = VoiceSessionService(db, context, settings)
    row = service.require_runtime_access(session_id)
    epoch = row.session_epoch
    owner = runtime_owner_id()
    if not service.claim_runtime_owner(
        session_id, owner, force=start.get("reconnecting") is True,
        expected_session_epoch=epoch,
    ):
        raise AppError(409, "voice_runtime_owned_elsewhere", "Voice runtime is owned by another instance")
    try:
        bound = service.bind_peer(session_id, f"wss:{uuid.uuid4()}", owner_id=owner)
    except Exception:
        service.release_runtime_owner(session_id, owner, expected_session_epoch=epoch)
        raise
    return {
        "voice_session_id": session_id,
        "runtime_session_epoch": epoch,
        "runtime_owner_id": owner,
        "runtime_peer_generation": bound.peer_generation,
    }


def install_websocket_runtime(app):
    @app.websocket("/api/v1/voice/sessions/{session_id}/api/audio")
    async def voice_audio(websocket: WebSocket, session_id: str, db: DB, settings: AppSettings):
        from app.voice.runtime import websocket_runtime_url

        if not websocket_runtime_url(settings):
            await websocket.close(code=1008)
            return
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host", "")
        allowed = set(settings.cors_origins)
        if origin and urlsplit(origin).netloc != host and origin not in allowed:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        body = None
        socket = BoundedVoiceSocket(websocket)
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            if len(raw) > 8192:
                raise ValueError("Handshake exceeds limit")
            start = json.loads(raw)
            if not isinstance(start, dict) or start.get("type") != "voice.start" or start.get("version") != 1:
                raise ValueError("Expected voice.start version 1")
            codec = start.get("codec")
            if codec not in {"opus", "pcm"}:
                raise ValueError("Unsupported voice codec")
            # Reject before allocating a runner if the optional transport cannot
            # start. Tokens stay in the first TLS message, never in URL/logs.
            from pipecat.runner.types import RunnerArguments
            from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
            from app.voice.websocket_serializer import VoiceWebSocketSerializer

            body = authorize_voice_socket(db, websocket, settings, session_id, start)
            # Do not hold a transaction/connection throughout a long voice call.
            db.close()
            serializer = VoiceWebSocketSerializer(codec)
            transport = FastAPIWebsocketTransport(
                socket,
                FastAPIWebsocketParams(
                    audio_in_enabled=True, audio_out_enabled=True,
                    audio_in_sample_rate=16_000, audio_out_sample_rate=24_000,
                    audio_out_channels=1, audio_out_10ms_chunks=2,
                    add_wav_header=False, serializer=serializer,
                    allowed_origins=[],
                ),
            )
            @transport.event_handler("on_client_connected")
            async def ready(*_):
                # Start microphone capture only once Pipecat starts its input,
                # not while ASR/VAD/model services are still being constructed.
                await socket.send_text(json.dumps({
                    "type": "voice.ready", "version": 1, "codec": codec,
                    "input_sample_rate": 48_000 if codec == "opus" else 16_000,
                    "output_sample_rate": 48_000 if codec == "opus" else 24_000,
                    "frame_ms": 20, "generation": 0,
                }))
            from app.voice.embedded_bot import run_bot

            await run_bot(transport, RunnerArguments(body=body, session_id=session_id))
        except AppError as exc:
            await socket.send_text(json.dumps({
                "type": "voice.error", "code": exc.code, "message": exc.message,
            }))
        except (ValueError, TimeoutError):
            with contextlib.suppress(Exception):
                await socket.send_text('{"type":"voice.error","code":"invalid_start","message":"Invalid voice handshake"}')
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Voice WSS runtime failed for session %s", session_id)
            with contextlib.suppress(Exception):
                await socket.send_text('{"type":"voice.error","code":"voice_runtime_failed","message":"Voice runtime could not continue"}')
        finally:
            if body:
                from app.voice.events import release_runtime_owner

                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        release_runtime_owner, session_id, body["runtime_owner_id"],
                        expected_session_epoch=body["runtime_session_epoch"],
                        expected_peer_generation=body["runtime_peer_generation"],
                    )
            await socket.close()
