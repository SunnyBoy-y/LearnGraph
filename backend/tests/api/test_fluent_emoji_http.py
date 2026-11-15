"""Real StreamUI preview/commit/history/replay keep same-origin icon identifiers."""
import json

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.chat import ChatService
from test_stream_ui_http import UIProvider


@pytest.mark.parametrize("runtime", [False, True])
@pytest.mark.parametrize("component", ["document", "metric", "timeline"])
def test_fluent_icons_persist_through_preview_patch_complete_and_replay(
    client, register_user, auth_headers, monkeypatch, runtime, component,
):
    from app.api.routers import chat as router

    if component == "document":
        collection = "blocks"
        initial = [
            {"id": "section", "type": "section", "label": "Section", "icon": "fluent:books"},
            {"id": "heading", "type": "heading", "parent_id": "section", "text": "Launch", "icon": "fluent:light-bulb"},
            {"id": "standalone", "type": "icon", "parent_id": "section", "icon": "fluent:rocket"},
            {"id": "list", "type": "feature_list", "parent_id": "section"},
            {"id": "rail", "type": "rail_item", "parent_id": "list", "label": "Think", "icon": "fluent:brain"},
            {"id": "metric", "type": "metric", "visual": {"props": {"title": "Count", "icon": "fluent:chart-increasing"},
                "items": [{"id": "count", "label": "Count", "value": 1, "icon": "fluent:direct-hit"}]}},
        ]
        addition = {"id": "later", "type": "paragraph", "text": "Done 🚀", "icon": "fluent:check-mark-button"}
    else:
        collection = "items"
        initial = [{"id": "first", "label": "First", "value": 1, "date": "2026-01-01", "icon": "fluent:rocket"},
                   {"id": "second", "label": "Second", "value": 2, "icon": "brain-circuit"}]
        addition = {"id": "later", "label": "Later", "value": 3, "icon": "fluent:check-mark-button"}

    commands = [
        {"op": "mount", "block_id": "icons", "component": component,
         "props": {"title": "Fluent icons", "icon": "fluent:sparkles"}, "upsert": {collection: initial}},
        {"op": "patch", "block_id": "icons", "base_revision": 0,
         "props": {"description": "Updated"}, "upsert": {collection: [addition]}},
        {"op": "complete", "block_id": "icons", "base_revision": 1},
    ]

    class Provider(UIProvider):
        step = 0

        def stream_chat(self, messages, **kwargs):
            if self.step < len(commands):
                command = commands[self.step]
                self.step += 1
                raw = json.dumps(command)
                call_id = f"icon_{self.step}"
                # Split after a completed first record for a persisted preview;
                # metric measurements stay withheld until the normal commit.
                if command["op"] != "complete":
                    split = raw.index('}, {') + 1 if '}, {' in raw else len(raw) - 2
                    for piece in (raw[:split], raw[split:]):
                        yield ProviderStreamEvent("tool_call_delta", tool_calls=[{"id": call_id, "index": 0,
                            "function": {"name": "ui_stream", "arguments": piece}}])
                yield ProviderStreamEvent("tool_calls", tool_calls=[{"id": call_id, "type": "function",
                    "function": {"name": "ui_stream", "arguments": raw}}])
                yield ProviderStreamEvent("completed", finish_reason="tool_calls")
            else:
                yield ProviderStreamEvent("text_delta", content="Done 🚀 [[artifact:block_id:icons]]")
                yield ProviderStreamEvent("completed", finish_reason="stop")

    provider = Provider()

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
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "Fluent icons"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                           json={"content": "Show the supplied information", "agent_mode": True, "web_search": False})
    assert response.status_code == 200, response.text
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == "message.completed", response.text[-6000:]
    updates = [event["payload"] for event in events if event.get("payload", {}).get("ui_operation")]
    previews = [update["part"]["data"] for update in updates if update["ui_operation"] == "ui.preview"]
    assert previews and all(snapshot["props"]["icon"] == "fluent:sparkles" for snapshot in previews)
    commits = [update for update in updates if update["ui_operation"] in {"ui.mount", "ui.patch", "ui.complete"}]
    assert [update["ui_operation"] for update in commits] == ["ui.mount", "ui.patch", "ui.complete"]
    assert [update["part"]["data"]["revision"] for update in commits] == [0, 1, 2]
    mounted, patched, completed = [update["part"]["data"] for update in commits]
    assert patched[collection][:-1] == mounted[collection]
    assert completed["props"]["icon"] == "fluent:sparkles" and completed["preview"] is None
    assert completed[collection][-1]["icon"] == "fluent:check-mark-button"
    assert completed["state"] == "completed"
    if component == "document":
        by_id = {node["id"]: node for node in completed["blocks"]}
        assert by_id["heading"]["icon"] == "fluent:light-bulb"
        assert by_id["standalone"]["icon"] == "fluent:rocket"
        assert by_id["rail"]["icon"] == "fluent:brain"
        assert by_id["metric"]["visual"]["items"][0]["icon"] == "fluent:direct-hit"
        assert any(any(node["id"] == "standalone" and node["icon"] == "fluent:rocket"
                       for node in snapshot["blocks"]) for snapshot in previews)
    mid = events[-1]["message_id"]
    history = next(item for item in client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"]
                   if item["id"] == mid)
    durable = next(part for part in history["parts"] if part["type"] == "ui")
    # The later artifact reference legitimately resolves the display layout.
    assert durable["data"] == {**completed, "layout": "block"}
    replay = client.get(f"/api/v1/sessions/{sid}/messages/{mid}/events", headers=headers).json()
    assert any(event.get("payload", {}).get("ui_operation") == "ui.preview" for event in replay)
    replayed = next(event["payload"]["part"]["data"] for event in replay
                    if event.get("payload", {}).get("ui_operation") == "ui.complete")
    assert replayed == completed
