from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from app.domain.schemas.research import SearchResult
from app.providers.ports.model import ProviderStreamEvent
from app.providers.remote.ollama import (
    coalesce_ollama_chat_messages,
    normalize_ollama_api_base_url,
    normalize_ollama_tool_definitions,
    ollama_native_origin,
    ollama_think_value,
)
from app.providers.remote.openai import (
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
    _StreamingHTTPProvider,
)
from app.providers.remote.search import (
    SearchProviderError,
    SearchProviderResponseError,
    SearchProviderTimeout,
    domain_is_allowed,
    normalize_domain,
)


class OllamaCloudChatProvider(_StreamingHTTPProvider):
    """ollama.com Cloud chat adapter over the native ``/api/chat`` NDJSON stream.

    Ollama Cloud is reached over normal HTTPS with the real ``OLLAMA_API_KEY``
    (unlike local Ollama, which substitutes a placeholder and bypasses the
    environment proxy).  The base URL is normalized to the ``/v1`` root; native
    endpoints are derived back to the bare origin.
    """

    available = True
    remote_capability = True
    supports_agent_tools = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = normalize_ollama_api_base_url(self.base_url)
        self.native_origin = ollama_native_origin(self.base_url)
        if not self.api_key or not str(self.api_key).strip():
            raise ValueError("Ollama Cloud requires an encrypted API key")

    def _apply_call_options(self, payload: dict[str, Any], *, responses: bool) -> dict[str, Any]:
        del responses
        options = self.call_options
        if options is None:
            return payload
        payload["think"] = ollama_think_value(
            options.thinking_mode,
            options.actual_reasoning_effort,
        )
        # Surface any extra provider_options except OpenAI-only thinking keys.
        for key, value in options.provider_options.items():
            if key in {"enable_thinking", "thinking_budget", "thinking", "think"}:
                continue
            payload[key] = value
        return payload

    @staticmethod
    def _cloud_error_message(error: object) -> str:
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message") or error.get("msg")
            parts = [part for part in (code, message) if isinstance(part, str) and part.strip()]
            if parts:
                return "; ".join(parts)[:300]
        if isinstance(error, str) and error.strip():
            return error.strip()[:300]
        return "Ollama Cloud stream returned an error event"

    def _stream_answer(self, prompt: str):
        from app.providers.ports.model import ProviderChatMessage

        for event in self.stream_chat([ProviderChatMessage(role="user", content=prompt)]):
            if event.type == "text_delta" and event.content:
                yield event.content

    def stream_chat(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
    ):
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

        tool_calls: list[dict[str, Any]] = []
        saw_model_output = False
        done_reason: str | None = None
        completed = False
        try:
            with self._client() as client:
                with client.stream(
                    "POST",
                    f"{self.native_origin}/api/chat",
                    json=payload,
                ) as response:
                    self._raise_for_status(response)
                    self.last_request_id = (
                        response.headers.get("request-id")
                        or response.headers.get("x-request-id")
                    )
                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ProviderResponseError(
                                "Ollama Cloud returned invalid NDJSON"
                            ) from exc
                        if not isinstance(event, dict):
                            continue
                        if event.get("error"):
                            raise ProviderHTTPError(
                                self._cloud_error_message(event.get("error"))
                            )
                        if event.get("done") is True:
                            completed = True
                            raw_reason = event.get("done_reason")
                            if isinstance(raw_reason, str) and raw_reason:
                                done_reason = raw_reason
                            break
                        message = event.get("message")
                        if not isinstance(message, dict):
                            continue
                        thinking = message.get("thinking")
                        if isinstance(thinking, str) and thinking:
                            saw_model_output = True
                            for chunk in self._text_deltas(thinking):
                                yield ProviderStreamEvent(
                                    "reasoning_delta",
                                    content=chunk,
                                    reasoning_kind="summary",
                                )
                        content = message.get("content")
                        if isinstance(content, str) and content:
                            saw_model_output = True
                            for chunk in self._text_deltas(content):
                                yield ProviderStreamEvent("text_delta", content=chunk)
                        raw_tools = message.get("tool_calls")
                        if isinstance(raw_tools, list):
                            for raw_tool in raw_tools:
                                if isinstance(raw_tool, dict):
                                    saw_model_output = True
                                    normalized = self._normalize_tool_call(
                                        raw_tool, len(tool_calls)
                                    )
                                    if normalized is not None:
                                        tool_calls.append(normalized)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Ollama Cloud chat stream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderHTTPError(
                f"Ollama Cloud stream transport failed ({type(exc).__name__})"
            ) from exc

        if not completed:
            raise ProviderHTTPError(
                "Ollama Cloud stream ended before completion "
                f"(done_reason={done_reason!r}, output={saw_model_output})"
            )
        if tool_calls:
            yield ProviderStreamEvent("tool_calls", tool_calls=tool_calls)
        yield ProviderStreamEvent("completed", finish_reason=done_reason)

    @staticmethod
    def _normalize_tool_call(
        raw: dict[str, Any], index: int
    ) -> dict[str, Any] | None:
        """Normalize a native Ollama tool call to the OpenAI-compatible shape.

        Ollama's native ``/api/chat`` returns ``arguments`` as an object and no
        ``id``/``type``.  LearnGraph's tool execution expects the OpenAI shape
        (``arguments`` as a JSON string), so the object is serialized here.
        """

        function = raw.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            return None
        name = function["name"]
        if not name.strip():
            return None
        raw_arguments = function.get("arguments")
        if isinstance(raw_arguments, dict):
            arguments = json.dumps(raw_arguments, ensure_ascii=False)
        elif isinstance(raw_arguments, str) and raw_arguments.strip():
            arguments = raw_arguments
        else:
            arguments = "{}"
        return {
            "id": raw.get("id") if isinstance(raw.get("id"), str) and raw["id"] else f"call_{index}",
            "type": raw.get("type") if isinstance(raw.get("type"), str) and raw["type"] else "function",
            "function": {"name": name, "arguments": arguments},
        }

    def generate_json(
        self,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        # Alibaba Cloud / Ollama Cloud: the official docs state Cloud does not
        # support structured outputs yet.
        raise ProviderResponseError(
            "Ollama Cloud does not support structured outputs (json_object/json_schema)"
        )


class OllamaCloudSearchProvider:
    """Ollama Cloud Web Search REST adapter (``POST /api/web_search``).

    Implements :class:`~app.providers.ports.search.SearchProviderPort`.  Results
    are always re-filtered locally against ``allowed_domains`` before returning.
    """

    remote_capability = True
    provider_type = "ollama_cloud_search"

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("Ollama Cloud Search requires a base URL")
        if not api_key or not api_key.strip():
            raise ValueError("Ollama Cloud Search requires an API key")
        self.provider_id = provider_id
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        query: str,
        max_results: int,
        *,
        allowed_domains: set[str] | None = None,
    ) -> list[SearchResult]:
        body: dict[str, object] = {
            "query": query,
            # The vendor API caps results at 10.
            "max_results": min(max(1, int(max_results)), 10),
        }
        try:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            with httpx.Client(
                headers=headers,
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(f"{self.base_url}/web_search", json=body)
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeout("Ollama Cloud Search timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderError("Ollama Cloud Search request failed") from exc
        if not response.is_success:
            raise SearchProviderError(
                f"Ollama Cloud Search returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchProviderResponseError(
                "Ollama Cloud Search returned non-JSON data"
            ) from exc
        if not isinstance(payload, dict):
            raise SearchProviderResponseError(
                "Ollama Cloud Search response must be an object"
            )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise SearchProviderResponseError(
                "Ollama Cloud Search response has no results array"
            )
        permitted_domains = {
            domain
            for value in (allowed_domains or set())
            if (domain := normalize_domain(value))
        }
        now = datetime.now(timezone.utc)
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            title = item.get("title")
            snippet = item.get("content")
            if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
                continue
            if not domain_is_allowed(url, permitted_domains):
                continue
            results.append(
                SearchResult(
                    title=title.strip()[:1_000],
                    url=url[:4_000],
                    snippet=str(snippet or "").strip()[:8_000],
                    source_type="ollama_cloud_web_search",
                    fetched_at=now,
                )
            )
            if len(results) >= max_results:
                break
        return results

    def probe(self) -> dict[str, object]:
        results = self.search("LearnGraph provider capability check", 1)
        return {
            "capability": "search",
            "provider_type": self.provider_type,
            "result_count": len(results),
        }
