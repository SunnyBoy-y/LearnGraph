import json

from app.providers.ports.model import ProviderStreamEvent
from app.services.chat import ChatService


def test_editorial_lists_preview_commit_and_replay_through_chat(client, register_user, auth_headers, monkeypatch):
    from app.api.routers import chat as router

    command = {"op": "mount", "block_id": "editorial", "component": "document", "props": {"title": "回答方式"}, "upsert": {"blocks": [
        {"id": "modes", "type": "feature_list"},
        {"id": "fast", "parent_id": "modes", "type": "rail_item", "icon": "zap", "label": "极速模式", "text": "先显示解释，再继续补充。" * 35},
        {"id": "steps", "type": "step_list"},
        {"id": "prepare", "parent_id": "steps", "type": "rail_item", "label": "确认数据", "text": "先确认数据口径。"},
        {"id": "states", "type": "status_list"},
        {"id": "done", "parent_id": "states", "type": "rail_item", "label": "完成", "text": "示例状态。", "item_status": "completed"},
    ]}}

    class Provider:
        available = remote_capability = supports_structured_chat = supports_agent_tools = True
        provider_id = model_id = "editorial-fixture"
        thinking_mode = "off"
        actual_reasoning_effort = None
        search_route = "disabled"
        last_usage = {}

        def stream_chat(self, messages, **kwargs):
            if messages[-1].role == "tool":
                yield ProviderStreamEvent("text_delta", content="[[artifact:block_id:editorial]]后续正文。")
                yield ProviderStreamEvent("completed", finish_reason="stop")
                return
            yield ProviderStreamEvent("text_delta", content="先解释背景。")
            raw = json.dumps(command, ensure_ascii=False)
            for position in range(0, len(raw), 140):
                yield ProviderStreamEvent("tool_call_delta", tool_calls=[{"id": "rail-mount", "index": 0,
                    "function": {"name": "ui_stream", "arguments": raw[position:position + 140]}}])
            yield ProviderStreamEvent("tool_calls", tool_calls=[
                {"id": "rail-mount", "type": "function", "function": {"name": "ui_stream", "arguments": raw}},
                {"id": "rail-complete", "type": "function", "function": {"name": "ui_stream", "arguments": json.dumps({"op": "complete", "block_id": "editorial", "base_revision": 0})}},
            ])
            yield ProviderStreamEvent("completed", finish_reason="tool_calls")

    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, Provider(), settings=settings)
        service._build_fast_preface_provider = lambda: None
        return service

    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "列表流式验收"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers, json={"content": "介绍回答模式和操作步骤", "agent_mode": True})
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == "message.completed", response.text[-5000:]
    updates = [event["payload"] for event in events if event.get("payload", {}).get("ui_operation")]
    operations = [update["ui_operation"] for update in updates]
    assert operations[0] == "ui.preview" and operations[-2:] == ["ui.mount", "ui.complete"]
    assert len({update["part"]["id"] for update in updates}) == 1
    assert any(0 < len(node["text"]) < len(command["upsert"]["blocks"][1]["text"])
               for update in updates if update["ui_operation"] == "ui.preview" for node in update["part"]["data"]["blocks"] if node["id"] == "fast")
    history = next(message for message in client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"] if message["id"] == events[-1]["message_id"])
    part = next(part for part in history["parts"] if part["type"] == "ui")
    assert part["id"] == updates[0]["part"]["id"] and part["data"]["preview"] is None
    assert part["data"]["blocks"][1]["icon"] == "zap"
    assert part["data"]["blocks"][-1]["item_status"] == "completed"
    assert {"feature_list", "step_list", "status_list"} <= {node["type"] for node in part["data"]["blocks"]}
    # Tool-stage narration is a durable text Part; final content is a projection
    # of the final answer. The renderer restores the ordered Parts together.
    prose = "".join(item.get("content") or "" for item in history["parts"] if item["type"] == "text")
    assert "先解释背景" in prose and "后续正文" in prose
