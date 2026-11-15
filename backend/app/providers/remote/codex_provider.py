"""Codex direct-login model adapter.

Wire-compatible with the OpenAI Responses API, so the streaming, tool-call and
reasoning-continuation logic is inherited unchanged.  What differs is the
credential (a rotating ChatGPT OAuth access token instead of an API key), the
Codex-specific request headers, and the base instructions the ChatGPT Codex
backend requires on every turn.

The ChatGPT Codex backend also rejects ``role: system`` items inside
``input`` with HTTP 400 ``System messages are not allowed``.  System content is
therefore folded into the top-level ``instructions`` field, matching how the
Codex CLI itself ships operating policy.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

import httpx

from app.providers.ports.model import ProviderChatMessage, ProviderStreamEvent
from app.providers.remote.codex import (
    CodexCredentials,
    codex_request_headers,
)
from app.providers.remote.openai import (
    OpenAIResponsesProvider, _ReusableHTTPClient, ProviderHTTPError,
    ProviderResponseError, ProviderTimeoutError,
)

# The backend rejects turns that arrive without operating instructions.  This
# is a neutral LearnGraph system preamble, not a copy of Codex's agent prompt.
CODEX_BASE_INSTRUCTIONS = (
    "You are a coding and reasoning assistant accessed through the Codex "
    "backend. Answer the user directly and accurately. Use the provided tools "
    "when they are needed to complete the task, and explain results clearly."
)


class CodexResponsesProvider(OpenAIResponsesProvider):
    """Responses adapter bound to ``chatgpt.com/backend-api/codex``."""

    def __init__(
        self,
        *args: Any,
        credentials: CodexCredentials,
        instructions: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.credentials = credentials
        self.instructions = instructions or CODEX_BASE_INSTRUCTIONS
        # One Codex session id per adapter instance keeps a turn's follow-up
        # calls correlated the way the CLI does.
        self.session_id = str(uuid.uuid4())
        # Rate-limit headers ride along with every inference response, so the
        # usage display can refresh without a separate request.
        self.last_rate_limits: dict[str, float | int | None] = {}
        # The ChatGPT login endpoint follows the Codex client contract, not
        # every field of the public Responses API. Expose the budget exception
        # instead of claiming a server-side output ceiling that is not sent.
        self.capabilities["output_budget_enforcement"] = "upstream_default"
        self.capabilities["output_budget_notice"] = (
            "Codex 登录通道由上游控制单次输出额度，不支持通用 max_output_tokens 字段。"
        )

    def _apply_output_budget(self, payload: dict[str, Any], *, responses: bool) -> dict[str, Any]:
        # Keep the configured ceiling for context/billing estimates, but do not
        # send unsupported public-API fields to the ChatGPT backend.
        for field in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
            payload.pop(field, None)
        return payload

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        # generate_json also goes through the streaming-only login contract;
        # return the terminal response object to the shared JSON validator.
        self.last_usage = {}
        self.last_request_id = None
        payload["stream"] = True
        try:
            with self._client() as client:
                with client.stream("POST", f"{self.base_url}/{path.lstrip('/')}", json=payload) as response:
                    self._raise_for_status(response)
                    self.last_request_id = response.headers.get("x-request-id")
                    for event in self._sse_payloads(response):
                        kind = event.get("type")
                        if kind == "response.completed" and isinstance(event.get("response"), dict):
                            return event["response"]
                        if kind in {"error", "response.failed", "response.incomplete"}:
                            raise ProviderResponseError("Codex structured generation did not complete")
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Codex structured generation timed out") from exc
        raise ProviderHTTPError("Codex stream ended without response.completed")

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            headers = codex_request_headers(
                self.credentials,
                accept="text/event-stream",
                session_id=self.session_id,
            )
            # Station-style custom headers may still be declared, but they can
            # never override the Codex credential headers.
            merged = {**self.extra_headers, **headers}
            self._http_client = _ReusableHTTPClient(
                httpx.Client(
                    headers=merged,
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                )
            )
        return self._http_client

    @staticmethod
    def _split_system_messages(
        messages: list[ProviderChatMessage],
    ) -> tuple[str | None, list[ProviderChatMessage]]:
        """Move system turns out of ``input`` and into ``instructions``.

        The ChatGPT Codex backend rejects ``role: system`` items in the
        Responses ``input`` array. LearnGraph still builds ordinary system
        messages for style/policy/context, so this adapter folds them into
        the top-level ``instructions`` field and keeps only non-system turns
        as ``input``.
        """

        system_parts: list[str] = []
        body: list[ProviderChatMessage] = []
        for message in messages:
            if message.role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue
            body.append(message)
        combined = "\n\n".join(system_parts) if system_parts else None
        return combined, body

    def _compose_instructions(self, system_text: str | None) -> str:
        parts = [part for part in (self.instructions, system_text) if part]
        return "\n\n".join(parts)

    def _apply_call_options(
        self,
        payload: dict[str, Any],
        *,
        responses: bool,
    ) -> dict[str, Any]:
        payload = super()._apply_call_options(payload, responses=responses)
        if responses:
            # Match Codex's live-search contract rather than relying on an
            # upstream cache-only default. Search still requires explicit opt-in
            # through call_options; never add a tool on offline turns.
            if isinstance(payload.get("tools"), list):
                payload["tools"] = [
                    {"external_web_access": True, **tool}
                    if isinstance(tool, dict) and tool.get("type") == "web_search"
                    else tool
                    for tool in payload["tools"]
                ]
            payload["stream"] = True
            if isinstance(payload.get("input"), str):
                payload["input"] = [{"role": "user", "content": [{"type": "input_text", "text": payload["input"]}]}]
            payload.setdefault("prompt_cache_key", f"lg-{self.session_id}")
            payload.setdefault("instructions", self.instructions)
            # Nothing is stored server-side on this path, so reasoning has to
            # travel with the request; the parent already sets store/include
            # for the streaming turn, and this covers the other call shapes.
            payload["store"] = False
            include = payload.get("include")
            if not isinstance(include, list):
                payload["include"] = ["reasoning.encrypted_content"]
            elif "reasoning.encrypted_content" not in include:
                payload["include"] = [*include, "reasoning.encrypted_content"]
        return payload

    def stream_chat(
        self,
        messages: list[ProviderChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterable[ProviderStreamEvent]:
        """Stream one Codex Responses turn without system roles in ``input``.

        LearnGraph's chat service builds ordinary ``role=system`` turns for
        style, tool policy, and authorized context. The ChatGPT Codex backend
        rejects those in ``input``, so they are folded into ``instructions``
        before the shared Responses stream path runs.
        """

        system_text, body_messages = self._split_system_messages(messages)
        previous_instructions = self.instructions
        self.instructions = self._compose_instructions(system_text)
        try:
            yield from super().stream_chat(body_messages, tools=tools)
        finally:
            self.instructions = previous_instructions

    @staticmethod
    def _rate_limit_number(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def capture_rate_limits(self, headers: httpx.Headers) -> None:
        """Record the ``x-codex-*`` limit headers seen on a response."""

        snapshot: dict[str, float | int | None] = {}
        for slot in ("primary", "secondary"):
            used = self._rate_limit_number(headers.get(f"x-codex-{slot}-used-percent"))
            if used is None:
                continue
            window = self._rate_limit_number(
                headers.get(f"x-codex-{slot}-window-minutes")
            )
            reset_at = self._rate_limit_number(headers.get(f"x-codex-{slot}-reset-at"))
            snapshot[f"{slot}_used_percent"] = used
            snapshot[f"{slot}_window_minutes"] = int(window) if window else None
            snapshot[f"{slot}_reset_at"] = int(reset_at) if reset_at else None
        if snapshot:
            self.last_rate_limits = snapshot
