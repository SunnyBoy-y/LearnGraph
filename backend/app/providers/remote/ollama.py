from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.providers.remote.embedding import OpenAICompatibleEmbeddingProvider
from app.providers.remote.openai import (
    OpenAICompatibleChatProvider,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
    merge_provider_request_headers,
    normalize_openai_api_base_url,
)


DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
# Ollama's OpenAI-compatible surface accepts any non-empty bearer value; clients
# conventionally send ``ollama`` when no real key is configured.
DEFAULT_OLLAMA_API_KEY = "ollama"


def is_ollama_provider_type(provider_type: str | None) -> bool:
    return (provider_type or "").strip().casefold() in {"ollama", "ollama_embedding"}


def resolve_ollama_api_key(api_key: str | None) -> str:
    """Return a usable bearer token for Ollama (real secret or local placeholder)."""

    cleaned = (api_key or "").strip()
    return cleaned or DEFAULT_OLLAMA_API_KEY


def normalize_ollama_api_base_url(base_url: str) -> str:
    """Normalize an Ollama base URL to the OpenAI-compatible ``/v1`` root.

    Accepts both ``http://host:11434`` and ``http://host:11434/v1`` so users can
    paste either the native origin or the documented OpenAI-compatible root.
    """

    normalized = normalize_openai_api_base_url(base_url.strip()).rstrip("/")
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return normalized
    path = parsed.path.rstrip("/")
    if path in {"", "/"}:
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", "", ""))
    if path.endswith("/api"):
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1", "", ""))
    return normalized


def ollama_native_origin(base_url: str) -> str:
    """Return the native Ollama origin (no ``/v1``) used by ``/api/tags``."""

    normalized = normalize_ollama_api_base_url(base_url)
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return normalized.removesuffix("/v1")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")] or ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def discover_ollama_models(
    *,
    base_url: str,
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 15.0,
    extra_headers: dict[str, str] | None = None,
) -> list[str]:
    """List local Ollama models via OpenAI ``/v1/models``, falling back to ``/api/tags``."""

    openai_base = normalize_ollama_api_base_url(base_url)
    bearer = resolve_ollama_api_key(api_key)
    headers = merge_provider_request_headers(
        api_key=bearer,
        extra_headers=extra_headers,
    )
    try:
        with httpx.Client(
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        ) as client:
            response = client.get(f"{openai_base}/models")
            if response.is_success:
                try:
                    payload = response.json()
                except json.JSONDecodeError as exc:
                    raise ProviderResponseError(
                        "Ollama model discovery returned non-JSON data"
                    ) from exc
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    model_ids = [
                        item.get("id")
                        for item in data
                        if isinstance(item, dict)
                        and isinstance(item.get("id"), str)
                        and item["id"].strip()
                    ]
                    if model_ids:
                        return sorted(set(model_ids))
            # Fall back to the native tags endpoint when /v1/models is missing or empty.
            native = ollama_native_origin(base_url)
            tags = client.get(f"{native}/api/tags")
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError("Ollama model discovery timed out") from exc
    except httpx.HTTPError as exc:
        raise ProviderHTTPError(f"Ollama model discovery failed: {exc}") from exc

    if not tags.is_success:
        raise ProviderHTTPError(
            f"Ollama model discovery returned HTTP {tags.status_code}",
            status_code=tags.status_code,
        )
    try:
        payload = tags.json()
    except json.JSONDecodeError as exc:
        raise ProviderResponseError("Ollama /api/tags returned non-JSON data") from exc
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ProviderResponseError("Ollama /api/tags response has no models array")
    model_ids: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name.strip():
            model_ids.append(name.strip())
    if not model_ids:
        return []
    return sorted(set(model_ids))


def coalesce_ollama_chat_messages(
    messages: list[Any],
) -> list[dict[str, Any]]:
    """Normalize LearnGraph chat turns for Ollama model templates.

    Many Ollama templates (Qwen3 / MiniCPM / etc.) accept at most **one** leading
    ``system`` message and raise:

        System message must be at the beginning.

    when a second system turn appears later. LearnGraph always emits several
    system turns (style, mode policy, agent guidance, workspace context). Fold
    every leading system into a single first message, and rewrite any later
    system turns as a note on the next user message so the transcript remains
    legal without dropping policy text.
    """

    from app.providers.ports.model import ProviderChatMessage

    payloads: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, ProviderChatMessage):
            payloads.append(message.as_payload())
        elif isinstance(message, dict):
            payloads.append(dict(message))
        else:
            raise TypeError("Ollama chat messages must be ProviderChatMessage or dict")

    leading_systems: list[str] = []
    deferred_systems: list[str] = []
    rest: list[dict[str, Any]] = []
    seen_non_system = False

    def _system_text(payload: dict[str, Any]) -> str:
        content = payload.get("content")
        if isinstance(content, str):
            return content.strip()
        return ""

    for payload in payloads:
        if payload.get("role") == "system":
            text = _system_text(payload)
            if not text:
                continue
            if not seen_non_system:
                leading_systems.append(text)
            else:
                deferred_systems.append(text)
            continue
        seen_non_system = True
        item = dict(payload)
        if deferred_systems and item.get("role") == "user":
            note = "\n\n".join(deferred_systems)
            deferred_systems = []
            user_content = item.get("content")
            if isinstance(user_content, str) and user_content:
                item["content"] = f"[System note]\n{note}\n\n{user_content}"
            elif user_content is None or user_content == "":
                item["content"] = f"[System note]\n{note}"
            # Multimodal content arrays: leave as-is and keep the note as a
            # separate leading system if we still have capacity; otherwise drop
            # into leading_systems for the single system block.
            else:
                leading_systems.append(note)
        rest.append(item)

    if deferred_systems:
        # No subsequent user turn to attach to (e.g. tool-only tails). Keep the
        # text by merging into the leading system block.
        leading_systems.extend(deferred_systems)

    result: list[dict[str, Any]] = []
    if leading_systems:
        result.append({"role": "system", "content": "\n\n".join(leading_systems)})
    result.extend(rest)
    # Template still requires at least one user turn for several local models.
    if not any(item.get("role") == "user" for item in result):
        result.append({"role": "user", "content": "Continue."})
    return result


def normalize_ollama_tool_definitions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy tool definitions without string-length grammar constraints.

    Ollama's llama.cpp grammar compiler can reject otherwise valid nested tool
    schemas containing ``maxLength`` (notably a value of 2000 inside an array
    item). LearnGraph validates tool arguments again before execution, so this
    compatibility copy only relaxes model-side constrained decoding.
    """

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: normalize(item)
                for key, item in value.items()
                if key != "maxLength"
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    return [normalize(tool) for tool in tools]


class OllamaChatProvider(OpenAICompatibleChatProvider):
    """Local Ollama chat adapter over the OpenAI-compatible Chat Completions API.

    Differences from the generic compatible adapter:
    - API key is optional (defaults to the conventional ``ollama`` bearer).
    - Thinking is controlled by Ollama's top-level ``think`` field
      (bool or ``low``/``medium``/``high``/``max``).
    - Streaming omits ``stream_options`` (not required by Ollama and rejected by
      some older local builds).
    - Multiple leading ``system`` turns are coalesced (many Ollama templates
      reject non-leading system messages with HTTP 400).
    - Reasoning deltas are accepted from ``reasoning_content``, ``thinking``,
      and Ollama's OpenAI-compat ``reasoning`` field.
    """

    supports_agent_tools = True

    def __init__(self, **kwargs: Any) -> None:
        if "base_url" in kwargs and isinstance(kwargs["base_url"], str):
            kwargs["base_url"] = normalize_ollama_api_base_url(kwargs["base_url"])
        kwargs["api_key"] = resolve_ollama_api_key(kwargs.get("api_key"))
        # Prefer json_object: many local models do not implement strict json_schema.
        kwargs.setdefault("structured_output_mode", "json_object")
        super().__init__(**kwargs)

    def _apply_call_options(self, payload: dict[str, Any], *, responses: bool) -> dict[str, Any]:
        # Skip OpenAI-style reasoning_effort injection; Ollama uses ``think``.
        options = self.call_options
        if options is None or responses:
            return payload
        mode = options.thinking_mode
        if mode == "off":
            payload["think"] = False
        else:
            actual = options.actual_reasoning_effort
            if isinstance(actual, bool):
                payload["think"] = actual
            elif isinstance(actual, str) and actual.casefold() in {
                "low",
                "medium",
                "high",
                "max",
                "true",
                "false",
            }:
                lowered = actual.casefold()
                if lowered == "true":
                    payload["think"] = True
                elif lowered == "false":
                    payload["think"] = False
                else:
                    payload["think"] = lowered
            else:
                # Default mapping when capability snapshots have no explicit value.
                payload["think"] = {
                    "low": "low",
                    "medium": "medium",
                    "high": "high",
                    "xhigh": "max",
                }.get(mode, True)
        # Surface any extra provider_options except OpenAI-only thinking keys.
        for key, value in options.provider_options.items():
            if key in {"enable_thinking", "thinking_budget", "thinking", "think"}:
                continue
            payload[key] = value
        return payload

    @staticmethod
    def _reasoning_delta_text(delta: dict[str, Any]) -> str | None:
        """Return the first non-empty reasoning fragment from a chat delta."""

        for key in ("reasoning_content", "thinking", "reasoning"):
            value = delta.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _stream_answer(self, prompt: str):
        with self._client() as client:
            payload = self._apply_call_options(
                {
                    "model": self.model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": True,
                },
                responses=False,
            )
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
                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    if not isinstance(delta, dict):
                        continue
                    content = delta.get("content")
                    if isinstance(content, str):
                        yield from self._text_deltas(content)

    def stream_chat(self, messages, *, tools=None):
        # Copy of the parent stream with Ollama-specific payload tweaks and an
        # extra reasoning field (``thinking`` / ``reasoning``) accepted from
        # OpenAI-compat deltas.
        from app.providers.ports.model import ProviderStreamEvent

        self.last_usage = {}
        self.last_sources = []
        self.last_request_id = None
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": coalesce_ollama_chat_messages(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = normalize_ollama_tool_definitions(tools)
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
                                "Ollama returned invalid SSE JSON"
                            ) from exc
                        if not isinstance(event, dict):
                            raise ProviderResponseError(
                                "Ollama returned an invalid SSE event"
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
                            completed = True
                        delta = choice.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            saw_model_output = True
                            for text_delta in self._text_deltas(content):
                                yield ProviderStreamEvent("text_delta", content=text_delta)
                        reasoning_content = self._reasoning_delta_text(delta)
                        if reasoning_content:
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
            raise ProviderTimeoutError("Ollama chat stream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderHTTPError(
                f"Ollama stream transport failed ({type(exc).__name__})"
            ) from exc

        if not completed:
            if finish_reason:
                completed = True
            else:
                raise ProviderHTTPError(
                    "Ollama stream ended before completion "
                    f"(finish_reason={finish_reason!r}, output={saw_model_output})"
                )
        tool_calls = self._completed_chat_tool_calls(tool_aggregates)
        if tool_calls:
            yield ProviderStreamEvent("tool_calls", tool_calls=tool_calls)
        yield ProviderStreamEvent("completed", finish_reason=finish_reason)


class OllamaEmbeddingProvider(OpenAICompatibleEmbeddingProvider):
    """Ollama embeddings over the OpenAI-compatible ``/v1/embeddings`` endpoint."""

    def __init__(self, **kwargs: Any) -> None:
        if "base_url" in kwargs and isinstance(kwargs["base_url"], str):
            kwargs["base_url"] = normalize_ollama_api_base_url(kwargs["base_url"])
        kwargs["api_key"] = resolve_ollama_api_key(kwargs.get("api_key"))
        super().__init__(**kwargs)
