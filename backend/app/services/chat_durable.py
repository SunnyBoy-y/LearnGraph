"""Durable dispatch for interrupted chat streams (P1-B chat continuation).

On a backend restart, ``mark_interrupted_message_streams`` parks each in-flight
assistant stream that reached a checkpoint in the non-terminal ``interrupted``
status with its continuation context preserved. This module reschedules those
resumable streams through the durable queue so the worker performs an audited
resume attempt:

* ``enqueue_interrupted_chat_resumes`` — startup pass that enqueues exactly one
  ``chat.continue_stream`` job per resumable checkpoint (dedupe per version).
* ``run_chat_continue_once`` — worker handler: opens a fresh session, revalidates
  the parked message/version, and either resumes generation through the provider
  continuation seam or records an audited ``chat.continue_unavailable`` outcome
  and leaves the message parked. The existing ``retry_message`` replay path stays
  the explicit, actionable retry and the checkpoint is never discarded.

Provider-native continuation is deliberately gated: only provider ids listed in
``CHAT_CONTINUATION_CAPABLE_PROVIDERS`` (empty by default) are attempted. No
current provider exposes stateless Responses continuation, so today every resume
attempt lands in the audited "unavailable" state rather than claiming a
transparent resume — generation is never re-run and no duplicate business facts
are produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import DurableJob, Message, MessageVersion, ProviderResponseState
from app.repositories.audit import AuditRepository
from app.services.durable_queue import DurableQueue


class ChatContinuationUnavailable(RuntimeError):
    """Expected outcome: the provider cannot continue this checkpoint."""


# Provider ids whose adapters expose stateless Responses continuation. Empty by
# default — adding one here lights up ``continue_chat_stream_from_checkpoint``
# for that provider without changing the dispatch path.
CHAT_CONTINUATION_CAPABLE_PROVIDERS: frozenset[str] = frozenset()

JOB_KIND = "chat.continue_stream"
RESUME_ACTOR = "system:chat-continue"


def _dedupe_key(message_version_id: str) -> str:
    return f"{JOB_KIND}:{message_version_id}"


@dataclass(frozen=True)
class InterruptedChatCheckpoint:
    workspace_id: str
    message_id: str
    message_version_id: str
    provider_id: str


def list_interrupted_chat_checkpoints(db: Session) -> list[InterruptedChatCheckpoint]:
    """Resumable checkpoints parked by a backend restart.

    A parked message is resumable when its latest version holds a persisted
    ``ProviderResponseState`` (the continuation material). Messages parked
    without a checkpoint are ``failed`` by ``mark_interrupted_message_streams``
    and are intentionally not re-enqueued.
    """

    out: list[InterruptedChatCheckpoint] = []
    messages = db.scalars(
        select(Message).where(
            Message.role == "assistant",
            Message.status == "interrupted",
        )
    ).all()
    for message in messages:
        version = db.scalar(
            select(MessageVersion)
            .where(
                MessageVersion.message_id == message.id,
                MessageVersion.status == "interrupted",
            )
            .order_by(MessageVersion.version.desc())
            .limit(1)
        )
        if version is None:
            continue
        state = db.scalar(
            select(ProviderResponseState).where(
                ProviderResponseState.message_version_id == version.id
            )
        )
        if state is None:
            continue
        out.append(
            InterruptedChatCheckpoint(
                workspace_id=message.workspace_id,
                message_id=message.id,
                message_version_id=version.id,
                provider_id=state.provider_id,
            )
        )
    return out


def enqueue_interrupted_chat_resumes() -> int:
    """Enqueue one durable resume job per resumable checkpoint (idempotent)."""

    settings = get_settings()
    ensured = 0
    with SessionLocal() as db:
        queue = DurableQueue(
            db,
            lease_seconds=settings.durable_queue_lease_seconds,
            max_attempts=settings.durable_queue_max_attempts,
        )
        for checkpoint in list_interrupted_chat_checkpoints(db):
            queue.enqueue(
                workspace_id=checkpoint.workspace_id,
                kind=JOB_KIND,
                payload={
                    "message_id": checkpoint.message_id,
                    "message_version_id": checkpoint.message_version_id,
                    "provider_id": checkpoint.provider_id,
                },
                dedupe_key=_dedupe_key(checkpoint.message_version_id),
            )
            ensured += 1
    return ensured


def continue_chat_stream_from_checkpoint(
    db: Session,
    *,
    message: Message,
    version: MessageVersion,
    provider_id: str,
) -> None:
    """Provider continuation seam (extended when a provider becomes capable).

    Today every provider is outside ``CHAT_CONTINUATION_CAPABLE_PROVIDERS``, so
    this raises ``ChatContinuationUnavailable`` — the message stays parked and
    the existing retry path remains. A future provider adapter that exposes
    stateless continuation is added to the capable registry and resumes the
    stream here by replaying the persisted ``ProviderResponseState``
    continuation material (never re-running already-committed steps). The seam
    owns message/version status transitions on success.
    """

    del db, message, version
    if provider_id not in CHAT_CONTINUATION_CAPABLE_PROVIDERS:
        raise ChatContinuationUnavailable(
            f"provider {provider_id!r} does not expose stateless chat continuation"
        )
    raise ChatContinuationUnavailable(
        f"provider {provider_id!r} continuation is not implemented"
    )


def run_chat_continue_once(payload: dict[str, Any]) -> bool:
    """Worker entry: one audited resume attempt for one interrupted checkpoint."""

    with SessionLocal() as db:
        version = db.get(MessageVersion, str(payload["message_version_id"]))
        if version is None:
            return True
        message = db.get(Message, str(payload["message_id"]))
        if message is None:
            return True
        # If the user already retried (status changed) or the version completed,
        # this is a no-op — never re-run generation for an already-terminal turn.
        if message.status != "interrupted" or version.status != "interrupted":
            return True
        state = db.scalar(
            select(ProviderResponseState).where(
                ProviderResponseState.message_version_id == version.id
            )
        )
        if state is None:
            return True
        audit = AuditRepository(db, message.workspace_id)
        try:
            continue_chat_stream_from_checkpoint(
                db,
                message=message,
                version=version,
                provider_id=state.provider_id,
            )
        except ChatContinuationUnavailable as exc:
            # Expected outcome: provider-native continuation is not available.
            # The message stays interrupted (checkpoint preserved) and the user
            # retry path (retry_message replay) remains explicit and actionable.
            audit.record(
                actor_id=RESUME_ACTOR,
                action="chat.continue_unavailable",
                resource_type="message",
                resource_id=message.id,
                outcome="deferred",
                details={
                    "message_version_id": version.id,
                    "provider_id": state.provider_id,
                    "reason": str(exc),
                    "retry_path": "user_retry_preserves_checkpoint",
                },
            )
            db.commit()
            return True
        audit.record(
            actor_id=RESUME_ACTOR,
            action="chat.continue_resumed",
            resource_type="message",
            resource_id=message.id,
            details={
                "message_version_id": version.id,
                "provider_id": state.provider_id,
            },
        )
        db.commit()
        return True
