"""Mode changes exercise the real router, history, SSE and durable UI lane.

The provider is deterministic here; the separate live script uses the configured
remote provider against a disposable database backup.
"""
import json

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.chat import ChatService


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="35" fill="green"/></svg>'
DOCUMENT = {"props": {"title": "西瓜"}, "upsert": {"blocks": [
    {"id": "explain", "type": "paragraph", "text": "西瓜通常外皮绿色，果肉红色。"},
    {"id": "picture", "type": "image", "query": "watermelon", "fallback_svg": SVG},
]}}
LEAK = '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="ui_stream"><｜｜DSML｜｜ parameter name="op">mount</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'


@pytest.mark.parametrize("structured", [True, False])
def test_high_fast_retry_high_preserves_content_without_execution_state(
    client, register_user, auth_headers, monkeypatch, structured,
):
    from app.api.routers import chat as router

    requests = []

    class Provider:
        available = remote_capability = supports_agent_tools = True
        supports_structured_chat = True
        provider_id = "mode-switch-provider"
        provider_type = "unknown"
        model_id = "mode-switch-model"
        search_route = "disabled"
        last_usage = {}

        def stream_chat(self, messages, *, tools=None):
            requests.append((self.thinking_mode, messages, tools))
            if self.thinking_mode == "high":
                yield ProviderStreamEvent("reasoning_delta", content="prior reasoning continuation")
                if messages[-1].role != "tool":
                    yield ProviderStreamEvent("tool_calls", tool_calls=[{
                        "id": "draw", "type": "function", "function": {
                            "name": "ui_stream", "arguments": json.dumps({
                                "op": "mount", "block_id": "watermelon", "component": "document", **DOCUMENT,
                            })},
                    }])
                    yield ProviderStreamEvent("completed", finish_reason="tool_calls", response_items=[{
                        "type": "reasoning", "encrypted_content": "opaque-prior-execution-state",
                    }])
                else:
                    yield ProviderStreamEvent("text_delta", content="前一轮的西瓜说明已经完成。")
                    yield ProviderStreamEvent("completed", finish_reason="stop", response_items=[{
                        "type": "reasoning", "encrypted_content": "opaque-final-execution-state",
                    }])
            else:
                for char in LEAK:
                    yield ProviderStreamEvent("text_delta", content=char)
                # Even a provider ignoring tools=None must never enter execution.
                yield ProviderStreamEvent("tool_calls", tool_calls=[{
                    "id": "forbidden", "type": "function", "function": {
                        "name": "ui_stream", "arguments": json.dumps({
                            "op": "mount", "component": "document", "block_id": "forbidden", **DOCUMENT,
                        })},
                }])
                yield ProviderStreamEvent("text_delta", content="\n:::ui fast_picture\n" + json.dumps(DOCUMENT) + "\n:::\n这是西瓜示意图。")
                yield ProviderStreamEvent("completed", finish_reason="tool_calls")

        def stream_answer(self, prompt):
            requests.append((self.thinking_mode, prompt, None))
            yield LEAK
            yield "\n:::ui fast_picture\n" + json.dumps(DOCUMENT) + "\n:::\n这是西瓜示意图。"

    def factory(db, context, settings, **kwargs):
        provider = Provider()
        provider.thinking_mode = kwargs.get("thinking_mode") or "off"
        provider.actual_reasoning_effort = None if provider.thinking_mode == "off" else provider.thinking_mode
        provider.supports_structured_chat = structured or provider.thinking_mode == "high"
        service = ChatService(db, context.workspace_id, context.principal.user_id, provider, settings=settings)
        service._build_fast_preface_provider = lambda: None
        return service

    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "high-fast-high", "memory_enabled": False}).json()["id"]

    def send(suffix, payload):
        result = client.post(f"/api/v1/sessions/{sid}/{suffix}", headers=headers, json=payload)
        assert result.status_code == 200, result.text
        events = [json.loads(line[5:]) for line in result.text.splitlines() if line.startswith("data:")]
        assert events[-1]["type"] == "message.completed", result.text[-3000:]
        items = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"]
        return next(item for item in items if item["id"] == events[-1]["message_id"]), events

    first, _ = send("messages/stream", {"content": "我不知道什么是西瓜，画一画。", "agent_mode": True, "thinking_mode": "high"})
    # Simulate an old completed message from before this repair. It must remain
    # inspectable in storage but cannot teach the next direct turn its protocol.
    from app.core.database import SessionLocal
    from app.domain.models import MessagePartRecord
    from sqlalchemy import select
    with SessionLocal() as db:
        part = db.scalar(select(MessagePartRecord).where(
            MessagePartRecord.message_id == first["id"], MessagePartRecord.part_type == "text",
        ))
        part.content += LEAK
        db.commit()
    first_fast_request = len(requests)
    fast, events = send("messages/stream", {"content": "继续解释刚才的西瓜，保留原生图文。", "agent_mode": False, "thinking_mode": "off"})
    for _, transcript, tools in requests[first_fast_request:]:
        assert tools is None
        if isinstance(transcript, list):
            assert all(message.role != "tool" and not message.tool_calls and not message.reasoning_content and not message.response_items for message in transcript)
            assert any("前一轮的西瓜说明" in (message.content or "") for message in transcript)
    for result in [fast, send(f"messages/{fast['id']}/retry", {"agent_mode": False, "thinking_mode": "off"})[0]]:
        serialized = json.dumps(result, ensure_ascii=False)
        assert "DSML" not in serialized and '"forbidden"' not in serialized
        ui = [part for part in result["parts"] if part["type"] == "ui" and part["data"].get("state") != "removed"]
        assert len(ui) == 1 and ui[0]["data"]["state"] == "completed"
        assert ui[0]["data"]["blocks"][1]["fallback_svg"] == SVG
        replay = client.get(f"/api/v1/sessions/{sid}/messages/{result['id']}/events", headers=headers)
        assert replay.status_code == 200 and "DSML" not in replay.text
    assert "DSML" not in json.dumps(events, ensure_ascii=False)
    send("messages/stream", {"content": "再详细讲一下。", "agent_mode": True, "thinking_mode": "high"})
    assert any(message.tool_calls for message in requests[-1][1])
    assert any(message.reasoning_content for message in requests[-1][1])
    assert any(message.response_items for message in requests[-1][1])
    for thinking, transcript, tools in requests:
        if thinking != "off":
            continue
        assert tools is None
        if isinstance(transcript, list):
            assert all(message.role != "tool" and not message.tool_calls and not message.reasoning_content and not message.response_items for message in transcript)
            assert "DSML" not in "".join(message.content or "" for message in transcript)
