from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import httpx

from app.providers.model_options import ModelCallOptions
from app.providers.ports.model import ProviderChatMessage, ProviderStreamEvent, ProviderUsage
from app.providers.remote.openai import (
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
    _ReusableHTTPClient,
    _StreamingHTTPProvider,
    merge_provider_request_headers,
    validate_http_base_url,
)
from app.providers.remote.schema_compat import sanitize_json_schema


def normalize_anthropic_api_base_url(base_url: str) -> str:
    """Return the documented Anthropic API origin without a trailing ``/v1``.

    Official requests go to ``https://api.anthropic.com/v1/messages``. Compatible
    proxy stations often expose the same path under a custom host; keep the
    configured root intact and only strip a trailing slash.
    """

    return base_url.strip().rstrip("/")


def discover_anthropic_models(
    *,
    base_url: str,
    api_key: str,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 15.0,
    extra_headers: dict[str, str] | None = None,
) -> list[str]:
    # Reject protocol-less URLs up front so the probe surfaces a clear 422
    # instead of a generic transport failure (see validate_http_base_url).
    validate_http_base_url(base_url)
    root = normalize_anthropic_api_base_url(base_url)
    # Accept both ``https://api.anthropic.com`` and ``…/v1``.
    models_url = f"{root}/v1/models" if not root.endswith("/v1") else f"{root}/models"
    try:
        with httpx.Client(
            headers=merge_provider_request_headers(
                api_key=api_key,
                extra_headers=extra_headers,
                authorization_scheme="anthropic",
            ),
            timeout=timeout_seconds,
            transport=transport,
        ) as client:
            response = client.get(models_url)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError("Anthropic model discovery timed out") from exc
    except httpx.HTTPError as exc:
        # Transport-level failures after URL validation (connection refused,
        # DNS, resets); without this catch they would escape as a raw 500.
        raise ProviderHTTPError(f"Anthropic model discovery failed: {exc}") from exc
    if not response.is_success:
        raise ProviderHTTPError(
            f"Provider returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProviderResponseError("Provider returned non-JSON model discovery data") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ProviderResponseError("Provider model discovery response has no data array")
    model_ids = [item.get("id") for item in data if isinstance(item, dict)]
    if any(not isinstance(model_id, str) or not model_id.strip() for model_id in model_ids):
        raise ProviderResponseError("Provider returned an invalid model identifier")
    return sorted(set(model_ids))


class AnthropicMessagesProvider(_StreamingHTTPProvider):
    """Anthropic Messages API adapter with SSE streaming and tool use."""

    supports_structured_chat = True
    supports_agent_tools = True

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("authorization_scheme", "anthropic")
        super().__init__(**kwargs)
        # Anthropic requests always use the JSON Accept header; streaming is
        # selected via the body ``stream`` flag rather than Accept negotiation.
        self.base_url = normalize_anthropic_api_base_url(self.base_url)

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = _ReusableHTTPClient(
                httpx.Client(
                    headers=merge_provider_request_headers(
                        api_key=self.api_key,
                        extra_headers=self.extra_headers,
                        accept="application/json",
                        authorization_scheme="anthropic",
                    ),
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                )
            )
        return self._http_client

    @staticmethod
    def _system_with_cache_control(system: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _messages_url(self) -> str:
        root = self.base_url
        if root.endswith("/v1"):
            return f"{root}/messages"
        return f"{root}/v1/messages"

    @staticmethod
    def _normalized_usage(usage: dict[str, Any]) -> ProviderUsage:
        uncached = max(0, int(usage.get("input_tokens") or 0))
        cached = max(0, int(usage.get("cache_read_input_tokens") or 0))
        created = max(0, int(usage.get("cache_creation_input_tokens") or 0))
        return {
            "input_tokens": uncached + cached + created,
            "cached_input_tokens": cached,
            "cache_creation_input_tokens": created,
            "output_tokens": max(0, int(usage.get("output_tokens") or 0)),
            "reasoning_tokens": 0,
        }

    def _merge_usage(self, usage: dict[str, Any]) -> None:
        current_cached = int(self.last_usage.get("cached_input_tokens") or 0)
        current_created = int(self.last_usage.get("cache_creation_input_tokens") or 0)
        current_uncached = max(
            0,
            int(self.last_usage.get("input_tokens") or 0)
            - current_cached
            - current_created,
        )
        uncached = (
            max(0, int(usage.get("input_tokens") or 0))
            if "input_tokens" in usage
            else current_uncached
        )
        cached = (
            max(0, int(usage.get("cache_read_input_tokens") or 0))
            if "cache_read_input_tokens" in usage
            else current_cached
        )
        created = (
            max(0, int(usage.get("cache_creation_input_tokens") or 0))
            if "cache_creation_input_tokens" in usage
            else current_created
        )
        output = (
            max(0, int(usage.get("output_tokens") or 0))
            if "output_tokens" in usage
            else int(self.last_usage.get("output_tokens") or 0)
        )
        self.last_usage = {
            "input_tokens": uncached + cached + created,
            "cached_input_tokens": cached,
            "cache_creation_input_tokens": created,
            "output_tokens": output,
            "reasoning_tokens": 0,
        }

    _MODEL_VERSION_RE = re.compile(r"^claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:[.-](\d+))?")

    @classmethod
    def _thinking_capabilities(cls, model_id: str) -> tuple[bool, bool, bool]:
        """Return ``(adaptive_thinking, supports_xhigh_effort, needs_explicit_disable)``.

        Claude Opus 4.7+/Opus 5, Sonnet 5, and Fable/Mythos 5 reject the legacy
        ``{"type": "enabled", "budget_tokens": N}`` config with HTTP 400 and use
        adaptive thinking with ``output_config.effort`` instead. Opus/Sonnet 4.6
        accept adaptive but not the ``xhigh`` effort tier (introduced with Opus
        4.7). Everything older keeps the enabled+budget form.

        ``needs_explicit_disable`` is true only for models that default to
        adaptive thinking when the ``thinking`` field is *omitted* — Claude
        Opus 5 / Sonnet 5. A fast/off request on those must send an explicit
        ``{"type": "disabled"}`` to really turn reasoning off. Fable/Mythos 5
        also default to adaptive on omission but reject ``{"type": "disabled"}``
        with HTTP 400 and are flagged ``thinking_required`` upstream, so fast
        mode never reaches the adapter for them.
        """

        match = cls._MODEL_VERSION_RE.search((model_id or "").strip().lower())
        if not match:
            return False, False, False
        family = match.group(1)
        major = int(match.group(2))
        minor = int(match.group(3) or 0)
        if family in {"fable", "mythos"}:
            return True, True, False
        if family in {"opus", "sonnet"}:
            if major >= 5:
                return True, True, True
            if major == 4 and minor >= 7:
                return True, True, False
            if major == 4 and minor == 6:
                return True, False, False
        return False, False, False

    def _apply_call_options(self, payload: dict[str, Any], *, responses: bool) -> dict[str, Any]:
        del responses
        options = self.call_options
        if options is None:
            return payload
        actual = options.actual_reasoning_effort
        thinking_mode = options.thinking_mode
        if thinking_mode == "off":
            _, _, needs_explicit_disable = self._thinking_capabilities(self.model_id)
            if needs_explicit_disable:
                # Claude Opus 5 / Sonnet 5 run adaptive thinking when the field
                # is omitted, so 极速 must send an explicit disable to really
                # turn reasoning off instead of hiding it.
                payload["thinking"] = {"type": "disabled"}
            return payload
        if not thinking_mode:
            return payload
        tier = str(actual or thinking_mode)
        if tier not in {"low", "medium", "high", "xhigh"}:
            tier = str(thinking_mode)
        adaptive, supports_xhigh, _ = self._thinking_capabilities(self.model_id)
        if adaptive:
            payload["thinking"] = {"type": "adaptive"}
            effort = tier
            if effort == "xhigh" and not supports_xhigh:
                effort = "high"
            if effort in {"low", "medium", "high", "xhigh"}:
                payload["output_config"] = {"effort": effort}
        else:
            budget = {
                "low": 4_000,
                "medium": 10_000,
                "high": 20_000,
                "xhigh": 32_000,
            }.get(tier, 10_000)
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        return payload

    @staticmethod
    def _split_system(messages: list[ProviderChatMessage]) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        body: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue
            if message.role == "tool":
                body.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id or "",
                                "content": message.content or "",
                            }
                        ],
                    }
                )
                continue
            if message.role == "assistant" and message.tool_calls:
                content_blocks: list[dict[str, Any]] = []
                if message.content:
                    content_blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    function = call.get("function") if isinstance(call, dict) else None
                    if not isinstance(function, dict):
                        continue
                    name = function.get("name")
                    raw_args = function.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        args = {"raw": raw_args}
                    if not isinstance(args, dict):
                        args = {"value": args}
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id") or "",
                            "name": name or "",
                            "input": args,
                        }
                    )
                body.append({"role": "assistant", "content": content_blocks})
                continue
            content: Any
            if message.content_parts:
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"type": "text", "text": message.content})
                for part in message.content_parts:
                    if part.get("type") != "input_image":
                        continue
                    image_url = part.get("image_url")
                    if not isinstance(image_url, str) or not image_url.startswith("data:image/"):
                        continue
                    # data:image/png;base64,xxxx
                    header, _, data = image_url.partition(",")
                    media_type = "image/png"
                    if header.startswith("data:") and ";base64" in header:
                        media_type = header[5:].split(";", 1)[0] or media_type
                    parts.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": data,
                            },
                        }
                    )
                content = parts
            else:
                content = message.content or ""
            body.append({"role": "user" if message.role == "user" else "assistant", "content": content})
        system = "\n\n".join(system_parts) if system_parts else None
        return system, body

    @staticmethod
    def _tools_payload(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        converted: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                function = tool["function"]
                converted.append(
                    {
                        "name": function.get("name") or "",
                        "description": function.get("description") or "",
                        "input_schema": sanitize_json_schema(
                            function.get("parameters")
                            or {"type": "object", "properties": {}}
                        ),
                    }
                )
            elif tool.get("name"):
                converted.append(
                    {
                        "name": tool.get("name") or "",
                        "description": tool.get("description") or "",
                        "input_schema": sanitize_json_schema(
                            tool.get("input_schema")
                            or tool.get("parameters")
                            or {"type": "object", "properties": {}}
                        ),
                    }
                )
        if converted:
            # Cache-breakpoint the trailing tool so the whole tool-definition
            # block is eligible for Anthropic prompt caching (system already
            # carries one breakpoint; this adds a second, independent one).
            converted[-1]["cache_control"] = {"type": "ephemeral"}
        return converted or None

    def _stream_answer(self, prompt: str) -> Iterable[str]:
        for event in self.stream_chat(
            [ProviderChatMessage(role="user", content=prompt)],
        ):
            if event.type == "text_delta" and event.content:
                yield event.content

    def stream_answer(self, prompt: str) -> Iterable[str]:
        self.last_usage = {}
        self.last_request_id = None
        try:
            yield from self._stream_answer(prompt)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Anthropic Messages stream timed out") from exc

    def stream_chat(
        self,
        messages: list[ProviderChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterable[ProviderStreamEvent]:
        self.last_usage = {}
        self.last_request_id = None
        system, body = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": body,
            "max_tokens": self.max_output_tokens,
            "stream": True,
        }
        if system:
            payload["system"] = self._system_with_cache_control(system)
        tools_payload = self._tools_payload(tools)
        if tools_payload:
            payload["tools"] = tools_payload
        payload = self._apply_call_options(payload, responses=False)

        tool_aggregates: dict[str, dict[str, Any]] = {}
        completed = False
        try:
            with self._client() as client:
                with client.stream(
                    "POST",
                    self._messages_url(),
                    json=payload,
                ) as response:
                    self._raise_for_status(response)
                    self.last_request_id = (
                        response.headers.get("request-id")
                        or response.headers.get("x-request-id")
                    )
                    for event in self._sse_payloads(response):
                        event_type = event.get("type")
                        if event_type == "content_block_delta":
                            delta = event.get("delta")
                            if not isinstance(delta, dict):
                                continue
                            delta_type = delta.get("type")
                            if delta_type == "text_delta":
                                text = delta.get("text")
                                if isinstance(text, str) and text:
                                    for chunk in self._text_deltas(text):
                                        yield ProviderStreamEvent("text_delta", content=chunk)
                            elif delta_type == "thinking_delta":
                                thinking = delta.get("thinking")
                                if isinstance(thinking, str) and thinking:
                                    for chunk in self._text_deltas(thinking):
                                        yield ProviderStreamEvent(
                                            "reasoning_delta",
                                            content=chunk,
                                            reasoning_kind="summary",
                                        )
                            elif delta_type == "input_json_delta":
                                partial = delta.get("partial_json")
                                index = event.get("index", 0)
                                key = str(index)
                                aggregate = tool_aggregates.setdefault(
                                    key,
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if isinstance(partial, str):
                                    aggregate["function"]["arguments"] += partial
                        elif event_type == "content_block_start":
                            block = event.get("content_block")
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "tool_use":
                                index = event.get("index", 0)
                                key = str(index)
                                aggregate = tool_aggregates.setdefault(
                                    key,
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if isinstance(block.get("id"), str):
                                    aggregate["id"] = block["id"]
                                if isinstance(block.get("name"), str):
                                    aggregate["function"]["name"] = block["name"]
                        elif event_type == "message_delta":
                            usage = event.get("usage")
                            if isinstance(usage, dict):
                                self._merge_usage(usage)
                            delta = event.get("delta")
                            if isinstance(delta, dict) and delta.get("stop_reason"):
                                # Defer completed until message_stop so tool aggregates settle.
                                pass
                        elif event_type == "message_start":
                            message = event.get("message")
                            if isinstance(message, dict):
                                usage = message.get("usage")
                                if isinstance(usage, dict):
                                    self._merge_usage(usage)
                        elif event_type == "message_stop":
                            completed = True
                            # Sort by the numeric content-block index so the
                            # provider_position contract does not depend on dict
                            # insertion order (aggregation keys are str(index)).
                            tool_calls = [
                                tool_aggregates[key]
                                for key in sorted(tool_aggregates, key=int)
                                if tool_aggregates[key].get("id")
                                and tool_aggregates[key]
                                .get("function", {})
                                .get("name")
                            ]
                            if tool_calls:
                                yield ProviderStreamEvent("tool_calls", tool_calls=tool_calls)
                            yield ProviderStreamEvent("completed", finish_reason="stop")
                        elif event_type == "error":
                            raise ProviderHTTPError("Anthropic stream terminated with error")
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Anthropic Messages stream timed out") from exc
        if not completed:
            raise ProviderHTTPError("Anthropic stream ended without message_stop")

    def generate_json(self, prompt: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        del schema_name
        system = (
            "Respond with a single JSON object that validates against the provided schema. "
            "Do not wrap the object in markdown."
        )
        payload: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_output_tokens,
            "system": self._system_with_cache_control(system),
            "messages": [
                {
                    "role": "user",
                    "content": f"Schema:\n{json.dumps(schema)}\n\nTask:\n{prompt}",
                }
            ],
        }
        payload = self._apply_call_options(payload, responses=False)
        try:
            with self._client() as client:
                response = client.post(self._messages_url(), json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Anthropic structured generation timed out") from exc
        self._raise_for_status(response)
        self.last_request_id = (
            response.headers.get("request-id") or response.headers.get("x-request-id")
        )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("Anthropic returned non-JSON generation data") from exc
        texts = [
            block.get("text")
            for block in data.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if not texts or not isinstance(texts[0], str):
            raise ProviderResponseError("Anthropic response contains no text result")
        try:
            result = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("Anthropic structured text is invalid JSON") from exc
        if not isinstance(result, dict):
            raise ProviderResponseError("Anthropic structured result must be an object")
        usage = data.get("usage")
        if isinstance(usage, dict):
            self._merge_usage(usage)
        return result
