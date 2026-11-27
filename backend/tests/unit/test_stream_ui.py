import copy

import pytest

from app.core.errors import AppError
from app.services.stream_ui import compile_command

FALLBACK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg>'



def mount(component="graph", **kwargs):
    return {"op": "mount", "block_id": "demo", "component": component,
            "props": {"title": "关系图"}, **kwargs}


def test_progressive_graph_preserves_ids_and_removes_incident_edges():
    skeleton = compile_command(mount()).model_dump(mode="json")
    assert skeleton["nodes"] == []
    one = compile_command({"op": "patch", "block_id": "demo", "base_revision": 0,
                           "upsert": {"nodes": [{"id": "a", "label": "小明"}, {"id": "b", "label": "项目"}],
                                      "edges": [{"id": "ab", "source": "a", "target": "b"}]}}, skeleton)
    two = compile_command({"op": "patch", "block_id": "demo", "base_revision": 1,
                           "upsert": {"nodes": [{"id": "a", "label": "小明（负责人）"}]}}, one.model_dump(mode="json"))
    assert len(two.nodes) == 2 and two.nodes[0].label == "小明（负责人）"
    three = compile_command({"op": "patch", "block_id": "demo", "base_revision": 2,
                             "delete": {"nodes": ["a"]}}, two.model_dump(mode="json"))
    assert three.edges == [] and [node.id for node in three.nodes] == ["b"]
    assert skeleton["nodes"] == []  # compilation never mutates an earlier event


@pytest.mark.parametrize("command", [
    mount(component="html"), mount(props={"title": "X", "onClick": "alert(1)"}),
    mount(upsert={"nodes": [{"id": "__proto__", "label": "X"}]}),
    mount(upsert={"nodes": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]}),
    mount(upsert={"edges": [{"id": "ab", "source": "a", "target": "b"}]}),
    mount(upsert={"items": [{"id": "x", "label": "wrong collection"}]}),
    mount(component="chart", props={"title": "X", "columns": [{"key": "v", "label": "值"}]},
          upsert={"rows": [{"id": "r", "values": {"v": float("nan")}}]}),
    mount(component="chart", props={"title": "X", "columns": [{"key": "v", "label": "值"}]},
          upsert={"rows": [{"id": "r", "values": {"v": True}}]}),
    mount(props={"title": "X", "description": "x" * 200_000}),
])
def test_untrusted_commands_are_rejected(command):
    with pytest.raises(AppError) as error:
        compile_command(command)
    assert error.value.status_code == 422


@pytest.mark.parametrize("background", ["auto", "paper", "warm", "cool", "dark"])
def test_graph_background_survives_stream_and_replay(background):
    state = compile_command(mount(props={"title": "图谱", "background": background})).model_dump(mode="json")
    assert state["props"]["background"] == background
    patched = compile_command({"op": "patch", "block_id": "demo", "base_revision": 0,
        "upsert": {"nodes": [{"id": "a", "label": "根节点"}]}}, state)
    completed = compile_command({"op": "complete", "block_id": "demo", "base_revision": 1}, patched.model_dump(mode="json"))
    assert completed.props.background == background


def test_revision_conflicts_and_terminal_state():
    state = compile_command(mount()).model_dump(mode="json")
    before = copy.deepcopy(state)
    with pytest.raises(AppError) as error:
        compile_command({"op": "patch", "block_id": "demo", "base_revision": 10}, state)
    assert error.value.code == "ui_revision_conflict" and state == before
    complete = compile_command({"op": "complete", "block_id": "demo"}, state)
    with pytest.raises(AppError) as error:
        compile_command({"op": "patch", "block_id": "demo", "base_revision": 1}, complete.model_dump(mode="json"))
    assert error.value.code == "ui_block_closed"
    removed = compile_command({"op": "remove", "block_id": "demo", "base_revision": 1}, complete.model_dump(mode="json"))
    assert removed.state == "removed"


def test_chart_partial_values_and_negative_numbers():
    result = compile_command(mount(component="chart", props={"title": "亏损", "columns": [{"key": "v", "label": "值"}]},
                                   upsert={"rows": [{"id": "a", "values": {"v": -12}}, {"id": "b"}]}))
    assert result.rows[0].values["v"] == -12 and result.rows[1].values == {}


def test_prepared_completion_preserves_resources_published_during_tool_execution():
    from app.services.stream_ui import merge_resource_results
    state = compile_command(mount(component="document", upsert={"blocks": [
        {"id": "a", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "subject one"},
        {"id": "b", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "subject two"},
    ]})).model_dump(mode="json")
    prepared = compile_command({"op": "complete", "block_id": "demo"}, state)
    state["blocks"][0].update(resource_state="unavailable", resource_reason="no_results")
    state["blocks"][1].update(resource_state="ready", resource_reason="ready", media=[{"url": "https://example.com/a.png"}])
    state["resource_revision"] = 2
    result = merge_resource_results(prepared, state)
    assert result.state == "completed" and result.resource_revision == 2
    assert result.blocks[0].resource_reason == "no_results"
    assert result.blocks[0].fallback_svg == FALLBACK_SVG
    assert result.blocks[1].resource_state == "ready" and result.blocks[1].media
    assert prepared.blocks[0].resource_state == "pending"
    changed = compile_command({"op": "patch", "block_id": "demo", "upsert": {"blocks": [
        {"id": "a", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "other subject"}]}}, state)
    assert merge_resource_results(changed, state).blocks[0].resource_state == "pending"


def test_published_resource_survives_expired_orm_snapshot_after_rollback():
    from app.services.stream_ui import merge_resource_results
    pending = compile_command(mount(component="document", upsert={"blocks": [
        {"id": "a", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "subject"},
    ]})).model_dump(mode="json")
    prepared = compile_command({"op": "complete", "block_id": "demo"}, pending)
    published = {"resource_revision": 1, "blocks": [{**pending["blocks"][0],
        "resource_state": "unavailable", "resource_reason": "storage_failed"}]}
    result = merge_resource_results(prepared, pending, published)
    assert result.blocks[0].resource_reason == "storage_failed"
    assert result.resource_revision == 1
    changed = compile_command({"op": "patch", "block_id": "demo", "upsert": {"blocks": [
        {"id": "a", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "new subject"}]}}, pending)
    assert merge_resource_results(changed, pending, published).blocks[0].resource_state == "pending"


def test_same_subject_patch_cannot_erase_saved_svg_after_search_failure():
    state = compile_command(mount(component="document", upsert={"blocks": [
        {"id": "pic", "type": "image", "query": "subject", "fallback_svg": FALLBACK_SVG},
    ]})).model_dump(mode="json")
    state["blocks"][0].update(resource_state="unavailable", resource_reason="search_failed")
    command = {"op": "patch", "block_id": "demo", "upsert": {"blocks": [
        {"id": "pic", "type": "image", "query": "subject", "caption": "补充说明"},
    ]}}
    patched = compile_command(command, state)
    assert patched.blocks[0].fallback_svg == FALLBACK_SVG
    assert patched.blocks[0].resource_reason == "search_failed"
    assert state["blocks"][0]["fallback_svg"] == FALLBACK_SVG
    assert "fallback_svg" not in command["upsert"]["blocks"][0]
    invalid = copy.deepcopy(command)
    invalid["upsert"]["blocks"][0]["fallback_svg"] = 0
    with pytest.raises(AppError):
        compile_command(invalid, state)


def test_queries_start_without_svg_and_changed_subject_cannot_reuse_old_svg():
    assert compile_command(mount(component="document", upsert={"blocks": [
        {"id": "pic", "type": "image", "query": "subject"},
    ]})).blocks[0].fallback_svg == ""
    state = compile_command(mount(component="document", upsert={"blocks": [
        {"id": "pic", "type": "image", "query": "subject", "fallback_svg": FALLBACK_SVG},
    ]})).model_dump(mode="json")
    changed = compile_command({"op": "patch", "block_id": "demo", "upsert": {"blocks": [
        {"id": "pic", "type": "image", "query": "different subject"},
    ]}}, state)
    assert changed.blocks[0].fallback_svg == ""
    # Historical snapshots remain readable/closable without a fabricated drawing.
    state["blocks"][0]["fallback_svg"] = ""
    assert compile_command({"op": "complete", "block_id": "demo"}, state).state == "completed"


def test_resource_rebase_restores_svg_omitted_by_an_already_prepared_patch():
    from app.domain.schemas.stream_ui import Snapshot
    from app.services.stream_ui import merge_resource_results
    saved = compile_command(mount(component="document", upsert={"blocks": [
        {"id": "pic", "type": "image", "query": "subject", "fallback_svg": FALLBACK_SVG},
    ]})).model_dump(mode="json")
    stale = {**saved, "blocks": [{**saved["blocks"][0], "fallback_svg": ""}]}
    saved["blocks"][0].update(resource_state="unavailable", resource_reason="timeout")
    restored = merge_resource_results(Snapshot.model_validate(stale), saved)
    assert restored.blocks[0].fallback_svg == FALLBACK_SVG
    assert restored.blocks[0].resource_reason == "timeout"


@pytest.mark.parametrize("interaction", ["auto", "static", "explore"])
def test_presentation_mode_survives_stream_updates(interaction):
    first = compile_command(mount(component="table", props={"title": "数据", "interaction": interaction}))
    patched = compile_command({"op": "patch", "block_id": "demo", "base_revision": 0,
                               "upsert": {"rows": [{"id": "a", "label": "A", "values": {}}]}}, first.model_dump(mode="json"))
    final = compile_command({"op": "complete", "block_id": "demo", "base_revision": 1}, patched.model_dump(mode="json"))
    assert final.props.interaction == interaction
    assert first.rows == []


def test_presentation_mode_rejects_arbitrary_ui_instructions():
    with pytest.raises(AppError):
        compile_command(mount(props={"title": "数据", "interaction": "eval"}))


def test_model_tool_schema_describes_the_actual_embedded_visual_contract():
    from app.services.stream_ui import tool_definitions
    schema = tool_definitions()[0]["function"]["parameters"]
    definitions = schema["$defs"]
    visual = definitions["EmbeddedVisual"]
    assert visual["additionalProperties"] is False
    assert set(visual["properties"]) == {"props", "rows", "nodes", "edges", "items"}
    assert definitions["DocumentNode"]["properties"]["visual"]["anyOf"][0]["$ref"] == "#/$defs/EmbeddedVisual"
    assert "title" in definitions["UIProps"]["required"]
    assert "required" not in schema["properties"]["props"]  # patches may change only one prop
    assert definitions["UIProps"]["additionalProperties"] is False
    assert "toc" in schema["properties"]["props"]["properties"]
    assert "unit" not in definitions["Series"]["properties"]


def test_data_model_separation_op_data_and_bindings():
    mounted = compile_command({
        "op": "mount",
        "block_id": "kpi",
        "component": "metric",
        "props": {"title": "系统指标", "unit": "%"},
        "data_model": {"cpu": 42, "memory": {"used": 64, "total": 128}},
        "upsert": {
            "items": [
                {"id": "cpu", "label": "CPU", "value": "@/cpu"},
                {"id": "mem", "label": "内存", "value": "@/memory/used", "description": "总量 @{/memory/total} GB"},
            ]
        },
    })
    assert mounted.data_model == {"cpu": 42, "memory": {"used": 64, "total": 128}}
    assert mounted.revision == 0

    updated = compile_command({
        "op": "data",
        "block_id": "kpi",
        "base_revision": 0,
        "data_model": {"memory": {"used": 80}},
    }, mounted.model_dump(mode="json"))
    assert updated.revision == 1
    assert updated.data_model == {"cpu": 42, "memory": {"used": 80, "total": 128}}
    assert [i.id for i in updated.items] == ["cpu", "mem"]

    deleted = compile_command({
        "op": "data",
        "block_id": "kpi",
        "base_revision": 1,
        "delete_paths": ["/memory/total"],
    }, updated.model_dump(mode="json"))
    assert deleted.revision == 2
    assert deleted.data_model == {"cpu": 42, "memory": {"used": 80}}

    completed = compile_command({"op": "complete", "block_id": "kpi", "base_revision": 2}, deleted.model_dump(mode="json"))
    assert completed.state == "completed"


def test_data_model_security_and_chart_binding_validation():
    # Reject prototype keys in data_model
    with pytest.raises(AppError) as err:
        compile_command(mount(data_model={"__proto__": {"admin": True}}))
    assert err.value.status_code == 422

    # Reject op=data mixing structure changes
    base = compile_command(mount(data_model={"x": 1})).model_dump(mode="json")
    with pytest.raises(AppError) as err2:
        compile_command({
            "op": "data",
            "block_id": "demo",
            "base_revision": 0,
            "data_model": {"x": 2},
            "props": {"title": "illegal"},
        }, base)
    assert err2.value.status_code == 422

    # Chart accepts @/path bindings resolving to numbers, rejects non-numeric resolved bindings
    chart = compile_command({
        "op": "mount",
        "block_id": "c1",
        "component": "chart",
        "props": {"title": "趋势", "columns": [{"key": "v", "label": "值"}]},
        "data_model": {"q1": {"v": 100}},
        "upsert": {"rows": [{"id": "r1", "label": "Q1", "values": {"v": "@/q1/v"}}]},
    })
    assert chart.rows[0].values["v"] == "@/q1/v"

    with pytest.raises(AppError):
        compile_command({
            "op": "data",
            "block_id": "c1",
            "base_revision": 0,
            "data_model": {"q1": {"v": "not-a-number"}},
        }, chart.model_dump(mode="json"))


def test_weight_image_variant_fit_and_catalog_checks():
    from app.services.genui_catalog import evaluate_checks

    snap = compile_command({
        "op": "mount",
        "block_id": "doc_layout",
        "component": "document",
        "props": {"title": "版式与校验"},
        "data_model": {"cpu": 95, "env": "prod"},
        "checks": [
            {"id": "c_cpu", "fn": "max_value", "path": "@/cpu", "args": [80], "severity": "warning", "message": "CPU 超出阈值"},
            {"id": "c_env", "fn": "one_of", "path": "@/env", "args": ["dev", "prod"], "severity": "info", "message": "环境非法"},
        ],
        "upsert": {
            "blocks": [
                {"id": "row1", "type": "row", "columns": 2},
                {"id": "left", "parent_id": "row1", "type": "paragraph", "text": "主内容", "weight": 2},
                {"id": "img1", "parent_id": "row1", "type": "image", "query": "server architecture", "image_variant": "hero", "fit": "cover"},
            ]
        },
    })
    assert snap.blocks[1].weight == 2
    assert snap.blocks[2].image_variant == "hero"
    assert snap.blocks[2].fit == "cover"
    assert len(snap.checks) == 2

    results = evaluate_checks([r.model_dump() for r in snap.checks], snap.data_model)
    assert results[0]["passed"] is False and results[0]["message"] == "CPU 超出阈值"
    assert results[1]["passed"] is True

    # Reject image_variant/fit on non-image node
    with pytest.raises(AppError):
        compile_command(mount(component="document", upsert={"blocks": [{"id": "p", "type": "paragraph", "text": "x", "image_variant": "hero"}]}))
    with pytest.raises(AppError):
        compile_command(mount(component="document", upsert={"blocks": [{"id": "p", "type": "paragraph", "text": "x", "fit": "cover"}]}))
    # Reject invalid check rule arguments
    with pytest.raises(AppError):
        compile_command(mount(checks=[{"id": "bad", "fn": "range", "path": "@/x", "args": [10, 5], "message": "bad range"}]))


def test_stream_ui_tolerates_composer_envelope_metadata():
    from app.services.stream_ui import strip_part_envelope

    base = compile_command(mount(component="chart", props={
        "title": "趋势图",
        "columns": [{"key": "v", "label": "值"}],
    })).model_dump(mode="json")
    enveloped = {
        **base,
        "title": "趋势图",
        "artifact_registry": {
            "canonical_id": "demo",
            "artifact_type": "ui",
            "title": "趋势图",
            "placement": "referenced",
        },
    }

    stripped = strip_part_envelope(enveloped)
    assert "artifact_registry" not in stripped and "title" not in stripped

    completed = compile_command({"op": "complete", "block_id": "demo", "base_revision": 0}, enveloped)
    assert completed.state == "completed" and completed.revision == 1

    # Unexpected non-envelope keys must still be rejected.
    with pytest.raises(AppError):
        compile_command(
            {"op": "complete", "block_id": "demo", "base_revision": 0},
            {**base, "unexpected_key": True},
        )


def test_stream_ui_composable_multi_svg_document_and_security():
    from app.services.genui_express import ExpressUIParser
    from app.services.rich_response import InlineUIParser
    from app.services.streamed_ui import UIArgumentStream

    svg_before = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200"><rect x="10" y="10" width="300" height="180" rx="8" fill="#ffffff" stroke="#d1d5db"/><rect x="90" y="50" width="140" height="80" rx="6" fill="#dbeafe"/></svg>'
    svg_after = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200"><rect x="10" y="10" width="300" height="180" rx="8" fill="#ffffff" stroke="#d1d5db"/><rect x="24" y="40" width="272" height="110" rx="6" fill="#dcfce7"/></svg>'
    snapshot = compile_command(mount(component="document", upsert={"blocks": [
        {"id": "h1", "type": "heading", "level": 2, "text": "修改前后的布局对比"},
        {"id": "cols", "type": "columns", "columns": 2},
        {"id": "left", "parent_id": "cols", "type": "card", "label": "修改前"},
        {"id": "svg_left", "parent_id": "left", "type": "svg", "svg": svg_before, "alt": "修改前布局"},
        {"id": "cap_left", "parent_id": "left", "type": "caption", "text": "卡片过窄，两侧空间浪费。"},
        {"id": "right", "parent_id": "cols", "type": "column", "label": "修改后"},
        {"id": "svg_right", "parent_id": "right", "type": "svg", "svg": svg_after, "alt": "修改后布局", "display_mode": "artifact"},
        {"id": "cap_right", "parent_id": "right", "type": "caption", "text": "充分利用宽度，统一表单边界。"},
        {"id": "div", "type": "divider"},
        {"id": "summary", "type": "caption", "text": "上方由标题、双列布局、两个独立 SVG 和说明文字共同组成。"},
    ]}))
    assert [node.type for node in snapshot.blocks] == [
        "heading", "columns", "card", "svg", "caption", "column", "svg", "caption", "divider", "caption",
    ]
    assert snapshot.blocks[3].display_mode == "inline"
    assert snapshot.blocks[6].display_mode == "artifact"

    # Security: unsafe SVG and misplaced fields are rejected.
    with pytest.raises(AppError):
        compile_command(mount(component="document", upsert={"blocks": [
            {"id": "bad", "type": "svg", "svg": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><script>alert(1)</script><rect width="10" height="10"/></svg>'},
        ]}))
    with pytest.raises(AppError):
        compile_command(mount(component="document", upsert={"blocks": [
            {"id": "bad", "type": "paragraph", "text": "x", "svg": svg_before},
        ]}))
    with pytest.raises(AppError):
        compile_command(mount(component="document", upsert={"blocks": [
            {"id": "bad", "type": "paragraph", "text": "x", "display_mode": "artifact"},
        ]}))

    # Inline :::ui parser accepts documents whose content is native SVG + caption (including svg_markup alias).
    parser = InlineUIParser()
    events = list(parser.feed(
        ':::ui compare\n'
        '{"props":{"title":"布局对比"},"upsert":{"blocks":['
        '{"id":"s1","type":"svg","svg_markup":' + __import__("json").dumps(svg_before) + ',"alt":"示意"}'
        ']}}\n'
        ':::\n'
    ))
    events.extend(parser.finish())
    assert not parser.failed_declaration
    assert any(getattr(e, "type", None) == "ui_command" and e.command.get("op") == "complete" for e in events)

    # Express DSL supports card/column/svg/caption.
    express = ExpressUIParser("exp1")
    for line in [
        "@title 对比",
        "columns#c columns=2",
        "  card#l: 修改前",
        f"    svg#s1: {svg_before}",
        "    caption#cap1: 说明文字",
    ]:
        express.feed_line(line + "\n")
    finished = express.finish()
    assert finished[-1]["op"] == "complete"

    # Streaming argument preview creates a stable pending slot for incomplete svg nodes.
    stream = UIArgumentStream()
    partial = stream.feed(
        '{"op":"mount","block_id":"d","component":"document","props":{"title":"图"},'
        '"upsert":{"blocks":[{"id":"s1","type":"svg","label":"架构图","svg":"<svg viewBox='
    )
    assert partial is not None
    assert partial["upsert"]["blocks"] == [{"id": "s1", "type": "svg", "label": "架构图", "state": "pending"}]


def test_response_composer_and_stream_svg_display_modes():
    from types import SimpleNamespace
    from app.services.response_composer import ArtifactRegistry
    from app.services.stream_svg import SvgDeclaration

    svg_markup = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"><rect width="80" height="40" fill="#dbeafe"/></svg>'

    # :::svg defaults to inline and supports explicit display_mode=artifact.
    decl_default = SvgDeclaration("s_inline")
    start_default = decl_default.feed_line('{"viewBox":[0,0,320,180],"title":"架构示意"}')
    assert start_default[0]["display_mode"] == "inline"

    decl_artifact = SvgDeclaration("s_art")
    start_artifact = decl_artifact.feed_line('{"viewBox":[0,0,320,180],"title":"可交付架构图","display_mode":"artifact"}')
    assert start_artifact[0]["display_mode"] == "artifact"

    decl_invalid = SvgDeclaration("s_bad")
    assert decl_invalid.feed_line('{"viewBox":[0,0,320,180],"title":"x","display_mode":"popup"}') == []

    # Same SVG format can be either inline or artifact based on purpose or explicit mode.
    native_svg = SimpleNamespace(id="p1", part_type="svg", status="completed", content="图", data={"block_id": "s1", "view_box": "0 0 320 180", "svg_markup": svg_markup})
    native_svg_as_artifact = SimpleNamespace(id="p2", part_type="svg", status="completed", content="图", data={"block_id": "s2", "view_box": "0 0 320 180", "svg_markup": svg_markup, "display_mode": "artifact"})
    file_svg = SimpleNamespace(id="p3", part_type="sandbox_artifact", status="completed", content="diagram.svg", data={"file_id": "f1", "path": "outputs/diagram.svg", "mime_type": "image/svg+xml"})
    file_svg_inline = SimpleNamespace(id="p4", part_type="sandbox_artifact", status="completed", content="diagram-inline.svg", data={"file_id": "f2", "path": "outputs/inline.svg", "mime_type": "image/svg+xml", "display_mode": "inline"})
    static_svg_card_markup = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"><circle cx="160" cy="90" r="40" fill="#dcfce7"/></svg>'
    static_svg_card = SimpleNamespace(id="p5", part_type="magic_card", status="completed", content="卡片图", data={"card_id": "c1", "preview_html": f"<main>{static_svg_card_markup}</main>"})
    interactive_html = SimpleNamespace(id="p6", part_type="magic_card", status="completed", content="交互应用", data={"card_id": "c2", "preview_html": "<button onclick='x()'>Go</button>"})

    ArtifactRegistry().reconcile([native_svg, native_svg_as_artifact, file_svg, file_svg_inline, static_svg_card, interactive_html])
    assert native_svg.data["artifact_registry"]["display_mode"] == "inline"
    assert native_svg_as_artifact.data["artifact_registry"]["display_mode"] == "artifact"
    assert file_svg.data["artifact_registry"]["display_mode"] == "artifact"
    assert file_svg_inline.data["artifact_registry"]["display_mode"] == "inline"
    assert static_svg_card.data["artifact_registry"]["display_mode"] == "inline"
    assert interactive_html.data["artifact_registry"]["display_mode"] == "artifact"


