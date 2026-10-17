import asyncio
import base64
import json
import struct
from unittest.mock import AsyncMock

from app.voice.embedded_dashscope_stt import DashScopeSTTService


def test_preserves_quiet_prefix_once_without_connecting_during_silence():
    async def exercise():
        service=DashScopeSTTService(api_key='test')
        service._ensure_upstream=AsyncMock()
        service._note_vad_missing=AsyncMock()
        class Socket:
            close_code=None
            closed=False
            def __init__(self): self.messages=[]
            async def send(self,data): self.messages.append(json.loads(data))
        socket=Socket()
        service._ws=socket
        service._upstream_alive=lambda: True
        quiet=struct.pack('<h',65)*320  # below 0.006 voice energy, above numerical zero
        speech=struct.pack('<h',1200)*320
        for _ in range(20):
            async for _ in service.run_stt(quiet): pass
        assert not socket.messages
        service._ensure_upstream.assert_not_awaited()
        async for _ in service.run_stt(speech): pass
        audio=base64.b64decode(socket.messages[0]['audio'])
        assert audio == quiet*12+speech  # last 240 ms plus current 20 ms, in order
        async for _ in service.run_stt(speech): pass
        assert base64.b64decode(socket.messages[1]['audio'])==speech
        assert not service._preroll
    asyncio.run(exercise())
