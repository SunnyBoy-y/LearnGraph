"""Transactional removal of memory derived from deleted conversations.

The caller validates the sessions and owns commit/rollback. External providers
are cleaned by the existing durable projection outbox, never before commit.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, delete, or_, select, update

from app.core.errors import AppError
from sqlalchemy.orm import Session

from app.domain.memory_event_models import (
    ConversationEpisode, MemoryAccessLog, MemoryContextPackage, MemoryEvent,
    MemoryFeedback, MemoryPayloadKey, MemoryRelation, MemoryScopeContext,
    MemorySearchDocument, MemoryStream,
)
from app.domain.models import (
    MemoryDeletionRecovery, MemoryDraft, MemoryEmbedding, MemoryEvidence,
    MemoryExtractionState, MemoryJournalEntry, MemoryProfileSnapshot,
    ChatSession, MemoryRecord, MemoryRevision, Message, Workspace, utc_now,
)
from app.services.memory_projector import MemoryProjector


def lock_session_memory_sources(db: Session, workspace_id: str, session_ids: list[str]) -> None:
    """Serialize memory writes with deletion without holding locks over model calls."""
    ids = set(session_ids)
    if not ids:
        return
    # A no-op UPDATE takes a write lock on SQLite and row locks on PostgreSQL.
    # Unlike a cached ORM lookup, it also notices a concurrently deleted source.
    result = db.execute(update(ChatSession).where(
        ChatSession.workspace_id == workspace_id, ChatSession.id.in_(ids),
    ).values(updated_at=ChatSession.updated_at).execution_options(synchronize_session=False))
    if result.rowcount != len(ids):
        raise AppError(404, "session_not_found", "Memory source session was deleted")


@dataclass
class SessionMemoryTargets:
    memory_ids: set[str]
    draft_ids: set[str]
    evidence_ids: set[str]
    episode_ids: set[str]
    stream_ids: set[str]


class SessionMemoryDeletionService:
    def __init__(self, db: Session, workspace: Workspace, actor_id: str) -> None:
        self.db = db
        self.workspace = workspace
        self.actor_id = actor_id

    def targets(self, session_ids: list[str]) -> SessionMemoryTargets:
        workspace_id = self.workspace.id
        sessions = set(session_ids)
        message_ids = set(self.db.scalars(select(Message.id).where(
            Message.workspace_id == workspace_id,
            Message.session_id.in_(session_ids),
        )))
        evidence_ids = set(self.db.scalars(select(MemoryEvidence.id).where(
            MemoryEvidence.workspace_id == workspace_id,
            or_(
                MemoryEvidence.message_id.in_(message_ids),
                and_(MemoryEvidence.source_kind.in_((
                    "message", "chat_message", "user_statement",
                )), MemoryEvidence.source_id.in_(message_ids)),
                and_(MemoryEvidence.source_kind.in_(("session", "conversation")),
                     MemoryEvidence.source_id.in_(session_ids)),
            ),
        )))
        source_ids = sessions | message_ids | evidence_ids
        drafts = []
        for draft in self.db.scalars(select(MemoryDraft).where(
            MemoryDraft.workspace_id == workspace_id,
        )):
            refs = {
                str(ref.get("id", "")) if isinstance(ref, dict) else str(ref)
                for ref in (draft.source_refs or [])
            }
            if (draft.session_id in sessions or draft.branch_session_id in sessions
                    or refs & source_ids):
                drafts.append(draft)
        draft_ids = {draft.id for draft in drafts}
        # A pending proposal targeting someone else's atom is NOT ownership.
        # A committed result, however, contains data derived from this session.
        result_ids = {draft.result_memory_id for draft in drafts if draft.result_memory_id}
        source_ids |= draft_ids
        revision_ids = {
            revision.memory_id
            for revision in self.db.scalars(select(MemoryRevision).where(
                MemoryRevision.workspace_id == workspace_id,
            ))
            if revision.session_id in sessions or set(revision.source_ids or []) & source_ids
        }
        memory_ids = {
            record.id
            for record in self.db.scalars(select(MemoryRecord).where(
                MemoryRecord.workspace_id == workspace_id,
                MemoryRecord.tenant_id == self.workspace.tenant_id,
            ))
            if (record.session_id in sessions or record.conversation_id in sessions
                or (record.scope_type == "session" and record.scope_id in sessions)
                or record.id in result_ids | revision_ids
                or set(record.source_ids or []) & source_ids
                or set(record.evidence_ids or []) & evidence_ids)
        }
        # V2-only memories need not have a legacy MemoryRecord.
        memory_ids.update(self.db.scalars(select(MemorySearchDocument.target_id).where(
            MemorySearchDocument.tenant_id == self.workspace.tenant_id,
            MemorySearchDocument.workspace_id == workspace_id,
            MemorySearchDocument.target_type == "memory",
            MemorySearchDocument.conversation_id.in_(session_ids),
        )))
        event_streams = select(MemoryEvent.stream_id).where(
            MemoryEvent.tenant_id == self.workspace.tenant_id,
            MemoryEvent.workspace_id == workspace_id,
            MemoryEvent.conversation_id.in_(session_ids),
        )
        memory_ids.update(self.db.scalars(select(MemoryStream.aggregate_id).where(
            MemoryStream.tenant_id == self.workspace.tenant_id,
            MemoryStream.workspace_id == workspace_id,
            MemoryStream.aggregate_type == "memory_atom",
            MemoryStream.id.in_(event_streams),
        )))
        episodes = list(self.db.scalars(select(ConversationEpisode).where(
            ConversationEpisode.tenant_id == self.workspace.tenant_id,
            ConversationEpisode.workspace_id == workspace_id,
            ConversationEpisode.conversation_id.in_(session_ids),
        )))
        stream_ids = set(self.db.scalars(select(MemoryStream.id).where(
            MemoryStream.tenant_id == self.workspace.tenant_id,
            MemoryStream.workspace_id == workspace_id,
            or_(
                and_(MemoryStream.aggregate_type == "memory_atom",
                     MemoryStream.aggregate_id.in_(memory_ids)),
                and_(MemoryStream.aggregate_type == "episode",
                     MemoryStream.id.in_(event_streams)),
            ),
        )))
        stream_ids.update(episode.stream_id for episode in episodes)
        return SessionMemoryTargets(memory_ids, draft_ids, evidence_ids,
                                    {episode.id for episode in episodes}, stream_ids)

    def delete(self, session_ids: list[str]) -> SessionMemoryTargets:
        lock_session_memory_sources(self.db, self.workspace.id, session_ids)
        targets = self.targets(session_ids)
        workspace_id = self.workspace.id
        tenant_id = self.workspace.tenant_id
        now = utc_now()
        records = list(self.db.scalars(select(MemoryRecord).where(
            MemoryRecord.workspace_id == workspace_id,
            MemoryRecord.tenant_id == tenant_id,
            MemoryRecord.id.in_(targets.memory_ids),
        )))
        if records:
            from app.core.config import get_settings
            from app.services.memory_commands import MemoryCommandService
            from app.services.memory_event_ingestor import event_cipher_from_settings
            from app.services.memory_event_store import MemoryEventStore

            commands = MemoryCommandService(self.db, MemoryEventStore(
                self.db, event_cipher_from_settings(get_settings()),
            ))
            for record in records:
                commands.forget(
                    MemoryScopeContext(
                        tenant_id=tenant_id, workspace_id=workspace_id,
                        principal_user_id=record.subject_user_id or self.actor_id,
                        conversation_id=record.conversation_id or record.session_id,
                    ),
                    record.id, actor_id=self.actor_id, confirmation="FORGET",
                    reason="source_session_deleted", commit=False,
                )
                record.canonical_key = ""

        # Destroy source-event keys too, including raw conversational evidence.
        # Shared task/project streams are not forgotten wholesale.
        source_key_ids = select(MemoryEvent.payload_key_id).where(
            MemoryEvent.tenant_id == tenant_id,
            MemoryEvent.workspace_id == workspace_id,
            MemoryEvent.conversation_id.in_(session_ids),
        )
        for key in self.db.scalars(select(MemoryPayloadKey).where(or_(
            MemoryPayloadKey.stream_id.in_(targets.stream_ids),
            MemoryPayloadKey.id.in_(source_key_ids),
        ))):
            key.wrapped_dek = None
            key.status = "destroyed"
            key.destroyed_at = now
            key.destroyed_by = self.actor_id
            key.reason = "source_session_deleted"
        for stream in self.db.scalars(select(MemoryStream).where(
            MemoryStream.tenant_id == tenant_id,
            MemoryStream.workspace_id == workspace_id,
            MemoryStream.id.in_(targets.stream_ids),
        )):
            stream.status = "forgotten"
            stream.payload_key_id = None

        documents = self.db.scalars(select(MemorySearchDocument).where(
            MemorySearchDocument.tenant_id == tenant_id,
            MemorySearchDocument.workspace_id == workspace_id,
            or_(
                and_(MemorySearchDocument.target_type == "memory",
                     MemorySearchDocument.target_id.in_(targets.memory_ids)),
                and_(MemorySearchDocument.target_type == "episode",
                     MemorySearchDocument.target_id.in_(targets.episode_ids)),
            ),
        )).all()
        projector = MemoryProjector(self.db)
        for document in documents:
            projector._delete_fts_row(document.id)
            self.db.delete(document)

        for model, column, ids in (
            (MemoryDraft, MemoryDraft.id, targets.draft_ids),
            (MemoryEvidence, MemoryEvidence.id, targets.evidence_ids),
            (MemoryExtractionState, MemoryExtractionState.session_id, session_ids),
            (MemoryEmbedding, MemoryEmbedding.memory_id, targets.memory_ids),
            (MemoryFeedback, MemoryFeedback.memory_id, targets.memory_ids),
            (MemoryAccessLog, MemoryAccessLog.memory_id, targets.memory_ids),
            (ConversationEpisode, ConversationEpisode.id, targets.episode_ids),
        ):
            self.db.execute(delete(model).where(
                model.workspace_id == workspace_id, column.in_(ids),
            ))
        for journal in self.db.scalars(select(MemoryJournalEntry).where(
            MemoryJournalEntry.workspace_id == workspace_id,
            MemoryJournalEntry.memory_id.in_(targets.memory_ids),
        )):
            journal.payload = {}
            journal.content_hash = ""
            journal.tombstone = True
            journal.recoverable_until = None
            journal.content_scrubbed_at = now
        for recovery in self.db.scalars(select(MemoryDeletionRecovery).where(
            MemoryDeletionRecovery.workspace_id == workspace_id,
            MemoryDeletionRecovery.memory_id.in_(targets.memory_ids),
        )):
            recovery.encrypted_payload = None
            recovery.recoverable_until = now
            recovery.destroyed_at = now

        affected_ids = targets.memory_ids | targets.episode_ids
        self.db.execute(delete(MemoryRelation).where(
            MemoryRelation.tenant_id == tenant_id,
            MemoryRelation.workspace_id == workspace_id,
            or_(
                and_(MemoryRelation.from_type.in_(("memory", "memory_atom", "episode")),
                     MemoryRelation.from_id.in_(affected_ids)),
                and_(MemoryRelation.to_type.in_(("memory", "memory_atom", "episode")),
                     MemoryRelation.to_id.in_(affected_ids)),
            ),
        ))
        for snapshot in self.db.scalars(select(MemoryProfileSnapshot).where(
            MemoryProfileSnapshot.workspace_id == workspace_id,
        )):
            claims = {atom_id for ids in (snapshot.claim_atom_map or {}).values() for atom_id in ids}
            if (set(snapshot.source_atom_ids or []) | claims) & targets.memory_ids:
                # Stale snapshots are still displayed by get_profile: erase,
                # don't merely mark stale, and let a later refresh rebuild.
                snapshot.markdown = ""
                snapshot.structured_sections = []
                snapshot.source_atom_ids = []
                snapshot.claim_atom_map = {}
                snapshot.source_fingerprint = ""
                snapshot.status = "stale"
                snapshot.stale_reason = "source_session_deleted"
        for package in self.db.scalars(select(MemoryContextPackage).where(
            MemoryContextPackage.workspace_id == workspace_id,
        )):
            affected = package.conversation_id in session_ids
            for field in (
                "candidate_ids_json", "retrieved_ids_json", "selected_ids_json",
                "injected_ids_json", "excluded_ids_json", "truncated_ids_json",
            ):
                ids = getattr(package, field) or []
                if set(ids) & affected_ids:
                    affected = True
                    setattr(package, field, [item for item in ids if item not in affected_ids])
            if affected:
                package.outcome_status = "invalidated_by_forget"
                package.reason_codes_json = {}
                package.dropped_reasons_json = {}
        self.db.flush()
        return targets
