"""Real HTTP/persistence path with deterministic upstream/tool fixtures."""
from __future__ import annotations

import json

import httpx
import pytest


def frames(response):
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


@pytest.mark.parametrize("tool_name,provider_label", [("browser_read_page", "隔离浏览器"), ("lg_mcp_server_lookup", "知识库 MCP")])
def test_native_turn_with_page_sources_and_no_external_search_provider(
    client, register_user, auth_headers, stub_chat_service, monkeypatch, tool_name, provider_label,
):
    from app.api.routers import chat as router
    from app.providers.ports.model import ProviderStreamEvent
    from app.services.chat import ChatService

    def factory(*args, **kwargs):
        service = stub_chat_service(*args, **kwargs)
        base = type(service.model_provider)

        class Provider(base):
            provider_type = "codex_chatgpt"
            search_route = "model_native"
            supports_structured_chat = True
            last_sources = []
            turns = 0

            def stream_chat(self, messages, **options):
                self.turns += 1
                if self.turns == 1:
                    yield ProviderStreamEvent("web_activity", web_activity={
                        "id": "native-1", "status": "streaming", "operation": "search",
                        "provider_label": "OpenAI", "provider_id": "stub-provider", "native": True,
                    })
                    yield ProviderStreamEvent("web_activity", web_activity={
                        "id": "native-1", "status": "completed", "operation": "search",
                        "provider_label": "OpenAI", "provider_id": "stub-provider", "native": True,
                    })
                    yield ProviderStreamEvent("tool_calls", tool_calls=[{
                        "id": "read-1", "type": "function", "function": {"name": tool_name,
                        "arguments": '{"url":"https://example.org/page"}'},
                    }])
                else:
                    yield ProviderStreamEvent("text_delta", content="返回的网页正文支持该结论（网页引用：1）。")
                yield ProviderStreamEvent("completed", finish_reason="stop")

        service.model_provider = Provider()
        assert service.search_provider is None
        return service

    monkeypatch.setattr(router, "service", factory)
    monkeypatch.setattr(ChatService, "_agent_tool_definitions", lambda *_a, **_k: [{"type": "function", "function": {
        "name": tool_name, "description": "read one page", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
    }}])
    monkeypatch.setattr(ChatService, "_agent_skill_package_instructions", lambda *_a, **_k: "")
    monkeypatch.setattr(ChatService, "_web_tool_metadata", lambda *_a, **_k: {"web_activity": {
        "operation": "fetch", "provider_label": provider_label, "provider_id": "reader",
    }})
    monkeypatch.setattr(ChatService, "_execute_agent_tool", lambda *_a, **_k: (
        json.dumps({"url": "https://example.org/page", "content": "The actual returned body."}),
        {"status": "completed", "provider_id": "reader", "provider_label": provider_label, "page_read": True},
        [{"url": "https://example.org/page", "title": "Read page", "provider_id": "reader",
          "provider_label": provider_label, "page_read": True, "evidence_kind": "page_text"}],
    ))
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    response = client.post("/api/v1/sessions", headers=headers, json={"title": "Web activity contract"})
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
        json={"content": "检索并读取页面", "agent_mode": True, "web_search": True, "search_route": "model_native"})
    assert response.status_code == 200, response.text
    events = frames(response)
    assert "message.failed" not in [e.get("type") for e in events], response.text[-5000:]
    assert "message.completed" in [e.get("type") for e in events]
    rows = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"]
    answer = next(row for row in rows if row["role"] == "assistant")
    native = [p for p in answer["parts"] if p.get("data", {}).get("web_activity", {}).get("native")]
    assert native and native[0]["status"] == "completed"
    source_part = next(p for p in answer["parts"] if p["type"] == "source_list")
    assert source_part["data"]["results"][0].get("page_read") is True, source_part
    assert source_part["data"]["results"][0]["index"] == 1
    assert source_part["data"]["results"][0]["provider_label"] == provider_label
    response = client.post(f"/api/v1/sessions/{sid}/messages/{answer['id']}/retry", headers=headers,
        json={"agent_mode": True, "web_search": True, "search_route": "model_native"})
    assert response.status_code == 200, response.text
    assert "message.failed" not in [e.get("type") for e in frames(response)], response.text[-5000:]
    assert "message.completed" in [e.get("type") for e in frames(response)]


def test_responses_stream_annotation_survives_send_retry_and_reload(
    client, register_user, auth_headers, stub_chat_service, monkeypatch,
):
    from app.api.routers import chat as router
    from app.providers.remote.openai import OpenAIResponsesProvider

    text = "来源已经核对。"
    url = "https://example.org/verified"
    events = [
        {"type": "response.output_text.delta", "delta": text},
        {"type": "response.output_text.annotation.added", "annotation": {
            "type": "url_citation", "url": url, "title": "Verified", "start_index": 0, "end_index": len(text),
        }},
        {"type": "response.completed", "response": {"status": "completed", "output": [{
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}],
        }]}},
    ]

    def factory(*args, **kwargs):
        service = stub_chat_service(*args, **kwargs)
        service.model_provider = OpenAIResponsesProvider(
            provider_id=service.model_provider.provider_id, provider_type="openai_responses",
            model_id=service.model_provider.model_id, base_url="https://example.org/v1", api_key="test-key",
            transport=httpx.MockTransport(lambda _: httpx.Response(
                200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events),
            )),
        )
        return service

    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "Stream annotations"}).json()["id"]
    path = f"/api/v1/sessions/{sid}/messages"

    def assert_citation(response):
        assert response.status_code == 200, response.text
        stream = frames(response)
        assert "message.completed" in [event.get("type") for event in stream], response.text[-5000:]
        assert any(event["type"] == "part.replaced" and event["payload"].get("reason") == "native_web_citations" for event in stream)
        answer = next(row for row in client.get(path, headers=headers).json()["items"] if row["role"] == "assistant")
        assert answer["content"] == text + "（网页引用：1）"
        source_part = next(part for part in answer["parts"] if part["type"] == "source_list")
        assert [(source["index"], source["url"]) for source in source_part["data"]["results"]] == [(1, url)]
        return answer["id"]

    answer_id = assert_citation(client.post(f"{path}/stream", headers=headers, json={"content": "检索来源"}))
    assert_citation(client.post(f"{path}/{answer_id}/retry", headers=headers, json={}))
