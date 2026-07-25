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
    ProviderConfig,
    ProviderResponseState,
    ResearchJob,
    ResearchJobEvent,
    SourceRecord,
    SuggestedPromptBatch,
    UsageEvent,
    WorkspaceSetting,
)
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
    def __init__(self, db: Session, workspace_id: str) -> None:
        super().__init__(db, GraphNode, workspace_id)


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
