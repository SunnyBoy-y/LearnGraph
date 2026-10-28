"""Rebuild the Tutor LLM context from durable voice state.

This adapter is the only place that turns a persisted voice context snapshot
and finalized turns into Pipecat ``LLMContext`` messages.  The audio pipeline
therefore does not need request-scoped chat objects, and a reconnect does not
silently start from an empty Tutor context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from uuid import NAMESPACE_URL, uuid5

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

        history_messages = self._append_history(messages, snapshot.get("history"))
        turns = self._turn_loader(self.voice_session_id, limit=self.max_messages)
        # Snapshot and voice journal can overlap. Match durable identities;
        # repeated words in a different turn are never an identity.
        history = snapshot.get("history") or []
        historical = list(history) if isinstance(history, list) else []
        seen_turns: set[str] = set()
        for turn in turns:
            turn_id = str(turn.get("turn_id") or "")
            if turn_id and turn_id in seen_turns:
                continue
            if turn_id:
                seen_turns.add(turn_id)
            user_text = str(turn.get("user_text") or "").strip()
            assistant_text = str(turn.get("assistant_text") or "").strip()
            key = str(turn.get("client_message_id") or turn_id)
            identities = {str(uuid5(NAMESPACE_URL, f"voice:{handle.workspace_id}:{handle.chat_session_id}:{key}:{role}"))
                          for role in ("user", "assistant")} if key and getattr(handle, "workspace_id", None) and getattr(handle, "chat_session_id", None) else set()
            overlapping = {str(item.get("id")) for item in historical if isinstance(item, Mapping) and (
                item.get("voice_turn_id") == turn_id and turn_id or str(item.get("id")) in identities)}
            # Latest journal projection wins for the same record (e.g. an
            # interrupted answer shortened after the chat snapshot was taken).
            if overlapping:
                replaced = [history_messages[key] for key in overlapping if key in history_messages]
                messages[:] = [message for message in messages if not any(message is old for old in replaced)]
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
    def _append_history(messages: list[dict[str, str]], history: Any) -> dict[str, dict[str, str]]:
        identities: dict[str, dict[str, str]] = {}
        if not isinstance(history, Iterable) or isinstance(history, (str, bytes, Mapping)):
            return identities
        for item in history:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role in {"system", "user", "assistant"} and content:
                message = {"role": role, "content": content}
                identity = str(item.get("id") or "")
                if identity:
                    old = identities.get(identity)
                    messages[:] = [existing for existing in messages if existing is not old]
                    identities[identity] = message
                messages.append(message)
        return identities


def _bounded_unique(
    messages: list[dict[str, str]], max_messages: int
) -> list[dict[str, str]]:
    # Kept for compatibility with callers. Identity deduplication happens at
    # the source boundary above, not by user/assistant text equality.
    deduped = list(messages)

    if len(deduped) <= max_messages:
        return deduped
    # Always preserve the system instruction and the most recent conversation.
    system = [item for item in deduped if item["role"] == "system"][:1]
    tail = [item for item in deduped if item not in system]
    return (system + tail[-(max_messages - len(system)) :])[-max_messages:]
