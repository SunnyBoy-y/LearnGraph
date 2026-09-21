"""Realtime dictation WebSocket regression tests.

两个主题：

1. **断开竞态**（原修复背景）：``/api/v1/sessions/dictation/realtime`` 在客户端刚
   断开时发送 ``ready`` 帧会抛 ``WebSocketDisconnect(1006)``（底层 websockets 库报
   ``InvalidState: connection is closing``）。该异常未捕获直接冒泡到 ASGI 层，
   uvicorn 会打印一长串无意义的 traceback（日志里的
   ``Exception in ASGI application``）。修复后 ``ready`` 帧发送被双重保护：
   - 状态检查不通过（客户端在 ASR 任务启动期间已断开）→ 提前返回，不发送；
   - 状态检查通过但发送瞬间客户端才断开（TOCTOU 竞态）→ 捕获异常、关闭上游连接
     并静默返回。

2. **两套上游方言按模型家族分流**（2026-09-20）：DashScope 的
   ``/api-ws/v1/inference``（run-task，paraformer/gummy 家族）与
   ``/api-ws/v1/realtime``（OpenAI realtime，qwen3-asr-flash-realtime 家族）是两套
   协议，把模型发到错的端点会立刻 ``task-failed / ModelNotFound``。这里断言端点、
   鉴权头、上行帧封装、收尾帧与就绪事件都跟着模型走。

本测试直接调用路由函数（``@router.websocket`` 装饰器返回原函数），用假 WebSocket /
上游替换全部外部依赖，因此不依赖真实 DashScope 网络。
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.routers import chat
from app.services.dictation import (
    build_openai_realtime_audio_frame,
    build_openai_realtime_session_update,
    dashscope_realtime_ws_url,
    parse_openai_realtime_upstream_event,
    uses_openai_realtime_transport,
)

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
INFERENCE_MODEL = "paraformer-realtime-v2"
OPENAI_MODEL = "qwen3-asr-flash-realtime"
INFERENCE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
OPENAI_WS_URL = (
    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime"
)

# 各方言的"任务已就绪"事件；假上游据此回答握手循环。
READY_EVENTS = {
    OPENAI_MODEL: {"type": "session.updated"},
    INFERENCE_MODEL: {"header": {"event": "task-started"}},
}


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
    """Fake DashScope socket: answers the handshake, then replays scripted events.

    ``recv``（握手循环）只回答"任务已就绪"事件——真实网关上转录事件不会早于它就
    到达；``__anext__``（泵送循环）按顺序吐出脚本事件，吐完即结束迭代。
    """

    def __init__(self, ready_event: dict, trailing: list[str] | None = None) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False
        self._ready = json.dumps(ready_event)
        self._inbound = list(trailing or [])

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return self._ready

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self) -> "_FakeUpstream":
        return self

    async def __anext__(self) -> str:
        if self._inbound:
            return self._inbound.pop(0)
        raise StopAsyncIteration


class _FakeDB:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _FakeAdapter:
    provider_id = "prov-1"
    base_url = DASHSCOPE_URL
    api_key = "sk-test"

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id


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


class _AudioThenStopWebSocket(_FakeWebSocket):
    """Sends one binary PCM frame, then a ``stop`` frame, then drops."""

    PCM = b"\x01\x02\x03\x04"

    def __init__(self):
        super().__init__(state="CONNECTED")
        self._recv_calls = 0

    async def receive(self) -> dict:
        self._recv_calls += 1
        if self._recv_calls == 1:
            return {"type": "websocket.receive", "bytes": self.PCM}
        if self._recv_calls == 2:
            return {"type": "websocket.receive", "text": json.dumps({"type": "stop"})}
        return {"type": "websocket.disconnect"}


def _patch(
    monkeypatch,
    model_id: str,
    trailing: list[str] | None = None,
    fail_with: dict | None = None,
) -> dict:
    """Wire the endpoint to fakes; returns the captured upstream state.

    ``fail_with`` replaces the handshake answer with a failure event, which is how
    a misrouted model (wrong endpoint for the model family) shows up.
    """

    holder: dict = {}

    async def _fake_connect(url, **kwargs):
        upstream = _FakeUpstream(fail_with or READY_EVENTS[model_id], trailing)
        holder["upstream"] = upstream
        holder["url"] = url
        holder["headers"] = kwargs.get("additional_headers") or {}
        return upstream

    monkeypatch.setattr(chat, "authenticate_realtime_dictation", lambda *a: "user-1")
    monkeypatch.setattr(
        chat, "transcription_provider_for_workspace", lambda *a, **k: _FakeAdapter(model_id)
    )
    monkeypatch.setattr(chat, "is_realtime_transcription_model", lambda model_id: True)
    monkeypatch.setattr(chat, "BillingService", _FakeBilling)
    monkeypatch.setattr("websockets.asyncio.client.connect", _fake_connect)
    return holder


def test_ready_send_race_disconnect_is_swallowed(monkeypatch):
    """Client drops right after the state check → send_json raises → clean return."""
    holder = _patch(monkeypatch, OPENAI_MODEL)
    ws = _FakeWebSocket(state="CONNECTED", send_raises=WebSocketDisconnect(code=1006))

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert ws.accepted
    assert ws.send_json_calls == [{"type": "ready", "sample_rate": 16_000}]
    # 上游连接必须被关闭,不能泄漏。
    assert holder["upstream"].closed is True


def test_disconnect_during_task_start_skips_ready_send(monkeypatch):
    """Client already gone when the ASR task starts → return before ready frame."""
    holder = _patch(monkeypatch, OPENAI_MODEL)
    ws = _FakeWebSocket(state="DISCONNECTED")

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert ws.accepted
    assert ws.send_json_calls == []
    assert holder["upstream"].closed is True


def test_happy_path_stop_still_sends_done(monkeypatch):
    """Normal flow (stop frame → done) must be unaffected by the guard."""
    holder = _patch(monkeypatch, OPENAI_MODEL)
    ws = _HappyWebSocket()

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert ws.send_json_calls == [
        {"type": "ready", "sample_rate": 16_000},
        {"type": "done"},
    ]
    assert ws.closed is True
    # stop 帧应触发收尾帧发送到上游。
    assert json.loads(holder["upstream"].sent[-1]) == {"type": "session.finish"}
    assert holder["upstream"].closed is True


# ---- 端点与协议按模型家族分流 -------------------------------------------------


def test_upstream_rejection_surfaces_as_degradable_error(monkeypatch):
    """上游拒掉任务时必须回 asr_task_failed（可降级），而不是静默挂住。

    这正是修复前的现场：把 qwen3-asr-flash-realtime 发到 inference 端点，上游立刻
    回 task-failed / ModelNotFound，前端据此连续失败两次后降级到分段模式。
    """
    holder = _patch(
        monkeypatch,
        OPENAI_MODEL,
        fail_with={"type": "error", "code": "ModelNotFound", "message": "Model not found"},
    )
    ws = _FakeWebSocket(state="CONNECTED")

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert ws.send_json_calls == [
        {
            "type": "error",
            "code": "asr_task_failed",
            "message": "ModelNotFound: Model not found",
        }
    ]
    assert ws.closed is True
    assert holder["upstream"].closed is True


def test_openai_realtime_model_uses_realtime_endpoint_and_dialect(monkeypatch):
    holder = _patch(monkeypatch, OPENAI_MODEL)
    ws = _AudioThenStopWebSocket()

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert holder["url"] == OPENAI_WS_URL
    assert holder["headers"]["Authorization"] == "Bearer sk-test"
    assert holder["headers"]["OpenAI-Beta"] == "realtime=v1"
    sent = holder["upstream"].sent
    # 1) 握手帧是 OpenAI realtime 的 session.update
    open_frame = json.loads(sent[0])
    assert open_frame["type"] == "session.update"
    assert open_frame["session"]["input_audio_transcription"]["model"] == OPENAI_MODEL
    # 2) 上行音频被 base64 封装成 append 帧（裸二进制会被该端点拒绝）
    append = json.loads(sent[1])
    assert append["type"] == "input_audio_buffer.append"
    assert base64.b64decode(append["audio"]) == _AudioThenStopWebSocket.PCM
    # 3) 收尾帧是 session.finish
    assert json.loads(sent[2]) == {"type": "session.finish"}
    assert ws.send_json_calls[-1] == {"type": "done"}


def test_paraformer_model_keeps_inference_endpoint_and_dialect(monkeypatch):
    holder = _patch(monkeypatch, INFERENCE_MODEL)
    ws = _AudioThenStopWebSocket()

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert holder["url"] == INFERENCE_WS_URL
    assert holder["headers"]["Authorization"] == "bearer sk-test"
    assert "OpenAI-Beta" not in holder["headers"]
    sent = holder["upstream"].sent
    open_frame = json.loads(sent[0])
    assert open_frame["header"]["action"] == "run-task"
    assert open_frame["payload"]["model"] == INFERENCE_MODEL
    # inference 端点的上行帧就是裸 PCM，不做封装。
    assert sent[1] == _AudioThenStopWebSocket.PCM
    assert json.loads(sent[2])["header"]["action"] == "finish-task"


def test_openai_dialect_relays_partial_and_final(monkeypatch):
    """云端 VAD 的 partial / final 必须分别变成 partial / final 文本帧。"""
    trailing = [
        json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.text",
                "text": "今天天气",
            }
        ),
        json.dumps(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "今天天气不错。",
                "usage": {"input_tokens": 12, "output_tokens": 7},
            }
        ),
        json.dumps({"type": "session.finished", "usage": {"input_tokens": 1}}),
    ]
    holder = _patch(monkeypatch, OPENAI_MODEL, trailing)
    ws = _HappyWebSocket()

    asyncio.run(chat.dictation_realtime(ws, db=_FakeDB(), settings=None))

    assert {"type": "partial", "text": "今天天气"} in ws.send_json_calls
    assert {"type": "final", "text": "今天天气不错。"} in ws.send_json_calls
    assert ws.send_json_calls[-1] == {"type": "done"}


# ---- 纯函数：端点派生 / 帧构造 / 事件解析 -------------------------------------


def test_uses_openai_realtime_transport_only_for_qwen_realtime():
    assert uses_openai_realtime_transport(OPENAI_MODEL) is True
    assert uses_openai_realtime_transport("qwen3-asr-flash-realtime-2026-01-01") is True
    # 非 realtime 模型与 paraformer/gummy 家族保持 inference 端点。
    assert uses_openai_realtime_transport("qwen3-asr-flash") is False
    assert uses_openai_realtime_transport(INFERENCE_MODEL) is False
    assert uses_openai_realtime_transport("gummy-realtime-v1") is False
    assert uses_openai_realtime_transport(None) is False


def test_dashscope_realtime_ws_url_picks_path_by_model():
    assert dashscope_realtime_ws_url(DASHSCOPE_URL, OPENAI_MODEL) == OPENAI_WS_URL
    assert (
        dashscope_realtime_ws_url(DASHSCOPE_URL, INFERENCE_MODEL) == INFERENCE_WS_URL
    )
    # 私有 MaaS 租户：同 host 派生，路径规则一致。
    maas = "https://dashscope-abc.maas.aliyuncs.com/compatible-mode/v1"
    assert (
        dashscope_realtime_ws_url(maas, OPENAI_MODEL)
        == "wss://dashscope-abc.maas.aliyuncs.com/api-ws/v1/realtime"
        "?model=qwen3-asr-flash-realtime"
    )
    # 非 DashScope 网关不可派生（保持原有拒绝语义）。
    assert dashscope_realtime_ws_url("https://api.openai.com/v1", OPENAI_MODEL) is None
    assert dashscope_realtime_ws_url(None, OPENAI_MODEL) is None


def test_openai_realtime_session_update_normalizes_language():
    frame = json.loads(
        build_openai_realtime_session_update(OPENAI_MODEL, 16_000, language="zh-CN")
    )
    assert frame["session"]["sample_rate"] == 16_000
    assert frame["session"]["input_audio_transcription"] == {
        "model": OPENAI_MODEL,
        "language": "zh",
    }
    # 听写通道不做回合管理，断句交给云端 VAD。
    assert frame["session"]["turn_detection"]["type"] == "server_vad"
    # auto / 缺省不下发 language（由模型自动检测）。
    auto = json.loads(
        build_openai_realtime_session_update(OPENAI_MODEL, 16_000, language="auto")
    )
    assert "language" not in auto["session"]["input_audio_transcription"]


def test_openai_realtime_audio_frame_is_base64_append():
    frame = json.loads(build_openai_realtime_audio_frame(b"\x00\x01\xff"))
    assert frame["type"] == "input_audio_buffer.append"
    assert base64.b64decode(frame["audio"]) == b"\x00\x01\xff"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # partial：新版放 text，旧版放 stash。
        (
            {"type": "conversation.item.input_audio_transcription.text", "text": "你好"},
            ("conversation.item.input_audio_transcription.text", "你好", False),
        ),
        (
            {
                "type": "conversation.item.input_audio_transcription.text",
                "text": "",
                "stash": "你好",
            },
            ("conversation.item.input_audio_transcription.text", "你好", False),
        ),
        # final：transcript 优先。
        (
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "你好。",
            },
            ("conversation.item.input_audio_transcription.completed", "你好。", True),
        ),
    ],
)
def test_parse_openai_realtime_upstream_event_text(payload, expected):
    event = parse_openai_realtime_upstream_event(json.dumps(payload))
    assert (event.event, event.text, event.final) == expected


def test_parse_openai_realtime_upstream_event_edges():
    # 空 partial / 空 final 不上屏（text=None 表示"这条事件没有文本"）。
    empty = parse_openai_realtime_upstream_event(
        json.dumps({"type": "conversation.item.input_audio_transcription.text"})
    )
    assert empty.text is None
    # 收尾事件带用量。
    finished = parse_openai_realtime_upstream_event(
        json.dumps({"type": "session.finished", "usage": {"input_tokens": 3}})
    )
    assert (finished.event, finished.usage) == ("session.finished", {"input_tokens": 3})
    # 失败事件归一成 task-failed，让两套方言共用同一段错误处理。
    failed = parse_openai_realtime_upstream_event(
        json.dumps({"type": "error", "code": "InvalidParameter", "message": "bad model"})
    )
    assert failed.event == "task-failed"
    assert failed.error == "InvalidParameter: bad model"
    # 无关事件原样透传；坏 JSON 不抛异常。
    assert (
        parse_openai_realtime_upstream_event(
            json.dumps({"type": "input_audio_buffer.speech_started"})
        ).event
        == "input_audio_buffer.speech_started"
    )
    assert parse_openai_realtime_upstream_event("{not json").event == "invalid"
