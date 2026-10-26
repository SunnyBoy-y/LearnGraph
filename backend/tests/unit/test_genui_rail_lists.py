import json

import pytest

from app.core.errors import AppError
from app.services.stream_ui import compile_command
from app.services.streamed_ui import UIArgumentStream
from app.services.rich_response import InlineUIParser, UICommandEvent, CAPABILITY_CATALOG, AGENT_CAPABILITY_CATALOG


def command(kind="feature_list"):
    return {"op": "mount", "block_id": "editorial", "component": "document", "props": {"title": "回答"},
            "upsert": {"blocks": [{"id": "list", "type": kind},
                                   {"id": "first", "parent_id": "list", "type": "rail_item", "label": "极速模式", "icon": "zap", "text": "第一段解释。", "caption": "补充说明"}]}}


@pytest.mark.parametrize("kind", ["feature_list", "step_list", "status_list"])
def test_lists_patch_individual_items_without_replacing_completed_siblings(kind):
    raw = command(kind)
    if kind == "status_list":
        raw["upsert"]["blocks"][1]["item_status"] = "active"
    mounted = compile_command(raw).model_dump(mode="json")
    patched = compile_command({"op": "patch", "block_id": "editorial", "base_revision": 0,
                               "upsert": {"blocks": [{"id": "second", "parent_id": "list", "type": "rail_item", "label": "Agent模式", "icon": "brain-circuit", "text": "第二段解释。", "item_status": "pending"}]}}, mounted)
    assert patched.blocks[1].model_dump(mode="json") == mounted["blocks"][1]
    assert [node.id for node in patched.blocks] == ["list", "first", "second"]
    assert compile_command({"op": "complete", "block_id": "editorial", "base_revision": 1}, patched.model_dump(mode="json")).state == "completed"


@pytest.mark.parametrize("kind", ["feature_list", "step_list", "status_list"])
def test_fast_mode_inline_lists_use_the_same_compiler(kind):
    raw = command(kind)
    parser = InlineUIParser()
    declaration = "前文\n:::ui list\n" + json.dumps({key: raw[key] for key in ("props", "upsert")}) + "\n:::\n后文"
    events = [event for char in declaration for event in parser.feed(char)] + list(parser.finish())
    state = None
    for event in events:
        if isinstance(event, UICommandEvent):
            state = compile_command(event.command, state).model_dump(mode="json")
    assert state["state"] == "completed" and state["blocks"][0]["type"] == kind
    assert "前文" in "".join(event.content or "" for event in events if event.type == "text_delta")


def test_item_text_is_readable_before_tool_argument_finishes():
    raw = command()
    raw["upsert"]["blocks"][1]["text"] = "逐段补充的解释。" * 60
    encoded = json.dumps(raw, ensure_ascii=False)
    stream = UIArgumentStream()
    seen = []
    for position in range(0, len(encoded), 7):
        preview = stream.feed(encoded[position:position + 7])
        if preview:
            seen.append(compile_command(preview))
    assert not stream.disabled
    assert any(len(snapshot.blocks) == 2 and 0 < len(snapshot.blocks[1].text) < 60 * len("逐段补充的解释。") for snapshot in seen)
    assert seen[-1].blocks[1].text == raw["upsert"]["blocks"][1]["text"]
    assert all(snapshot.blocks[1].id == "first" for snapshot in seen if len(snapshot.blocks) > 1)


@pytest.mark.parametrize("change", ["icon", "state", "parent", "child", "count", "style", "title"])
def test_invalid_rail_data_is_rejected(change):
    raw = command()
    item = raw["upsert"]["blocks"][1]
    if change == "icon": item["icon"] = "<svg onload=alert(1)>"
    if change == "state": item["item_status"] = "running-code"
    if change == "parent": item.pop("parent_id")
    if change == "child": item["type"] = "paragraph"
    if change == "style": item["style"] = {"color": "red"}
    if change == "title": item["label"] = " "
    if change == "count": raw["upsert"]["blocks"] += [{**item, "id": f"item{i}"} for i in range(12)]
    with pytest.raises(AppError):
        compile_command(raw)


def test_both_modes_receive_semantic_selection_rules_and_exact_icon_allowlist():
    for catalog in (CAPABILITY_CATALOG, AGENT_CAPABILITY_CATALOG):
        assert all(name in catalog for name in ("IconFeatureList", "StepList", "StatusList", "rail_item", "brain-circuit"))
        assert "未验证的执行结果" in catalog
