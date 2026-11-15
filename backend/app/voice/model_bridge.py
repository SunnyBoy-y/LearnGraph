"""Pipecat chunk facade backed by the same protocol adapters as text/Agent.

This module sends no OpenAI requests. It preserves native tool continuation,
configuration and usage, and keeps blocking provider I/O off the event loop.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from threading import Event
from types import SimpleNamespace as NS
from typing import Any

from app.providers.ports.model import ProviderChatMessage
from app.providers.remote.openai import ProviderResponseError


class ProviderChatClient:
    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.chat = NS(completions=self)
        self._lock = asyncio.Lock()
        self._continuations: dict[str, tuple[str, list[dict]]] = {}
        self._closed = False
        self.last_usage: dict[str, int] = {}
        self.usage_emitted = False

    def _messages(self, raw: list[dict]) -> list[ProviderChatMessage]:
        result = []
        for item in raw:
            role = item.get("role")
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant", "tool"}:
                raise ProviderResponseError(f"Unsupported voice message role: {role}")
            calls = list(item.get("tool_calls") or [])
            reasoning, response_items = "", []
            for call in calls:
                saved = self._continuations.get(str(call.get("id") or ""))
                if saved:
                    reasoning, response_items = saved
                    break
            content = item.get("content")
            if isinstance(content, list):
                if any(part.get("type") not in {"text", "input_text"} for part in content):
                    raise ProviderResponseError("Voice context contains unsupported non-text content")
                content = "\n".join(str(part.get("text") or "") for part in content)
            result.append(ProviderChatMessage(
                role=role, content=content, tool_calls=calls,
                tool_call_id=item.get("tool_call_id"),
                reasoning_content=item.get("reasoning_content") or reasoning or None,
                response_items=list(response_items),
            ))
        return result

    def _chunk(self, delta: Any = None, *, usage: Any = None) -> Any:
        return NS(model=self.provider.model_id, usage=usage, choices=[] if usage else [
            NS(delta=delta or NS(content=None, tool_calls=None), finish_reason=None)
        ])

    def usage_chunk(self) -> Any:
        usage = self.last_usage
        prompt, output = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        return self._chunk(usage=NS(
            prompt_tokens=prompt, completion_tokens=output, total_tokens=prompt + output,
            prompt_tokens_details=NS(cached_tokens=int(usage.get("cached_input_tokens") or 0),
                                     cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0)),
            completion_tokens_details=NS(reasoning_tokens=int(usage.get("reasoning_tokens") or 0)),
        ))

    async def create(self, *, messages: list[dict], stream: bool = True, tools: Any = None,
                     tool_choice: Any = None, **params: Any) -> Any:
        actual_tools = tools if isinstance(tools, list) else []
        if tool_choice == "none":
            actual_tools = []
        elif isinstance(tool_choice, dict) or tool_choice == "required":
            raise ProviderResponseError("Forced voice tool selection is not supported by the shared adapter")
        if actual_tools and not getattr(self.provider, "supports_agent_tools", False):
            raise ProviderResponseError("The selected voice model does not support tools")
        chunks = self._stream(messages, actual_tools, params)
        if stream:
            return chunks
        text = []
        async for chunk in chunks:
            if chunk.choices and chunk.choices[0].delta.content:
                text.append(chunk.choices[0].delta.content)
        return NS(choices=[NS(message=NS(content="".join(text)))])

    async def _reset_transport(self) -> None:
        reset = getattr(self.provider, "reset_transport", None)
        if callable(reset):
            await asyncio.wait_for(asyncio.to_thread(reset), timeout=2)

    async def _stream(self, messages: list[dict], tools: list[dict], params: dict):
        async with self._lock:
            if self._closed:
                raise ProviderResponseError("Voice model client is closed")
            if tools and not getattr(self.provider, "supports_agent_tools", False):
                raise ProviderResponseError("The selected voice model does not support tools")
            messages = self._messages(messages)
            self.last_usage, self.usage_emitted = {}, False
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue(maxsize=16)
            stopped = Event()
            old_cap = self.provider.max_output_tokens
            task_cap = next((params[k] for k in ("max_completion_tokens", "max_tokens")
                             if isinstance(params.get(k), int) and not isinstance(params[k], bool) and params[k] > 0), None)
            if task_cap:
                self.provider.max_output_tokens = min(old_cap, task_cap)

            def put(value: Any) -> None:
                future = asyncio.run_coroutine_threadsafe(queue.put(value), loop)
                while not stopped.is_set():
                    try:
                        future.result(timeout=0.2)
                        return
                    except FutureTimeout:
                        continue
                future.cancel()

            def pump() -> None:
                iterator = None
                failure = None
                try:
                    iterator = iter(self.provider.stream_chat(messages, tools=tools or None))
                    for event in iterator:
                        if stopped.is_set():
                            break
                        put(event)
                except Exception as exc:
                    failure = exc
                finally:
                    try:
                        close = getattr(iterator, "close", None)
                        if callable(close):
                            close()
                    except Exception as exc:
                        failure = failure or exc
                    finally:
                        # Copy only after the worker owns/completes generator
                        # cleanup; cancelled callers never read live usage state.
                        self.last_usage = dict(getattr(self.provider, "last_usage", {}) or {})
                        if not stopped.is_set():
                            if failure is not None:
                                put(failure)
                            put(None)

            worker = asyncio.create_task(asyncio.to_thread(pump))
            receive = None
            reasoning, final_calls, final_items = [], [], []
            try:
                while True:
                    receive = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait({receive, worker}, return_when=asyncio.FIRST_COMPLETED)
                    if receive not in done and worker.done() and queue.empty():
                        receive.cancel()
                        await asyncio.gather(receive, return_exceptions=True)
                        await worker  # Propagate any unforeseen pump failure.
                        break
                    event = await receive
                    if event is None:
                        break
                    if isinstance(event, Exception):
                        raise event
                    if event.type == "text_delta":
                        yield self._chunk(NS(content=event.content, tool_calls=None))
                    elif event.type == "reasoning_delta":
                        reasoning.append(event.content or "")
                    elif event.type == "tool_calls":
                        final_calls = event.tool_calls
                    elif event.type == "completed":
                        final_items = event.response_items
                await worker
                for index, call in enumerate(final_calls):
                    function = call.get("function") or {}
                    call_id = str(call.get("id") or "")
                    self._continuations[call_id] = ("".join(reasoning), list(final_items))
                    yield self._chunk(NS(content=None, tool_calls=[NS(
                        index=index, id=call_id, type="function",
                        function=NS(name=function.get("name", ""), arguments=function.get("arguments", "{}")),
                    )]))
                self.usage_emitted = True
                yield self.usage_chunk()
            finally:
                stopped.set()
                if receive is not None and not receive.done():
                    receive.cancel()
                    await asyncio.gather(receive, return_exceptions=True)
                try:
                    if not worker.done():
                        try:
                            await self._reset_transport()
                        except Exception:
                            self._closed = True
                        try:
                            await asyncio.wait_for(asyncio.shield(worker), timeout=5)
                        except (TimeoutError, asyncio.CancelledError):
                            self._closed = True
                    else:
                        await worker
                finally:
                    self.provider.max_output_tokens = old_cap

    async def rebind(self, provider: Any) -> None:
        """Switch only between turns; never mix credentials or continuation state."""
        async with self._lock:
            await self._reset_transport()
            self.provider = provider
            self._continuations.clear()
            self.last_usage, self.usage_emitted = {}, False
            self._closed = False

    async def close(self) -> None:
        self._closed = True
        await self._reset_transport()

    def is_closed(self) -> bool:
        return self._closed
