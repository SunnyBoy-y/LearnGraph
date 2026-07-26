from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.providers.ports.model import (
    ProviderChatMessage,
    ProviderStreamEvent,
)
from app.providers.remote.openai import (
    OpenAICompatibleChatProvider,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
    merge_provider_request_headers,
)


class DeepSeekBalanceError(RuntimeError):
    """A safe, provider-neutral balance retrieval failure."""


@dataclass(frozen=True, slots=True)
class DeepSeekBalanceInfo:
    currency: str
    total_balance: str
    granted_balance: str
    topped_up_balance: str


def is_official_deepseek_api_base_url(base_url: str | None) -> bool:
    """Return whether ``base_url`` is the documented DeepSeek API origin.

    ``deepseek_chat`` is LearnGraph's native, credential-bearing DeepSeek
    integration.  Treating an arbitrary OpenAI-compatible URL as native would
    make the balance endpoint send the saved bearer token to that host.  Keep
    the trust boundary deliberately narrow: custom gateways belong to the
    generic ``openai_compatible_chat`` provider type.
    """

    if not base_url:
        return False
    try:
        parsed = urlsplit(base_url.strip())
        return (
            parsed.scheme.casefold() == "https"
            and (parsed.hostname or "").casefold() == "api.deepseek.com"
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def is_deepseek_chat_configuration(
    provider_type: str,
    base_url: str | None,
) -> bool:
    """Recognize the official endpoint, including pre-DeepSeek catalog rows.

    Early LearnGraph workspaces could only save DeepSeek as an
    ``openai_compatible_chat`` Provider.  Its protocol is compatible, but its
    native thinking/tool stream is not generic OpenAI text streaming.  Detect
    only the documented official host so an arbitrary compatible endpoint can
    never be upgraded implicitly.
    """

    return provider_type in {"deepseek_chat", "openai_compatible_chat"} and (
        is_official_deepseek_api_base_url(base_url)
    )


def fetch_deepseek_balance(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = 15.0,
    transport: httpx.BaseTransport | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bool, list[DeepSeekBalanceInfo]]:
    """Read the official DeepSeek account balance without persisting it.

    The upstream response is intentionally not included in raised errors or
    audit payloads: account status is useful to the workspace manager, while
    raw provider diagnostics must not become a side channel for credentials or
    account data.
    """

    if not is_official_deepseek_api_base_url(base_url):
        raise DeepSeekBalanceError(
            "DeepSeek balance is only available for the official API origin"
        )

    try:
        with httpx.Client(
            headers=merge_provider_request_headers(
                api_key=api_key,
                extra_headers=extra_headers,
            ),
            timeout=timeout_seconds,
            transport=transport,
        ) as client:
            response = client.get(f"{base_url.rstrip('/')}/user/balance")
    except httpx.TimeoutException as exc:
        raise DeepSeekBalanceError("DeepSeek balance request timed out") from exc
    except httpx.HTTPError as exc:
        raise DeepSeekBalanceError("DeepSeek balance request could not be sent") from exc

    if not response.is_success:
        raise DeepSeekBalanceError(
            f"DeepSeek balance request failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise DeepSeekBalanceError("DeepSeek balance response was not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("is_available"), bool):
        raise DeepSeekBalanceError("DeepSeek balance response had an invalid schema")
    raw_infos = payload.get("balance_infos")
    if not isinstance(raw_infos, list):
        raise DeepSeekBalanceError("DeepSeek balance response had no balance_infos array")

    infos: list[DeepSeekBalanceInfo] = []
    for raw in raw_infos:
        if not isinstance(raw, dict):
            raise DeepSeekBalanceError("DeepSeek balance response contained an invalid item")
        currency = raw.get("currency")
        values = {
            key: raw.get(key)
            for key in ("total_balance", "granted_balance", "topped_up_balance")
        }
        if currency not in {"CNY", "USD"} or any(
            not isinstance(value, str) for value in values.values()
        ):
            raise DeepSeekBalanceError("DeepSeek balance response contained invalid values")
        try:
            for value in values.values():
                Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise DeepSeekBalanceError(
                "DeepSeek balance response contained a non-decimal amount"
            ) from exc
        infos.append(
            DeepSeekBalanceInfo(
                currency=currency,
                total_balance=str(values["total_balance"]),
                granted_balance=str(values["granted_balance"]),
                topped_up_balance=str(values["topped_up_balance"]),
            )
        )
    return payload["is_available"], infos


class DeepSeekChatProvider(OpenAICompatibleChatProvider):
    """DeepSeek's Chat Completions adapter with native thinking/tool streams."""

    supports_structured_chat = True
    supports_agent_tools = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            structured_output_mode="json_object",
            supports_structured_chat=True,
            **kwargs,
        )

    def _apply_call_options(self, payload: dict[str, Any], *, responses: bool) -> dict[str, Any]:
        payload = super()._apply_call_options(payload, responses=responses)
        if responses:
            return payload
        thinking_mode = self.call_options.thinking_mode if self.call_options else "off"
        payload["thinking"] = {
            "type": "disabled" if thinking_mode == "off" else "enabled"
        }
        return payload

    @staticmethod
    def _usage_from_chunk(usage: object) -> dict[str, int] | None:
        if not isinstance(usage, dict):
            return None
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        cached = usage.get("prompt_cache_hit_tokens")
        if cached is None and isinstance(prompt_details, dict):
            cached = prompt_details.get("cached_tokens")
        return {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "cached_input_tokens": int(cached or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "reasoning_tokens": int(
                completion_details.get("reasoning_tokens") or 0
                if isinstance(completion_details, dict)
                else 0
            ),
        }

    @staticmethod
    def _merge_tool_delta(
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
        # Identity fields are announced once. A blank repeat on a continuation
        # fragment must never erase them; see the parent adapter's note.
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
    def _error_event_message(error: object) -> str:
        """Build a short, non-secret description of an upstream stream error."""

        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message") or error.get("msg")
            parts = [part for part in (code, message) if isinstance(part, str) and part.strip()]
            if parts:
                return "; ".join(parts)[:300]
        if isinstance(error, str) and error.strip():
            return error.strip()[:300]
        return "DeepSeek stream returned an error event"

    def stream_chat(
        self,
        messages: list[ProviderChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterable[ProviderStreamEvent]:
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
                        # DeepSeek sends keep-alive comments and ordinary blank
                        # event separators. Neither is a model output chunk.
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
                                "DeepSeek returned invalid SSE JSON"
                            ) from exc
                        if not isinstance(event, dict):
                            raise ProviderResponseError(
                                "DeepSeek returned an invalid SSE event"
                            )
                        if event.get("error"):
                            raise ProviderHTTPError(
                                self._error_event_message(event.get("error"))
                            )
                        usage = self._usage_from_chunk(event.get("usage"))
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
                            # Some proxies close after finish_reason without the
                            # trailing [DONE] sentinel. A non-empty finish_reason is
                            # a complete model turn (stop / tool_calls / length).
                            completed = True
                        delta = choice.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        reasoning = delta.get("reasoning_content")
                        if isinstance(reasoning, str) and reasoning:
                            saw_model_output = True
                            for reasoning_delta in self._text_deltas(reasoning):
                                yield ProviderStreamEvent(
                                    "reasoning_delta",
                                    content=reasoning_delta,
                                )
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            saw_model_output = True
                            for text_delta in self._text_deltas(content):
                                yield ProviderStreamEvent(
                                    "text_delta",
                                    content=text_delta,
                                )
                        raw_tool_calls = delta.get("tool_calls")
                        if isinstance(raw_tool_calls, list):
                            for fallback_index, raw_tool_call in enumerate(raw_tool_calls):
                                if isinstance(raw_tool_call, dict):
                                    saw_model_output = True
                                    self._merge_tool_delta(
                                        tool_aggregates,
                                        raw_tool_call,
                                        fallback_index,
                                    )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("DeepSeek chat stream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderHTTPError(
                f"DeepSeek chat stream transport failed ({type(exc).__name__})"
            ) from exc

        # Accept a finished choice without [DONE]. Proxies and some official
        # edge cases close after finish_reason without the trailing sentinel.
        # An empty truncated stream (no finish_reason) must still fail loudly.
        if not completed:
            if finish_reason:
                completed = True
            else:
                raise ProviderHTTPError(
                    "DeepSeek stream ended before completion "
                    f"(finish_reason={finish_reason!r}, output={saw_model_output})"
                )
        tool_calls = self._completed_chat_tool_calls(tool_aggregates)
        if tool_calls:
            yield ProviderStreamEvent("tool_calls", tool_calls=tool_calls)
        yield ProviderStreamEvent("completed", finish_reason=finish_reason)
