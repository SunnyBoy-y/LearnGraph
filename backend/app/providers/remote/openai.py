from __future__ import annotations

import json
from collections.abc import Iterable
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse, urlsplit, urlunsplit

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.providers.model_options import ModelCallOptions
from app.providers.ports.model import ProviderChatMessage, ProviderStreamEvent


class ProviderHTTPError(RuntimeError):
    pass


class ProviderTimeoutError(ProviderHTTPError):
    pass


class ProviderResponseError(ProviderHTTPError):
    pass


def normalize_openai_api_base_url(base_url: str) -> str:
    """Return the documented ``/v1`` root for the official OpenAI hostname.

    Workspace configuration normally comes from the provider catalog and is
    already ``https://api.openai.com/v1``.  Normalizing the historical/root
    spelling here keeps both model discovery and the native Responses adapter
    on the documented API root without rewriting custom compatible endpoints.
    """

    normalized = base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
        hostname = (parsed.hostname or "").casefold()
    except ValueError:
        return normalized
    if (
        parsed.scheme == "https"
        and hostname == "api.openai.com"
        and parsed.path.rstrip("/") == ""
        and not parsed.query
        and not parsed.fragment
    ):
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", "", ""))
    return normalized


def merge_provider_request_headers(
    *,
    api_key: str,
    extra_headers: dict[str, str] | None = None,
    accept: str = "application/json",
    authorization_scheme: str = "bearer",
) -> dict[str, str]:
    """Compose outbound Provider headers for official APIs and proxy stations.

    Custom headers are applied first so a relay may declare station-specific
    fields (for example ``X-Foo``). Credential headers always come from the
    workspace Secret Store afterwards and therefore cannot be overwritten by
    user-supplied values.
    """

    headers: dict[str, str] = {}
    if extra_headers:
        for raw_key, raw_value in extra_headers.items():
            key = str(raw_key).strip()
            value = str(raw_value).strip()
            if not key or not value:
                continue
            # Reject credential smuggling through the custom-header surface.
            if key.casefold() in {
                "authorization",
                "x-api-key",
                "api-key",
                "proxy-authorization",
            }:
                continue
            headers[key] = value
    headers["Accept"] = accept
    if authorization_scheme == "anthropic":
        headers["x-api-key"] = api_key
        headers.setdefault("anthropic-version", "2023-06-01")
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def discover_remote_models(
    *,
    base_url: str,
    api_key: str,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 15.0,
    extra_headers: dict[str, str] | None = None,
) -> list[str]:
    normalized_base_url = normalize_openai_api_base_url(base_url)
    try:
        with httpx.Client(
            headers=merge_provider_request_headers(
                api_key=api_key,
                extra_headers=extra_headers,
            ),
            timeout=timeout_seconds,
            transport=transport,
        ) as client:
            response = client.get(f"{normalized_base_url}/models")
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError("Provider model discovery timed out") from exc
    if not response.is_success:
        raise ProviderHTTPError(f"Provider returned HTTP {response.status_code}")
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


class _StreamingHTTPProvider:
    available = True
    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        provider_type: str = "openai_compatible_chat",
        model_id: str,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 60.0,
        context_window_tokens: int = 128_000,
        max_output_tokens: int = 4_096,
        call_options: ModelCallOptions | None = None,
        supports_image_input: bool = False,
        image_input_mode: str = "auto",
        capabilities: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        authorization_scheme: str = "bearer",
    ) -> None:
        self.provider_id = provider_id
        self.provider_type = provider_type
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.context_window_tokens = context_window_tokens
        self.max_output_tokens = max_output_tokens
        self.call_options = call_options
        self.thinking_mode = call_options.thinking_mode if call_options else "off"
        self.actual_reasoning_effort = (
            call_options.actual_reasoning_effort if call_options else None
        )
        self.search_route = call_options.search_route if call_options else "disabled"
        self.last_sources: list[dict[str, str]] = []
        self.last_usage: dict[str, int] = {}
        self.last_request_id: str | None = None
        # This is a workspace-confirmed model capability, not an inference from
        # a provider family.  A provider can expose text-only and vision models
        # under the same endpoint.
        self.supports_image_input = supports_image_input
        self.image_input_mode = image_input_mode or "auto"
        self.capabilities = dict(capabilities or {})
        self.extra_headers = dict(extra_headers or {})
        self.authorization_scheme = authorization_scheme

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers=merge_provider_request_headers(
                api_key=self.api_key,
                extra_headers=self.extra_headers,
                accept="text/event-stream",
                authorization_scheme=self.authorization_scheme,
            ),
            timeout=self.timeout_seconds,
            transport=self.transport,
        )

    @staticmethod
    def _text_deltas(content: str, max_chars: int = 24) -> Iterable[str]:
        """Cap provider-coalesced text without delaying genuine token deltas.

        Most upstreams already emit one or a few tokens. Some compatible
        gateways buffer a sentence or paragraph into one SSE event; slicing
        only those oversized deltas keeps the browser cadence responsive while
        preserving exact Unicode text and durable event ordering.
        """

        for offset in range(0, len(content), max_chars):
            yield content[offset : offset + max_chars]

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Usage and request IDs describe exactly one provider invocation.  Clear
        # them before the request so an early timeout/transport failure cannot
        # accidentally reuse metadata from a previous successful call.
        self.last_usage = {}
        self.last_request_id = None
        try:
            with self._client() as client:
                response = client.post(f"{self.base_url}/{path.lstrip('/')}", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Provider structured generation timed out") from exc
        self._raise_for_status(response)
        self.last_request_id = response.headers.get("x-request-id")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("Provider returned non-JSON generation data") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("Provider generation response must be an object")
        return data

    def _apply_call_options(self, payload: dict[str, Any], *, responses: bool) -> dict[str, Any]:
        options = self.call_options
        if options is None:
            return payload
        actual = options.actual_reasoning_effort
        if actual is not None:
            if responses or options.reasoning_parameter == "reasoning.effort":
                payload["reasoning"] = {"effort": actual}
            else:
                payload["reasoning_effort"] = actual
        if responses and options.native_web_search:
            raw_tools = payload.get("tools")
            tools = list(raw_tools) if isinstance(raw_tools, list) else []
            if not any(
                isinstance(item, dict) and item.get("type") == "web_search"
                for item in tools
            ):
                tools.append({"type": "web_search"})
            payload["tools"] = tools
        return payload

    def _capture_response_sources(self, response_payload: dict[str, Any]) -> None:
        sources: list[dict[str, Any]] = []
        response = response_payload.get("response")
        if not isinstance(response, dict):
            response = response_payload
        for item in response.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                for annotation in content.get("annotations") or []:
                    if not isinstance(annotation, dict):
                        continue
                    annotation_type = str(annotation.get("type") or "")
                    url = annotation.get("url")
                    if not isinstance(url, str) or not url.startswith(
                        ("http://", "https://")
                    ):
                        # Some Responses payloads nest the URL under url_citation.
                        nested = annotation.get("url_citation")
                        if isinstance(nested, dict):
                            url = nested.get("url")
                            title_fallback = nested.get("title")
                            start_index = nested.get("start_index")
                            end_index = nested.get("end_index")
                        else:
                            continue
                    else:
                        title_fallback = annotation.get("title")
                        start_index = annotation.get("start_index")
                        end_index = annotation.get("end_index")
                    if not isinstance(url, str) or not url.startswith(
                        ("http://", "https://")
                    ):
                        continue
                    if annotation_type and annotation_type not in {
                        "url_citation",
                        "citation",
                        "",
                    }:
                        # Keep unknown annotation types that still carry a URL.
                        pass
                    entry: dict[str, Any] = {
                        "url": url,
                        "title": str(title_fallback or url)[:1_000],
                    }
                    if isinstance(start_index, int) and not isinstance(start_index, bool):
                        entry["start_index"] = start_index
                    if isinstance(end_index, int) and not isinstance(end_index, bool):
                        entry["end_index"] = end_index
                    sources.append(entry)
        # Deduplicate by URL while preserving first-seen order and assign 1-based
        # citation indices used by the chat UI for inline badges.
        deduped: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for item in sources:
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            item = dict(item)
            item["index"] = len(deduped) + 1
            deduped.append(item)
        self.last_sources = deduped

    @staticmethod
    def _validate_structured_result(
        result: object,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ProviderResponseError("Structured result must be an object")
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(result)
        except SchemaError as exc:
            raise ProviderResponseError("Configured structured output schema is invalid") from exc
        except ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "$"
            raise ProviderResponseError(
                f"Structured result does not match the declared schema at {location}"
            ) from exc
        return result

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        request_id = response.headers.get("x-request-id")
        detail = response.text[:500]
        raise ProviderHTTPError(
            f"Provider returned HTTP {response.status_code}; request_id={request_id}; body={detail}"
        )

    @staticmethod
    def _sse_payloads(response: httpx.Response) -> Iterable[dict[str, Any]]:
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ProviderHTTPError("Provider returned invalid SSE JSON") from exc
            if isinstance(payload, dict):
                yield payload


class OpenAIResponsesProvider(_StreamingHTTPProvider):
    """OpenAI-native Responses adapter using the Responses SSE protocol.

    Unlike Chat Completions, stateless Responses continuations must replay the
    previous ``response.output`` items.  In particular, reasoning items have
    opaque encrypted continuation state and cannot be reconstructed from a
    visible summary.  The adapter therefore exposes the completed output items
    through :class:`ProviderStreamEvent` and consumes them from
    :class:`ProviderChatMessage` unchanged.
    """

    supports_structured_chat = True
    supports_agent_tools = True

    @staticmethod
    def _usage_from_response(response: object) -> dict[str, int]:
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            return {}
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        return {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "cached_input_tokens": int(
                (input_details.get("cached_tokens") or 0)
                if isinstance(input_details, dict)
                else 0
            ),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_tokens": int(
                (output_details.get("reasoning_tokens") or 0)
                if isinstance(output_details, dict)
                else 0
            ),
        }

    @staticmethod
    def _response_items(response: object) -> list[dict[str, Any]]:
        if not isinstance(response, dict):
            return []
        raw_items = response.get("output")
        if raw_items is None:
            return []
        if not isinstance(raw_items, list) or any(
            not isinstance(item, dict) for item in raw_items
        ):
            raise ProviderResponseError("Responses completion output must be an item array")
        # The caller persists these exact provider-owned values for the next
        # stateless request.  Do not mutate or rebuild them from visible text.
        return deepcopy(raw_items)

    @staticmethod
    def _response_function_call(item: dict[str, Any]) -> dict[str, Any] | None:
        if item.get("type") != "function_call":
            return None
        call_id = item.get("call_id")
        name = item.get("name")
        arguments = item.get("arguments")
        if not isinstance(call_id, str) or not call_id:
            raise ProviderResponseError("Responses function call has no call_id")
        if not isinstance(name, str) or not name:
            raise ProviderResponseError("Responses function call has no name")
        if not isinstance(arguments, str):
            raise ProviderResponseError("Responses function call has invalid arguments")
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }

    @classmethod
    def _tool_calls_from_response_items(
        cls,
        response_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        seen_call_ids: set[str] = set()
        for item in response_items:
            tool_call = cls._response_function_call(item)
            if tool_call is None or tool_call["id"] in seen_call_ids:
                continue
            seen_call_ids.add(tool_call["id"])
            tool_calls.append(tool_call)
        return tool_calls

    @staticmethod
    def _response_tool_definitions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert the existing Chat Completions function shape to Responses."""

        definitions: list[dict[str, Any]] = []
        for raw_tool in tools:
            if not isinstance(raw_tool, dict) or raw_tool.get("type") != "function":
                raise ProviderResponseError(
                    "Responses structured chat only accepts function tool definitions"
                )
            source = raw_tool.get("function")
            if not isinstance(source, dict):
                # Accept an already-normalized Responses definition as well.
                source = raw_tool
            name = source.get("name")
            parameters = source.get("parameters")
            if not isinstance(name, str) or not name.strip():
                raise ProviderResponseError("Responses function tool has no name")
            if not isinstance(parameters, dict):
                raise ProviderResponseError("Responses function tool has no JSON schema")
            definition: dict[str, Any] = {
                "type": "function",
                "name": name,
                "parameters": parameters,
            }
            description = source.get("description")
            if isinstance(description, str) and description:
                definition["description"] = description
            strict = source.get("strict")
            if isinstance(strict, bool):
                definition["strict"] = strict
            definitions.append(definition)
        return definitions

    @staticmethod
    def _legacy_function_call_item(raw_tool_call: dict[str, Any]) -> dict[str, Any]:
        """Turn a Chat-Completions tool-call history entry into a Responses item."""

        function = raw_tool_call.get("function")
        if not isinstance(function, dict):
            raise ProviderResponseError("Historical function call has no function payload")
        call_id = raw_tool_call.get("id") or raw_tool_call.get("call_id")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(call_id, str) or not call_id:
            raise ProviderResponseError("Historical function call has no call id")
        if not isinstance(name, str) or not name:
            raise ProviderResponseError("Historical function call has no name")
        if not isinstance(arguments, str):
            raise ProviderResponseError("Historical function call has invalid arguments")
        return {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
        }

    @classmethod
    def _response_input_from_messages(
        cls,
        messages: list[ProviderChatMessage],
    ) -> list[dict[str, Any]]:
        """Build stateless Responses ``input`` without losing opaque output items."""

        input_items: list[dict[str, Any]] = []
        for message in messages:
            if message.response_items:
                if any(not isinstance(item, dict) for item in message.response_items):
                    raise ProviderResponseError("Historical response_items must be objects")
                # A native output already contains its assistant text, reasoning
                # item(s), and any function calls in exact provider order.
                input_items.extend(deepcopy(message.response_items))
                continue
            if message.role == "tool":
                if not message.tool_call_id:
                    raise ProviderResponseError("Tool history entry has no tool call id")
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content or "",
                    }
                )
                continue
            if message.role == "assistant":
                if message.content is not None:
                    input_items.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": message.content,
                        }
                    )
                for tool_call in message.tool_calls:
                    if not isinstance(tool_call, dict):
                        raise ProviderResponseError("Historical tool call must be an object")
                    input_items.append(cls._legacy_function_call_item(tool_call))
                # ``reasoning_content`` is intentionally not synthesized into a
                # reasoning item. Only raw provider output can carry the opaque
                # encrypted state required for a correct Responses continuation.
                continue
            if message.content_parts:
                content: list[dict[str, Any]] = []
                if message.content:
                    content.append({"type": "input_text", "text": message.content})
                for part in message.content_parts:
                    if part.get("type") != "input_image":
                        continue
                    image_url = part.get("image_url")
                    if not isinstance(image_url, str) or not image_url.startswith("data:image/"):
                        raise ProviderResponseError("Image input must be a data:image URL")
                    image_part: dict[str, Any] = {
                        "type": "input_image",
                        "image_url": image_url,
                    }
                    detail = part.get("detail")
                    if detail in {"low", "high", "auto"}:
                        image_part["detail"] = detail
                    content.append(image_part)
                input_items.append(
                    {
                        "type": "message",
                        "role": message.role,
                        "content": content,
                    }
                )
            else:
                input_items.append(
                    {
                        "type": "message",
                        "role": message.role,
                        "content": message.content or "",
                    }
                )
        return input_items

    @staticmethod
    def _merge_function_call_stream_event(
        aggregates: dict[str, dict[str, Any]],
        event: dict[str, Any],
    ) -> None:
        """Accumulate function-call SSE deltas until their response completes."""

        event_type = event.get("type")
        raw_item = event.get("item")
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            if not isinstance(raw_item, dict) or raw_item.get("type") != "function_call":
                return
            item_id = raw_item.get("id") or raw_item.get("call_id")
            if not isinstance(item_id, str) or not item_id:
                raise ProviderResponseError("Responses function call item has no id")
            aggregate = aggregates.setdefault(
                item_id,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            call_id = raw_item.get("call_id")
            name = raw_item.get("name")
            arguments = raw_item.get("arguments")
            if isinstance(call_id, str):
                aggregate["id"] = call_id
            if isinstance(name, str):
                aggregate["function"]["name"] = name
            if isinstance(arguments, str):
                aggregate["function"]["arguments"] = arguments
            return

        if event_type not in {
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        }:
            return
        item_id = event.get("item_id") or event.get("call_id")
        if not isinstance(item_id, str) or not item_id:
            raise ProviderResponseError("Responses function call event has no item id")
        aggregate = aggregates.setdefault(
            item_id,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        call_id = event.get("call_id")
        name = event.get("name")
        if isinstance(call_id, str):
            aggregate["id"] = call_id
        if isinstance(name, str):
            aggregate["function"]["name"] = name
        if event_type == "response.function_call_arguments.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                aggregate["function"]["arguments"] += delta
        else:
            arguments = event.get("arguments")
            if isinstance(arguments, str):
                aggregate["function"]["arguments"] = arguments

    @staticmethod
    def _completed_stream_tool_calls(
        aggregates: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        completed: list[dict[str, Any]] = []
        for aggregate in aggregates.values():
            function = aggregate.get("function")
            if not isinstance(function, dict):
                continue
            call_id = aggregate.get("id")
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(call_id, str) or not call_id:
                continue
            if not isinstance(name, str) or not name or not isinstance(arguments, str):
                raise ProviderResponseError("Responses stream ended with an incomplete function call")
            completed.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
        return completed

    def _complete_response_metadata(
        self,
        event: dict[str, Any],
        *,
        require_output: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        response = event.get("response")
        if not isinstance(response, dict):
            if require_output:
                raise ProviderResponseError(
                    "Responses completion is missing the required response output"
                )
            # Keep compatibility with the text-only interface, whose caller
            # never replays a provider-native conversation state.
            response = {}
        if require_output and not isinstance(response.get("output"), list):
            raise ProviderResponseError(
                "Responses completion is missing the required response output"
            )
        status = response.get("status")
        if isinstance(status, str) and status != "completed":
            raise ProviderResponseError(
                f"Responses stream completed with unexpected status '{status}'"
            )
        self._capture_response_sources(event)
        self.last_usage = self._usage_from_response(response)
        return response, self._response_items(response)

    def _stream_answer(self, prompt: str) -> Iterable[str]:
        with self._client() as client:
            payload = self._apply_call_options(
                {"model": self.model_id, "input": prompt, "stream": True, "store": False},
                responses=True,
            )
            with client.stream(
                "POST",
                f"{self.base_url}/responses",
                json=payload,
            ) as response:
                self._raise_for_status(response)
                self.last_request_id = response.headers.get("x-request-id")
                completed = False
                for event in self._sse_payloads(response):
                    event_type = event.get("type")
                    if event_type in {
                        "response.output_text.delta",
                        "response.refusal.delta",
                    }:
                        delta = event.get("delta")
                        if isinstance(delta, str):
                            yield delta
                    elif event_type == "response.completed":
                        completed = True
                        self._complete_response_metadata(event)
                    elif event_type in {
                        "response.failed",
                        "response.incomplete",
                        "response.cancelled",
                        "error",
                    }:
                        raise ProviderHTTPError(
                            f"Responses stream terminated with {event_type}"
                        )
                if not completed:
                    raise ProviderHTTPError("Responses stream ended without response.completed")

    def stream_answer(self, prompt: str) -> Iterable[str]:
        self.last_usage = {}
        self.last_request_id = None
        self.last_sources = []
        try:
            yield from self._stream_answer(prompt)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Responses stream timed out") from exc

    def stream_chat(
        self,
        messages: list[ProviderChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterable[ProviderStreamEvent]:
        """Stream one stateless Responses turn with native tool continuation."""

        self.last_usage = {}
        self.last_request_id = None
        self.last_sources = []
        payload: dict[str, Any] = {
            "model": self.model_id,
            "input": self._response_input_from_messages(messages),
            "stream": True,
            "store": False,
            # Required by OpenAI for manual/stateless multi-turn continuation
            # of reasoning models. The returned item is persisted verbatim.
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            payload["tools"] = self._response_tool_definitions(tools)
        payload = self._apply_call_options(payload, responses=True)
        if self.actual_reasoning_effort is not None:
            reasoning = payload.get("reasoning")
            if not isinstance(reasoning, dict):
                reasoning = {}
            # OpenAI emits the summary delta events only when a summary is
            # requested. This is explicitly exposed provider output, not an
            # attempt to reconstruct hidden reasoning.
            reasoning["summary"] = "auto"
            payload["reasoning"] = reasoning

        function_call_aggregates: dict[str, dict[str, Any]] = {}
        completed = False
        try:
            with self._client() as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/responses",
                    json=payload,
                ) as response:
                    self._raise_for_status(response)
                    self.last_request_id = response.headers.get("x-request-id")
                    for event in self._sse_payloads(response):
                        event_type = event.get("type")
                        if event_type in {
                            "response.output_text.delta",
                            "response.refusal.delta",
                        }:
                            delta = event.get("delta")
                            if isinstance(delta, str) and delta:
                                for text_delta in self._text_deltas(delta):
                                    yield ProviderStreamEvent(
                                        "text_delta",
                                        content=text_delta,
                                    )
                        elif event_type == "response.reasoning_summary_text.delta":
                            delta = event.get("delta")
                            if isinstance(delta, str) and delta:
                                for reasoning_delta in self._text_deltas(delta):
                                    yield ProviderStreamEvent(
                                        "reasoning_delta",
                                        content=reasoning_delta,
                                        reasoning_kind="summary",
                                    )
                        elif event_type in {
                            "response.output_item.added",
                            "response.output_item.done",
                            "response.function_call_arguments.delta",
                            "response.function_call_arguments.done",
                        }:
                            self._merge_function_call_stream_event(
                                function_call_aggregates,
                                event,
                            )
                        elif event_type == "response.completed":
                            completed = True
                            response_payload, response_items = self._complete_response_metadata(
                                event,
                                require_output=True,
                            )
                            tool_calls = self._tool_calls_from_response_items(
                                response_items
                            )
                            if not tool_calls:
                                tool_calls = self._completed_stream_tool_calls(
                                    function_call_aggregates
                                )
                            if tool_calls:
                                yield ProviderStreamEvent(
                                    "tool_calls",
                                    tool_calls=tool_calls,
                                )
                            finish_reason = response_payload.get("status")
                            yield ProviderStreamEvent(
                                "completed",
                                finish_reason=(
                                    finish_reason
                                    if isinstance(finish_reason, str)
                                    else "completed"
                                ),
                                response_items=response_items,
                            )
                        elif event_type in {
                            "response.failed",
                            "response.incomplete",
                            "response.cancelled",
                            "error",
                        }:
                            raise ProviderHTTPError(
                                f"Responses stream terminated with {event_type}"
                            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Responses structured chat stream timed out") from exc
        if not completed:
            raise ProviderHTTPError("Responses stream ended without response.completed")

    def generate_json(self, prompt: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.last_sources = []
        payload = self._apply_call_options({
            "model": self.model_id, "input": prompt, "store": False,
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        }, responses=True)
        response = self._post_json("responses", payload)
        texts = [content.get("text") for item in response.get("output", []) if isinstance(item, dict) for content in item.get("content", []) if isinstance(content, dict) and content.get("type") == "output_text"]
        if len(texts) != 1 or not isinstance(texts[0], str):
            raise ProviderResponseError("Responses output contains no single structured text result")
        try:
            result = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("Responses structured text is invalid JSON") from exc
        self.last_usage = self._usage_from_response(response)
        return self._validate_structured_result(result, schema)


class OpenAICompatibleChatProvider(_StreamingHTTPProvider):
    """Chat Completions compatible adapter for DeepSeek and similar providers."""

    def __init__(
        self,
        *,
        structured_output_mode: str | None = None,
        supports_structured_chat: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # Chat Completions is role-aware even for a text-only model.  Image
        # input remains independently guarded by ``supports_image_input``;
        # do not turn an otherwise capable Agent endpoint into a text-only
        # prompt path merely because its selected model has no vision flag.
        self.supports_structured_chat = supports_structured_chat
        if structured_output_mode is None:
            hostname = (urlparse(self.base_url).hostname or "").casefold()
            structured_output_mode = (
                "json_object"
                if hostname == "deepseek.com" or hostname.endswith(".deepseek.com")
                else "json_schema"
            )
        if structured_output_mode not in {"json_object", "json_schema"}:
            raise ValueError("structured_output_mode must be json_object or json_schema")
        self.structured_output_mode = structured_output_mode

    def _stream_answer(self, prompt: str) -> Iterable[str]:
        with self._client() as client:
            payload = self._apply_call_options({
                "model": self.model_id,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "stream_options": {"include_usage": True},
            }, responses=False)
            with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
            ) as response:
                self._raise_for_status(response)
                self.last_request_id = response.headers.get("x-request-id")
                for event in self._sse_payloads(response):
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        input_details = usage.get("prompt_tokens_details") or {}
                        output_details = usage.get("completion_tokens_details") or {}
                        self.last_usage = {
                            "input_tokens": int(usage.get("prompt_tokens") or 0),
                            "cached_input_tokens": int(input_details.get("cached_tokens") or 0),
                            "output_tokens": int(usage.get("completion_tokens") or 0),
                            "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
                        }
                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(content, str):
                        yield from self._text_deltas(content)

    def stream_answer(self, prompt: str) -> Iterable[str]:
        self.last_usage = {}
        self.last_request_id = None
        try:
            yield from self._stream_answer(prompt)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Compatible Chat stream timed out") from exc

    @staticmethod
    def _usage_from_chat_chunk(usage: object) -> dict[str, int] | None:
        if not isinstance(usage, dict):
            return None
        input_details = usage.get("prompt_tokens_details") or {}
        output_details = usage.get("completion_tokens_details") or {}
        return {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "cached_input_tokens": int(
                input_details.get("cached_tokens") or 0
                if isinstance(input_details, dict)
                else 0
            ),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "reasoning_tokens": int(
                output_details.get("reasoning_tokens") or 0
                if isinstance(output_details, dict)
                else 0
            ),
        }

    @staticmethod
    def _merge_chat_tool_delta(
        aggregates: dict[int, dict[str, Any]],
        raw: dict[str, Any],
        fallback_index: int,
    ) -> None:
        raw_index = raw.get("index", fallback_index)
        index = raw_index if isinstance(raw_index, int) and raw_index >= 0 else fallback_index
        item = aggregates.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if isinstance(raw.get("id"), str):
            item["id"] = raw["id"]
        if isinstance(raw.get("type"), str):
            item["type"] = raw["type"]
        function = raw.get("function")
        if not isinstance(function, dict):
            return
        target = item["function"]
        if isinstance(function.get("name"), str):
            target["name"] += function["name"]
        if isinstance(function.get("arguments"), str):
            target["arguments"] += function["arguments"]

    @staticmethod
    def _chat_stream_error_message(error: object) -> str:
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message") or error.get("msg")
            parts = [part for part in (code, message) if isinstance(part, str) and part.strip()]
            if parts:
                return "; ".join(parts)[:300]
        if isinstance(error, str) and error.strip():
            return error.strip()[:300]
        return "Compatible Chat stream returned an error event"

    def stream_chat(
        self,
        messages: list[ProviderChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterable[ProviderStreamEvent]:
        """Stream a role-aware OpenAI Chat Completions request.

        Text-only compatible models can use this role-aware transport for
        Agent tool calls.  Image parts are still inserted only after the
        selected model's explicit ``supports_image_input`` confirmation.
        """

        self.last_usage = {}
        self.last_request_id = None
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": [message.as_payload() for message in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        payload = self._apply_call_options(payload, responses=False)
        tool_aggregates: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        completed = False
        saw_model_output = False
        try:
            with self._client() as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=payload,
                ) as response:
                    self._raise_for_status(response)
                    self.last_request_id = (
                        response.headers.get("x-request-id")
                        or response.headers.get("request-id")
                    )
                    for line in response.iter_lines():
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            completed = True
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError as exc:
                            raise ProviderResponseError(
                                "Compatible Chat returned invalid SSE JSON"
                            ) from exc
                        if not isinstance(event, dict):
                            raise ProviderResponseError(
                                "Compatible Chat returned an invalid SSE event"
                            )
                        if event.get("error"):
                            raise ProviderHTTPError(
                                self._chat_stream_error_message(event.get("error"))
                            )
                        usage = self._usage_from_chat_chunk(event.get("usage"))
                        if usage is not None:
                            self.last_usage = usage
                        choices = event.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue
                        choice = choices[0]
                        if not isinstance(choice, dict):
                            continue
                        raw_finish = choice.get("finish_reason")
                        if isinstance(raw_finish, str) and raw_finish:
                            finish_reason = raw_finish
                            # Proxies may omit the trailing [DONE] after a terminal
                            # finish_reason. Treat that as a complete turn.
                            completed = True
                        delta = choice.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            saw_model_output = True
                            for text_delta in self._text_deltas(content):
                                yield ProviderStreamEvent("text_delta", content=text_delta)
                        raw_tool_calls = delta.get("tool_calls")
                        if isinstance(raw_tool_calls, list):
                            for fallback_index, raw_tool_call in enumerate(raw_tool_calls):
                                if isinstance(raw_tool_call, dict):
                                    saw_model_output = True
                                    self._merge_chat_tool_delta(
                                        tool_aggregates,
                                        raw_tool_call,
                                        fallback_index,
                                    )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Compatible Chat structured stream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderHTTPError(
                f"Compatible Chat stream transport failed ({type(exc).__name__})"
            ) from exc

        if not completed:
            if finish_reason:
                completed = True
            else:
                raise ProviderHTTPError(
                    "Compatible Chat stream ended before completion "
                    f"(finish_reason={finish_reason!r}, output={saw_model_output})"
                )
        tool_calls = [
            item
            for _, item in sorted(tool_aggregates.items())
            if item.get("id")
            and isinstance(item.get("function"), dict)
            and item["function"].get("name")
        ]
        if tool_calls:
            yield ProviderStreamEvent("tool_calls", tool_calls=tool_calls)
        yield ProviderStreamEvent("completed", finish_reason=finish_reason)

    def generate_json(self, prompt: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self.structured_output_mode == "json_object":
            schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            messages = [
                {
                    "role": "system",
                    "content": "Return exactly one valid JSON object and no Markdown or commentary.",
                },
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\nReturn JSON matching the schema named {schema_name}. "
                        f"Every required field and constraint must be satisfied. JSON Schema: {schema_json}"
                    ),
                },
            ]
            response_format: dict[str, Any] = {"type": "json_object"}
        else:
            messages = [{"role": "user", "content": prompt}]
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
        payload = self._apply_call_options({
            "model": self.model_id,
            "messages": messages,
            "response_format": response_format,
        }, responses=False)
        response = self._post_json("chat/completions", payload)
        try:
            content = response["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError("Compatible Chat structured response is invalid") from exc
        usage = response.get("usage") or {}
        input_details = usage.get("prompt_tokens_details") or {}
        output_details = usage.get("completion_tokens_details") or {}
        self.last_usage = {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "cached_input_tokens": int(input_details.get("cached_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
        }
        return self._validate_structured_result(result, schema)
