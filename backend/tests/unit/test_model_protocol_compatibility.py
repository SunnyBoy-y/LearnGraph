"""No-network wire contracts across every generation adapter and call shape."""
from dataclasses import replace
import json

import httpx
import pytest

from app.providers.model_options import ModelCallOptions, ModelCapabilityError
from app.providers.ports.model import ProviderChatMessage
from app.providers.remote.anthropic import AnthropicMessagesProvider
from app.providers.remote.codex import CodexCredentials
from app.providers.remote.codex_provider import CodexResponsesProvider
from app.providers.remote.copilot import GitHubCopilotChatProvider, CopilotToken
from app.providers.remote.deepseek import DeepSeekChatProvider
from app.providers.remote.ollama import OllamaChatProvider
from app.providers.remote.ollama_cloud import OllamaCloudChatProvider
from app.providers.remote.openai import OpenAICompatibleChatProvider, OpenAIResponsesProvider, QwenChatProvider, ProviderResponseError


def options(**overrides):
    return replace(ModelCallOptions("off", "none", "reasoning_effort", "disabled", False, {}), **overrides)


def make_provider(cls, **overrides):
    return cls(**{"provider_id": "audit", "model_id": "private-audit", "base_url": "https://relay.invalid/v1",
                  "api_key": "test-key-not-real", "max_output_tokens": 8192, "call_options": options(), **overrides})


def test_relay_qwen_does_not_add_private_fields_to_an_explicit_none_mapping():
    p = make_provider(QwenChatProvider)
    body = p._apply_call_options({}, responses=False)
    assert body["reasoning_effort"] == "none"
    assert "enable_thinking" not in body
    assert "preserve_thinking" not in body


def test_responses_does_not_silently_ignore_an_incompatible_numeric_budget():
    p = make_provider(OpenAIResponsesProvider, call_options=options(thinking_mode="high", actual_reasoning_effort=4096,
                                                                 reasoning_parameter="thinking_budget", provider_options={"thinking_budget": 4096}))
    with pytest.raises(ProviderResponseError, match="Responses requires"):
        p._apply_call_options({}, responses=True)


def test_adaptive_anthropic_preserves_a_declared_max_effort():
    p = make_provider(AnthropicMessagesProvider, model_id="claude-opus-5",
                      call_options=options(thinking_mode="xhigh", actual_reasoning_effort="max"))
    assert p._apply_call_options({}, responses=False)["output_config"]["effort"] == "max"


def test_native_search_keeps_the_output_cap_for_supported_native_controls():
    p = make_provider(QwenChatProvider, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        call_options=options(actual_reasoning_effort=False, reasoning_parameter="enable_thinking",
                             search_route="model_native", native_web_search=True, provider_options={"enable_thinking": False}))
    assert p._use_native_dashscope([])
    body = p._native_dashscope_body([])["parameters"]
    assert body["max_tokens"] == 8192
    assert body["enable_thinking"] is False


@pytest.mark.parametrize("preserve", [False, True])
def test_precise_controls_fall_back_to_compatible_search_instead_of_being_lost(preserve):
    p = make_provider(QwenChatProvider, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        preserve_thinking=preserve, call_options=options(thinking_mode="high", actual_reasoning_effort="max",
                                                    search_route="model_native", native_web_search=True))
    assert p._use_native_dashscope([]) is False
    body = p._apply_call_options({}, responses=False)
    assert body["reasoning_effort"] == "max" and body["max_tokens"] == 8192
    assert body["enable_search"] is True
    assert body.get("preserve_thinking", False) is preserve


@pytest.mark.parametrize("model", ["claude-3-7-sonnet-20250219", "claude-sonnet-4-20250514", "claude-sonnet-4-6", "claude-opus-4.6"])
def test_legacy_numeric_anthropic_budgets_are_kept_including_optional_adaptive_models(model):
    p = make_provider(AnthropicMessagesProvider, model_id=model,
        call_options=options(thinking_mode="low", actual_reasoning_effort=1234,
                             reasoning_parameter="thinking_budget", provider_options={"thinking_budget": 1234}))
    body = p._apply_call_options({"max_tokens": 8192}, responses=False)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 1234}


def test_anthropic_thinking_is_bounded_after_a_memory_task_reduces_output():
    p = make_provider(AnthropicMessagesProvider, model_id="claude-3-7-sonnet-20250219", max_output_tokens=2048,
        call_options=options(thinking_mode="high", actual_reasoning_effort=10000, reasoning_parameter="thinking_budget"))
    body = p._apply_call_options({"max_tokens": 2048}, responses=False)
    assert body["thinking"]["budget_tokens"] == 2047
    assert p.effective_reasoning_budget_tokens == 2047


def test_adaptive_only_anthropic_rejects_numeric_configuration_before_a_request():
    p = make_provider(AnthropicMessagesProvider, model_id="claude-opus-4-7",
        call_options=options(thinking_mode="low", actual_reasoning_effort=1234, reasoning_parameter="thinking_budget"))
    with pytest.raises(ModelCapabilityError, match="adaptive effort"):
        p._apply_call_options({}, responses=False)


def _sse(events):
    return "\n\n".join("data: " + json.dumps(e) for e in events) + "\n\ndata: [DONE]\n\n"


def response(request, body):
    if request.url.path.endswith("/responses"):
        output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": '{"ok":true}'}]}]
        final = {"id": "r-audit", "status": "completed", "output": output,
                 "usage": {"input_tokens": 11, "output_tokens": 2}}
        if not body.get("stream"):
            return httpx.Response(200, json=final)
        return httpx.Response(200, text=_sse([
            {"type": "response.output_text.delta", "delta": "ok"},
            {"type": "response.completed", "response": final},
        ]))
    if request.url.path.endswith("/messages"):
        if not body.get("stream"):
            return httpx.Response(200, json={"content": [{"type": "text", "text": '{"ok":true}'}],
                                            "usage": {"input_tokens": 11, "output_tokens": 2}})
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 11}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        return httpx.Response(200, text="\n\n".join(f"event: {e['type']}\ndata: {json.dumps(e)}" for e in events) + "\n\n")
    if request.url.path.endswith("/api/chat"):
        return httpx.Response(200, text="\n".join(json.dumps(e) for e in [
            {"message": {"role": "assistant", "content": "ok"}, "done": False},
            {"done": True, "done_reason": "stop", "prompt_eval_count": 11, "eval_count": 2},
        ]))
    if not body.get("stream"):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}],
                                        "usage": {"prompt_tokens": 11, "completion_tokens": 2}})
    return httpx.Response(200, text=_sse([
        {"choices": [{"delta": {"content": "ok"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 11, "completion_tokens": 2}},
    ]))


ADAPTERS = [
    (OpenAICompatibleChatProvider, "https://relay.invalid/v1", "max_tokens"),
    (OpenAICompatibleChatProvider, "https://api.openai.com/v1", "max_completion_tokens"),
    (QwenChatProvider, "https://dashscope.aliyuncs.com/compatible-mode/v1", "max_tokens"),
    (DeepSeekChatProvider, "https://api.deepseek.com/v1", "max_tokens"),
    (OpenAIResponsesProvider, "https://api.openai.com/v1", "max_output_tokens"),
    (AnthropicMessagesProvider, "https://api.anthropic.com", "max_tokens"),
    (OllamaChatProvider, "http://localhost:11434/v1", "max_tokens"),
    (OllamaCloudChatProvider, "https://ollama.com/v1", "num_predict"),
    (GitHubCopilotChatProvider, "https://api.githubcopilot.com", "max_tokens"),
    (CodexResponsesProvider, "https://chatgpt.com/backend-api/codex", "max_output_tokens"),
]


@pytest.mark.parametrize("cls,url,field", ADAPTERS)
@pytest.mark.parametrize("shape", ["chat", "tools", "structured", "answer"])
def test_actual_request_builders_across_protocol_and_call_shape(cls, url, field, shape):
    bodies = []
    def handle(request):
        body = json.loads(request.content)
        bodies.append(body)
        return response(request, body)
    extra = {"transport": httpx.MockTransport(handle), "base_url": url}
    if cls is CodexResponsesProvider:
        extra["credentials"] = CodexCredentials("test-access-not-real", "test-refresh-not-real", account_id="test-account-not-real")
    p = make_provider(cls, **extra)
    if cls is GitHubCopilotChatProvider:
        p._copilot_token = CopilotToken(value="test-copilot-not-real", expires_at=float("inf"))
    if shape == "structured":
        if cls is OllamaCloudChatProvider:
            with pytest.raises(ProviderResponseError, match="does not support structured"):
                p.generate_json("json", "Result", {"type": "object"})
            assert not bodies
            return
        assert p.generate_json("json", "Result", {"type": "object"}) == {"ok": True}
    elif shape == "answer":
        assert "".join(p.stream_answer("hello")) == "ok"
    else:
        tools = [{"type": "function", "function": {"name": "audit", "description": "audit", "parameters": {"type": "object", "properties": {}}}}] if shape == "tools" else None
        assert list(p.stream_chat([ProviderChatMessage("user", "hello")], tools=tools))
    assert len(bodies) == 1
    body = bodies[0]
    if cls is CodexResponsesProvider:
        assert body["stream"] is True and isinstance(body["input"], list)
        assert not ({"max_tokens", "max_completion_tokens", "max_output_tokens"} & set(body))
        assert p.capabilities["output_budget_enforcement"] == "upstream_default"
        assert p.capabilities["output_budget_notice"]
    elif field == "num_predict":
        assert body["options"]["num_predict"] == 8192
        assert "max_tokens" not in body
    else:
        assert body[field] == 8192
    if cls not in {QwenChatProvider, DeepSeekChatProvider, AnthropicMessagesProvider, OllamaChatProvider, OllamaCloudChatProvider}:
        assert "enable_thinking" not in body
        assert "thinking_budget" not in body
