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


def test_graph_stream_exposes_background_before_nodes_arrive():
    raw = json.dumps({"op": "mount", "block_id": "map", "component": "graph",
        "props": {"title": "思维导图", "background": "warm", "graph_layout": "mindmap", "interaction": "explore"},
        "upsert": {"nodes": [{"id": "root", "label": "根节点"}], "edges": []}}, ensure_ascii=False)
    stream, seen = UIArgumentStream(), []
    for char in raw:
        command = stream.feed(char)
        if command:
            seen.append(compile_command(command))
    assert not stream.disabled
    assert any(s.props.background == "warm" and not s.nodes for s in seen)
    assert seen[-1].props.background == "warm"
    assert seen[-1].props.graph_layout == "mindmap"
    assert seen[-1].nodes[0].label == "根节点"


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


def test_progressive_keys_stream_label_caption_and_timeline_text_before_close():
    long_step_label = "第一步规划阶段说明" * 20
    long_rail_label = "配置环境与依赖安装" * 20
    long_caption = "这是正在流式输出的图注" * 20
    long_tl_label = "第一阶段启动里程碑" * 10
    long_tl_desc = "完成核心架构设计与验证" * 20
    # Document block label & caption stream while strings are still open
    doc_cmd = {
        "op": "mount",
        "block_id": "doc_prog",
        "component": "document",
        "props": {"title": "进度文档"},
        "upsert": {
            "blocks": [
                {"id": "steps", "type": "step_list", "label": long_step_label},
                {"id": "s1", "parent_id": "steps", "type": "rail_item", "label": long_rail_label, "caption": "补充说明文字"},
                {"id": "fig", "type": "figure", "caption": long_caption},
            ]
        },
    }
    raw = json.dumps(doc_cmd, ensure_ascii=False)
    stream = UIArgumentStream()
    seen = []
    for ch in raw:
        cmd = stream.feed(ch)
        if cmd:
            seen.append(compile_command(cmd))
    assert not stream.disabled
    assert any(
        any(b.id == "steps" and 0 < len(b.label) < len(long_step_label) for b in s.blocks)
        for s in seen
    )
    assert any(
        any(b.id == "s1" and 0 < len(b.label) < len(long_rail_label) for b in s.blocks)
        for s in seen
    )
    assert any(
        any(b.id == "fig" and 0 < len(b.caption) < len(long_caption) for b in s.blocks)
        for s in seen
    )

    # Timeline items stream label & description before item object closes
    tl_cmd = {
        "op": "mount",
        "block_id": "tl_prog",
        "component": "timeline",
        "props": {"title": "时间线"},
        "upsert": {
            "items": [
                {"id": "t1", "label": long_tl_label, "description": long_tl_desc},
            ]
        },
    }
    raw_tl = json.dumps(tl_cmd, ensure_ascii=False)
    tl_stream = UIArgumentStream()
    tl_seen = []
    for ch in raw_tl:
        cmd = tl_stream.feed(ch)
        if cmd:
            tl_seen.append(compile_command(cmd))
    assert not tl_stream.disabled
    assert any(
        s.items and 0 < len(s.items[0].label) < len(long_tl_label)
        for s in tl_seen
    )
    assert any(
        s.items and 0 < len(s.items[0].description) < len(long_tl_desc)
        for s in tl_seen
    )
