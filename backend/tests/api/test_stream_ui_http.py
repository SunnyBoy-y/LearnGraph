import json

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.chat import ChatService


class UIProvider:
    available = True
    remote_capability = True
    provider_id = "stub-provider"
    model_id = "stub-model"
    supports_structured_chat = True
    supports_agent_tools = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"
    last_usage = {}

    def __init__(self, close=True):
        self.close = close
        self.histories = []

    def stream_chat(self, messages, **kwargs):
        self.histories.append(messages)
        if messages[-1].role == "user" and messages[-1].content.startswith("请解释图表"):
            yield ProviderStreamEvent("text_delta", content="小明是项目参与者。")
            yield ProviderStreamEvent("completed", finish_reason="stop")
            return
        if messages[-1].role != "tool":
            commands = [
                {"op": "mount", "block_id": "people", "component": "graph", "props": {"title": "小明的参与事件"}},
                {"op": "patch", "block_id": "people", "base_revision": 0,
                 "upsert": {"nodes": [{"id": "p1", "label": "小明"}, {"id": "e1", "label": "开源项目"}],
                            "edges": [{"id": "r1", "source": "p1", "target": "e1", "label": "参与"}]}},
            ]
            if self.close:
                commands.append({"op": "complete", "block_id": "people", "base_revision": 1})
            yield ProviderStreamEvent("text_delta", content="先看这张关系图。")
            yield ProviderStreamEvent("tool_calls", tool_calls=[
                {"id": f"ui_{index}", "type": "function", "function": {"name": "ui_stream", "arguments": json.dumps(command)}}
                for index, command in enumerate(commands)
            ])
            yield ProviderStreamEvent("completed", finish_reason="tool_calls")
        else:
            yield ProviderStreamEvent("text_delta", content="图示如下：[[artifact:block_id:people|layout=block]]小明参与了开源项目。")
            yield ProviderStreamEvent("completed", finish_reason="stop")


@pytest.mark.parametrize("runtime,close,message_status", [
    (False, True, "completed"), (True, True, "completed"), (True, False, "completed"),
    (True, True, "interrupted"), (True, True, "failed"), (True, True, "cancelled"),
])
def test_ui_stream_live_history_replay_actions_and_retry(client, register_user, auth_headers, monkeypatch, runtime, close, message_status):
    from app.api.routers import chat as router
    provider = UIProvider(close)

    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, provider, settings=settings)
        service._build_fast_preface_provider = lambda: None
        if runtime:
            from app.services.chat_service_factory import build_agent_tool_runtime
            service.agent_tool_runtime = build_agent_tool_runtime(db, context, settings)
        return service

    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "stream UI"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                           json={"content": "生成关系图", "agent_mode": True})
    assert response.status_code == 200, response.text
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == "message.completed", response.text[-6000:]
    updates = [event for event in events if event.get("payload", {}).get("ui_operation")]
    assert [event["payload"]["ui_operation"] for event in updates] == ["ui.mount", "ui.patch", "ui.complete"]
    parts = [event["payload"]["part"] for event in updates]
    assert len({part["id"] for part in parts}) == 1
    assert parts[0]["data"]["nodes"] == [] and len(parts[1]["data"]["nodes"]) == 2
    assert [part["data"]["revision"] for part in parts] == [0, 1, 2]
    mid = events[-1]["message_id"]
    history = next(item for item in client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"]
                   if item["id"] == mid)
    durable = next(part for part in history["parts"] if part["type"] == "ui")
    assert durable["id"] == parts[0]["id"] and durable["data"]["state"] == "completed"
    assert "[[artifact:" not in history["content"]
    ordered = sorted(history["parts"], key=lambda part: part["sequence"])
    position = next(index for index, part in enumerate(ordered) if part["type"] == "ui")
    assert ordered[position + 1]["content"] == "小明参与了开源项目。"
    replay = client.get(f"/api/v1/sessions/{sid}/messages/{mid}/events", headers=headers)
    assert replay.status_code == 200 and any(event.get("payload", {}).get("ui_operation") == "ui.patch" for event in replay.json())
    # A later failure/stop in the answer must not invalidate an already sealed graph.
    if message_status != "completed":
        from sqlalchemy import select
        from app.core.database import SessionLocal
        from app.domain.models import Message, MessageVersion
        with SessionLocal() as db:
            message = db.get(Message, mid)
            message.status = message_status
            version = db.scalar(select(MessageVersion).where(
                MessageVersion.message_id == mid, MessageVersion.version == message.version,
            ))
            version.status = message_status
            db.commit()
    action = {"part_id": durable["id"], "revision": 2, "action": "graph.node.explain", "node_id": "p1"}
    whole = client.post(f"/api/v1/sessions/{sid}/ui/actions", headers=headers,
                        json={"part_id": durable["id"], "revision": 2, "action": "explain"})
    assert whole.status_code == 200 and "小明的参与事件" in whole.json()["prompt"]
    result = client.post(f"/api/v1/sessions/{sid}/ui/actions", headers=headers, json=action)
    assert result.status_code == 200, result.text
    assert "小明" in result.json()["prompt"]
    followup = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                           json={"content": result.json()["prompt"], "ui_action": action, "agent_mode": True})
    assert followup.status_code == 200 and '"type": "message.completed"' in followup.text
    current_input = provider.histories[-1]
    assert any('"selected_node_id": "p1"' in (message.content or "") for message in current_input)
    context_message = next(message.content for message in current_input if '"selected_node_id": "p1"' in (message.content or ""))
    assert '"chart_type"' not in context_message and '"revision"' not in context_message
    user_rows = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"]
    assert "snapshot" not in [row for row in user_rows if row["role"] == "user"][-1]["content"]
    assert client.post(f"/api/v1/sessions/{sid}/ui/actions", headers=headers, json={**action, "revision": 0}).status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                       json={"content": "解释", "ui_action": {**action, "revision": 0}}).status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/ui/actions", headers=headers, json={**action, "node_id": "foreign"}).status_code == 422
    token2, workspace2, _, _ = register_user(prefix="other")
    assert client.post(f"/api/v1/sessions/{sid}/ui/actions", headers=auth_headers(token2, workspace2), json=action).status_code == 404
    retried = client.post(f"/api/v1/sessions/{sid}/messages/{mid}/retry", headers=headers, json={})
    assert retried.status_code == 200
    retry_events = [json.loads(line[5:]) for line in retried.text.splitlines() if line.startswith("data:")]
    assert retry_events[-1]["type"] == "message.completed", retried.text[-5000:]
    assert any(event.get("payload", {}).get("ui_operation") == "ui.complete" for event in retry_events)
    assert client.post(f"/api/v1/sessions/{sid}/ui/actions", headers=headers, json=action).status_code == 404


@pytest.mark.parametrize("message_status,part_status,block_state", [
    ("pending", "completed", "completed"), ("streaming", "completed", "completed"),
    ("interrupted", "completed", "streaming"), ("interrupted", "failed", "completed"),
    ("failed", "streaming", "completed"), ("completed", "completed", "removed"),
])
def test_ui_actions_reject_unfinished_or_removed_blocks(
    client, register_user, auth_headers, stub_chat_service, message_status, part_status, block_state,
):
    from app.core.database import SessionLocal
    from app.domain.models import Message, MessagePartRecord, MessageVersion
    from app.services.stream_ui import compile_command

    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "UI action guards"}).json()["id"]
    data = compile_command({"op": "mount", "block_id": "graph", "component": "graph",
                            "props": {"title": "时间复杂度"},
                            "upsert": {"nodes": [{"id": "n", "label": "O(n)"}]}}).model_dump(mode="json")
    data["state"] = block_state
    with SessionLocal() as db:
        message = Message(workspace_id=workspace, session_id=sid, role="assistant", status=message_status)
        db.add(message)
        db.flush()
        version = MessageVersion(workspace_id=workspace, message_id=message.id, version=1, status=message_status)
        db.add(version)
        db.flush()
        part = MessagePartRecord(workspace_id=workspace, message_version_id=version.id,
                                 ordinal=1, part_type="ui", status=part_status, data=data)
        db.add(part)
        db.flush()
        part_id = part.id
        db.commit()
    for kind in ("explain", "graph.node.explain"):
        action = {"part_id": part_id, "revision": 0, "action": kind,
                  **({"node_id": "n"} if kind == "graph.node.explain" else {})}
        result = client.post(f"/api/v1/sessions/{sid}/ui/actions", headers=headers, json=action)
        assert result.status_code == 409 and result.json()["error"]["code"] == "ui_not_ready"
        followup = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                               json={"content": "解释图表", "ui_action": action})
        assert followup.status_code == 409 and followup.json()["error"]["code"] == "ui_not_ready"
