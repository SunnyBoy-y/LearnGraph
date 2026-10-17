import json

import httpx
import pytest

from app.providers.ports.model import ProviderChatMessage
from app.providers.remote.anthropic import AnthropicMessagesProvider
from app.providers.remote.deepseek import DeepSeekChatProvider
from app.providers.remote.openai import OpenAICompatibleChatProvider


@pytest.mark.parametrize("provider_type", [OpenAICompatibleChatProvider, DeepSeekChatProvider])
def test_chat_adapter_exposes_arguments_before_completed_call(provider_type):
    pieces = ['{"path":"work/a.py","content":"', 'print(42)\\n"}']
    chunks = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call", "function": {
        "name": "sandbox_write_file", "arguments": pieces[0]}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": pieces[1]}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}]
    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    provider = provider_type(provider_id="p", model_id="model", base_url="https://example.com/v1", api_key="test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    events = provider.stream_chat([ProviderChatMessage(role="user", content="write file")])
    first = next(events)
    assert first.type == "tool_call_delta" and first.tool_calls[0]["function"]["arguments"] == pieces[0]
    remaining = list(events)
    delta = next(e for e in remaining if e.type == "tool_call_delta")
    assert delta.tool_calls[0]["id"] == "call"
    assert delta.tool_calls[0]["function"]["name"] == "sandbox_write_file"
    completed = next(e for e in remaining if e.type == "tool_calls")
    assert completed.tool_calls[0]["function"]["arguments"] == "".join(pieces)
    provider.reset_transport()


@pytest.mark.parametrize("tool_name", ["sandbox_write_file", "ui_stream"])
def test_anthropic_exposes_json_fragments_and_requests_eager_input(tool_name):
    fragments = ['{"path":"work/a.py",', '"content":"print(42)"}']
    packets = [
        ("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "call", "name": tool_name, "input": {}}}),
        *[("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": part}}) for part in fragments],
        ("message_stop", {}),
    ]
    body = "".join(f"event: {kind}\ndata: {json.dumps({'type': kind, **value})}\n\n" for kind, value in packets)
    def respond(request):
        payload = json.loads(request.content)
        assert payload["tools"][0]["eager_input_streaming"] is True
        return httpx.Response(200, text=body)
    provider = AnthropicMessagesProvider(provider_id="p", model_id="model", base_url="https://example.com", api_key="test",
        transport=httpx.MockTransport(respond))
    events = list(provider.stream_chat([ProviderChatMessage(role="user", content="write")], tools=[{
        "type": "function", "function": {"name": tool_name, "parameters": {"type": "object", "properties": {}}},
    }]))
    assert [e.type for e in events] == ["tool_call_delta", "tool_call_delta", "tool_calls", "completed"]
    assert "".join(e.tool_calls[0]["function"]["arguments"] for e in events if e.type == "tool_call_delta") == "".join(fragments)
    provider.reset_transport()
