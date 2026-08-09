from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.models import TimestampMixin, WorkspaceScopedMixin, new_id, utc_now


class MemoryStream(Base, TimestampMixin):
    """CAS-protected aggregate stream for memory-domain events."""

    __tablename__ = "memory_streams"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "aggregate_type",
            "aggregate_id",
            name="uq_memory_stream_aggregate",
        ),
        Index("ix_memory_stream_scope", "tenant_id", "workspace_id", "task_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    aggregate_type: Mapped[str] = mapped_column(String(40), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    payload_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class MemoryPayloadKey(Base, TimestampMixin):
    """Wrapped event data key; destroying it makes ciphertext irrecoverable."""

    __tablename__ = "memory_payload_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    stream_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    wrapped_dek: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    algorithm: Mapped[str] = mapped_column(String(40), default="fernet-dek-v1")
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    key_provider: Mapped[str] = mapped_column(String(40), default="application-master-key")
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    destroyed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(240), default="")


class MemoryEvent(Base):
    """Immutable event envelope. Sensitive payloads are ciphertext only."""

    __tablename__ = "memory_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_memory_event_id"),
        UniqueConstraint("stream_id", "stream_version", name="uq_memory_event_stream_version"),
        UniqueConstraint(
            "tenant_id", "producer", "idempotency_key", name="uq_memory_event_idempotency"
        ),
        Index("ix_memory_event_scope_position", "tenant_id", "workspace_id", "global_position"),
    )

    global_position: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    event_id: Mapped[str] = mapped_column(String(64), default=new_id)
    stream_id: Mapped[str] = mapped_column(
        ForeignKey("memory_streams.id", ondelete="RESTRICT"), index=True
    )
    stream_version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    event_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    producer: Mapped[str] = mapped_column(String(40), index=True)
    actor_type: Mapped[str] = mapped_column(String(32), default="system")
    actor_id: Mapped[str] = mapped_column(String(64), default="system")
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    knowledge_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sensitivity: Mapped[str] = mapped_column(String(24), default="normal", index=True)
    payload_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payload_key_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_payload_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    payload_hash: Mapped[str] = mapped_column(String(71))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryProjectionCheckpoint(Base):
    __tablename__ = "memory_projection_checkpoints"

    projector_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    projection_version: Mapped[int] = mapped_column(Integer, default=1)
    last_global_position: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryProjectionOutbox(Base, TimestampMixin):
    __tablename__ = "memory_projection_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_memory_projection_outbox_dedupe"),
        Index("ix_memory_outbox_claim", "status", "available_at", "lease_until"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    projection_kind: Mapped[str] = mapped_column(String(40), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(240))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")


class MemorySearchDocument(Base, TimestampMixin):
    __tablename__ = "memory_search_documents"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", name="uq_memory_search_target"),
        Index("ix_memory_search_scope", "tenant_id", "workspace_id", "task_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    target_type: Mapped[str] = mapped_column(String(40), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    target_version: Mapped[int] = mapped_column(Integer, default=1)
    memory_layer: Mapped[str] = mapped_column(String(16), default="L3", index=True)
    memory_type: Mapped[str] = mapped_column(String(64), default="semantic_memory", index=True)
    subject: Mapped[str] = mapped_column(String(240), default="")
    slot_key: Mapped[str] = mapped_column(String(240), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    keywords_text: Mapped[str] = mapped_column(Text, default="")
    entity_aliases_text: Mapped[str] = mapped_column(Text, default="")
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    knowledge_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    sensitivity: Mapped[str] = mapped_column(String(24), default="normal", index=True)
    zone: Mapped[str] = mapped_column(String(16), default="recent", index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    source_event_id: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    projection_version: Mapped[int] = mapped_column(Integer, default=1)


class MemoryRelation(Base, TimestampMixin):
    __tablename__ = "memory_relations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "from_type", "from_id", "relation_type", "to_type", "to_id",
            name="uq_memory_relation_edge",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    from_type: Mapped[str] = mapped_column(String(40))
    from_id: Mapped[str] = mapped_column(String(64), index=True)
    relation_type: Mapped[str] = mapped_column(String(40), index=True)
    to_type: Mapped[str] = mapped_column(String(40))
    to_id: Mapped[str] = mapped_column(String(64), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source_event_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)


class MemoryFeedback(Base, WorkspaceScopedMixin):
    __tablename__ = "memory_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(String(64), index=True)
    context_build_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    feedback_type: Mapped[str] = mapped_column(String(40), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    applied_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MemoryContextPackage(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "memory_context_packages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    request_hash: Mapped[str] = mapped_column(String(64))
    query_hash: Mapped[str] = mapped_column(String(64))
    scope_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(40))
    builder_version: Mapped[str] = mapped_column(String(40))
    agent_id: Mapped[str] = mapped_column(String(80))
    provider_id: Mapped[str] = mapped_column(String(80), default="")
    model_id: Mapped[str] = mapped_column(String(200), default="")
    candidate_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    retrieved_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    injected_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    excluded_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    truncated_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason_codes_json: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    excluded_counts_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    dropped_reasons_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    section_token_usage_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    package_hash: Mapped[str] = mapped_column(String(64), index=True)
    outcome_status: Mapped[str | None] = mapped_column(String(24), nullable=True)


class MemoryAccessLog(Base, WorkspaceScopedMixin):
    __tablename__ = "memory_access_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    context_build_id: Mapped[str] = mapped_column(String(64), index=True)
    memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(80), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    retrieval_reason: Mapped[str] = mapped_column(String(160), default="")
    component_scores_json: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    injected_tokens: Mapped[int] = mapped_column(Integer, default=0)
    used_as: Mapped[str] = mapped_column(String(24), default="context")
    outcome: Mapped[str] = mapped_column(String(24), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryTaskState(Base, TimestampMixin):
    __tablename__ = "memory_task_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    stream_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    stream_version: Mapped[int] = mapped_column(Integer, default=0)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    goal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="planned", index=True)
    current_stage: Mapped[str] = mapped_column(String(120), default="")
    completed_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    pending_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    constraints_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    blocked_by_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    decisions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    artifact_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    related_file_refs_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    next_action: Mapped[str] = mapped_column(Text, default="")
    source_event_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    head_event_id: Mapped[str] = mapped_column(String(64), index=True)
    projection_version: Mapped[int] = mapped_column(Integer, default=1)


class ConversationEpisode(Base, TimestampMixin):
    __tablename__ = "conversation_episodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    stream_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    stream_version: Mapped[int] = mapped_column(Integer, default=0)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text, default="")
    decisions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    open_questions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    constraints_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    entities_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_message_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_event_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    start_event_position: Mapped[int] = mapped_column(BigInteger, default=0)
    end_event_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    boundary_reason: Mapped[str] = mapped_column(String(120), default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    head_event_id: Mapped[str] = mapped_column(String(64), index=True)
    projection_version: Mapped[int] = mapped_column(Integer, default=1)


class LearningNodeState(Base, TimestampMixin):
    __tablename__ = "learning_node_states"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject_user_id", "workspace_id", "knowledge_node_id",
            name="uq_learning_node_state_subject",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    knowledge_node_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="unseen", index=True)
    mastery_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    misconceptions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    last_studied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_evidence_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    algorithm_version: Mapped[str] = mapped_column(String(40), default="learning-state-v1")
    stream_version: Mapped[int] = mapped_column(Integer, default=0)
    head_event_id: Mapped[str] = mapped_column(String(64), index=True)


class AgentRunProjection(Base, TimestampMixin):
    __tablename__ = "agent_run_projections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    stream_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    agent_id: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(200), default="")
    input_scope_hash: Mapped[str] = mapped_column(String(64))
    context_build_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_call_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    artifact_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    head_event_id: Mapped[str] = mapped_column(String(64))
    projection_version: Mapped[int] = mapped_column(Integer, default=1)


class StrategyMemory(Base, TimestampMixin):
    __tablename__ = "strategy_memories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="candidate", index=True)
    title: Mapped[str] = mapped_column(String(240))
    reproducible_steps_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    environment_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    successful_run_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    failed_run_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    success_rate: Mapped[float] = mapped_column(Float, default=0.0)
    tool_versions_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    head_event_id: Mapped[str] = mapped_column(String(64))
    projection_version: Mapped[int] = mapped_column(Integer, default=1)


class MemoryRetrievalTrace(Base, TimestampMixin):
    """Memory-domain retrieval trace. Query plaintext is never persisted."""

    __tablename__ = "memory_retrieval_traces"
    __table_args__ = (
        Index("ix_memory_retrieval_trace_build", "context_build_id"),
        Index("ix_memory_retrieval_trace_scope", "tenant_id", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    context_build_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(80), default="main_agent")
    query_hash: Mapped[str] = mapped_column(String(64))
    routes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    signals_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_counts_json: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    degraded_modes_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    fts_capability: Mapped[str] = mapped_column(String(24), default="unknown")
    strategy: Mapped[str] = mapped_column(String(40), default="hybrid_memory_v2")
    status: Mapped[str] = mapped_column(String(24), default="completed")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


@dataclass(frozen=True, slots=True)
class MemoryScopeContext:
    tenant_id: str
    principal_user_id: str
    workspace_id: str
    task_id: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    goal_id: str | None = None
    node_ids: tuple[str, ...] = ()
    agent_id: str = "main_agent"
    allowed_sensitivity: frozenset[str] = frozenset({"public", "normal", "private"})
    cross_workspace_authorized: bool = False
