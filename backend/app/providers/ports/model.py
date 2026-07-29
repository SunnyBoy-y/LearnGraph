from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict


class ProviderUsage(TypedDict, total=False):
    """Provider-neutral token usage.

    ``input_tokens`` is the total prompt input. Cache reads and cache writes are
    disjoint subsets of that total; output reasoning is a subset of output.
    """

    input_tokens: int
    cached_input_tokens: int
    cache_creation_input_tokens: int
    output_tokens: int
    reasoning_tokens: int


@dataclass(frozen=True, slots=True)
class ProviderChatMessage:
    """A provider-neutral durable chat turn.

    LearnGraph owns the durable message timeline.  Adapters receive this
    structured representation instead of a flattened prompt when a protocol
    requires role-aware multi-turn context. ``response_items`` is deliberately
    separate from the ordinary role/content fields: it is an opaque,
    JSON-compatible sequence of provider response items that must be replayed
    without reconstruction (for example, a Responses API reasoning item and
    its encrypted continuation state).

    Only an adapter whose protocol defines such items may consume
    ``response_items``. It is never included in :meth:`as_payload`, which is
    the Chat Completions representation used by DeepSeek and other compatible
    providers.
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    response_items: list[dict[str, Any]] = field(default_factory=list)
    # Ephemeral provider input only.  LearnGraph stores a durable FileRecord
    # reference in MessagePart instead of persisting a base64 data URL or
    # copying user binary data into the chat timeline.
    content_parts: list[dict[str, Any]] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role}
        if self.content_parts:
            parts: list[dict[str, Any]] = []
            if self.content:
                parts.append({"type": "text", "text": self.content})
            for part in self.content_parts:
                if part.get("type") == "input_image":
                    image_url = part.get("image_url")
                    if not isinstance(image_url, str) or not image_url.startswith(
                        "data:image/"
                    ):
                        continue
                    detail = part.get("detail")
                    payload_part: dict[str, Any] = {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    }
                    if detail in {"low", "high", "auto"}:
                        payload_part["image_url"]["detail"] = detail
                    parts.append(payload_part)
                elif part.get("type") == "input_video":
                    video_url = part.get("video_url")
                    if not isinstance(video_url, str) or not video_url.startswith(
                        "data:video/"
                    ):
                        continue
                    payload_part = {
                        "type": "video_url",
                        "video_url": {"url": video_url},
                    }
                    fps = part.get("fps")
                    if isinstance(fps, (int, float)) and not isinstance(fps, bool):
                        payload_part["fps"] = max(0.1, min(float(fps), 10.0))
                    parts.append(payload_part)
            payload["content"] = parts
        elif self.content is not None:
            # Chat Completions / DeepSeek: assistant turns that only emit tool
            # calls should carry ``content: null`` (or omit a blank string). A
            # literal empty string is rejected by several providers mid-tool
            # loop and surfaces as a generic stream failure.
            if self.tool_calls and self.role == "assistant" and self.content == "":
                payload["content"] = None
            else:
                payload["content"] = self.content
        elif self.tool_calls and self.role == "assistant":
            payload["content"] = None
        if self.reasoning_content:
            payload["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            payload["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload


@dataclass(frozen=True, slots=True)
class ProviderStreamEvent:
    """Normalized event emitted while a remote chat invocation is live.

    ``reasoning_kind='summary'`` denotes a provider-exposed reasoning summary,
    never an inferred private chain of thought. ``response_items`` is populated
    only on a completed invocation when an adapter must preserve opaque native
    output items for a later stateless continuation.
    """

    type: Literal["reasoning_delta", "text_delta", "tool_calls", "completed"]
    content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    reasoning_kind: Literal["summary"] | None = None
    response_items: list[dict[str, Any]] = field(default_factory=list)


class ModelProviderPort(Protocol):
    provider_id: str
    model_id: str
    available: bool
    remote_capability: bool
    context_window_tokens: int
    max_output_tokens: int
    last_usage: ProviderUsage
    last_request_id: str | None

    def stream_answer(self, prompt: str) -> Iterable[str]: ...

    def stream_chat(
        self,
        messages: list[ProviderChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterable[ProviderStreamEvent]: ...

    def generate_json(self, prompt: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]: ...
