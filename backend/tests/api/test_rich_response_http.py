import json

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.services.chat import ChatService

FALLBACK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg>'



DOCUMENT = {"props": {"title": "芝诺悖论"}, "upsert": {"blocks": [
    {"id": "columns", "type": "media_text"},
    {"id": "explain", "type": "paragraph", "parent_id": "columns", "text": "有限时间可以包含无限细分的步骤。"},
    {"id": "chase", "type": "simulation", "parent_id": "columns", "engine": "chase"},
    {"id": "image", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "Achilles tortoise", "alt": "追赶示意"},
]}}


class RichProvider:
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

    def __init__(self, mode):
        self.supports_structured_chat = mode != "legacy"
        self.agent = mode == "agent"

    def stream_answer(self, prompt):
        assert ":::ui" in prompt
        text = "先解释有限和无限。\n:::ui chase\n" + json.dumps(DOCUMENT, ensure_ascii=False) + "\n:::\n所以追赶者会相遇。"
        yield from text  # deliberately split at every character, including delimiters

    def stream_chat(self, messages, **kwargs):
        self.last_messages = messages
        instructions = "\n".join(message.content or "" for message in messages if message.role == "system")
        # Agent tools and text declarations must not advertise two publication
        # lanes at once: a real model duplicated a chart as an empty document.
        assert (":::ui" in instructions) is not self.agent
        if self.agent:
            assert "ui_stream" in instructions
        if self.agent and messages[-1].role != "tool":
            yield ProviderStreamEvent("text_delta", content="先解释有限和无限。")
            yield ProviderStreamEvent("tool_calls", tool_calls=[{"id": "ui1", "type": "function", "function": {
                "name": "ui_stream", "arguments": json.dumps({"op": "mount", "component": "document", "block_id": "chase", **DOCUMENT})}}])
            yield ProviderStreamEvent("completed", finish_reason="tool_calls")
        elif self.agent:
            yield ProviderStreamEvent("text_delta", content="[[artifact:block_id:chase]]所以追赶者会相遇。")
            yield ProviderStreamEvent("completed", finish_reason="stop")
        else:
            for chunk in self.stream_answer(":::ui"):
                yield ProviderStreamEvent("text_delta", content=chunk)
            yield ProviderStreamEvent("completed", finish_reason="stop")


@pytest.mark.parametrize("mode", ["fast", "legacy", "agent"])
def test_same_durable_renderer_lane_in_all_modes(client, register_user, auth_headers, monkeypatch, mode):
    from app.api.routers import chat as router
    provider = RichProvider(mode)
    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, provider, settings=settings)
        service._build_fast_preface_provider = lambda: None
        return service
    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "rich response"}).json()["id"]
    def send(url, payload):
        response = client.post(url, headers=headers, json=payload)
        assert response.status_code == 200, response.text
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
        assert events[-1]["type"] == "message.completed", response.text[-6000:]
        ui = [event for event in events if event.get("payload", {}).get("part", {}).get("type") == "ui"]
        assert len({event["part_id"] for event in ui}) == 1
        assert all(event["payload"]["part"]["data"]["component"] == "document" for event in ui)
        return events
    events = send(f"/api/v1/sessions/{sid}/messages/stream", {"content": "解释芝诺悖论", "agent_mode": mode == "agent"})
    items = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"]
    history = next(item for item in items if item["id"] == events[-1]["message_id"])
    assert ":::ui" not in history["content"] and "[[artifact:" not in history["content"]
    document = next(part for part in history["parts"] if part["type"] == "ui")
    assert document["data"]["state"] == "completed"
    assert document["data"]["blocks"][-1]["resource_state"] == "unavailable"
    assert len(document["data"]["blocks"]) == 4
    assert document["data"]["blocks"][-1]["resource_reason"] == "disabled"
    assert document["data"]["blocks"][-1]["fallback_svg"] == FALLBACK_SVG
    ordered = sorted(history["parts"], key=lambda part: part["sequence"])
    position = ordered.index(document)
    assert "所以追赶者会相遇" in ordered[position + 1]["content"]
    replay = client.get(f"/api/v1/sessions/{sid}/messages/{history['id']}/events", headers=headers)
    assert replay.status_code == 200
    # Regeneration uses the same protocol and creates a fresh message version.
    send(f"/api/v1/sessions/{sid}/messages/{history['id']}/retry", {})
    if mode != "legacy":
        send(f"/api/v1/sessions/{sid}/messages/stream", {"content": "刚才为什么没有图片？", "agent_mode": mode == "agent"})
        notes = [message.content for message in provider.last_messages if message.role == "system" and "服务端图片资源记录" in (message.content or "")]
        assert len(notes) == 1
        assert '"reason": "disabled"' in notes[0]
        assert "不要把查不到或下载失败说成没有图库权限" in notes[0]


@pytest.mark.parametrize("lock_once", [False, True])
def test_fast_mode_resolves_images_without_an_agent_runtime(client, register_user, auth_headers, monkeypatch, lock_once):
    from app.api.routers import chat as router
    import app.services.rich_media as media
    queries = []
    eager_reads = []
    from app.services.session_workspace import SessionWorkspaceService
    from sqlalchemy.exc import OperationalError
    import sqlite3
    original_put = SessionWorkspaceService.put_bytes
    saves = []
    def put(self, **kwargs):
        saves.append(1)
        if lock_once and len(saves) == 1:
            raise OperationalError('INSERT image blob', {}, sqlite3.OperationalError('database is locked'))
        return original_put(self, **kwargs)
    monkeypatch.setattr(SessionWorkspaceService, 'put_bytes', put)
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    content = BytesIO()
    Image.new("RGB", (4, 3), "blue").save(content, format="PNG")
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", lambda *_a, **_kw: SimpleNamespace(data=content.getvalue()))
    class Images:
        def image_search(self, query):
            queries.append(query)
            return [{"url": "https://images.example.com/chase.png", "source_url": "https://example.com/chase", "title": "追赶图", "attribution": "CC BY"}]
    provider = RichProvider("fast")
    provider.search_route = "model_native"
    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, provider, settings=settings,
                              image_search_provider_loader=lambda: Images())
        service._build_fast_preface_provider = lambda: None
        original_encode = service._encode_event
        def encode(event):
            if event.payload.get("ui_operation") == "ui.resource":
                for node in event.payload["part"]["data"].get("blocks", []):
                    for item in node.get("media", []):
                        # A separate HTTP request races the very first event,
                        # before any later text batch or final placement.
                        status = client.get(f"/api/v1/files/{item['file_id']}/stream-ticket", headers=headers).status_code
                        eager_reads.append(status)
                        assert status == 200
            return original_encode(event)
        service._encode_event = encode
        return service
    monkeypatch.setattr(router, "service", factory)
    monkeypatch.setattr(media, "require_public_http_url", lambda *_: "93.184.216.34")
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "image resource"}).json()["id"]
    result = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                         json={"content": "解释追赶", "web_search": True})
    assert result.status_code == 200
    events = [json.loads(line[5:]) for line in result.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == "message.completed", result.text[-3000:]
    resources = [event for event in events if event.get("payload", {}).get("ui_operation") == "ui.resource"]
    assert len(resources) == 1 and queries == ["Achilles tortoise"]
    assert eager_reads == [200]
    assert len(saves) == (2 if lock_once else 1)
    part = resources[0]["payload"]["part"]
    assert part["data"]["resource_revision"] == 1
    assert part["data"]["blocks"][-1]["media"][0]["title"] == "追赶图"
    file_id = part["data"]["blocks"][-1]["media"][0]["file_id"]
    image = client.get(f"/api/v1/files/{file_id}/content", headers=headers)
    assert image.status_code == 200 and image.headers["content-type"].startswith("image/png")
    assert client.get(f"/api/v1/files/{file_id}/content").status_code == 401
    other_token, other_workspace, _, _ = register_user()
    assert client.get(f"/api/v1/files/{file_id}/content", headers=auth_headers(other_token, other_workspace)).status_code == 404
    assert "_data" not in json.dumps(part)
    ui_parts = [event["payload"]["part"] for event in events if event.get("payload", {}).get("ui_operation")]
    assert len({item["id"] for item in ui_parts}) == 1
    assert [item["data"]["revision"] for item in ui_parts][-1] == 2
    followup = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                          json={"content": "刚才图片怎么样？", "web_search": False})
    assert followup.status_code == 200
    note = next(message.content for message in provider.last_messages if message.role == "system" and "服务端图片资源记录" in (message.content or ""))
    assert '"reason": "ready"' in note and file_id in note
    assert "https://" not in note and "_data" not in note
    assert "记录未区分具体阶段" in note


@pytest.mark.parametrize("mode", ["fast", "agent"])
def test_empty_search_is_replayed_as_no_results_not_a_permission_error(client, register_user, auth_headers, monkeypatch, mode):
    from app.api.routers import chat as router
    class EmptyImages:
        def image_search(self, query):
            return []
    provider = RichProvider(mode)
    provider.search_route = "model_native"
    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, provider, settings=settings,
                              image_search_provider_loader=lambda: EmptyImages())
        service._build_fast_preface_provider = lambda: None
        return service
    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "empty search"}).json()["id"]
    for content in ["解释追赶", "刚才怎么没有图片？"]:
        response = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                               json={"content": content, "web_search": True, "agent_mode": mode == "agent"})
        assert response.status_code == 200 and 'message.completed' in response.text
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
        resources = [event["payload"]["part"] for event in events if event.get("payload", {}).get("ui_operation") == "ui.resource"]
        assert resources[-1]["data"]["blocks"][-1]["fallback_svg"] == FALLBACK_SVG
        items = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"]
        history = next(item for item in items if item["id"] == events[-1]["message_id"])
        document = next(part for part in history["parts"] if part["type"] == "ui")
        assert document["data"]["blocks"][-1]["fallback_svg"] == FALLBACK_SVG
    notes = [m.content for m in provider.last_messages if m.role == "system" and "服务端图片资源记录" in (m.content or "")]
    assert len(notes) == 1 and '"reason": "no_results"' in notes[0]
    assert '"state": "unavailable"' in notes[0] and '"files": []' in notes[0]
    assert '"fallback": "svg"' in notes[0]
