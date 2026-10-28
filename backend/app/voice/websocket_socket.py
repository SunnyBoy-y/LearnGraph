"""Bounded ASGI socket IO for the duplex voice transport."""

import asyncio
import contextlib
import json

from fastapi import WebSocketDisconnect


class BoundedVoiceSocket:
    """Serialize writers, bound stalled sends/reads, and handle keepalives."""

    def __init__(self, websocket):
        self.websocket = websocket
        self.lock = asyncio.Lock()

    def __getattr__(self, name):
        return getattr(self.websocket, name)

    async def send_text(self, text):
        try:
            async with self.lock:
                await asyncio.wait_for(self.websocket.send_text(text), timeout=2)
        except TimeoutError:
            await self.close(code=1013)
            raise

    async def send_bytes(self, data):
        try:
            async with self.lock:
                await asyncio.wait_for(self.websocket.send_bytes(data), timeout=2)
        except TimeoutError:
            await self.close(code=1013)
            raise

    async def close(self, code=1000):
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.websocket.close(code=code), timeout=1)

    async def receive(self):
        while True:
            try:
                message = await asyncio.wait_for(self.websocket.receive(), timeout=45)
            except TimeoutError:
                await self.close(code=1001)
                raise WebSocketDisconnect(code=1001) from None
            data = message.get("bytes") or message.get("text") or b""
            if len(data) > 16_384:
                await self.close(code=1009)
                raise WebSocketDisconnect(code=1009)
            if message.get("text"):
                try:
                    control = json.loads(message["text"])
                except ValueError:
                    control = None
                if isinstance(control, dict) and control.get("type") == "voice.ping":
                    await self.send_text('{"type":"voice.pong"}')
                    continue
            return message
