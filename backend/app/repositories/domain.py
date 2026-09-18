from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.models import (
    AnswerRecord,
    BudgetAlert,
    BudgetPolicy,
    ChatSession,
    Evidence,
    Exercise,
    ExchangeRateVersion,
    FileReference,
    FileRecord,
    FileTextChunk,
    Goal,
    Graph,
    GraphChangeSet,
    GraphEdge,
    GraphNodeMerge,
    GraphNode,
    ImageGenerationTask,
    MemoryDeletionRecovery,
    MemoryDraft,
    MemoryJournalEntry,
    MemoryProviderBinding,
    MemoryRecord,
    MemoryRevision,
    MasteryReviewJob,
    MasterySchedule,
    MasteryMessageActivity,
    MasterySessionState,
    Message,
    MessagePartRecord,
    MessageStreamEvent,
    MessageSubmission,
    MessageVersion,
    MigrationJob,
    PluginRecord,
    PriceVersion,
    PracticeSession,
    ProviderConfig,
    ProviderResponseState,
    ResearchJob,
    ResearchJobEvent,
    SourceRecord,
    SuggestedPromptBatch,
    UsageEvent,
    WorkspaceSetting,
    VoiceEventRecord,
    VoiceResultInboxRecord,
    VoiceSessionRecord,
    VoiceSpeechDeliveryRecord,
    VoiceTaskLinkRecord,
    VoiceTurnRecord,
)
from app.domain.learning_package_models import open_eligibility_guard
from app.repositories.scoped import ScopedRepository


class GoalRepository(ScopedRepository[Goal]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, Goal, workspace_id)


class GraphRepository(ScopedRepository[Graph]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, Graph, workspace_id)


class GraphChangeSetRepository(ScopedRepository[GraphChangeSet]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, GraphChangeSet, workspace_id)


class GraphNodeRepository(ScopedRepository[GraphNode]):
    """The single funnel for node creation, so the eligibility guard is never missed.

    ``add`` opens the node's learning-package eligibility guard row right after
    the node row exists. The ordering is explicit on purpose: the guard's foreign
    keys point at ``graph_nodes``/``graphs``, and the unit of work has no
    relationship to order those mappers by, so it falls back to sorting them by
    ``module.ClassName``. Creating the guard in the same flush as its node is
    therefore emitted child-first and rejected by SQLite (PRAGMA
    foreign_keys=ON). See ``open_eligibility_guard``.
    """

    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, GraphNode, workspace_id)

    def add(self, instance: GraphNode) -> GraphNode:
        node = super().add(instance)
        open_eligibility_guard(self.db, node)
        return node


class GraphEdgeRepository(ScopedRepository[GraphEdge]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, GraphEdge, workspace_id)


class GraphNodeMergeRepository(ScopedRepository[GraphNodeMerge]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, GraphNodeMerge, workspace_id)


class SessionRepository(ScopedRepository[ChatSession]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ChatSession, workspace_id)


class MessageRepository(ScopedRepository[Message]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, Message, workspace_id)


class MessageVersionRepository(ScopedRepository[MessageVersion]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MessageVersion, workspace_id)


class ProviderResponseStateRepository(ScopedRepository[ProviderResponseState]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ProviderResponseState, workspace_id)


class MessagePartRepository(ScopedRepository[MessagePartRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MessagePartRecord, workspace_id)


class MessageStreamEventRepository(ScopedRepository[MessageStreamEvent]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MessageStreamEvent, workspace_id)


class MessageSubmissionRepository(ScopedRepository[MessageSubmission]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MessageSubmission, workspace_id)


class ImageGenerationTaskRepository(ScopedRepository[ImageGenerationTask]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ImageGenerationTask, workspace_id)


class SuggestedPromptBatchRepository(ScopedRepository[SuggestedPromptBatch]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, SuggestedPromptBatch, workspace_id)


class FileRepository(ScopedRepository[FileRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, FileRecord, workspace_id)


class FileTextChunkRepository(ScopedRepository[FileTextChunk]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, FileTextChunk, workspace_id)


class FileReferenceRepository(ScopedRepository[FileReference]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, FileReference, workspace_id)


class ResearchRepository(ScopedRepository[ResearchJob]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ResearchJob, workspace_id)


class ResearchEventRepository(ScopedRepository[ResearchJobEvent]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ResearchJobEvent, workspace_id)


class SourceRecordRepository(ScopedRepository[SourceRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, SourceRecord, workspace_id)


class EvidenceRepository(ScopedRepository[Evidence]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, Evidence, workspace_id)


class MasteryScheduleRepository(ScopedRepository[MasterySchedule]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MasterySchedule, workspace_id)


class MasterySessionStateRepository(ScopedRepository[MasterySessionState]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MasterySessionState, workspace_id)


class MasteryMessageActivityRepository(ScopedRepository[MasteryMessageActivity]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MasteryMessageActivity, workspace_id)


class MasteryReviewJobRepository(ScopedRepository[MasteryReviewJob]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MasteryReviewJob, workspace_id)


class ExerciseRepository(ScopedRepository[Exercise]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, Exercise, workspace_id)


class AnswerRepository(ScopedRepository[AnswerRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, AnswerRecord, workspace_id)


class PracticeSessionRepository(ScopedRepository[PracticeSession]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, PracticeSession, workspace_id)


class MemoryRepository(ScopedRepository[MemoryRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MemoryRecord, workspace_id)


class MemoryDraftRepository(ScopedRepository[MemoryDraft]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MemoryDraft, workspace_id)


class MemoryRevisionRepository(ScopedRepository[MemoryRevision]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MemoryRevision, workspace_id)


class MemoryJournalRepository(ScopedRepository[MemoryJournalEntry]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MemoryJournalEntry, workspace_id)


class MemoryBindingRepository(ScopedRepository[MemoryProviderBinding]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MemoryProviderBinding, workspace_id)


class MemoryRecoveryRepository(ScopedRepository[MemoryDeletionRecovery]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MemoryDeletionRecovery, workspace_id)


class ProviderRepository(ScopedRepository[ProviderConfig]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ProviderConfig, workspace_id)


class UsageRepository(ScopedRepository[UsageEvent]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, UsageEvent, workspace_id)


class PriceVersionRepository(ScopedRepository[PriceVersion]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, PriceVersion, workspace_id)


class ExchangeRateVersionRepository(ScopedRepository[ExchangeRateVersion]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, ExchangeRateVersion, workspace_id)


class BudgetPolicyRepository(ScopedRepository[BudgetPolicy]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, BudgetPolicy, workspace_id)


class BudgetAlertRepository(ScopedRepository[BudgetAlert]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, BudgetAlert, workspace_id)


class PluginRepository(ScopedRepository[PluginRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, PluginRecord, workspace_id)


class MigrationRepository(ScopedRepository[MigrationJob]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, MigrationJob, workspace_id)


class SettingRepository(ScopedRepository[WorkspaceSetting]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, WorkspaceSetting, workspace_id)


class VoiceSessionRepository(ScopedRepository[VoiceSessionRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, VoiceSessionRecord, workspace_id)

    def get_owned(
        self, voice_session_id: str, *, tenant_id: str, owner_user_id: str
    ) -> VoiceSessionRecord | None:
        return self.db.scalar(
            self.query().where(
                VoiceSessionRecord.id == voice_session_id,
                VoiceSessionRecord.tenant_id == tenant_id,
                VoiceSessionRecord.owner_user_id == owner_user_id,
            )
        )

    def find_active_for_chat(
        self,
        chat_session_id: str,
        *,
        tenant_id: str,
        owner_user_id: str,
    ) -> VoiceSessionRecord | None:
        return self.db.scalar(
            self.query()
            .where(
                VoiceSessionRecord.chat_session_id == chat_session_id,
                VoiceSessionRecord.tenant_id == tenant_id,
                VoiceSessionRecord.owner_user_id == owner_user_id,
                VoiceSessionRecord.status == "active",
            )
            .order_by(VoiceSessionRecord.updated_at.desc())
        )


class VoiceTurnRepository(ScopedRepository[VoiceTurnRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, VoiceTurnRecord, workspace_id)

    def get_for_session(
        self, voice_session_id: str, turn_id: str
    ) -> VoiceTurnRecord | None:
        return self.db.scalar(
            self.query().where(
                VoiceTurnRecord.voice_session_id == voice_session_id,
                VoiceTurnRecord.id == turn_id,
            )
        )


class VoiceEventRepository(ScopedRepository[VoiceEventRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, VoiceEventRecord, workspace_id)


class VoiceTaskLinkRepository(ScopedRepository[VoiceTaskLinkRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, VoiceTaskLinkRecord, workspace_id)

    def get_by_subagent(
        self,
        voice_session_id: str,
        subagent_id: str,
        *,
        tenant_id: str,
    ) -> VoiceTaskLinkRecord | None:
        return self.db.scalar(
            self.query().where(
                VoiceTaskLinkRecord.voice_session_id == voice_session_id,
                VoiceTaskLinkRecord.subagent_id == subagent_id,
                VoiceTaskLinkRecord.tenant_id == tenant_id,
            )
        )

    def get_by_idempotency_key(
        self, idempotency_key: str, *, tenant_id: str
    ) -> VoiceTaskLinkRecord | None:
        if not idempotency_key:
            return None
        return self.db.scalar(
            self.query().where(
                VoiceTaskLinkRecord.tenant_id == tenant_id,
                VoiceTaskLinkRecord.idempotency_key == idempotency_key
            )
        )

    def list_for_session(
        self, voice_session_id: str, *, tenant_id: str
    ) -> list[VoiceTaskLinkRecord]:
        return list(
            self.db.scalars(
                self.query()
                .where(VoiceTaskLinkRecord.tenant_id == tenant_id)
                .where(VoiceTaskLinkRecord.voice_session_id == voice_session_id)
                .order_by(VoiceTaskLinkRecord.created_at.asc())
            ).all()
        )


class VoiceResultInboxRepository(ScopedRepository[VoiceResultInboxRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, VoiceResultInboxRecord, workspace_id)

    def get_by_dedupe_key(
        self, dedupe_key: str, *, tenant_id: str
    ) -> VoiceResultInboxRecord | None:
        return self.db.scalar(
            self.query().where(
                VoiceResultInboxRecord.tenant_id == tenant_id,
                VoiceResultInboxRecord.dedupe_key == dedupe_key,
            )
        )

    def list_for_session(
        self,
        voice_session_id: str,
        *,
        tenant_id: str,
        include_terminal: bool = True,
        limit: int = 50,
    ) -> list[VoiceResultInboxRecord]:
        query = self.query().where(
            VoiceResultInboxRecord.tenant_id == tenant_id,
            VoiceResultInboxRecord.voice_session_id == voice_session_id
        )
        if not include_terminal:
            query = query.where(
                VoiceResultInboxRecord.status.in_((
                    "pending",
                    "ready",
                    "failed",
                    "stale",
                ))
            )
        return list(
            self.db.scalars(
                query.order_by(
                    VoiceResultInboxRecord.available_at.desc(),
                    VoiceResultInboxRecord.result_version.desc(),
                ).limit(max(1, min(int(limit), 200)))
            ).all()
        )


class VoiceSpeechDeliveryRepository(ScopedRepository[VoiceSpeechDeliveryRecord]):
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, VoiceSpeechDeliveryRecord, workspace_id)

    def get_by_result(
        self, result_id: str, *, tenant_id: str
    ) -> VoiceSpeechDeliveryRecord | None:
        return self.db.scalar(
            self.query().where(
                VoiceSpeechDeliveryRecord.tenant_id == tenant_id,
                VoiceSpeechDeliveryRecord.result_id == result_id,
            )
        )

    def get_by_idempotency_key(
        self, idempotency_key: str, *, tenant_id: str
    ) -> VoiceSpeechDeliveryRecord | None:
        if not idempotency_key:
            return None
        return self.db.scalar(
            self.query().where(
                VoiceSpeechDeliveryRecord.tenant_id == tenant_id,
                VoiceSpeechDeliveryRecord.idempotency_key == idempotency_key,
            )
        )
