"""Realtime dictation WebSocket disconnect-race regression tests.

修复背景：``/api/v1/sessions/dictation/realtime`` 在客户端刚断开时发送
``ready`` 帧会抛 ``WebSocketDisconnect(1006)``（底层 websockets 库报
``InvalidState: connection is closing``）。该异常未捕获直接冒泡到 ASGI
层，uvicorn 会打印一长串无意义的 traceback（日志里的
``Exception in ASGI application``）。

修复后 ``ready`` 帧发送被双重保护：
- 状态检查不通过（客户端在 ASR 任务启动期间已断开）→ 提前返回，不发送；
- 状态检查通过但发送瞬间客户端才断开（TOCTOU 竞态）→ 捕获异常、
  关闭上游连接并静默返回。

本测试直接调用路由函数（``@router.websocket`` 装饰器返回原函数），
用假 WebSocket / 上游替换全部外部依赖，因此不依赖真实 DashScope 网络。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.routers import chat

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
REALTIME_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


class _ClientState:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeWebSocket:
    """Minimal Starlette-like WebSocket for the realtime dictation endpoint."""

    def __init__(self, state: str = "CONNECTED", send_raises: Exception | None = None):
        self.client_state = _ClientState(state)
        self._send_raises = send_raises
        self.send_json_calls: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        return json.dumps(
            {
                "type": "start",
                "token": "session-token",
                "workspace_id": "ws-1",
                "sample_rate": 16_000,
            }
        )

    async def send_json(self, data: dict) -> None:
        self.send_json_calls.append(data)
        if self._send_raises is not None:
            raise self._send_raises

    async def receive(self) -> dict:
        return {"type": "websocket.disconnect"}

    async def close(self) -> None:
        self.closed = True


class _FakeUpstream:
    """Fake DashScope inference socket: task-started on first recv, then done."""

    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return json.dumps({"header": {"event": "task-started"}})

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> "_FakeUpstream":
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


class _FakeDB:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _FakeAdapter:
    provider_id = "prov-1"
    model_id = "qwen3-asr-flash-realtime"
    base_url = DASHSCOPE_URL
    api_key = "sk-test"


class _FakeBilling:
    def __init__(self, *args, **kwargs):
        self.recorded: list[tuple] = []

    def preflight_model_call(self, **kwargs):
        return {"quote_id": "quote-1"}

    def record_usage(self, *args, **kwargs):
        self.recorded.append((args, kwargs))


class _HappyWebSocket(_FakeWebSocket):
    """Stays connected; sends a ``stop`` frame first, then drops."""

    def __init__(self):
        super().__init__(state="CONNECTED")
        self._recv_calls = 0

    async def receive(self) -> dict:
        self._recv_calls += 1
        if self._recv_calls == 1:
            return {"type": "websocket.receive", "text": json.dumps({"type": "stop"})}
        return {"type": "websocket.disconnect"}


@pytest.fixture
def _patched(monkeypatch):
    holder: dict = {}

    async def _fake_connect(*args, **kwargs):
        upstream = _FakeUpstream()
        holder["upstream"] = upstream
        return upstream

    monkeypatch.setattr(chat, "authenticate_realtime_dictation", lambda *a: "user-1")
    monkeypatch.setattr(chat, "transcription_provider_for_workspace", lambda *a, **k: _FakeAdapter())
    monkeypatch.setattr(chat, "is_realtime_transcription_model", lambda model_id: True)
    monkeypatch.setattr(chat, "dashscope_realtime_ws_url", lambda base_url: REALTIME_WS_URL)
    monkeypatch.setattr(chat, "BillingService", _FakeBilling)
    monkeypatch.setattr("websockets.asyncio.client.connect", _fake_connect)
    return holder


def test_ready_send_race_disconnect_is_swallowed(_patched):
    """Client drops right after the state check → send_json raises → clean return."""
    ws = _FakeWebSocket(state="CONNECTED", send_raises=WebSocketDisconnect(code=1006))

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert ws.accepted
    assert ws.send_json_calls == [{"type": "ready", "sample_rate": 16_000}]
    # 上游连接必须被关闭,不能泄漏。
    assert _patched["upstream"].closed is True


def test_disconnect_during_task_start_skips_ready_send(_patched):
    """Client already gone when the ASR task starts → return before ready frame."""
    ws = _FakeWebSocket(state="DISCONNECTED")

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert ws.accepted
    assert ws.send_json_calls == []
    assert _patched["upstream"].closed is True


def test_happy_path_stop_still_sends_done(_patched):
    """Normal flow (stop frame → done) must be unaffected by the guard."""
    ws = _HappyWebSocket()

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert ws.send_json_calls == [
        {"type": "ready", "sample_rate": 16_000},
        {"type": "done"},
    ]
    assert ws.closed is True
    # stop 帧应触发 finish-task 发送到上游。
    assert any("finish-task" in str(item) for item in _patched["upstream"].sent)
    assert _patched["upstream"].closed is True
