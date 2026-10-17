import json

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.rich_response import UICommandEvent, rich_events
from app.services.tool_protocol_guard import ToolProtocolGuard


ENVELOPES = [
    '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="ui_stream">secret</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>',
    '<｜DSML｜function_calls><｜DSML｜invoke name="sandbox_bash">secret</｜DSML｜invoke></｜DSML｜function_calls>',
    '<|DSML|function_calls>secret</|DSML|function_calls>',
    '<tool_call>{"name":"sandbox_bash","arguments":{"command":"secret"}}</tool_call>',
    '<function_calls>secret</function_calls>',
]


def parse(chunks):
    guard = ToolProtocolGuard()
    return guard, "".join(guard.feed(chunk) for chunk in chunks) + guard.finish()


@pytest.mark.parametrize("envelope", ENVELOPES)
def test_rejected_envelope_never_crosses_any_stream_chunk_boundary(envelope):
    text = "前文" + envelope + "后文"
    for chunks in [list(text), *[[text[:index], text[index:]] for index in range(len(text) + 1)]]:
        guard, prose = parse(chunks)
        assert guard.suppressed and prose == "前文后文"


@pytest.mark.parametrize("literal", [
    "普通文字与 HTML <span>示例</span>，a < b，结尾 <",
    "```xml\n" + ENVELOPES[0] + "\n```\n",
    "~~~xml\n" + ENVELOPES[1] + "\n~~~\n",
    "行内 `" + ENVELOPES[0] + "`，以及 ``" + ENVELOPES[1] + "``。",
    ':::ui example\n' + json.dumps({"upsert": {"blocks": [{"id": "p", "type": "paragraph", "text": ENVELOPES[0]}]}}) + '\n:::\n',
])
def test_literal_examples_and_native_declaration_data_are_preserved(literal):
    guard, prose = parse(list(literal))
    assert prose == literal and not guard.suppressed


def test_closing_inline_code_does_not_allow_a_following_live_protocol():
    guard, prose = parse(list("`example`" + ENVELOPES[0] + "文字"))
    assert guard.suppressed and prose == "`example`文字"


@pytest.mark.parametrize("tail", ["<｜｜DSML｜｜ calls>never closed", "<｜DSML｜inv", "<｜｜DS", "<tool_call"])
def test_truncated_protocol_is_not_flushed_into_the_answer(tail):
    events = list(rich_events([ProviderStreamEvent("text_delta", content=tail)], allow_tool_calls=False))
    assert len(events) == 1 and "没有执行" in events[0].content
    assert "DSML" not in events[0].content and "<" not in events[0].content


def test_direct_stream_rejects_structured_calls_without_executing_or_previewing():
    events = list(rich_events([
        ProviderStreamEvent("tool_call_delta", tool_calls=[{"function": {"name": "ui_stream", "arguments": "{}"}}]),
        ProviderStreamEvent("tool_calls", tool_calls=[{"function": {"name": "sandbox_bash", "arguments": "{}"}}]),
        ProviderStreamEvent("completed", finish_reason="tool_calls", response_items=[{"type": "function_call"}]),
    ], tool_names={"ui_stream"}, allow_tool_calls=False))
    assert [event.type for event in events] == ["text_delta", "completed"]
    assert events[-1].finish_reason == "stop" and not events[-1].response_items


def test_rejected_protocol_cannot_remove_a_valid_native_document():
    document = {"props": {"title": "西瓜"}, "upsert": {"blocks": [
        {"id": "p", "type": "paragraph", "text": "西瓜外皮通常为绿色。"},
    ]}}
    text = ENVELOPES[0] + "\n:::ui watermelon\n" + json.dumps(document) + "\n:::\n"
    events = list(rich_events([ProviderStreamEvent("text_delta", content=char) for char in text], allow_tool_calls=False))
    assert [event.command["op"] for event in events if isinstance(event, UICommandEvent)] == ["mount", "patch", "complete"]
    assert not any("DSML" in (getattr(event, "content", "") or "") for event in events)


def test_agent_tool_frames_are_unchanged():
    event = ProviderStreamEvent("tool_calls", tool_calls=[{"id": "real", "function": {"name": "sandbox_bash", "arguments": "{}"}}])
    assert list(rich_events([event])) == [event]


def test_large_unclosed_call_has_bounded_memory_and_does_not_delay_normal_prose():
    guard = ToolProtocolGuard()
    assert guard.feed("立即显示") == "立即显示"
    assert guard.feed("<｜DSML｜function_calls>" + "x" * 1000000) == ""
    assert len(guard._tail) < 100 and not guard._candidate
