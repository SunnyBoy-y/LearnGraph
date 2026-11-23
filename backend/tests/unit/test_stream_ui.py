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
