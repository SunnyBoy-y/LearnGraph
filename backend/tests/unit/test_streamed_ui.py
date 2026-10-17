import json

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.rich_response import rich_events
from app.services.stream_ui import compile_command
from app.services.streamed_ui import UIArgumentStream


COMMAND = {"op": "mount", "block_id": "doc", "component": "document", "props": {"title": "内容"},
           "upsert": {"blocks": [{"id": "root", "type": "section"},
                                  {"id": "p", "parent_id": "root", "type": "paragraph", "text": '引号"换行\n表情🐉' * 50},
                                  {"id": "later", "parent_id": "root", "type": "paragraph", "text": "第二段"}]}}


@pytest.mark.parametrize("chunk", [1, 3, 17, 128, 4000])
def test_preview_unicode_strings_and_records_before_call_finishes(chunk):
    raw = json.dumps(COMMAND, ensure_ascii=True)
    stream = UIArgumentStream()
    seen = []
    for position in range(0, len(raw), chunk):
        command = stream.feed(raw[position:position + chunk])
        if command:
            snapshot = compile_command(command)
            snapshot.model_dump_json().encode("utf-8")
            seen.append(snapshot)
    assert not stream.disabled
    assert seen[-1].blocks[-1].text == "第二段"
    assert seen[-1].blocks[1].text == COMMAND["upsert"]["blocks"][1]["text"]
    if chunk < 4000:
        assert any(len(s.blocks) < 3 for s in seen)
        assert any(0 < len(s.blocks[1].text) < len(COMMAND["upsert"]["blocks"][1]["text"])
                   for s in seen if len(s.blocks) > 1)
    assert len(seen) < 80


@pytest.mark.parametrize("raw", ['{"op":"mount","op":"patch"}', '{"op":"mount",}',
                                 '{"a":[1,]}', '{"a":"\\uD800"}', '{"a":true false}',
                                 '{"a":' + '[' * 26])
def test_malformed_inputs_disable_only_optimization(raw):
    stream = UIArgumentStream()
    stream.feed(raw)
    assert stream.disabled


def test_size_limit_and_undisclosed_tools():
    stream = UIArgumentStream()
    assert stream.feed(" " * 192001) is None and stream.disabled
    event = ProviderStreamEvent("tool_call_delta", tool_calls=[{"id": "c", "index": 0,
        "function": {"name": "ui_stream", "arguments": json.dumps(COMMAND)}}])
    assert list(rich_events([event])) == [event]


@pytest.mark.parametrize("field", ["props", "upsert"])
def test_wrong_container_types_fall_back_to_normal_dispatch(field):
    stream = UIArgumentStream()
    stream.feed(json.dumps({**COMMAND, field: "wrong type"}))
    assert stream.disabled


def test_non_string_and_invalid_unicode_deltas_do_not_break_the_tool_loop():
    for delta in ({"bad": "fragment"}, "\ud800"):
        stream = UIArgumentStream()
        assert stream.feed(delta) is None and stream.disabled


def test_transport_failure_rolls_back_preview_and_raises():
    def source():
        yield ProviderStreamEvent("tool_call_delta", tool_calls=[{"id": "c", "index": 0,
            "function": {"name": "ui_stream", "arguments": json.dumps(COMMAND)}}])
        raise RuntimeError("transport failed")
    events = rich_events(source(), tool_names={"ui_stream"})
    assert next(events).command["command"]["block_id"] == "doc"
    assert next(events).type == "tool_call_delta"
    assert next(events).command["rollback"] is True
    with pytest.raises(RuntimeError, match="transport failed"):
        next(events)
