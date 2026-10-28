import json

import pytest

pytest.importorskip("pipecat.transports.websocket.fastapi")

from app.core.database import SessionLocal
from app.domain.models import VoiceSessionRecord


def make_session(client, register_user, auth_headers, prefix):
    token, workspace, *_ = register_user(prefix)
    headers = auth_headers(token, workspace)
    chat = client.post("/api/v1/sessions", headers=headers, json={"title": "WSS"}).json()
    response = client.post("/api/v1/voice/sessions", headers=headers, json={"chat_session_id": chat["id"]})
    assert response.status_code == 200, response.text
    voice = response.json()
    assert voice["websocket_url"].endswith("/api/audio")
    return token, workspace, voice


def start(token, workspace, **extra):
    return dict(type="voice.start", version=1, codec="pcm", token=token, workspace_id=workspace, **extra)


@pytest.fixture
def echo_bot(monkeypatch):
    from app.voice import embedded_bot

    calls = []

    async def echo(transport, args):
        from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, InterruptionFrame

        calls.append(args)
        serializer = transport._params.serializer
        socket = transport._client._websocket
        await transport._on_client_connected(socket)
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                return
            frame = await serializer.deserialize(message.get("bytes") or message.get("text"))
            if isinstance(frame, InputAudioRawFrame):
                output = OutputAudioRawFrame(audio=b"\0" * 960, sample_rate=24000, num_channels=1)
            else:
                output = InterruptionFrame()
            payload = await serializer.serialize(output)
            if isinstance(payload, bytes):
                await socket.send_bytes(payload)
            elif payload:
                await socket.send_text(payload)

    monkeypatch.setattr(embedded_bot, "run_bot", echo)
    return calls


def test_duplex_pcm_and_rtvi_bind_and_release_same_session(client, register_user, auth_headers, echo_bot):
    from app.voice.websocket_serializer import HEADER

    token, workspace, voice = make_session(client, register_user, auth_headers, "ws_ok")
    path = voice["websocket_url"].replace("{session_id}", voice["id"])
    with client.websocket_connect(path) as socket:
        socket.send_json(start(token, workspace))
        assert socket.receive_json()["type"] == "voice.ready"
        socket.send_bytes(HEADER.pack(b"LG", 1, 2, 0, 0, 0) + b"\0" * 640)
        response = socket.receive_bytes()
        assert len(response) == 16 + 960
        assert HEADER.unpack_from(response)[-1] == 0
        socket.send_json({"label": "rtvi-ai", "type": "client-ready", "id": "ready"})
        assert socket.receive_json() == {"type": "voice.clear", "generation": 1}
        socket.send_json({"type": "voice.ping"})
        assert socket.receive_json()["type"] == "voice.pong"
    assert len(echo_bot) == 1
    assert echo_bot[0].body["voice_session_id"] == voice["id"]
    with SessionLocal() as db:
        row = db.get(VoiceSessionRecord, voice["id"])
        assert row.peer_connection_id.startswith("wss:")
        assert row.runtime_owner_id is None


@pytest.mark.parametrize("invalid", ["token", "workspace", "session"])
def test_socket_rejects_foreign_identity_and_scope(client, register_user, auth_headers, echo_bot, invalid):
    token, workspace, voice = make_session(client, register_user, auth_headers, f"ws_{invalid}")
    if invalid == "token":
        token = "invalid"
    elif invalid == "workspace":
        _, workspace, *_ = register_user("ws_other")
    else:
        token, workspace, *_ = register_user("ws_foreign")
    path = voice["websocket_url"].replace("{session_id}", voice["id"])
    with client.websocket_connect(path) as socket:
        socket.send_json(start(token, workspace))
        error = socket.receive_json()
        assert error["type"] == "voice.error"
        assert error["code"] in {"session_invalid", "workspace_forbidden", "voice_session_not_found"}
    assert not echo_bot


@pytest.mark.parametrize("message", [
    {"type": "voice.start", "version": 2, "codec": "pcm"},
    {"type": "voice.start", "version": 1, "codec": "webm"},
    [],
])
def test_invalid_handshake_does_not_start_pipeline(client, echo_bot, message):
    with client.websocket_connect("/api/v1/voice/sessions/missing/api/audio") as socket:
        socket.send_text(json.dumps(message))
        assert socket.receive_json()["code"] == "invalid_start"
    assert not echo_bot


def test_reconnect_increments_fencing_without_creating_business_session(client, register_user, auth_headers, echo_bot):
    token, workspace, voice = make_session(client, register_user, auth_headers, "ws_reconnect")
    path = voice["websocket_url"].replace("{session_id}", voice["id"])
    for reconnecting in (False, True):
        with client.websocket_connect(path) as socket:
            socket.send_json(start(token, workspace, reconnecting=reconnecting))
            assert socket.receive_json()["type"] == "voice.ready"
    assert [args.body["runtime_peer_generation"] for args in echo_bot] == [1, 2]
    assert {args.session_id for args in echo_bot} == {voice["id"]}


def test_browser_origin_is_checked_before_accepting_audio(client, echo_bot):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/voice/sessions/missing/api/audio",
            headers={"Origin": "https://untrusted.example"},
        ):
            pass
    assert not echo_bot


def test_disabled_wss_is_not_advertised_or_accepted(client, register_user, auth_headers, echo_bot, monkeypatch):
    from app.core.config import get_settings
    from starlette.websockets import WebSocketDisconnect

    token, workspace, voice = make_session(client, register_user, auth_headers, "ws_disabled")
    monkeypatch.setattr(get_settings(), "voice_wss_enabled", False)
    headers = auth_headers(token, workspace)
    assert client.get(f"/api/v1/voice/sessions/{voice['id']}", headers=headers).json()["websocket_url"] is None
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(voice["websocket_url"].replace("{session_id}", voice["id"])):
            pass
    assert not echo_bot
