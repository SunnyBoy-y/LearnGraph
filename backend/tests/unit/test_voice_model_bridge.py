"""Voice uses native generation adapters, not a parallel request dialect."""
import asyncio
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.providers.ports.model import ProviderStreamEvent
from app.providers.remote.openai import ProviderResponseError
from app.voice.model_bridge import ProviderChatClient


class FakeProvider:
    model_id = "first-model"
    max_output_tokens = 8192
    supports_agent_tools = True

    def __init__(self):
        self.calls = []
        self.last_usage = {}
        self.stop = Event()

    def reset_transport(self):
        self.stop.set()

    def stream_chat(self, messages, *, tools=None):
        self.calls.append((messages, tools, self.max_output_tokens))
        self.last_usage = {"input_tokens": 11, "output_tokens": 2, "reasoning_tokens": 1}
        yield ProviderStreamEvent("text_delta", content="hello")
        yield ProviderStreamEvent("completed", finish_reason="stop")


async def collect(client, **kwargs):
    stream = await client.create(messages=[{"role": "user", "content": "hi"}], **kwargs)
    return [chunk async for chunk in stream]


def test_voice_keeps_provider_budget_usage_and_can_rebind_actual_model():
    async def run():
        old, new = FakeProvider(), FakeProvider()
        new.model_id = "second-model"
        client = ProviderChatClient(old)
        chunks = await collect(client, max_tokens=2048)
        assert old.calls[0][2] == 2048
        assert old.max_output_tokens == 8192
        assert chunks[-1].usage.prompt_tokens == 11
        assert chunks[-1].usage.completion_tokens_details.reasoning_tokens == 1
        assert client.usage_emitted
        client._continuations["old-call"] = ("old reasoning", [])
        await client.rebind(new)
        assert not client._continuations
        await collect(client)
        assert len(old.calls) == len(new.calls) == 1
        assert client.provider.model_id == "second-model"
    asyncio.run(run())


def test_constructor_failure_propagates_instead_of_hanging_on_queue():
    async def run():
        provider = FakeProvider()
        def fail(*args, **kwargs):
            raise ValueError("constructor failed")
        provider.stream_chat = fail
        with pytest.raises(ValueError, match="constructor failed"):
            await asyncio.wait_for(collect(ProviderChatClient(provider)), timeout=1)
    asyncio.run(run())


def test_iterator_cleanup_failure_propagates_instead_of_hanging():
    class Iterator:
        def __iter__(self):
            return self
        def __next__(self):
            raise StopIteration
        def close(self):
            raise ValueError("close failed")
    async def run():
        provider = FakeProvider()
        provider.stream_chat = lambda *a, **k: Iterator()
        with pytest.raises(ValueError, match="close failed"):
            await asyncio.wait_for(collect(ProviderChatClient(provider)), timeout=1)
    asyncio.run(run())


def test_cancel_closes_transport_restores_budget_and_keeps_known_usage():
    async def run():
        provider = FakeProvider()
        def blocked(messages, *, tools=None):
            provider.last_usage = {"input_tokens": 321, "output_tokens": 1}
            yield ProviderStreamEvent("text_delta", content="first")
            provider.stop.wait(timeout=2)
            yield ProviderStreamEvent("completed")
        provider.stream_chat = blocked
        client = ProviderChatClient(provider)
        stream = await client.create(messages=[{"role": "user", "content": "hi"}], max_tokens=1024)
        assert (await anext(stream)).choices[0].delta.content == "first"
        await asyncio.wait_for(stream.aclose(), timeout=1)
        assert provider.stop.is_set()
        assert provider.max_output_tokens == 8192
        assert client.last_usage["input_tokens"] == 321
        assert not client.usage_emitted
    asyncio.run(run())


def test_native_tool_continuation_and_reasoning_are_not_reconstructed_or_lost():
    calls = [{"id": "call-a", "type": "function", "function": {"name": "audit", "arguments": "{}"}}]
    items = [{"type": "reasoning", "id": "opaque", "encrypted_content": "test-state"}]
    class ToolProvider(FakeProvider):
        def stream_chat(self, messages, *, tools=None):
            self.calls.append((messages, tools, self.max_output_tokens))
            self.last_usage = {"input_tokens": 1}
            yield ProviderStreamEvent("reasoning_delta", content="provider-exposed-state")
            yield ProviderStreamEvent("tool_calls", tool_calls=calls)
            yield ProviderStreamEvent("completed", response_items=items)
    async def run():
        provider = ToolProvider()
        client = ProviderChatClient(provider)
        chunks = await collect(client, tools=[{"type": "function", "function": {"name": "audit"}}])
        emitted = [c.choices[0].delta.tool_calls for c in chunks if c.choices and c.choices[0].delta.tool_calls]
        assert emitted[0][0].id == "call-a"
        stream = await client.create(messages=[
            {"role": "assistant", "content": None, "tool_calls": calls},
            {"role": "tool", "content": "ok", "tool_call_id": "call-a"},
        ])
        _ = [c async for c in stream]
        previous_assistant = provider.calls[-1][0][0]
        assert previous_assistant.reasoning_content == "provider-exposed-state"
        assert previous_assistant.response_items == items
    asyncio.run(run())


def test_voice_does_not_send_tools_to_a_model_that_disables_them():
    async def run():
        provider = FakeProvider()
        provider.supports_agent_tools = False
        with pytest.raises(ProviderResponseError, match="does not support tools"):
            await collect(ProviderChatClient(provider), tools=[{"type": "function"}])
        assert not provider.calls
    asyncio.run(run())


def test_reactivation_does_not_readvertise_tools_after_switching_to_a_text_only_model():
    pytest.importorskip("pipecat")
    from app.voice.embedded_bot import TutorAgent
    tutor = TutorAgent.__new__(TutorAgent)
    provider = FakeProvider()
    tutor._llm = NS(_model_provider=provider)
    tutor._delegation_port = None
    tool = NS(name="read-workspace")
    tutor._foreground_tools = (tool,)
    assert tutor.build_tools() == [tool]
    provider.supports_agent_tools = False
    assert tutor.build_tools() == []
    provider.supports_agent_tools = True
    assert tutor.build_tools() == [tool]


def test_pipecat_service_uses_bridge_and_reports_failed_call_usage_once(monkeypatch):
    pytest.importorskip("pipecat")
    from app.voice.embedded_memory import voice_memory_llm_service_class
    from pipecat.services.openai.llm import OpenAILLMService
    async def run():
        cls = voice_memory_llm_service_class()
        provider = FakeProvider()
        def fail_after_input(messages, *, tools=None):
            provider.last_usage = {"input_tokens": 99, "output_tokens": 0}
            raise ValueError("upstream failed after input")
            yield  # pragma: no cover
        provider.stream_chat = fail_after_input
        service = cls(model_provider=provider, api_key="test-not-real",
                      settings=OpenAILLMService.Settings(model=provider.model_id))
        assert isinstance(service._client, ProviderChatClient)
        async def base_process(self, context):
            await collect(self._client)
        monkeypatch.setattr(cls.__mro__[1], "_process_context", base_process)
        service.start_llm_usage_metrics = AsyncMock()
        with pytest.raises(ValueError, match="upstream failed"):
            await service._process_context(NS())
        service.start_llm_usage_metrics.assert_awaited_once()
        assert service.start_llm_usage_metrics.call_args.args[0].prompt_tokens == 99
        new = FakeProvider()
        new.model_id = "second-model"
        await service.rebind_model_provider(new)
        assert service._client.provider is new
        assert service._settings.model == "second-model"
    asyncio.run(run())
