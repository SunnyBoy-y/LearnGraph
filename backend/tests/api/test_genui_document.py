import copy
import json

import pytest

from app.core.errors import AppError
from app.domain.schemas.stream_ui import UIAction
from app.services.stream_ui import action_prompt, compile_command, prepare_command, prepare_preview


def test_document_uses_the_real_chat_sse_and_history_lane(client, register_user, auth_headers, monkeypatch):
    from app.api.routers import chat as router
    from app.providers.ports.model import ProviderStreamEvent
    from app.services.chat import ChatService

    class DocumentProvider:
        available = remote_capability = supports_structured_chat = supports_agent_tools = True
        provider_id = model_id = "document-fixture"
        thinking_mode = "off"
        actual_reasoning_effort = None
        search_route = "disabled"
        last_usage = {}

        def stream_chat(self, messages, **kwargs):
            if messages[-1].role == "tool":
                yield ProviderStreamEvent("text_delta", content="后续分析仍在同一回答中。[[artifact:block_id:report]]")
                yield ProviderStreamEvent("completed", finish_reason="stop")
                return
            yield ProviderStreamEvent("text_delta", content="先解释结论。")
            command = {"op": "mount", "block_id": "report", "component": "document", "props": {"title": "增长报告", "density": "editorial", "toc": True},
                       "upsert": {"blocks": [{"id": "h", "type": "heading", "level": 2, "text": "关键指标"},
                                             {"id": "m", "type": "metric", "visual": {"props": {"title": "月活", "data_kind": "example"}, "items": [{"id": "v", "label": "用户数", "value": 3180}]}}]}}
            commands = [command, {"op": "complete", "block_id": "report", "base_revision": 0}]
            yield ProviderStreamEvent("tool_calls", tool_calls=[{"id": f"ui_{i}", "type": "function", "function": {"name": "ui_stream", "arguments": json.dumps(command)}} for i, command in enumerate(commands)])
            yield ProviderStreamEvent("completed", finish_reason="tool_calls")

    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, DocumentProvider(), settings=settings)
        service._build_fast_preface_provider = lambda: None
        return service

    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "GenUI SSE"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers, json={"content": "用文档分析增长", "agent_mode": True})
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == "message.completed", response.text[-5000:]
    updates = [event["payload"] for event in events if event.get("payload", {}).get("ui_operation")]
    assert [entry["ui_operation"] for entry in updates] == ["ui.mount", "ui.complete"]
    assert len({entry["part"]["id"] for entry in updates}) == 1
    mid = events[-1]["message_id"]
    history = next(item for item in client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"] if item["id"] == mid)
    part = next(part for part in history["parts"] if part["type"] == "ui")
    assert part["id"] == updates[-1]["part"]["id"]
    assert part["data"]["props"]["density"] == "editorial" and part["data"]["props"]["toc"] is True
    assert part["data"]["blocks"][1]["visual"]["items"][0]["value"] == 3180
    assert "后续分析仍在同一回答中" in history["content"]


@pytest.fixture
def document_context(client, register_user, auth_headers):
    from app.core.database import SessionLocal
    from app.domain.models import Message, MessageVersion, MessagePartRecord
    token, workspace, _, _ = register_user()
    sid = client.post("/api/v1/sessions", headers=auth_headers(token, workspace), json={"title": "Document GenUI"}).json()["id"]
    with SessionLocal() as db:
        message = Message(workspace_id=workspace, session_id=sid, role="assistant", status="completed")
        db.add(message)
        db.flush()
        version = MessageVersion(workspace_id=workspace, message_id=message.id, version=1, status="completed")
        db.add(version)
        db.flush()
        tool = MessagePartRecord(workspace_id=workspace, message_version_id=version.id, ordinal=1,
                                 part_type="tool_call", status="completed", data={"tool_name": "analytics", "output": {"dataset": [
                                     {"id": "a", "label": "5月", "values": {"v": 1200}},
                                     {"id": "b", "label": "6月", "values": {"v": 3180}},
                                 ]}})
        db.add(tool)
        db.commit()
        yield db, workspace, sid, message.id, version.id, tool.id


def chart_command():
    return {"op": "mount", "block_id": "growth", "component": "chart", "props": {
        "title": "用户增长", "columns": [{"key": "v", "label": "用户"}],
    }, "upsert": {"rows": [{"id": "fake", "values": {"v": 999}}]}}


def test_source_reference_uses_saved_collection_and_rejects_other_scope(document_context):
    from app.services.genui_sources import resolve_command_sources, resolve_collection
    db, workspace, sid, mid, vid, tool_id = document_context
    raw = chart_command()
    raw["props"]["data_ref"] = {"part_id": tool_id, "path": ["dataset"]}
    before = copy.deepcopy(raw)
    result = prepare_command(db, workspace, sid, mid, vid, raw)["snapshot"]
    assert result["rows"][0]["values"]["v"] == 1200
    assert len(result["rows"]) == 2 and result["props"]["source_note"] == "analytics · 已保存数据"
    assert raw == before
    for w, s in [(workspace, "other"), ("other", sid)]:
        with pytest.raises(AppError) as error:
            resolve_collection(db, w, s, raw["props"]["data_ref"])
        assert error.value.code == "ui_source_not_found"
    current = {**result, "rows": result["rows"] + [{"id": "fake", "values": {"v": 999}}]}
    patch = {"op": "patch", "block_id": "growth", "base_revision": 0, "upsert": {"rows": [{"id": "fake", "values": {"v": 123}}]}}
    normalized = resolve_command_sources(db, workspace, sid, patch, current)
    assert [row.id for row in compile_command(normalized, current).rows] == ["a", "b"]
    with pytest.raises(AppError):
        resolve_collection(db, workspace, sid, {"part_id": tool_id, "path": ["missing"]})


def test_numeric_preview_is_a_placeholder_until_commit(document_context):
    db, workspace, sid, mid, vid, _ = document_context
    raw = chart_command()
    preview = prepare_preview(db, workspace, sid, mid, vid, {"command": raw, "_preview": "call1"})["snapshot"]
    assert preview["rows"] == [] and preview["preview"]["base"] is None
    assert prepare_command(db, workspace, sid, mid, vid, raw)["snapshot"]["rows"][0]["values"]["v"] == 999
    doc = {"op": "mount", "block_id": "report", "component": "document", "props": {"title": "报告", "density": "editorial", "toc": True},
           "upsert": {"blocks": [{"id": "p", "type": "paragraph", "text": "立即出现的解释"},
                                {"id": "m", "type": "metric", "visual": {"props": {"title": "月活"}, "items": [{"id": "v", "label": "用户数", "value": 3180}]}}]}}
    preview = prepare_preview(db, workspace, sid, mid, vid, {"command": doc, "_preview": "call2"})["snapshot"]
    assert preview["blocks"][0]["text"] == "立即出现的解释"
    assert preview["blocks"][1]["state"] == "pending" and preview["blocks"][1]["visual"] is None
    final = prepare_command(db, workspace, sid, mid, vid, doc)["snapshot"]
    assert final["blocks"][1]["visual"]["items"][0]["value"] == 3180


def test_source_mapping_is_deterministic_and_pending_sources_are_rejected(document_context):
    from app.domain.models import MessagePartRecord
    db, workspace, sid, mid, vid, tool_id = document_context
    tool = db.get(MessagePartRecord, tool_id)
    tool.data = {"tool_name": "analytics", "output": {"records": [{"month": "10月", "users": 3180}]}}
    db.commit()
    raw = chart_command()
    raw["props"]["data_ref"] = {"part_id": tool_id, "path": ["records"], "label_key": "month", "value_keys": {"v": "users"}}
    result = prepare_command(db, workspace, sid, mid, vid, raw)["snapshot"]
    assert result["rows"] == [{"id": "row_0", "label": "10月", "values": {"v": 3180.0}}]
    tool.status = "streaming"
    db.commit()
    with pytest.raises(AppError) as error:
        prepare_command(db, workspace, sid, mid, vid, raw)
    assert error.value.code == "ui_source_not_found"


def test_reference_hints_support_real_dataset_field_names_without_exposing_values():
    from app.services.genui_sources import source_reference_hint
    from app.domain.schemas.stream_ui import DataReference
    output = {"统计结果": [{"月份": "10月", "月活用户": 3180}], "logs": "private text"}
    hint = source_reference_hint("tool-part", output)
    assert '"path": ["统计结果"]' in hint and '"part_id": "tool-part"' in hint
    assert "3180" not in hint and "private text" not in hint
    assert not source_reference_hint("tool-part", {"ui_stream": {"snapshot": output}})
    assert DataReference(part_id="tool-part", path=["统计结果"], label_key="月份", value_keys={"active": "月活用户"}).value_keys == {"active": "月活用户"}


def test_oversized_command_is_rejected_before_source_lookup_or_copy():
    from app.services.genui_sources import resolve_command_sources
    with pytest.raises(AppError) as error:
        resolve_command_sources(None, "workspace", "session", {"props": {"title": "x" * 200000}}, None)
    assert error.value.code == "invalid_ui_command"


def test_numeric_preview_keeps_the_last_committed_values(document_context):
    from app.domain.models import MessagePartRecord
    db, workspace, sid, mid, vid, _ = document_context
    current = compile_command(chart_command()).model_dump(mode="json")
    part = MessagePartRecord(workspace_id=workspace, message_version_id=vid, ordinal=2, part_type="ui", status="streaming", data=current)
    db.add(part)
    db.commit()
    raw = {"op": "patch", "block_id": "growth", "base_revision": 0, "upsert": {"rows": [{"id": "fake", "values": {"v": 123}}]}}
    preview = prepare_preview(db, workspace, sid, mid, vid, {"command": raw, "_preview": "call"})["snapshot"]
    assert preview["rows"][0]["values"]["v"] == 999
    assert preview["revision"] == 0 and preview["preview"]["base"] == current
    assert prepare_command(db, workspace, sid, mid, vid, raw)["snapshot"]["rows"][0]["values"]["v"] == 123
    assert part.data == current


def test_explanation_respects_visible_range_and_series(document_context):
    from app.domain.models import MessagePartRecord
    db, workspace, sid, _, vid, _ = document_context
    raw = chart_command()
    raw["props"].update(columns=[{"key": "v", "label": "用户"}, {"key": "hidden", "label": "隐藏"}], data_kind="example")
    raw["upsert"]["rows"] = [{"id": f"m{i}", "label": str(i), "values": {"v": i, "hidden": 900}} for i in range(6)]
    data = compile_command(raw).model_dump(mode="json")
    data["state"] = "completed"
    part = MessagePartRecord(workspace_id=workspace, message_version_id=vid, ordinal=2, part_type="ui", status="completed", data=data)
    db.add(part)
    db.commit()
    action = UIAction(part_id=part.id, revision=0, action="explain", view_state={"mode": "bar", "limit": 3, "hidden": ["hidden"]})
    prompt = action_prompt(db, workspace, sid, action)
    context = json.loads(prompt.split("\n", 1)[1])
    assert [row["id"] for row in context["data"]["rows"]] == ["m3", "m4", "m5"]
    assert all("hidden" not in row["values"] for row in context["data"]["rows"])
    assert context["data_kind"] == "example"
    assert context["scope"] == "用户当前筛选的视图"
    assert part.data["rows"][0]["id"] == "m0"


def test_revise_targets_one_completed_historical_block_and_preserves_history(document_context):
    from app.domain.models import Message, MessagePartRecord, MessageVersion
    db, workspace, sid, _, vid, _ = document_context
    original = compile_command(chart_command()).model_dump(mode="json")
    original["state"] = "completed"
    part = MessagePartRecord(workspace_id=workspace, message_version_id=vid, ordinal=2, part_type="ui", status="completed", data=original)
    db.add(part)
    next_message = Message(workspace_id=workspace, session_id=sid, role="assistant", status="streaming")
    db.add(next_message)
    db.flush()
    next_version = MessageVersion(workspace_id=workspace, message_id=next_message.id, version=1, status="streaming")
    db.add(next_version)
    db.commit()
    raw = {"op": "revise", "block_id": "growth", "base_revision": 0, "props": {"chart_type": "bar"}}
    update = prepare_command(db, workspace, sid, next_message.id, next_version.id, raw)
    assert update["part_id"] is None  # new answer part, never writes old history
    assert update["snapshot"]["props"]["chart_type"] == "bar"
    assert update["snapshot"]["rows"] == original["rows"]
    assert update["snapshot"]["revision"] == 1 and update["snapshot"]["state"] == "streaming"
    assert db.get(MessagePartRecord, part.id).data == original
    with pytest.raises(AppError) as error:
        prepare_command(db, workspace, sid, next_message.id, next_version.id, {**raw, "base_revision": 15})
    assert error.value.code == "ui_revision_conflict"
    with pytest.raises(AppError) as error:
        prepare_command(db, workspace, sid, next_message.id, next_version.id, {**raw, "block_id": "missing"})
    assert error.value.code == "ui_block_not_found"


def test_embedded_chart_action_is_scoped_to_a_real_document_block(document_context):
    from app.domain.models import MessagePartRecord
    db, workspace, sid, _, vid, _ = document_context
    raw = {"op": "mount", "block_id": "report", "component": "document", "props": {"title": "报告"},
           "upsert": {"blocks": [{"id": "trend", "type": "chart", "visual": {"props": chart_command()["props"], "rows": chart_command()["upsert"]["rows"]}},
                                {"id": "text", "type": "paragraph", "text": "不相关的另一节"}]}}
    data = compile_command(raw).model_dump(mode="json")
    data.update(state="completed", revision=7)
    part = MessagePartRecord(workspace_id=workspace, message_version_id=vid, ordinal=2, part_type="ui", status="completed", data=data)
    db.add(part)
    db.commit()
    action = UIAction(part_id=part.id, revision=7, action="document.block.explain", block_id="trend", view_state={"mode": "bar"})
    prompt = action_prompt(db, workspace, sid, action)
    context = json.loads(prompt.split("\n", 1)[1])
    assert context["title"] == "用户增长" and context["data"]["rows"][0]["values"]["v"] == 999
    assert "不相关的另一节" not in prompt
    with pytest.raises(AppError) as error:
        action_prompt(db, workspace, sid, action.model_copy(update={"block_id": "text"}))
    assert error.value.code == "ui_invalid_action"


@pytest.mark.parametrize("view", [{"mode": []}, {"hidden": "bad"}, {"limit": -1}, {"sort": {"key": "x", "descending": "yes"}}, {"query": "x" * 501}])
def test_invalid_view_state_is_rejected(view):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        UIAction(part_id="part", revision=0, action="explain", view_state=view)
