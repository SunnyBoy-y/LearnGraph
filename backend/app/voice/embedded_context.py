"""Rebuild the Tutor LLM context from durable voice state.

This adapter is the only place that turns a persisted voice context snapshot
and finalized turns into Pipecat ``LLMContext`` messages.  The audio pipeline
therefore does not need request-scoped chat objects, and a reconnect does not
silently start from an empty Tutor context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from app.voice.events import load_session
from app.voice.turns import list_turns


MAX_CONTEXT_MESSAGES = 40


@dataclass(frozen=True, slots=True)
class VoiceContextState:
    version: str | None
    messages: tuple[dict[str, str], ...]
    context_build_id: str | None = None
    source: str = "empty"


class VoiceContextAdapter:
    """Load a bounded, durable Tutor context without holding a DB session."""

    def __init__(
        self,
        voice_session_id: str,
        system_instruction: str,
        *,
        max_messages: int = MAX_CONTEXT_MESSAGES,
        session_loader: Callable[[str], Any] = load_session,
        turn_loader: Callable[..., list[dict[str, Any]]] = list_turns,
    ) -> None:
        self.voice_session_id = voice_session_id
        self.system_instruction = str(system_instruction or "").strip()
        self.max_messages = max(2, int(max_messages))
        self._session_loader = session_loader
        self._turn_loader = turn_loader

    def load(self) -> VoiceContextState:
        handle = self._session_loader(self.voice_session_id)
        if handle is None:
            return VoiceContextState(
                version=None,
                messages=tuple(self._base_messages()),
                source="missing_session",
            )

        snapshot = dict(getattr(handle, "context_snapshot", None) or {})
        context_build_id = (
            snapshot.get("context_build_id")
            or snapshot.get("version")
            or snapshot.get("created_at")
        )
        messages: list[dict[str, str]] = self._base_messages()

        prompt_block = str(snapshot.get("prompt_block") or "").strip()
        if prompt_block:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The following is the persisted LearnGraph context for this "
                        "voice conversation. Follow it unless the live conversation "
                        f"supersedes it.\n\n{prompt_block}"
                    ),
                }
            )

        self._append_history(messages, snapshot.get("history"))
        turns = self._turn_loader(self.voice_session_id, limit=self.max_messages)
        for turn in turns:
            user_text = str(turn.get("user_text") or "").strip()
            assistant_text = str(turn.get("assistant_text") or "").strip()
            if user_text:
                messages.append({"role": "user", "content": user_text})
            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})

        messages = _bounded_unique(messages, self.max_messages)
        return VoiceContextState(
            version=str(context_build_id) if context_build_id else None,
            context_build_id=(
                str(context_build_id) if context_build_id is not None else None
            ),
            messages=tuple(messages),
            source="snapshot_and_turns",
        )

    def apply(self, context: Any, state: VoiceContextState) -> None:
        messages = [dict(message) for message in state.messages]
        setter = getattr(context, "set_messages", None)
        if callable(setter):
            setter(messages)
            return
        context._messages[:] = messages

    def _base_messages(self) -> list[dict[str, str]]:
        if not self.system_instruction:
            return []
        return [{"role": "system", "content": self.system_instruction}]

    @staticmethod
    def _append_history(messages: list[dict[str, str]], history: Any) -> None:
        if not isinstance(history, Iterable) or isinstance(history, (str, bytes, Mapping)):
            return
        for item in history:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role in {"system", "user", "assistant"} and content:
                messages.append({"role": role, "content": content})


def _bounded_unique(
    messages: list[dict[str, str]], max_messages: int
) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        key = (message["role"], message["content"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(message)

    if len(deduped) <= max_messages:
        return deduped
    # Always preserve the system instruction and the most recent conversation.
    system = [item for item in deduped if item["role"] == "system"][:1]
    tail = [item for item in deduped if item not in system]
    return (system + tail[-(max_messages - len(system)) :])[-max_messages:]
