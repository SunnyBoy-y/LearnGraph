"""Fast Preface (agent-mode progressive response) unit tests.

Exercises ``ChatService._stream_fast_preface`` control flow with a fake fast
provider and faked persistence, plus the degradation paths (no provider /
upstream failure / empty output) and cancellation propagation. Also covers the
thin ``_build_fast_preface_provider`` guard for a missing model target.
"""
from __future__ import annotations

import types
import uuid

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.chat import (
    FAST_PREFACE_PART_KIND,
    ChatService,
    _GenerationCancellationRequested,
)


class _FakeFastProvider:
    """Streams a fixed short reply (or fails / yields nothing)."""

    def __init__(self, chunks=("先给你一个方向：", "关键是前置条件。"), fail=False):
        self.chunks = chunks
        self.fail = fail

    def stream_chat(self, messages, tools=None):
        if self.fail:
            raise RuntimeError("upstream boom")
        for chunk in self.chunks:
            yield ProviderStreamEvent(type="text_delta", content=chunk)
        yield ProviderStreamEvent(type="completed", finish_reason="stop")


def _make_service(fast_provider):
    svc = object.__new__(ChatService)
    svc.workspace_id = "ws-test"
    svc.db = None
    svc.settings = None
    svc._build_fast_preface_provider = lambda: fast_provider
    svc._preflight_model_call = lambda *args, **kwargs: None
    svc._structured_history_messages = lambda sid, before_message_id=None: []

    events = []

    def _append_event(**kwargs):
        event = {"type": kwargs["event_type"], "payload": kwargs["payload"]}
        events.append(event)
        return event

    svc._append_event = _append_event
    svc._encode_event = lambda env: f"EVENT:{env['type']}"
    svc._events = events

    def _add(record):
        if record.id is None:
            record.id = str(uuid.uuid4())
        return record

    svc.message_parts = types.SimpleNamespace(add=_add)
    return svc


def _drive(svc, *, cancelled=None, **kwargs):
    if cancelled is None:
        cancelled = lambda: False  # noqa: E731
    gen = svc._stream_fast_preface(
        session_id="s",
        message_id="m",
        message_version_id="v",
        user_content="帮我规划学习路线",
        history_before_message_id="u",
        ordinal=1,
        sequence=1,
        cancelled=cancelled,
        **kwargs,
    )
    frames = []
    result = None
    while True:
        try:
            frames.append(next(gen))
        except StopIteration as exc:
            result = exc.value
            break
    return frames, result


def test_stream_fast_preface_emits_started_delta_completed():
    svc = _make_service(_FakeFastProvider())
    frames, (record, text, sequence) = _drive(svc)

    assert text == "先给你一个方向：关键是前置条件。"
    assert sequence == 5  # started + 2 deltas + completed (started at 1)
    assert record is not None
    assert record.part_type == "text"
    assert record.status == "completed"
    assert record.data == {"kind": FAST_PREFACE_PART_KIND}

    assert [e["type"] for e in svc._events] == [
        "part.started",
        "part.delta",
        "part.delta",
        "part.completed",
    ]

    started_part = svc._events[0]["payload"]["part"]
    assert started_part["type"] == "text"
    assert started_part["status"] == "streaming"
    assert started_part["data"] == {"kind": FAST_PREFACE_PART_KIND}
    assert started_part["sequence"] == 1  # reserved ordinal is honored

    assert frames == [
        "EVENT:part.started",
        "EVENT:part.delta",
        "EVENT:part.delta",
        "EVENT:part.completed",
    ]


def test_stream_fast_preface_no_provider_degrades():
    svc = _make_service(None)
    frames, (record, text, sequence) = _drive(svc)
    assert record is None
    assert text == ""
    assert sequence == 1
    assert frames == []
    assert svc._events == []


def test_stream_fast_preface_upstream_failure_degrades():
    svc = _make_service(_FakeFastProvider(fail=True))
    frames, (record, text, sequence) = _drive(svc)
    assert record is None
    assert text == ""
    assert sequence == 1
    assert frames == []


def test_stream_fast_preface_empty_output_degrades():
    svc = _make_service(_FakeFastProvider(chunks=()))
    frames, (record, text, sequence) = _drive(svc)
    assert record is None
    assert text == ""
    assert frames == []


def test_stream_fast_preface_cancel_propagates():
    svc = _make_service(_FakeFastProvider())
    with pytest.raises(_GenerationCancellationRequested):
        _drive(svc, cancelled=lambda: True)


def test_build_fast_preface_provider_requires_model_target():
    svc = object.__new__(ChatService)
    svc.model_provider = types.SimpleNamespace(model_id=None, provider_id="p")
    assert ChatService._build_fast_preface_provider(svc) is None

    svc.model_provider = types.SimpleNamespace(model_id="m", provider_id=None)
    assert ChatService._build_fast_preface_provider(svc) is None
