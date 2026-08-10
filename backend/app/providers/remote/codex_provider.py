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
from app.providers.remote.openai import OpenAIResponsesProvider, _ReusableHTTPClient

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
