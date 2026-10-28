import asyncio
import json

import pytest
from fastapi import WebSocketDisconnect

from app.voice.websocket_socket import BoundedVoiceSocket


def test_socket_handles_heartbeat_without_forwarding_it_to_rtvi():
    class Socket:
        def __init__(self):
            self.messages = [
                {"type": "websocket.receive", "text": '{"type":"voice.ping"}'},
                {"type": "websocket.receive", "bytes": b"audio"},
            ]
            self.sent = []

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    async def check():
        raw = Socket()
        socket = BoundedVoiceSocket(raw)
        assert (await socket.receive())["bytes"] == b"audio"
        assert raw.sent == [{"type": "voice.pong"}]

    asyncio.run(check())


def test_socket_closes_instead_of_waiting_forever_on_a_stalled_writer():
    class Socket:
        code = None

        async def send_bytes(self, _):
            raise TimeoutError

        async def close(self, code):
            self.code = code

    async def check():
        raw = Socket()
        with pytest.raises(TimeoutError):
            await BoundedVoiceSocket(raw).send_bytes(b"audio")
        assert raw.code == 1013

    asyncio.run(check())


def test_socket_rejects_oversized_audio_before_deserialization():
    class Socket:
        code = None

        async def receive(self):
            return {"type": "websocket.receive", "bytes": b"x" * 16385}

        async def close(self, code):
            self.code = code

    async def check():
        raw = Socket()
        with pytest.raises(WebSocketDisconnect):
            await BoundedVoiceSocket(raw).receive()
        assert raw.code == 1009

    asyncio.run(check())
