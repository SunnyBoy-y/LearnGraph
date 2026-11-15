"""Fluent icon identifiers remain inert across every declarative UI entrypoint."""
import json
from typing import get_args

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.domain.schemas.stream_ui import DocumentNode, Item, LegacyRailIcon, Props, Snapshot
from app.services.genui_catalog import presentation_catalog_prompt
from app.services.rich_response import AGENT_CAPABILITY_CATALOG, CAPABILITY_CATALOG, InlineUIParser, UICommandEvent
from app.services.stream_ui import compile_command, tool_definitions
from app.services.streamed_ui import UIArgumentStream


KNOWN_SLUGS = (
    "rocket", "light-bulb", "brain", "books", "direct-hit", "check-mark-button",
    "warning", "sparkles", "magnifying-glass-tilted-left", "chart-increasing",
)


def icon_models(icon):
    return (
        Props(title="Heading", icon=icon),
        Item(id="item", label="Metric", icon=icon),
        DocumentNode(id="heading", type="heading", text="Heading", icon=icon),
        DocumentNode(id="standalone", type="icon", icon=icon),
    )


@pytest.mark.parametrize("icon", [*("fluent:" + slug for slug in KNOWN_SLUGS),
                                  "fluent:3d-rocket-2", "fluent:" + "a" * 93,
                                  *get_args(LegacyRailIcon)])
def test_fluent_and_legacy_icons_validate_and_round_trip(icon):
    for model in icon_models(icon):
        assert type(model).model_validate_json(model.model_dump_json()).icon == icon


@pytest.mark.parametrize("icon", [
    "", "fluent:", "fluent:-rocket", "fluent:rocket-", "fluent:rocket--ship",
    "fluent:Rocket", "Fluent:rocket", "fluent:rocket_ship", "fluent:rocket.svg",
    "fluent:../rocket", "fluent:..", "fluent:/rocket", "fluent:rocket/brain",
    "fluent:rocket\\brain", "fluent:%2e%2e", "fluent:rocket?x=1", "fluent:rocket#id",
    "https://example.com/rocket.png", "//example.com/rocket", "data:image/svg+xml,hi",
    "<svg/>", "🚀", "fluent:火箭", "fluent:rоcket", "fluent:rocket\n", "fluent:rocket\x00",
    " fluent:rocket", "fluent:rocket ", "fluent:rocket\t", "fluent:" + "a" * 94,
    "unknown-lucide-name", True, 123, {}, [],
])
def test_invalid_icons_are_rejected_in_all_fields(icon):
    for model, required in ((Props, {"title": "Title"}),
                            (Item, {"id": "item", "label": "Item"}),
                            (DocumentNode, {"id": "heading", "type": "heading"}),
                            (DocumentNode, {"id": "icon", "type": "icon"})):
        with pytest.raises(ValidationError):
            model.model_validate({**required, "icon": icon})


def test_historical_missing_and_null_icons_remain_none():
    for raw in ({}, {"icon": None}):
        assert Props(title="Title", **raw).icon is None
        assert Item(id="i", label="Item", **raw).icon is None
        assert DocumentNode(id="p", type="paragraph", **raw).icon is None
    for raw in ({}, {"icon": None}):
        with pytest.raises(ValidationError, match="require an icon"):
            DocumentNode(id="i", type="icon", **raw)


@pytest.mark.parametrize("kind", get_args(DocumentNode.model_fields["type"].annotation))
def test_every_document_node_can_have_an_icon_but_status_stays_rail_only(kind):
    node = DocumentNode(id="n", type=kind, label="Title", icon="fluent:brain")
    assert node.icon == "fluent:brain"
    if kind != "rail_item":
        with pytest.raises(ValidationError, match="item_status belongs"):
            DocumentNode(id="n", type=kind, label="Title", icon="fluent:brain", item_status="active")
    else:
        assert DocumentNode(id="n", type=kind, label="Title", item_status="active").item_status == "active"


def test_standalone_icon_cannot_contain_children():
    with pytest.raises(AppError, match="Only layout nodes"):
        compile_command({"op": "mount", "block_id": "doc", "component": "document", "props": {"title": "Title"},
                         "upsert": {"blocks": [{"id": "i", "type": "icon", "icon": "fluent:rocket"},
                                               {"id": "p", "type": "paragraph", "parent_id": "i", "text": "No"}]}})


@pytest.mark.parametrize("component", ["metric", "timeline"])
def test_items_and_props_survive_mount_patch_complete_revise_and_replay(component):
    mounted = compile_command({"op": "mount", "block_id": "stats", "component": component,
                               "props": {"title": "Title", "icon": "fluent:chart-increasing"},
                               "upsert": {"items": [{"id": "one", "label": "One", "value": 1, "icon": "fluent:rocket"}]}})
    patched = compile_command({"op": "patch", "block_id": "stats", "base_revision": 0,
                               "upsert": {"items": [{"id": "two", "label": "Two", "value": 2, "icon": "brain-circuit"}]}},
                              mounted.model_dump(mode="json"))
    done = compile_command({"op": "complete", "block_id": "stats", "base_revision": 1}, patched.model_dump(mode="json"))
    replay = Snapshot.model_validate_json(done.model_dump_json())
    assert replay.props.icon == "fluent:chart-increasing"
    assert [item.icon for item in replay.items] == ["fluent:rocket", "brain-circuit"]
    revised = compile_command({"op": "revise", "block_id": "stats", "base_revision": 2,
                               "props": {"icon": "fluent:brain"}}, replay.model_dump(mode="json"))
    assert revised.props.icon == "fluent:brain" and revised.items == replay.items
    assert replay.props.icon == "fluent:chart-increasing"
    cleared = compile_command({"op": "patch", "block_id": "stats", "base_revision": 3,
                               "props": {"icon": None}}, revised.model_dump(mode="json"))
    assert cleared.props.icon is None


def test_streamed_icon_is_not_exposed_until_identifier_is_complete():
    stream = UIArgumentStream()
    first = stream.feed('{"op":"mount","block_id":"doc","component":"document","props":{"title":"Title"},'
                        '"upsert":{"blocks":[{"id":"i","type":"icon","icon":"fluent:rock')
    assert first is not None and not compile_command(first).blocks
    last = stream.feed('et"}]}}')
    assert compile_command(last).blocks[0].icon == "fluent:rocket"
    assert not stream.disabled


def test_direct_inline_icon_only_document_is_useful_and_keeps_unicode_prose():
    parser = InlineUIParser()
    raw = {"props": {"title": "Launch", "icon": "fluent:brain"},
           "upsert": {"blocks": [{"id": "launch", "type": "icon", "icon": "fluent:rocket"}]}}
    events = list(parser.feed("🚀\n:::ui launch\n" + json.dumps(raw) + "\n:::\n💡")) + list(parser.finish())
    current = None
    for event in events:
        if isinstance(event, UICommandEvent):
            current = compile_command(event.command, current).model_dump(mode="json")
    assert not parser.failed_declaration
    assert current["state"] == "completed" and current["props"]["icon"] == "fluent:brain"
    assert current["blocks"][0]["icon"] == "fluent:rocket"
    text = "".join(event.content or "" for event in events if event.type == "text_delta")
    assert "🚀" in text and "💡" in text


def test_all_ai_modes_explain_optional_safe_icons_and_unicode_markdown():
    definition = tool_definitions()[0]["function"]
    for prompt in (definition["description"], presentation_catalog_prompt(), CAPABILITY_CATALOG, AGENT_CAPABILITY_CATALOG):
        for term in ("fluent:", *KNOWN_SLUGS, "heading", "section", "rail_item", "props.icon", "items[].icon", "Unicode", "100"):
            assert term in prompt
    schema = definition["parameters"]
    assert "icon" in schema["$defs"]["DocumentNode"]["properties"]["type"]["enum"]
    assert "icon" in schema["$defs"]["Item"]["properties"]
    assert "icon" in schema["properties"]["props"]["properties"]
    assert "media" not in schema["$defs"]["DocumentNode"]["properties"]
