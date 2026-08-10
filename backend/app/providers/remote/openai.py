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
from app.providers.qwen_catalog import is_dashscope_origin
from app.providers.ports.model import ProviderChatMessage, ProviderStreamEvent


class ProviderHTTPError(RuntimeError):
    """Provider call failure.

    ``status_code`` carries the upstream HTTP status when the failure came
    from an HTTP response, so callers can tell transient gateway errors
    (502/503/529) from permanent request errors. Transport-level failures
    leave it ``None``.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderTimeoutError(ProviderHTTPError):
    pass


class ProviderResponseError(ProviderHTTPError):
    pass


class ProviderInvalidUrlError(ProviderHTTPError):
    """The configured base URL cannot be issued as an HTTP(S) request."""


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a Pydantic schema to the strict structured-output subset."""

    def convert(node: object) -> object:
        if isinstance(node, list):
            return [convert(item) for item in node]
        if not isinstance(node, dict):
            return node

        result: dict[str, Any] = {}
        if "$ref" in node and isinstance(node["$ref"], str):
            result["$ref"] = node["$ref"]
        if "anyOf" in node and isinstance(node["anyOf"], list):
            result["anyOf"] = [convert(item) for item in node["anyOf"]]
        elif "oneOf" in node and isinstance(node["oneOf"], list):
            result["anyOf"] = [convert(item) for item in node["oneOf"]]
        elif isinstance(node.get("type"), str):
            result["type"] = node["type"]
        elif isinstance(node.get("type"), list):
            result["anyOf"] = [
                {"type": item}
                for item in node["type"]
                if isinstance(item, str)
            ]

        if isinstance(node.get("enum"), list):
            result["enum"] = list(node["enum"])

        properties = node.get("properties")
        if isinstance(properties, dict):
            result["type"] = "object"
            result["properties"] = {
                str(name): convert(value) for name, value in properties.items()
            }
            result["required"] = [str(name) for name in properties]
            result["additionalProperties"] = False
        elif result.get("type") == "object":
            result["properties"] = {}
            result["required"] = []
            result["additionalProperties"] = False

        if result.get("type") == "array" and "items" in node:
            result["items"] = convert(node["items"])

        if isinstance(node.get("$defs"), dict):
            result["$defs"] = {
                str(name): convert(value) for name, value in node["$defs"].items()
            }
        return result

    converted = convert(schema)
    return converted if isinstance(converted, dict) else {}


def _parse_json_object_text(content: object) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ProviderResponseError("Provider response contains no JSON text")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ProviderResponseError("Provider response does not contain a JSON object") from None
        try:
            result = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderResponseError("Provider response contains invalid JSON") from exc
    if not isinstance(result, dict):
        raise ProviderResponseError("Structured result must be an object")
    return result


def _is_aliyun_responses_endpoint(base_url: str) -> bool:
    """Return whether ``base_url`` addresses Alibaba Cloud Model Studio Responses.

    Both the classic ``dashscope.aliyuncs.com/compatible-mode/v1`` origin and the
    newer workspace form ``{WorkspaceId}.cn-beijing.maas.aliyuncs.com/...`` live
    under ``aliyuncs.com``.  This is consulted only on the Responses protocol
    (``OpenAIResponsesProvider``), where Alibaba Cloud documents a
    ``reasoning.effort`` ``none`` tier that disables thinking; native OpenAI's
    ``api.openai.com`` never matches.
    """

    if not base_url:
        return False
    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    return bool(host) and host.endswith(".aliyuncs.com")


def normalize_openai_api_base_url(base_url: str) -> str:
    """Return the documented ``/v1`` root for the official OpenAI hostname.

    Workspace configuration normally comes from the provider catalog and is
    already ``https://api.openai.com/v1``.  Normalizing the historical/root
    spelling here keeps both model discovery and the native Responses adapter
    on the documented API root without rewriting custom compatible endpoints.

    This stays pure (never raises): provider construction also routes through
    it, where a stored-but-unusable URL must not crash adapter wiring.
    Discovery validates the URL with :func:`validate_http_base_url` first.
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


def validate_http_base_url(base_url: str) -> str:
    """Reject Base URLs that httpx could never issue as an HTTP(S) request.

    A missing scheme (``api.deepseek.com``) or a non-HTTP scheme used to crash
    discovery with ``httpcore.UnsupportedProtocol`` and turn a user input
    mistake into a backend 500.  Raises :class:`ProviderInvalidUrlError` so the
    service layer can map it to a clear 4xx instead.
    """

    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ProviderInvalidUrlError("Provider base URL is missing")
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        raise ProviderInvalidUrlError(
            f"Provider base URL is not a valid URL (got {normalized!r})"
        ) from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderInvalidUrlError(
            f"Provider base URL must start with http:// or https:// (got {normalized!r})"
        )
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

    # API keys are paste artifacts: strip trailing whitespace/newlines so a
    # copied key never produces an invalid Authorization header that httpx/h11
    # rejects as a LocalProtocolError.
    api_key = api_key.strip()

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
    validate_http_base_url(base_url)
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
    except httpx.HTTPError as exc:
        # Transport-level failures after URL validation (connection refused,
        # DNS, resets); without this catch they would escape as a raw 500.
        # Protocol-less URLs are rejected earlier by validate_http_base_url.
        raise ProviderHTTPError(f"Provider model discovery failed: {exc}") from exc
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


class _ReusableHTTPClient:
    """Keep one httpx.Client alive while preserving ``with`` call sites.

    The context-manager protocol is intentionally a no-op for exit so provider
    adapters can continue using ``with self._client() as client`` without
    tearing down the connection pool between Agent tool rounds.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __enter__(self) -> "_ReusableHTTPClient":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


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
        self.supports_video_input = (
            self.capabilities.get("supports_video_input") is True
        )
        self.extra_headers = dict(extra_headers or {})
        self.authorization_scheme = authorization_scheme
        self._http_client: _ReusableHTTPClient | None = None

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = _ReusableHTTPClient(self._build_http_client())
        return self._http_client

    def _build_http_client(self) -> httpx.Client:
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
        thinking_mode = options.thinking_mode
        if thinking_mode == "off" and responses and _is_aliyun_responses_endpoint(
            self.base_url
        ):
            # Alibaba Cloud Model Studio's Responses-compatible endpoint exposes
            # ``none`` as the lowest reasoning.effort tier and documents it as
            # the way to disable thinking (it also outranks ``enable_thinking``).
            # Send it so 极速 really turns reasoning off. Native OpenAI's API has
            # no ``none`` tier; its reasoning models are flagged thinking_required
            # so fast mode never reaches here for them.
            payload["reasoning"] = {"effort": "none"}
        elif actual is not None and options.reasoning_parameter in {
            "reasoning_effort",
            "reasoning.effort",
        }:
            if responses or options.reasoning_parameter == "reasoning.effort":
                payload["reasoning"] = {"effort": actual}
            else:
                payload["reasoning_effort"] = actual
        if not responses:
            payload.update(options.provider_options)
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
        request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
        try:
            # Streaming responses (``client.stream``) have an unread body here;
            # accessing ``.text`` without ``read()`` raises httpx.ResponseNotRead.
            response.read()
            detail = response.text[:500]
        except (httpx.HTTPError, httpx.StreamError):
            detail = "<error body unavailable>"
        raise ProviderHTTPError(
            f"Provider returned HTTP {response.status_code}; request_id={request_id}; body={detail}",
            status_code=response.status_code,
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
            # A relay that echoes the identity keys as blanks must not erase
            # what the opening item already established; an empty call id is
            # discarded downstream and would lose the call silently.
            if isinstance(call_id, str) and call_id:
                aggregate["id"] = call_id
            if isinstance(name, str) and name:
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
        if isinstance(call_id, str) and call_id:
            aggregate["id"] = call_id
        if isinstance(name, str) and name:
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
        wire_schema = _strict_json_schema(schema)
        request_input = prompt
        payload: dict[str, Any] = {
            "model": self.model_id,
            "input": request_input,
            "store": False,
        }
        if self.capabilities.get("supports_structured_output") is False:
            schema_json = json.dumps(
                wire_schema,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            payload["input"] = (
                f"{prompt}\n\nReturn exactly one valid JSON object matching the schema "
                f"named {schema_name}. Do not use Markdown or add commentary. "
                f"Every field must be present and valid. JSON Schema: {schema_json}"
            )
        else:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": wire_schema,
                }
            }
        payload = self._apply_call_options(payload, responses=True)
        response = self._post_json("responses", payload)
        texts = [content.get("text") for item in response.get("output", []) if isinstance(item, dict) for content in item.get("content", []) if isinstance(content, dict) and content.get("type") == "output_text"]
        if len(texts) != 1 or not isinstance(texts[0], str):
            raise ProviderResponseError("Responses output contains no single structured text result")
        result = _parse_json_object_text(texts[0])
        self.last_usage = self._usage_from_response(response)
        return self._validate_structured_result(result, wire_schema)


class OpenAICompatibleChatProvider(_StreamingHTTPProvider):
    """Chat Completions compatible adapter for DeepSeek and similar providers."""

    # Function calling is part of the Chat Completions contract implemented by
    # ``stream_chat`` below.  Keep this explicit so generic compatible
    # providers receive the same Agent capability declaration as native
    # OpenAI, Anthropic, and DeepSeek adapters.
    supports_agent_tools = True

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
                else "json_object"
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
        # Identity fields are announced once and then repeated as blanks by some
        # gateways.  OpenAI and DeepSeek omit ``id``/``name`` on continuation
        # fragments, but DashScope echoes ``"id": ""`` and ``"name": ""`` on
        # every argument chunk.  Overwriting with those blanks erases the call
        # identity and the completed tool call is dropped by the filter below,
        # which silently disables Agent mode on that gateway.
        if isinstance(raw.get("id"), str) and raw["id"]:
            item["id"] = raw["id"]
        if isinstance(raw.get("type"), str) and raw["type"]:
            item["type"] = raw["type"]
        function = raw.get("function")
        if not isinstance(function, dict):
            return
        target = item["function"]
        if isinstance(function.get("name"), str) and function["name"]:
            target["name"] += function["name"]
        if isinstance(function.get("arguments"), str):
            target["arguments"] += function["arguments"]

    @staticmethod
    def _completed_chat_tool_calls(
        aggregates: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return the tool calls a compatible gateway finished streaming.

        A function name is the only field the caller cannot substitute, so it
        stays required.  ``id`` is synthesized when a gateway never sends one:
        it is only correlation state that LearnGraph echoes back as
        ``tool_call_id``, and the same object is replayed on both the assistant
        turn and its tool result, so a local identifier round-trips correctly.
        """

        tool_calls: list[dict[str, Any]] = []
        for index, item in sorted(aggregates.items()):
            function = item.get("function")
            if not isinstance(function, dict) or not function.get("name"):
                continue
            if not item.get("id"):
                item["id"] = f"call_{index}"
            if not item.get("type"):
                item["type"] = "function"
            tool_calls.append(item)
        return tool_calls

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

    def _capture_chat_sources(self, event: dict[str, Any]) -> None:
        candidates: list[object] = [
            event.get("search_info"),
            event.get("web_search"),
        ]
        choices = event.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
            candidates.extend(
                [
                    choice.get("search_info"),
                    choice.get("web_search"),
                ]
            )
            for container_key in ("delta", "message"):
                container = choice.get(container_key)
                if isinstance(container, dict):
                    candidates.extend(
                        [
                            container.get("search_info"),
                            container.get("web_search"),
                        ]
                    )
        sources = list(self.last_sources)
        seen = {
            item.get("url")
            for item in sources
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        }
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            raw_results = (
                candidate.get("search_results")
                or candidate.get("results")
                or candidate.get("sources")
            )
            if not isinstance(raw_results, list):
                continue
            for raw in raw_results:
                if not isinstance(raw, dict):
                    continue
                url = raw.get("url") or raw.get("link")
                if (
                    not isinstance(url, str)
                    or not url.startswith(("http://", "https://"))
                    or url in seen
                ):
                    continue
                seen.add(url)
                sources.append(
                    {
                        "index": len(sources) + 1,
                        "url": url,
                        "title": str(
                            raw.get("title") or raw.get("site_name") or url
                        )[:1_000],
                    }
                )
        self.last_sources = sources

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
        self.last_sources = []
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
                        self._capture_chat_sources(event)
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
                        reasoning_content = delta.get("reasoning_content")
                        if isinstance(reasoning_content, str) and reasoning_content:
                            saw_model_output = True
                            for reasoning_delta in self._text_deltas(reasoning_content):
                                yield ProviderStreamEvent(
                                    "reasoning_delta",
                                    content=reasoning_delta,
                                    reasoning_kind="summary",
                                )
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
        tool_calls = self._completed_chat_tool_calls(tool_aggregates)
        if tool_calls:
            yield ProviderStreamEvent("tool_calls", tool_calls=tool_calls)
        yield ProviderStreamEvent("completed", finish_reason=finish_reason)

    def _generate_prompted_json(
        self,
        prompt: str,
        schema_name: str,
        wire_schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_json = json.dumps(wire_schema, ensure_ascii=False, separators=(",", ":"))
        messages = [
            {
                "role": "system",
                "content": "Return exactly one valid JSON object and no Markdown or commentary.",
            },
            {
                "role": "user",
                "content": (
                    f"{prompt}\n\nReturn JSON matching the schema named {schema_name}. "
                    f"Every field must be present and valid. JSON Schema: {schema_json}"
                ),
            },
        ]
        payload = self._apply_call_options(
            {
                "model": self.model_id,
                "messages": messages,
            },
            responses=False,
        )
        response = self._post_json("chat/completions", payload)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError("Provider response contains no chat text") from exc
        result = _parse_json_object_text(content)
        self.last_usage = self._usage_from_chat_chunk(response.get("usage")) or {}
        return self._validate_structured_result(result, wire_schema)

    def generate_json(self, prompt: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        wire_schema = _strict_json_schema(schema)
        if self.capabilities.get("supports_structured_output") is False:
            return self._generate_prompted_json(prompt, schema_name, wire_schema)
        if self.structured_output_mode == "json_object":
            schema_json = json.dumps(wire_schema, ensure_ascii=False, separators=(",", ":"))
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
                    "schema": wire_schema,
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
        return self._validate_structured_result(result, wire_schema)


class QwenChatProvider(OpenAICompatibleChatProvider):
    """DashScope OpenAI-compatible Chat adapter.

    Qwen thinking controls are extra body fields rather than OpenAI's standard
    reasoning shape. Streaming reasoning arrives as ``reasoning_content`` and
    is normalized by the parent adapter.
    """

    def __init__(self, *args: Any, preserve_thinking: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.preserve_thinking = preserve_thinking

    def _apply_call_options(
        self,
        payload: dict[str, Any],
        *,
        responses: bool,
    ) -> dict[str, Any]:
        payload = super()._apply_call_options(payload, responses=responses)
        # DashScope enables thinking by default for several Qwen generations.
        # Older LearnGraph provider rows may still carry a generic
        # ``reasoning_effort`` capability snapshot, so relying only on
        # provider_options would omit the disable switch in fast mode.
        # Make the product-level "off" choice authoritative at the wire.
        if (
            not responses
            and self.call_options is not None
            and self.call_options.thinking_mode == "off"
        ):
            payload["enable_thinking"] = False
        # DashScope's built-in search is a whole-turn alternative to function
        # calling, not an additional tool: compatible mode rejects the request
        # when ``enable_search`` accompanies ``tools``.  An Agent turn always
        # carries function tools and reaches the web through its own search
        # lane, so the hosted switch yields to them instead of failing the turn.
        # The switch is DashScope wire dialect: third-party OpenAI-compatible
        # relays reject ``enable_search`` with UNKNOWN_FIELD (HTTP 400), so it
        # is only emitted against a DashScope / Model Studio origin.
        if (
            not responses
            and not payload.get("tools")
            and self.call_options is not None
            and self.call_options.native_web_search
            and is_dashscope_origin(self.base_url)
        ):
            payload["enable_search"] = True
            strategy = self.capabilities.get("chat_search_strategy")
            if strategy not in {"turbo", "max", "agent", "agent_max"}:
                strategy = "turbo"
            payload.setdefault(
                "search_options",
                {"search_strategy": strategy, "enable_source": True},
            )
        # ``preserve_thinking`` only controls how reasoning state is carried
        # across turns.  Sending it during a fast call is contradictory to the
        # explicit ``enable_thinking=false`` override and can make DashScope
        # continue a previous thinking turn.  Keep fast mode unambiguous.
        # Like ``enable_search`` it is DashScope-only wire dialect; relays
        # reject it as an unknown field, so only DashScope origins emit it.
        if (
            not responses
            and self.preserve_thinking
            and self.call_options is not None
            and self.call_options.thinking_mode != "off"
            and is_dashscope_origin(self.base_url)
        ):
            payload["preserve_thinking"] = True
        return payload

    def generate_json(
        self,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        # DashScope JSON mode rejects enable_thinking=true. Models that require
        # thinking must therefore use an ordinary chat completion whose prompt
        # carries the schema instead of being rejected before the HTTP call.
        if self.capabilities.get("thinking_required") is True:
            return self._generate_prompted_json(
                prompt,
                schema_name,
                _strict_json_schema(schema),
            )
        original_options = self.call_options
        if original_options is not None:
            self.call_options = ModelCallOptions(
                thinking_mode="off",
                actual_reasoning_effort=None,
                reasoning_parameter=original_options.reasoning_parameter,
                search_route="disabled",
                native_web_search=False,
                provider_options={"enable_thinking": False},
            )
        try:
            return super().generate_json(prompt, schema_name, schema)
        finally:
            self.call_options = original_options
