"""Context and durable transcript bridge for the full duplex voice runtime.

The audio worker must not build its own view of a chat session.  This module
keeps that rule in one place: ``load_snapshot`` delegates context assembly to
the existing :class:`ChatService`, while ``finalize_turn`` writes the same
Message/MessageVersion/MessagePart records used by ordinary chat.  Interim
ASR and interrupted text never reaches this module.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.models import (
    Message,
    MessagePartRecord,
    MessageStreamEvent,
    MessageVersion,
)

logger = logging.getLogger(__name__)

#: Retrieval label for every memory read the voice path causes. It only lands in
#: the trace/telemetry columns -- ranking never sees it -- which is what makes it
#: safe to give voice its own label instead of sharing text chat's default.
VOICE_MEMORY_AGENT_ID = "voice"


def _last_user_text(timeline: list[Any]) -> str:
    """Newest thing the user said in this conversation, or an empty string."""

    for item in reversed(timeline):
        if str(getattr(item, "role", "") or "") != "user":
            continue
        text = str(getattr(item, "content", "") or "").strip()
        if text:
            return text
    return ""


@dataclass(frozen=True, slots=True)
class VoiceContextSnapshot:
    """Immutable context package pinned for a voice session/epoch."""

    chat_session_id: str
    context_build_id: str | None
    prompt_block: str
    history: list[dict[str, Any]] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    learning_states: list[dict[str, Any]] = field(default_factory=list)
    task_state: dict[str, Any] | None = None
    token_budget: int = 0
    provider_id: str | None = None
    model_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["created_at"] = self.created_at.isoformat()
        return value


class VoiceContextService:
    """Adapter around a request-scoped ``ChatService``.

    ``ChatService`` remains the source of truth for authorization, provider
    selection, ContextBuilder and memory policy.  A caller can keep the
    returned snapshot on a persisted voice session and reuse it across worker
    reconnects without recomputing a different context midway through a turn.
    """

    def __init__(self, chat_service: Any) -> None:
        self.chat = chat_service

    def load_snapshot(
        self,
        chat_session_id: str,
        *,
        query: str = "",
        node_ids: list[str] | None = None,
        task_id: str | None = None,
        token_budget: int | None = None,
    ) -> VoiceContextSnapshot:
        # Build through the ordinary prompt path so session ACLs, memory scope,
        # model budget and ContextBuilder policy stay identical to text chat.
        #
        # The query is what makes the retrieval conditioned.  It used to be
        # empty here, which meant the pinned memory block was whatever the
        # retriever ranked first with no question attached to it -- for a
        # conversation already in progress that is close to noise.  The newest
        # user turn in this chat session is the best query available at session
        # start; the live turn gets its own recall in the audio worker
        # (``app/voice/embedded_memory.py``), which is where per-turn relevance
        # actually comes from.
        timeline = self.chat._session_timeline(chat_session_id)
        effective_query = str(query or "").strip() or _last_user_text(timeline)
        prompt, _summary = self.chat._build_model_prompt(
            chat_session_id,
            effective_query,
            node_ids=node_ids,
            additional_context="",
            agent_mode=False,
            # Label every retrieval this snapshot causes as voice: the trace takes
            # its agent from the scope, so without this a spoken recall and a
            # typed one are the same row in ``memory_retrieval_traces``.
            agent_id=VOICE_MEMORY_AGENT_ID,
        )
        history = [
            {"id": item.id, "role": item.role, "content": item.content,
             "created_at": item.created_at.isoformat() if item.created_at else None}
            for item in timeline
        ]
        context_build_id: str | None = None
        memories: list[dict[str, Any]] = []
        learning: list[dict[str, Any]] = []
        task_state: dict[str, Any] | None = None
        telemetry: dict[str, Any] = {}
        try:
            # In events mode this is the exact ContextBuilder package injected
            # by _build_model_prompt; in legacy/shadow mode it is still useful
            # telemetry and leaves legacy authorization untouched.
            #
            # The package comes back with the build it already paid for, so the
            # structured memories/learning_states do not cost a second
            # retrieval: this used to run the whole ContextBuilder path a third
            # time just to read the view off it.
            _block, telemetry, built = self.chat._build_v2_memory_package(
                chat_session_id,
                effective_query,
                node_ids=node_ids,
                task_id=task_id,
                agent_id=VOICE_MEMORY_AGENT_ID,
            )
            context_build_id = telemetry.get("context_build_id")
            if built is not None:
                context_build_id = built.view.context_build_id
                memories = [item.model_dump(mode="json") for item in built.view.memories]
                learning = list(built.view.learning_states or [])
                task_state = built.view.task_state
        except Exception:
            telemetry = {}
        # Keep the snapshot transport-safe and useful to the UI/reconnect path.
        # Detailed evidence remains in the ContextBuilder event/telemetry rows.
        return VoiceContextSnapshot(
            chat_session_id=chat_session_id,
            context_build_id=context_build_id,
            prompt_block=prompt,
            history=history,
            memories=memories,
            learning_states=learning,
            task_state=task_state,
            token_budget=int(token_budget or self.chat._input_token_budget()),
            provider_id=getattr(self.chat.model_provider, "provider_id", None),
            model_id=getattr(self.chat.model_provider, "model_id", None),
        )

    def finalize_turn(
        self,
        chat_session_id: str,
        *,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        request_id: str | None = None,
        client_message_id: str | None = None,
        audio_cursor_ms: int | None = None,
        commit: bool = True,
        memory: bool = True,
        include_assistant: bool = True,
        assistant_status: str = "completed",
    ) -> tuple[Message, Message | None]:
        """Persist one authoritative voice turn idempotently.

        The idempotency key is the turn id (or client message id for typed
        input).  Replaying a ``turn.finalized`` event therefore returns the
        existing pair instead of creating duplicate chat messages.

        ``memory=False`` writes the exchange to the transcript but does not offer
        it to long-term memory; barged-in turns use it because the user never
        heard the whole answer.

        ``include_assistant=False`` persists the user's question *without* an
        answer. That is the failed-turn case (provider error or idle timeout):
        the question must survive a refresh -- dropping it made a transient blip
        look like the user never spoke -- but writing an empty assistant message
        would render an empty bubble, so the absence of an answer is expressed by
        the turn status instead.
        """
        if not str(user_text or "").strip():
            raise ValueError("voice finalized turn requires non-empty user_text")
        if not str(turn_id or "").strip():
            raise ValueError("voice finalized turn requires turn_id")
        key = str(client_message_id or turn_id)
        session = self.chat.sessions.require(chat_session_id, "session")
        existing: dict[str, Message] = {}
        rows = self.chat.db.scalars(
            select(Message)
            .where(Message.workspace_id == self.chat.workspace_id,
                   Message.session_id == chat_session_id)
            .order_by(Message.created_at.asc())
        ).all()
        for row in rows:
            trace = row.provider_trace if isinstance(row.provider_trace, dict) else {}
            if trace.get("voice_turn_id") == turn_id or trace.get("client_message_id") == key:
                existing[row.role] = row
        if "user" in existing and (not include_assistant or "assistant" in existing):
            # Replayed finalize: the pair already exists, but a previous attempt
            # may have crashed before the memory job was enqueued.  The queue
            # deduplicates on (session, message), so re-enqueueing is safe.
            if memory and "assistant" in existing:
                self._enqueue_memory(existing["assistant"])
            # Same reasoning for the stream event: a crash between the message
            # commit and the event write leaves a turn the client cannot replay,
            # and a replay of the finalize is the only place that can notice.
            if commit and "assistant" in existing:
                self._record_message_completion(existing["assistant"])
            return existing["user"], existing.get("assistant")

        # Stable ids make a retry safe even when two workers race before either
        # can observe the first transaction.  The database primary key then
        # provides the final idempotency gate.
        deterministic_key = f"voice:{self.chat.workspace_id}:{chat_session_id}:{key}"
        user_id = str(uuid5(NAMESPACE_URL, deterministic_key + ":user"))
        assistant_id = str(uuid5(NAMESPACE_URL, deterministic_key + ":assistant"))
        user_part_id = str(uuid5(NAMESPACE_URL, deterministic_key + ":user-part"))
        assistant_part_id = str(uuid5(NAMESPACE_URL, deterministic_key + ":assistant-part"))
        trace = {
            # Both spellings are written on purpose: the canvas gates captions and
            # voice-only affordances on `voice_turn`, while rows created
            # optimistically by the page carry the same key, so reading only one of
            # them is what used to drop a voice answer back to "ordinary message"
            # as soon as the history was refetched from the server.
            "voice": True,
            "voice_turn": True,
            "voice_turn_id": turn_id,
            "request_id": request_id,
            "client_message_id": client_message_id,
            "audio_cursor_ms": audio_cursor_ms,
            "provider_id": getattr(self.chat.model_provider, "provider_id", None),
            "model_id": getattr(self.chat.model_provider, "model_id", None),
        }
        user = Message(
            id=user_id, workspace_id=self.chat.workspace_id,
            session_id=session.id, role="user", status="completed",
            content=user_text, parts=[{"id": user_part_id, "type": "text", "status": "completed", "content": user_text}],
            provider_trace=trace,
        )
        assistant = Message(
            id=assistant_id, workspace_id=self.chat.workspace_id,
            session_id=session.id, parent_message_id=user.id,
            role="assistant", status=assistant_status, content=assistant_text,
            parts=[{"id": assistant_part_id, "type": "text", "status": assistant_status, "content": assistant_text}],
            provider_trace=trace,
        )
        try:
            with self.chat.db.begin_nested():
                self.chat.db.add(user)
                self.chat.db.flush()
                user_version = MessageVersion(
                    workspace_id=self.chat.workspace_id, message_id=user.id,
                    version=1, status="completed",
                )
                self.chat.db.add(user_version)
                self.chat.db.flush()
                self.chat.db.add(MessagePartRecord(
                    id=user_part_id, workspace_id=self.chat.workspace_id,
                    message_version_id=user_version.id, ordinal=0,
                    part_type="text", status="completed", content=user_text,
                ))
                if include_assistant:
                    self.chat.db.add(assistant)
                    self.chat.db.flush()
                    assistant_version = MessageVersion(
                        workspace_id=self.chat.workspace_id, message_id=assistant.id,
                        version=1, status=assistant_status, provider_trace=trace,
                    )
                    self.chat.db.add(assistant_version)
                    self.chat.db.flush()
                    self.chat.db.add(MessagePartRecord(
                        id=assistant_part_id, workspace_id=self.chat.workspace_id,
                        message_version_id=assistant_version.id, ordinal=0,
                        part_type="text", status=assistant_status, content=assistant_text,
                    ))
                self.chat.db.flush()
        except IntegrityError:
            # Another worker won the race.  Savepoint rollback leaves the
            # caller's outer transaction usable; return its authoritative pair.
            rows = self.chat.db.scalars(
                select(Message).where(
                    Message.workspace_id == self.chat.workspace_id,
                    Message.session_id == chat_session_id,
                    Message.id.in_((user_id, assistant_id)),
                )
            ).all()
            by_role = {row.role: row for row in rows}
            if "user" in by_role:
                if commit:
                    self.chat.db.commit()
                if memory and "assistant" in by_role:
                    self._enqueue_memory(by_role["assistant"])
                return by_role["user"], by_role.get("assistant")
            raise
        if commit:
            self.chat.db.commit()
            self.chat.db.refresh(user)
            if include_assistant:
                self.chat.db.refresh(assistant)
        # Long-term memory is fed only from a finalized, committed voice turn.
        # Interim ASR, speculative drafts and barged-in text never reach this
        # point because they never become a turn -- an interrupted turn passes
        # memory=False, and a failed turn has no answer to remember at all.
        if memory and include_assistant:
            self._enqueue_memory(assistant)
        if commit and include_assistant:
            self._record_message_completion(
                assistant, message_version_id=str(assistant_version.id)
            )
        return user, (assistant if include_assistant else None)

    def _record_message_completion(
        self, assistant: Message, *, message_version_id: str | None = None
    ) -> None:
        """Write the ordinary ``message.completed`` stream event for a voice turn.

        Text chat records one of these per finished assistant message
        (``ChatService.create_stream``) and the client replays them after a
        dropped stream; a voice answer is the same kind of durable message, so it
        records the same event.  Being explicit about what this does *not* buy:

        * it is **not** the sensitive-data gate.  That filter lives on the
          memory event stream (``MemoryEventStore.append`` →
          ``SensitiveDataFilter``), and a voice turn's extracted atoms already
          pass through it like any other turn.  ``message_stream_events`` has no
          backend consumer today; this is parity and audit, not safety.
        * it is **not** idempotent at the database level:
          ``uq_message_stream_sequence`` turns a second insert for the same
          message version into an IntegrityError.  Existence is therefore checked
          first, and any failure is logged rather than raised -- an audit row must
          never turn a successfully written transcript into a failed turn.
        """

        if not str(getattr(assistant, "content", "") or "").strip():
            return
        try:
            version_id = message_version_id or self.chat.db.scalar(
                select(MessageVersion.id)
                .where(MessageVersion.message_id == assistant.id)
                .order_by(MessageVersion.version.desc())
            )
            if not version_id:
                return
            already = self.chat.db.scalar(
                select(MessageStreamEvent.id).where(
                    MessageStreamEvent.workspace_id == self.chat.workspace_id,
                    MessageStreamEvent.message_version_id == version_id,
                    MessageStreamEvent.sequence == 0,
                )
            )
            if already is not None:
                return
            self.chat._append_event(
                session_id=str(assistant.session_id),
                message_id=str(assistant.id),
                message_version_id=str(version_id),
                part_id=None,
                sequence=0,
                event_type="message.completed",
                payload={
                    "status": str(assistant.status or "completed"),
                    "provider_trace": dict(assistant.provider_trace or {}),
                },
            )
        except Exception:  # pragma: no cover - audit write must never fail a turn
            logger.warning(
                "voice message.completed event failed for message %s",
                getattr(assistant, "id", None),
                exc_info=True,
            )
            try:
                self.chat.db.rollback()
            except Exception:
                pass

    def _enqueue_memory(self, assistant_message: Message) -> None:
        """Hand a finalized voice exchange to the ordinary memory outbox.

        Same queue and same deduplication key as text chat, so a voice turn and
        a typed turn are indistinguishable to the memory pipeline.  Failures are
        logged, never raised: memory extraction is recoverable background work
        and must not turn a successful turn into an error.
        """
        try:
            text = str(getattr(assistant_message, "content", "") or "").strip()
            if not text:
                return
            from app.services.durable_queue import enqueue_memory_extraction

            enqueue_memory_extraction(
                self.chat.workspace_id,
                str(assistant_message.session_id),
                self.chat.actor_id,
                str(assistant_message.id),
            )
        except Exception:  # pragma: no cover - queue/DB outage
            logger.warning(
                "voice memory extraction enqueue failed for message %s",
                getattr(assistant_message, "id", None),
                exc_info=True,
            )
