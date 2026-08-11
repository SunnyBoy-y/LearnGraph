from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, Text as SAText


class LenientJSON(TypeDecorator):
    """JSON 列容错读取（T1-3）：脏数据（裸字符串/非容器 JSON）降级为空容器，
    避免读行即 JSONDecodeError 导致 500；写侧仍正常序列化。"""

    impl = SAText
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return __import__("json").dumps(value, ensure_ascii=False, default=str)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                parsed = __import__("json").loads(value)
                return parsed if isinstance(parsed, (dict, list)) else {}
            except (ValueError, TypeError):
                return {}
        return {}


from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class WorkspaceScopedMixin:
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "username_normalized", name="uq_user_tenant_username"),
        UniqueConstraint("tenant_id", "email_normalized", name="uq_user_tenant_email"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(120))
    username_normalized: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_normalized: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    is_system_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_organization_tenant_name"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(160))
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(300), default="")


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_role_organization_name"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(300), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class RolePermission(Base, TimestampMixin):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), index=True
    )
    permission_id: Mapped[str] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), index=True
    )


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    role_id: Mapped[str] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    invited_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base, TimestampMixin):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(120), default="")
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    device_id: Mapped[str] = mapped_column(String(128), default="", index=True)


class ResourceACL(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "resource_acls"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "resource_type",
            "resource_id",
            "grantee_type",
            "grantee_id",
            name="uq_resource_acl_grantee",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str] = mapped_column(String(80), index=True)
    grantee_type: Mapped[str] = mapped_column(String(32), index=True)
    grantee_id: Mapped[str] = mapped_column(String(64), index=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    granted_by: Mapped[str] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecurityEvent(Base):
    __tablename__ = "security_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(500), default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="local-tenant")
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_kind: Mapped[str] = mapped_column(String(32), default="personal", index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    # ask / allow / deny — global consent for event-driven subapp Agent turns.
    subapp_agent_consent: Mapped[str] = mapped_column(
        String(16), default="ask", index=True
    )


class Project(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    primary_goal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    primary_graph_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Goal(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "goals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(240))
    raw_prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="clarifying", index=True)
    intent: Mapped[str] = mapped_column(String(240), default="")
    time_limit: Mapped[str] = mapped_column(String(120), default="")
    # ``time_limit`` remains the user's narrative constraint.  The planner only
    # reads these structured fields so a phrase such as "two weeks" is never
    # silently parsed into a deadline or availability assumption.
    target_weight: Mapped[int] = mapped_column(Integer, default=50)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    availability: Mapped[dict[str, Any]] = mapped_column(LenientJSON(), default=dict)
    preferences: Mapped[dict[str, Any]] = mapped_column(LenientJSON(), default=dict)
    desired_outcome: Mapped[str] = mapped_column(Text, default="")
    constraints: Mapped[dict[str, Any]] = mapped_column(LenientJSON(), default=dict)
    assumptions: Mapped[list[dict[str, Any]]] = mapped_column(LenientJSON(), default=list)


class Graph(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "graphs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    title: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(40), default="candidate", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GraphNode(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "graph_nodes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graphs.id"), index=True)
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    node_type: Mapped[str] = mapped_column(String(40), default="concept")
    # Importance is local to this occurrence in a goal graph.  It is not a
    # global concept score and must not be inferred from browsing activity.
    target_weight: Mapped[int] = mapped_column(Integer, default=50)
    # Subject-aware teaching plan generated with the candidate graph. Hidden from
    # the chat transcript but injected when the user studies this node.
    teaching_strategy: Mapped[str] = mapped_column(Text, default="")
    external_concept_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    mastery_stars: Mapped[int] = mapped_column(Integer, default=0)
    retrieval_state: Mapped[str] = mapped_column(String(40), default="unverified")
    evidence_state: Mapped[str] = mapped_column(String(40), default="none")
    attention_state: Mapped[str] = mapped_column(String(40), default="normal")


class GraphRevision(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "graph_revisions"
    __table_args__ = (UniqueConstraint("workspace_id", "graph_id", "revision", name="uq_graph_revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graphs.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    change_type: Mapped[str] = mapped_column(String(40), default="node_update")
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor_id: Mapped[str] = mapped_column(String(64))


class GraphEdge(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "graph_edges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graphs.id"), index=True)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"))
    target_node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"))
    relation: Mapped[str] = mapped_column(String(80), default="prerequisite")


class GraphNodeMerge(Base, TimestampMixin, WorkspaceScopedMixin):
    """An auditable logical equivalence edge between two node occurrences.

    Nodes remain owned by their goal graph.  A merge therefore records an
    equivalence relationship instead of deleting or rewriting either node;
    undoing a merge cannot invalidate existing sessions, exercises, or files.
    """

    __tablename__ = "graph_node_merges"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"), index=True)
    target_node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="proposed", index=True)
    decision_source: Mapped[str] = mapped_column(String(40), default="user")
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GraphChangeSet(Base, TimestampMixin, WorkspaceScopedMixin):
    """A model-proposed graph mutation that is inert until a user confirms it.

    The proposal is persisted so a message can be replayed and reviewed after a
    refresh, but it is deliberately separate from the Graph/Node/Edge facts.
    Foreign-key cascades keep Session/Goal deletion safe without leaving an
    actionable orphan proposal behind.
    """

    __tablename__ = "graph_change_sets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    goal_id: Mapped[str] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), index=True
    )
    graph_id: Mapped[str | None] = mapped_column(
        ForeignKey("graphs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_user_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    source_assistant_message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    component_part_id: Mapped[str | None] = mapped_column(
        ForeignKey("message_parts.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(32), default="proposed", index=True)
    base_revision: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str] = mapped_column(Text, default="")


class ChatSession(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(240))
    goal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    graph_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Separate "use existing memory" from "contribute new memory". The legacy
    # switch remains the master per-session gate for compatibility.
    memory_recall_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    memory_learning_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_kind: Mapped[str] = mapped_column(String(32), default="main", index=True)
    writeback_policy: Mapped[str] = mapped_column(String(32), default="normal")
    context_capsule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # LLM-generated "learning event" description for the dashboard activity view.
    # Falls back to ``title`` when absent or when the provider is unavailable.
    activity_summary: Mapped[str | None] = mapped_column(String(240), nullable=True)
    # T1-1: session-create idempotency. Hash of the client Idempotency-Key
    # (SHA-256 hex); a repeat POST with the same key returns the first session.
    idempotency_key_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, unique=True
    )


class SubAppSession(Base, TimestampMixin, WorkspaceScopedMixin):
    """Workspace-scoped, replay-resistant runtime for one published sub-application."""

    __tablename__ = "subapp_sessions"
    __table_args__ = (
        Index(
            "ix_subapp_sessions_workspace_actor_status",
            "workspace_id",
            "actor_id",
            "status",
        ),
        Index(
            "ix_subapp_sessions_workspace_chat_session",
            "workspace_id",
            "chat_session_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    # These references intentionally remain application-validated until their
    # table lifecycles are stable across all supported SQLite installations.
    chat_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Contract snapshots prevent an artifact version edit from changing an
    # already-instantiated session's accepted events or states.
    event_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    # Persist only a digest and a non-secret display prefix; raw capabilities
    # never enter this model or the database.
    current_token_hash: Mapped[str] = mapped_column(String(64), default="")
    current_token_prefix: Mapped[str] = mapped_column(String(24), default="")
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    state_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Event-driven Agent task state. agent_consent is ask / allowed_session /
    # denied; deny only affects one consent request, the session returns to ask.
    agent_triggers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    agent_status: Mapped[str] = mapped_column(String(16), default="idle", index=True)
    agent_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_processed_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_consent: Mapped[str] = mapped_column(String(16), default="ask")


class SubAppState(Base, WorkspaceScopedMixin):
    """Immutable, versioned state snapshot for a :class:`SubAppSession`."""

    __tablename__ = "subapp_states"
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_subapp_state_session_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("subapp_sessions.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SubAppAgentRun(Base, TimestampMixin, WorkspaceScopedMixin):
    """One event-driven Agent processing attempt for a sub-application event."""

    __tablename__ = "subapp_agent_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "event_id", name="uq_subapp_agent_run_event"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("subapp_sessions.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("subapp_interaction_events.id", ondelete="CASCADE"), index=True
    )
    chat_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="queued", index=True
    )  # queued/processing/completed/failed/skipped
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SubAppAgentConsentRequest(Base, TimestampMixin, WorkspaceScopedMixin):
    """Pending/decided consent for one event-driven Agent turn."""

    __tablename__ = "subapp_agent_consent_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "event_id", name="uq_subapp_agent_consent_event"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("subapp_sessions.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("subapp_interaction_events.id", ondelete="CASCADE"), index=True
    )
    artifact_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending/allowed/denied/expired/superseded
    scope: Mapped[str] = mapped_column(String(16), default="session")
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "messages"
    __table_args__ = (
        # B1-2: session-history reads filter by workspace+session and order by
        # created_at; the composite index serves both filter and sort.
        Index(
            "ix_messages_workspace_session_created",
            "workspace_id",
            "session_id",
            "created_at",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    parent_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    content: Mapped[str] = mapped_column(Text, default="")
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    provider_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MessageVersion(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "message_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "message_id", "version", name="uq_message_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    provider_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProviderResponseState(Base, TimestampMixin, WorkspaceScopedMixin):
    """Opaque provider continuation state for a completed message version.

    Responses API reasoning items can include encrypted continuation material.
    LearnGraph needs to retain those items verbatim to continue an agent turn,
    but they are transport state rather than user-visible message Parts or a
    Provider Trace. Keeping them in this server-only table prevents an SSE,
    message DTO, audit record, or browser log from exposing them.
    """

    __tablename__ = "provider_response_states"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "message_version_id",
            name="uq_provider_response_state_version",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_version_id: Mapped[str] = mapped_column(
        ForeignKey("message_versions.id", ondelete="CASCADE"), index=True
    )
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_type: Mapped[str] = mapped_column(String(80))
    # The terminal non-tool response for the message version.
    response_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Each completed agent step maps its Part id to the raw output items that
    # preceded its function_call_output items. This preserves the exact order
    # required by a later stateless Responses continuation.
    agent_response_items: Mapped[dict[str, list[dict[str, Any]]]] = mapped_column(
        JSON, default=dict
    )


class CompositeDraft(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "composite_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    source_version_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    content: Mapped[str] = mapped_column(Text)
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    confirmed_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class MessagePartRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "message_parts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "message_version_id",
            "ordinal",
            name="uq_message_part_ordinal",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_version_id: Mapped[str] = mapped_column(
        ForeignKey("message_versions.id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    part_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MessageStreamEvent(Base, WorkspaceScopedMixin):
    __tablename__ = "message_stream_events"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "message_version_id",
            "sequence",
            name="uq_message_stream_sequence",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    message_version_id: Mapped[str] = mapped_column(
        ForeignKey("message_versions.id"), index=True
    )
    part_id: Mapped[str | None] = mapped_column(
        ForeignKey("message_parts.id"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MessageSubmission(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "message_submissions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "session_id",
            "idempotency_key_hash",
            name="uq_message_submission_idempotency",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    user_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    assistant_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    message_version_id: Mapped[str] = mapped_column(
        ForeignKey("message_versions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="streaming", index=True)


class ContextSummary(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "context_summaries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # 'mechanical'      — truncated concatenation written inline by chat compaction
    # 'model'           — background LLM rolling summary (memory_enhancement)
    # 'model_composite' — chat compaction that reused a model summary prefix
    kind: Mapped[str] = mapped_column(String(24), default="mechanical", index=True)
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    estimated_tokens_before: Mapped[int] = mapped_column(Integer)
    estimated_tokens_after: Mapped[int] = mapped_column(Integer)


class SuggestedPromptBatch(Base, TimestampMixin, WorkspaceScopedMixin):
    """A model-generated, context-bound set of follow-up questions.

    The generation key makes a repeated frontend refresh idempotent. The
    context hash covers authorized session, Goal/Graph, and Memory context but
    the source prompt and Memory text are deliberately not persisted here.
    """

    __tablename__ = "suggested_prompt_batches"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "session_id",
            "generation_key",
            name="uq_suggested_prompt_generation",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    anchor_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    anchor_message_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("message_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    context_hash: Mapped[str] = mapped_column(String(64), index=True)
    generation_key: Mapped[str] = mapped_column(String(64))
    source_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    memory_context_used: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_count: Mapped[int] = mapped_column(Integer)
    prompts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    provider_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProviderAttempt(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "provider_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    message_version_id: Mapped[str] = mapped_column(ForeignKey("message_versions.id"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    provider_id: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), index=True)
    received_first_token: Mapped[bool] = mapped_column(Boolean, default=False)
    error_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    remote_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    backoff_ms: Mapped[int] = mapped_column(Integer, default=0)


class MessageControl(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "message_controls"
    message_version_id: Mapped[str] = mapped_column(ForeignKey("message_versions.id"), primary_key=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class ImageGenerationTask(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "image_generation_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    message_version_id: Mapped[str] = mapped_column(
        ForeignKey("message_versions.id", ondelete="CASCADE"), index=True
    )
    source_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), index=True)
    provider_id: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(160))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    prompt_summary: Mapped[str] = mapped_column(String(240), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress_mode: Mapped[str] = mapped_column(String(32), default="indeterminate")
    partial_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_id: Mapped[str | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FileRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    original_name: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(500), unique=True)
    mime_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_status: Mapped[str] = mapped_column(String(40), default="stored")
    parse_capability: Mapped[str] = mapped_column(String(40), default="attachment_only")
    parse_status: Mapped[str] = mapped_column(String(40), default="not_requested")
    parser_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    logical_version: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(40), default="upload")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(24), default="active", index=True)


class ImageDescriptionCache(Base, TimestampMixin, WorkspaceScopedMixin):
    """Durable visual description keyed by image content and vision model."""

    __tablename__ = "image_description_cache"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "image_sha256",
            "provider_id",
            "model_id",
            "prompt_version",
            name="uq_image_description_cache_key",
        ),
        Index("ix_image_description_cache_lookup", "workspace_id", "image_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    image_sha256: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(36))
    model_id: Mapped[str] = mapped_column(String(160))
    media_kind: Mapped[str] = mapped_column(String(16), default="image")
    prompt_version: Mapped[str] = mapped_column(String(40), default="v1")
    status: Mapped[str] = mapped_column(String(24), default="completed", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(String(500), default="")


class AudioTranscription(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "audio_transcriptions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key_hash",
            name="uq_audio_transcription_idempotency",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    provider_id: Mapped[str] = mapped_column(String(36), index=True)
    model_id: Mapped[str] = mapped_column(String(160))
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    transcript: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FileTextChunk(Base, TimestampMixin, WorkspaceScopedMixin):
    """Extracted, inert text with a stable location for citations and prompts."""

    __tablename__ = "file_text_chunks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "file_id", "ordinal", name="uq_file_text_chunk_ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    document_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    locator: Mapped[str] = mapped_column(String(255), default="")
    locator_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    section_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    lifecycle_status: Mapped[str] = mapped_column(String(24), default="active", index=True)


class DocumentRevision(Base, TimestampMixin, WorkspaceScopedMixin):
    """Immutable parse result for one stored file byte sequence."""

    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "file_id", "revision_no", name="uq_document_revision_no"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime_detected: Mapped[str] = mapped_column(String(160))
    processor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    processor_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    config_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    supersedes_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    index_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    embedding_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(24), default="active", index=True)


class DocumentCollection(Base, TimestampMixin, WorkspaceScopedMixin):
    """A user-owned, reusable authorization scope for multi-document learning."""

    __tablename__ = "document_collections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    goal_id: Mapped[str | None] = mapped_column(
        ForeignKey("goals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    graph_id: Mapped[str | None] = mapped_column(
        ForeignKey("graphs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(String(64), index=True)


class DocumentCollectionItem(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "document_collection_items"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "collection_id",
            "file_id",
            name="uq_document_collection_file",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    collection_id: Mapped[str] = mapped_column(
        ForeignKey("document_collections.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    document_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    added_by: Mapped[str] = mapped_column(String(64), index=True)


class DocumentJob(Base, TimestampMixin, WorkspaceScopedMixin):
    """Durable control record for parse/index work."""

    __tablename__ = "document_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "idempotency_key_hash", name="uq_document_job_idempotency"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    document_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_type: Mapped[str] = mapped_column(String(32), default="parse_index")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="validate")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_token: Mapped[str] = mapped_column(String(36), default=new_id)
    created_by: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class DurableJob(Base, TimestampMixin, WorkspaceScopedMixin):
    """Lease-fenced durable dispatch record for resumable background work."""

    __tablename__ = "durable_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "dedupe_key", name="uq_durable_job_dedupe"),
        Index(
            "ix_durable_job_claim",
            "status",
            "available_at",
            "lease_expires_at",
            "priority",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DocumentJobEvent(Base, WorkspaceScopedMixin):
    __tablename__ = "document_job_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "job_id", "sequence", name="uq_document_job_event"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("document_jobs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RetrievalTrace(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "retrieval_traces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    # Older MVP databases require this column. Keep it empty so retrieval
    # traces remain compatible without persisting the user's plaintext query.
    query_text: Mapped[str] = mapped_column(Text, default="")
    query_hash: Mapped[str] = mapped_column(String(64))
    file_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    strategy: Mapped[str] = mapped_column(String(40), default="fts5_bm25")
    status: Mapped[str] = mapped_column(String(32), default="completed")
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    index_revision: Mapped[str] = mapped_column(String(80), default="file_chunks_v1")
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RetrievalHit(Base, WorkspaceScopedMixin):
    __tablename__ = "retrieval_hits"
    __table_args__ = (
        UniqueConstraint("workspace_id", "trace_id", "rank", name="uq_retrieval_hit_rank"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trace_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_traces.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("file_text_chunks.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    used_in_context: Mapped[bool] = mapped_column(Boolean, default=False)


class FileReference(Base, TimestampMixin, WorkspaceScopedMixin):
    """A workspace-scoped, auditable relationship from a file to a domain fact.

    Target ownership remains with the target aggregate.  The polymorphic target
    is validated by the service layer; deleting a file cascades only this link,
    never the referenced Goal, Graph, Message, Evidence, or SourceLink.
    """

    __tablename__ = "file_references"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "file_id",
            "target_type",
            "target_id",
            "relation",
            "locator",
            name="uq_file_reference_target",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    relation: Mapped[str] = mapped_column(String(40), default="reference")
    locator: Mapped[str] = mapped_column(String(255), default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ResearchJob(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "research_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="completed", index=True)
    provider_id: Mapped[str] = mapped_column(String(80), default="local_mock")
    budget_cny: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    actual_cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    provider_task_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    approval_status: Mapped[str] = mapped_column(String(40), default="not_required")
    source_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_pack: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ResearchJobEvent(Base, WorkspaceScopedMixin):
    __tablename__ = "research_job_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "research_job_id", "sequence", name="uq_research_job_event_sequence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_job_id: Mapped[str] = mapped_column(ForeignKey("research_jobs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    """A fetched source body with its authorization and retrieval provenance."""

    __tablename__ = "source_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(String(4_000))
    final_url: Mapped[str] = mapped_column(String(4_000))
    title: Mapped[str] = mapped_column(String(1_000), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(160), default="text/markdown")
    authorized_domain: Mapped[str] = mapped_column(String(255))
    cache_status: Mapped[str] = mapped_column(String(40), default="fresh")
    research_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_jobs.id"), nullable=True, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SourceCitation(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "source_citations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_records.id"), index=True)
    research_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_jobs.id"), nullable=True, index=True
    )
    quote: Mapped[str] = mapped_column(Text, default="")
    locator: Mapped[str] = mapped_column(String(500), default="")


class SourceLink(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "source_links"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_id", "target_type", "target_id", name="uq_source_link_target"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_records.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    relation: Mapped[str] = mapped_column(String(40), default="reference")


class ActionItem(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "action_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(32), default="user", index=True)
    action_type: Mapped[str] = mapped_column(String(48), default="todo", index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    goal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    graph_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    roadmap_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    day_index: Mapped[int] = mapped_column(Integer, default=1)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Roadmap(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "roadmaps"
    __table_args__ = (UniqueConstraint("workspace_id", "goal_id", "version", name="uq_roadmap_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    graph_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    graph_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    # Immutable input facts used by this draft.  The planner writes a hash of
    # the graph state here so a candidate-graph edit cannot be published with
    # an out-of-date route by accident.
    planning_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Evidence(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(String(36), index=True)
    source_type: Mapped[str] = mapped_column(String(50))
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[str] = mapped_column(String(32), default="observed")
    difficulty: Mapped[float] = mapped_column(Float, default=0.5)
    assistance_level: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_ref: Mapped[str] = mapped_column(String(200), default="")
    source_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_content_hash: Mapped[str] = mapped_column(String(64), default="")
    validity_status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MasterySchedule(Base, TimestampMixin, WorkspaceScopedMixin):
    """The current review schedule for one graph node in one workspace."""

    __tablename__ = "mastery_schedules"
    __table_args__ = (UniqueConstraint("workspace_id", "node_id", name="uq_mastery_schedule_node"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id"), index=True)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_qualified_recall_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_message_count: Mapped[int] = mapped_column(Integer, default=0)
    active_rule_version: Mapped[str] = mapped_column(String(40), default="mastery-v1")


class MasterySessionState(Base, TimestampMixin, WorkspaceScopedMixin):
    """Durable per-session cursor for message-threshold and idle review triggers."""

    __tablename__ = "mastery_session_states"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "session_id",
            name="uq_mastery_session_state",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    pending_message_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    pending_node_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    activity_version: Mapped[int] = mapped_column(Integer, default=0)
    processed_version: Mapped[int] = mapped_column(Integer, default=0)
    enqueued_version: Mapped[int] = mapped_column(Integer, default=0)
    last_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    idle_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MasteryMessageActivity(Base, WorkspaceScopedMixin):
    """Exactly-once admission record for a user message entering mastery work."""

    __tablename__ = "mastery_message_activities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "message_id",
            name="uq_mastery_message_activity",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    activity_version: Mapped[int] = mapped_column(Integer, default=0)
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class MasteryReviewJob(Base, TimestampMixin, WorkspaceScopedMixin):
    """Persisted scheduler work so a retry cannot award the same star twice."""

    __tablename__ = "mastery_review_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "dedupe_key",
            name="uq_mastery_review_job_dedupe",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trigger: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="")
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Exercise(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "exercises"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(String(36), index=True)
    question_type: Mapped[str] = mapped_column(String(40), default="single_choice")
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Server-only answer material. Never serialize into ExerciseView / bank exports.
    answer_key: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    generation_batch_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Short-answer points and other scoring rules. Never exposed on ExerciseView.
    rubric_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AnswerRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "answer_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    exercise_id: Mapped[str] = mapped_column(ForeignKey("exercises.id"), index=True)
    answer: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean)
    feedback: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class SubAppInteractionEvent(Base, TimestampMixin, WorkspaceScopedMixin):
    """Persist one sub-application event, following the AnswerRecord audit pattern.

    The raw event is retained for agent analysis; answer-like events are later
    projected into AnswerRecord by the service layer for learning workflows.
    """

    __tablename__ = "subapp_interaction_events"
    __table_args__ = (
        Index(
            "ix_subapp_interaction_events_workspace_session",
            "workspace_id",
            "session_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # Deliberately not a foreign key until T2.3 creates subapp_sessions.
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    chat_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(120))
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class MemoryRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    """Scoped recallable memory. Canonical mastery/graph/file state lives elsewhere.

    knowledge_scope (scope_type + scope_id/goal_id/node_id) is independent from
    conversation scope (namespace + session_id). Child nodes do not store parent
    memory copies; callers assemble an effective view at query time.
    """

    __tablename__ = "memory_records"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    namespace: Mapped[str] = mapped_column(String(40), default="workspace")
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Knowledge scope (Goal-Scoped Memory Graph). Defaults keep pre-scope rows valid.
    scope_type: Mapped[str] = mapped_column(String(24), default="workspace", index=True)
    scope_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    goal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    record_kind: Mapped[str] = mapped_column(String(64), default="semantic_memory")
    merge_strategy: Mapped[str] = mapped_column(
        String(40), default="UNION"
    )
    zone: Mapped[str] = mapped_column(String(24), default="topics", index=True)
    state: Mapped[str] = mapped_column(String(24), default="active", index=True)
    title: Mapped[str] = mapped_column(String(240))
    content_hash: Mapped[str] = mapped_column(String(64))
    relative_path: Mapped[str] = mapped_column(String(500))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(120), default="user")
    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    structured_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Processed atomic fact lifecycle. Raw chat/file text remains in its own
    # source tables and is referenced through MemoryEvidence.
    atom_schema_version: Mapped[int] = mapped_column(Integer, default=1, index=True)
    canonical_key: Mapped[str] = mapped_column(String(240), default="", index=True)
    atom_kind: Mapped[str] = mapped_column(String(64), default="fact", index=True)
    ledger_status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    temporal_status: Mapped[str] = mapped_column(String(32), default="timeless", index=True)
    summary_eligibility: Mapped[str] = mapped_column(
        String(32), default="durable", index=True
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timezone_name: Mapped[str] = mapped_column(String(80), default="Asia/Shanghai")
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    strength: Mapped[float] = mapped_column(Float, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmation_count: Mapped[int] = mapped_column(Integer, default=0)
    successful_use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_status: Mapped[str] = mapped_column(String(40), default="none")
    decay_policy: Mapped[str] = mapped_column(String(40), default="SLOW")
    supersedes_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider_id: Mapped[str] = mapped_column(
        String(80), default="local_workspace_markdown"
    )
    provider_binding_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    recoverable_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    content_destroyed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), default="local-tenant", index=True)
    subject_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    audience_type: Mapped[str] = mapped_column(String(24), default="workspace", index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    memory_layer: Mapped[str] = mapped_column(String(16), default="L4", index=True)
    assertion_type: Mapped[str] = mapped_column(String(32), default="explicit", index=True)
    sensitivity: Mapped[str] = mapped_column(String(24), default="normal", index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    head_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    projection_version: Mapped[int] = mapped_column(Integer, default=1)
    auto_recall_suppressed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    child_agent_denied: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class MemoryEvidence(Base, TimestampMixin, WorkspaceScopedMixin):
    """Typed provenance for memory atoms, separate from the atom ledger."""

    __tablename__ = "memory_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_kind: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str] = mapped_column(String(160), index=True)
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_part_id: Mapped[str | None] = mapped_column(
        ForeignKey("message_parts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    file_id: Mapped[str | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    authorship: Mapped[str] = mapped_column(String(32), index=True)
    derived_from: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    profile_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    eligibility_reason: Mapped[str] = mapped_column(String(240), default="")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class MemoryProfileSnapshot(Base, TimestampMixin, WorkspaceScopedMixin):
    """Versioned, fully rewritten user-facing memory summary."""

    __tablename__ = "memory_profile_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "owner_subject_id",
            "version",
            name="uq_memory_profile_snapshot_version",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_subject_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    markdown: Mapped[str] = mapped_column(Text, default="")
    structured_sections: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    source_atom_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    claim_atom_map: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(40), default="memory-profile-v1")
    model_id: Mapped[str] = mapped_column(String(200), default="")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stale_reason: Mapped[str] = mapped_column(String(240), default="")


class MemoryDraft(Base, TimestampMixin, WorkspaceScopedMixin):
    """Buffered memory mutation proposed by agents or users before commit.

    Mirrors GraphChangeSet: child sessions and tools propose drafts; only commit
    promotes them into MemoryRecord + MemoryRevision under LearnGraph authority.
    """

    __tablename__ = "memory_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation: Mapped[str] = mapped_column(String(24), default="CREATE", index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    memory_type: Mapped[str] = mapped_column(String(64), default="semantic_memory")
    target_memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    proposed_scope_type: Mapped[str] = mapped_column(String(24), default="workspace")
    proposed_scope_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    goal_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    branch_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    structured_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    suggested_decay_policy: Mapped[str] = mapped_column(String(40), default="SLOW")
    conflicts_with: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(80), default="user")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    result_memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MemoryRevision(Base, TimestampMixin, WorkspaceScopedMixin):
    """Provider-neutral immutable history for one LearnGraph memory ID."""

    __tablename__ = "memory_revisions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "memory_id", "revision", name="uq_memory_revision"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("memory_records.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    base_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operation: Mapped[str] = mapped_column(String(32), default="ADD")
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    namespace: Mapped[str] = mapped_column(String(40), default="workspace")
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    record_kind: Mapped[str] = mapped_column(String(64), default="semantic_memory")
    zone: Mapped[str] = mapped_column(String(24), default="topics")
    source: Mapped[str] = mapped_column(String(120), default="user")
    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    actor_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class MemoryJournalEntry(Base, TimestampMixin, WorkspaceScopedMixin):
    """Durable mutation payload independent from any active provider UUID."""

    __tablename__ = "memory_journal_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(32), index=True)
    provider_id: Mapped[str] = mapped_column(String(80))
    provider_epoch: Mapped[int] = mapped_column(Integer, default=1)
    provider_record_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tombstone: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    recoverable_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    audit_retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    content_scrubbed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MemoryProviderBinding(Base, TimestampMixin, WorkspaceScopedMixin):
    """Rebuildable mapping from an LG memory revision to a provider record."""

    __tablename__ = "memory_provider_bindings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider_instance_id",
            "memory_id",
            "revision",
            name="uq_memory_provider_binding_revision",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_instance_id: Mapped[str] = mapped_column(String(80), index=True)
    memory_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    provider_record_id: Mapped[str] = mapped_column(String(160), index=True)
    provider_entity_kind: Mapped[str] = mapped_column(String(32))
    provider_entity_value: Mapped[str] = mapped_column(String(160))
    source_content_hash: Mapped[str] = mapped_column(String(64))
    target_readback_hash: Mapped[str] = mapped_column(String(64))
    import_event_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    binding_status: Mapped[str] = mapped_column(String(32), default="verified")
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(Text, default="")


class MemoryDeletionRecovery(Base, TimestampMixin, WorkspaceScopedMixin):
    """Encrypted, time-limited deletion payload whose key lives outside SQLite."""

    __tablename__ = "memory_deletion_recoveries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "memory_id", name="uq_memory_recovery"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(String(64), index=True)
    encrypted_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_relative_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recoverable_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    audit_retention_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    destroyed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MemoryEmbedding(Base, TimestampMixin, WorkspaceScopedMixin):
    """Cached embedding vector for one memory under one embedding model.

    A projection, not a fact source: rows are rebuilt lazily whenever the
    memory content hash or the configured embedding model changes. Recall
    works without any row here (heuristic scoring only).
    """

    __tablename__ = "memory_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "memory_id", "model_key", name="uq_memory_embedding_model"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    memory_id: Mapped[str] = mapped_column(String(64), index=True)
    model_key: Mapped[str] = mapped_column(String(200), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    dim: Mapped[int] = mapped_column(Integer, default=0)
    vector: Mapped[list[float]] = mapped_column(JSON, default=list)


class MemoryExtractionState(Base, TimestampMixin, WorkspaceScopedMixin):
    """Per-session cursor for background memory extraction sweeps."""

    __tablename__ = "memory_extraction_states"
    __table_args__ = (
        UniqueConstraint("workspace_id", "session_id", name="uq_memory_extraction_session"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    last_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str] = mapped_column(String(40), default="never_run")
    last_error: Mapped[str] = mapped_column(String(500), default="")
    extracted_count: Mapped[int] = mapped_column(Integer, default=0)


class ProviderConfig(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("workspace_id", "display_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(160))
    provider_type: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    secret_fingerprint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_capability: Mapped[bool] = mapped_column(Boolean, default=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="unconfigured")


class SandboxSession(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "sandbox_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    backend_id: Mapped[str] = mapped_column(String(80), default="docker")
    backend_session_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    policy_revision: Mapped[str] = mapped_column(String(40), default="sandbox-policy-v1")
    runtime_kind: Mapped[str] = mapped_column(String(40), default="python-node", index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="CREATED", index=True)
    workspace_relative_path: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    resource_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    network_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    runtime_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    runtime_last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    workspace_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    cleanup_status: Mapped[str] = mapped_column(String(32), default="not_started", index=True)
    cleanup_error_class: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    active_command_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    command_generation: Mapped[int] = mapped_column(Integer, default=0)


class SandboxTask(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "sandbox_tasks"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "owner_user_id", "idempotency_key_hash", name="uq_sandbox_task_idempotency"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    sandbox_session_id: Mapped[str] = mapped_column(
        ForeignKey("sandbox_sessions.id", ondelete="CASCADE"), index=True
    )
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="RESTRICT"), index=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    output_format: Mapped[str] = mapped_column(String(64), default="metadata_json")
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    artifact_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_class: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class SandboxExecution(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "sandbox_executions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "task_id", "attempt_no", name="uq_sandbox_execution_attempt"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_session_id: Mapped[str] = mapped_column(
        ForeignKey("sandbox_sessions.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("sandbox_tasks.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    argv_digest: Mapped[str] = mapped_column(String(64))
    argv_redacted: Mapped[list[str]] = mapped_column(JSON, default=list)
    cwd_relative: Mapped[str] = mapped_column(String(255), default=".")
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(120), nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    resource_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stdout_summary: Mapped[str] = mapped_column(Text, default="")
    stderr_summary: Mapped[str] = mapped_column(Text, default="")
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)


class SandboxAgentCommand(Base, TimestampMixin, WorkspaceScopedMixin):
    """An auditable command requested by an Agent inside a sandbox Session.

    This intentionally is not a ``SandboxTask``: file parsing tasks only admit
    a fixed runner and a required uploaded FileRecord, while an Agent command
    operates on its isolated, transient workspace.  The actual argv is never
    stored verbatim because command arguments can accidentally contain
    credentials; the durable record keeps a digest and redacted projection.
    """

    __tablename__ = "sandbox_agent_commands"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "idempotency_key_hash",
            name="uq_sandbox_agent_command_idempotency",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    sandbox_session_id: Mapped[str] = mapped_column(
        ForeignKey("sandbox_sessions.id", ondelete="CASCADE"), index=True
    )
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    argv_digest: Mapped[str] = mapped_column(String(64))
    argv_redacted: Mapped[list[str]] = mapped_column(JSON, default=list)
    cwd_relative: Mapped[str] = mapped_column(String(255), default=".")
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    resource_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stdout_summary: Mapped[str] = mapped_column(Text, default="")
    stderr_summary: Mapped[str] = mapped_column(Text, default="")
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)


class ContentBlob(Base, TimestampMixin, WorkspaceScopedMixin):
    """Content-addressed bytes in the unified file zone (one physical copy per hash)."""

    __tablename__ = "content_blobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "sha256", name="uq_content_blob_workspace_sha256"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    object_key: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    ref_count: Mapped[int] = mapped_column(Integer, default=0)


class SubAppBundleValidation(Base, TimestampMixin, WorkspaceScopedMixin):
    """Immutable offline validation snapshot for a multi-file teaching application."""

    __tablename__ = "subapp_bundle_validations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    sandbox_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sandbox_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    output_root: Mapped[str] = mapped_column(String(255))
    entry_path: Mapped[str] = mapped_column(String(255))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest_sha256: Mapped[str] = mapped_column(String(64), index=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="passed", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SubAppBundle(Base, TimestampMixin, WorkspaceScopedMixin):
    """Durable immutable multi-file application bundle served only by preview gateway."""

    __tablename__ = "subapp_bundles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    sandbox_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sandbox_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    validation_id: Mapped[str] = mapped_column(
        ForeignKey("subapp_bundle_validations.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    entry_path: Mapped[str] = mapped_column(String(255))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest_sha256: Mapped[str] = mapped_column(String(64), index=True)
    # Optional bidirectional sub-application contract snapshotted from an Agent
    # ``sandbox_publish_web_app`` call. When present, the bundle is also linked to
    # a lightweight ``ComponentManifestVersion`` (``component_manifest_id``) so the
    # frontend can instantiate a T2.6 interactive session. Absent both fields the
    # bundle remains a static preview.
    interaction_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    component_manifest_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # App-level allowlist for event-driven Agent processing.
    agent_consent_allowlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    preferred_height: Mapped[int] = mapped_column(Integer, default=420)


class SubAppBundleFile(Base, TimestampMixin, WorkspaceScopedMixin):
    """One immutable normalized bundle path mapped to a content-addressed blob."""

    __tablename__ = "subapp_bundle_files"
    __table_args__ = (UniqueConstraint("bundle_id", "path", name="uq_subapp_bundle_file_path"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    bundle_id: Mapped[str] = mapped_column(
        ForeignKey("subapp_bundles.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(255))
    blob_sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(160))
    size_bytes: Mapped[int] = mapped_column(Integer)


class SubAppBundlePreviewGrant(Base, TimestampMixin, WorkspaceScopedMixin):
    """Short-lived opaque capability for one viewer to fetch one immutable bundle."""

    __tablename__ = "subapp_bundle_preview_grants"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_subapp_bundle_preview_grant_token"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    bundle_id: Mapped[str] = mapped_column(
        ForeignKey("subapp_bundles.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionWorkspaceEntry(Base, TimestampMixin, WorkspaceScopedMixin):
    """Logical path tree for one chat session; points at content blobs / files."""

    __tablename__ = "session_workspace_entries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "chat_session_id",
            "owner_user_id",
            "path",
            name="uq_session_workspace_path",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    sandbox_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sandbox_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    path: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="work", index=True)
    blob_sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_id: Mapped[str | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    source: Mapped[str] = mapped_column(String(40), default="agent")


class SandboxDestructiveGrant(Base, TimestampMixin, WorkspaceScopedMixin):
    """User-approved destructive action scoped to a chat sandbox workspace."""

    __tablename__ = "sandbox_destructive_grants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    sandbox_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sandbox_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(40), default="delete_path")
    path_prefix: Mapped[str] = mapped_column(String(255))
    command_intent_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    granted_by: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityGrant(Base, TimestampMixin, WorkspaceScopedMixin):
    """Generic capability grant for any action type and resource.

    Extends the delete-only SandboxDestructiveGrant pattern to arbitrary
    actions (host write, GitHub push, API egress, device capability, etc.).
    Old SandboxDestructiveGrant rows remain valid for their existing API.
    """

    __tablename__ = "capability_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    chat_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sandbox_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sandbox_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    command_intent_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    session_origin: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    single_use: Mapped[bool] = mapped_column(default=True)
    usage_limit: Mapped[int] = mapped_column(default=1)
    usage_count: Mapped[int] = mapped_column(default=0)
    granted_by: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Artifact(Base, TimestampMixin):
    """Tenant-visible logical collection of immutable published outputs."""

    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)


class ArtifactCard(Base, TimestampMixin, WorkspaceScopedMixin):
    """Index of interactive HTML cards emitted by the agent in chat sessions.

    One row per stable card identity (``card_id``); re-emitting the same card id
    refreshes the row so the artifacts page always shows the latest draft. The
    ``preview_snapshot`` keeps a copy of the render data at emit time so cards
    stay previewable even after the source message is pruned.
    """

    __tablename__ = "artifact_cards"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "card_id",
            name="uq_artifact_card_workspace_card",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    # Stable identity chosen by the agent; ``card_instance_id`` changes on every emit.
    card_id: Mapped[str] = mapped_column(String(64), index=True)
    card_instance_id: Mapped[str] = mapped_column(String(64), index=True)
    card_type: Mapped[str] = mapped_column(String(32), default="magic_card")
    # True when the card carries an agent round-trip runtime (react-sandbox-v1)
    # or declarative events; False for static inline HTML pages.
    interactive: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String(240), default="交互卡片")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    chat_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    part_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    preview_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArtifactVersion(Base, TimestampMixin):
    """Immutable snapshot of one FileRecord published as an artifact output."""

    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version", name="uq_artifact_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="RESTRICT"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(160))
    source_workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    source_chat_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    published_by: Mapped[str] = mapped_column(String(64))
    release_notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)


class ArtifactShareToken(Base, TimestampMixin):
    """Revocable, read-only token scoped to one immutable artifact version."""

    __tablename__ = "artifact_share_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_artifact_share_token_hash"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artifact_version_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_versions.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    token_prefix: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(120), default="")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    max_downloads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class ProviderSecret(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "provider_secrets"
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("provider_configs.id", ondelete="CASCADE"), primary_key=True
    )
    ciphertext: Mapped[str] = mapped_column(Text)
    algorithm: Mapped[str] = mapped_column(String(40), default="fernet_sha256_v1")
    key_provider: Mapped[str] = mapped_column(String(32), default="environment")
    key_version: Mapped[int] = mapped_column(Integer, default=1, index=True)
    secret_version: Mapped[int] = mapped_column(Integer, default=1)
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WorkspaceSecretReference(Base, TimestampMixin, WorkspaceScopedMixin):
    """Opaque workspace secret addressable by label but never readable."""

    __tablename__ = "workspace_secret_references"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "label",
            name="uq_workspace_secret_reference_label",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    label: Mapped[str] = mapped_column(String(120), index=True)
    purpose: Mapped[str] = mapped_column(String(80), default="provider_api_key")
    ciphertext: Mapped[str] = mapped_column(Text)
    algorithm: Mapped[str] = mapped_column(String(64))
    key_provider: Mapped[str] = mapped_column(String(32))
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    secret_masked: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)


class PriceVersion(Base, TimestampMixin, WorkspaceScopedMixin):
    """Immutable model/feature tariff version in USD."""

    __tablename__ = "price_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider_id",
            "model_id",
            "feature",
            "version",
            name="uq_price_version_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(String(80), default="*", index=True)
    model_id: Mapped[str] = mapped_column(String(160), default="*", index=True)
    feature: Mapped[str] = mapped_column(String(80), default="*", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    input_usd_per_million: Mapped[float] = mapped_column(Float, default=0.0)
    cached_input_usd_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    cache_write_usd_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_usd_per_million: Mapped[float] = mapped_column(Float, default=0.0)
    fixed_usd_per_call: Mapped[float] = mapped_column(Float, default=0.0)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(160), default="workspace_manual")
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ExchangeRateVersion(Base, TimestampMixin, WorkspaceScopedMixin):
    """Immutable exchange-rate version used to snapshot historical costs."""

    __tablename__ = "exchange_rate_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "base_currency",
            "quote_currency",
            "version",
            name="uq_exchange_rate_version_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    quote_currency: Mapped[str] = mapped_column(String(3), default="CNY")
    version: Mapped[int] = mapped_column(Integer, default=1)
    rate: Mapped[float] = mapped_column(Float)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(160), default="workspace_manual")


class BudgetPolicy(Base, TimestampMixin, WorkspaceScopedMixin):
    """Workspace budget scope; wildcards allow global and targeted policies."""

    __tablename__ = "budget_policies"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider_id",
            "model_id",
            "feature",
            "period",
            name="uq_budget_policy_scope",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    provider_id: Mapped[str] = mapped_column(String(80), default="*", index=True)
    model_id: Mapped[str] = mapped_column(String(160), default="*", index=True)
    feature: Mapped[str] = mapped_column(String(80), default="*", index=True)
    period: Mapped[str] = mapped_column(String(32), default="calendar_month_utc")
    # The soft/hard limit values are expressed in `limit_currency` (USD or CNY).
    # The "_cny" column name is retained for schema continuity; at evaluation
    # time a USD limit is converted to CNY by the current exchange rate.
    soft_limit_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_limit_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class BudgetAlert(Base, TimestampMixin, WorkspaceScopedMixin):
    """Durable soft-warning or hard-block decision for one budget period."""

    __tablename__ = "budget_alerts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "policy_id",
            "level",
            "period_start",
            name="uq_budget_alert_period",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("budget_policies.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    provider_id: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(160))
    feature: Mapped[str] = mapped_column(String(80))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    spent_cny: Mapped[float] = mapped_column(Float, default=0.0)
    projected_cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    limit_cny: Mapped[float] = mapped_column(Float)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UsageEvent(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "usage_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(160))
    feature: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    cost_status: Mapped[str] = mapped_column(String(32), default="unpriced")
    price_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("price_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    exchange_rate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("exchange_rate_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    input_usd_per_million: Mapped[float] = mapped_column(Float, default=0.0)
    cached_input_usd_per_million: Mapped[float] = mapped_column(Float, default=0.0)
    cache_write_usd_per_million: Mapped[float] = mapped_column(Float, default=0.0)
    price_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    output_usd_per_million: Mapped[float] = mapped_column(Float, default=0.0)
    fixed_usd_per_call: Mapped[float] = mapped_column(Float, default=0.0)
    usd_cny_rate: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)


class PluginRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "plugins"
    __table_args__ = (UniqueConstraint("workspace_id", "plugin_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plugin_key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(40))
    plugin_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="configured")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)


class ComponentManifestVersion(Base, TimestampMixin, WorkspaceScopedMixin):
    """Immutable, workspace-scoped snapshot of one component package manifest."""

    __tablename__ = "component_manifest_versions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "plugin_id",
            "version",
            name="uq_component_manifest_plugin_version",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plugin_id: Mapped[str] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), index=True
    )
    component_id: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(40))
    display_name: Mapped[str] = mapped_column(String(160))
    renderer: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(160))
    author: Mapped[str] = mapped_column(String(160), default="")
    package_hash: Mapped[str] = mapped_column(String(64))
    package_hash_status: Mapped[str] = mapped_column(String(40))
    signature_status: Mapped[str] = mapped_column(String(40))
    signature_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    compatible_learngraph: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    uninstall_behavior: Mapped[str] = mapped_column(String(80), default="retain_data")
    data_schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    event_schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    interaction_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    size_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    skill_triggers: Mapped[list[str]] = mapped_column(JSON, default=list)
    example_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_hash: Mapped[str] = mapped_column(String(64))
    permissions_hash: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64), index=True)
    issuer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    trusted_bundle_eligible: Mapped[bool] = mapped_column(Boolean, default=False)


class ComponentIssuer(Base, TimestampMixin, WorkspaceScopedMixin):
    """Registered publisher whose public keys may sign third-party component packages."""

    __tablename__ = "component_issuers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "issuer_key", name="uq_component_issuer_key"),
        UniqueConstraint("workspace_id", "key_id", name="uq_component_issuer_key_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    issuer_key: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    key_id: Mapped[str] = mapped_column(String(160), index=True)
    algorithm: Mapped[str] = mapped_column(String(40), default="ed25519")
    public_key_pem: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str] = mapped_column(String(240), default="")
    rotated_from_key_id: Mapped[str | None] = mapped_column(String(160), nullable=True)


class ComponentAuthorization(Base, TimestampMixin, WorkspaceScopedMixin):
    """Authorization history; upgrades supersede rather than mutate prior grants."""

    __tablename__ = "component_authorizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plugin_id: Mapped[str] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), index=True
    )
    manifest_version_id: Mapped[str] = mapped_column(
        ForeignKey("component_manifest_versions.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(40), default="current_workspace")
    status: Mapped[str] = mapped_column(String(32), default="authorized", index=True)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    permissions_hash: Mapped[str] = mapped_column(String(64))
    authorized_by: Mapped[str] = mapped_column(String(64))
    authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoke_reason: Mapped[str] = mapped_column(String(240), default="")


class ComponentCheckRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    """Truthful static, health, or render check result for one manifest version."""

    __tablename__ = "component_check_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plugin_id: Mapped[str] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), index=True
    )
    manifest_version_id: Mapped[str] = mapped_column(
        ForeignKey("component_manifest_versions.id", ondelete="CASCADE"), index=True
    )
    check_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    executor: Mapped[str] = mapped_column(String(80))
    runtime_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checked_by: Mapped[str] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class ComponentCapabilityToken(Base, TimestampMixin, WorkspaceScopedMixin):
    """Short-lived, audience-bound capability token for one trusted render.

    Issued server-side when a third-party component is fully eligible for the
    trusted renderer channel (registered active issuer, verified signature,
    package hash match, passed renderer health, active workspace authorization).
    The raw token secret is returned to the host exactly once inside the sealed
    envelope; only its SHA-256 hash is persisted. The token gates the bounded
    postMessage protocol: audience (component + workspace) must match, the token
    expires within seconds, and it is scoped to a single render (``data_sha256``
    binding). ``message_count`` enforces the per-render message rate cap.
    """

    __tablename__ = "component_capability_tokens"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "token_hash",
            name="uq_component_capability_token_hash",
        ),
        Index(
            "ix_component_capability_token_active",
            "workspace_id",
            "component_id",
            "status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plugin_id: Mapped[str] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), index=True
    )
    manifest_version_id: Mapped[str] = mapped_column(
        ForeignKey("component_manifest_versions.id", ondelete="CASCADE"), index=True
    )
    authorization_id: Mapped[str] = mapped_column(
        ForeignKey("component_authorizations.id", ondelete="CASCADE"), index=True
    )
    component_id: Mapped[str] = mapped_column(String(120), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    token_prefix: Mapped[str] = mapped_column(String(16))
    audience: Mapped[str] = mapped_column(String(255))
    protocol_version: Mapped[str] = mapped_column(String(16), default="1")
    data_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    single_use: Mapped[bool] = mapped_column(Boolean, default=True)
    max_messages: Mapped[int] = mapped_column(Integer, default=128)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    issued_by: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(240), default="")


class MigrationJob(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "migration_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_kind: Mapped[str] = mapped_column(String(80))
    target_kind: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="preflight")
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AuditEvent(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(40), default="success")
    trace_id: Mapped[str] = mapped_column(String(64), default=new_id)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class FetchAuthorizationRequest(Base, TimestampMixin, WorkspaceScopedMixin):
    """A user decision required before a single agent web-fetch call may run."""

    __tablename__ = "fetch_authorization_requests"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "chat_session_id",
            "tool_call_id",
            name="uq_fetch_authorization_tool_call",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chat_session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    tool_call_id: Mapped[str] = mapped_column(String(160))
    tool_name: Mapped[str] = mapped_column(String(80), default="fetch_web_page")
    requested_url: Mapped[str] = mapped_column(Text)
    hostname: Mapped[str] = mapped_column(String(253), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Serialized original 极速/思考 request (MessageCreateRequest + idempotency
    # key + resolved provider options) so a non-agent authorization pause can be
    # resumed server-side after the user approves.  Agent-mode challenges (the
    # model re-calls fetch_web_page itself) leave this empty.
    resume_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    # Assistant message the non-agent pause created; the resumed generation
    # updates this same message in place so the transcript keeps one bubble.
    assistant_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class UserWebFetchPolicy(Base, TimestampMixin, WorkspaceScopedMixin):
    """User-scoped web-fetch whitelist, unioned with the workspace policy.

    ``allow_always`` in the authorization card writes here instead of the
    workspace setting, so an ordinary user's choice affects only themselves and
    no ``workspace.manage`` permission is required.  The effective domains for a
    fetch decision are ``user allowed_domains ∪ workspace allowed_domains``.
    """

    __tablename__ = "user_web_fetch_policies"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_user_web_fetch_policy",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    allow_without_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkspaceSetting(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "workspace_settings"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(120))
    value: Mapped[Any] = mapped_column(JSON)


# --- Generic Agent egress approval queue (D2.1) ---------------------------
# Durable control-plane foundation for *generic* Agent outbound authorization,
# parallel to (and deliberately separate from) the web_fetch approval
# machinery. These tables never read/write ``web_fetch.policy``,
# ``UserWebFetchPolicy``, or the reviewed ``{workspace_id}.json`` policy files.
EGRESS_APPROVAL_CAPABILITY = "agent_egress"
EGRESS_APPROVAL_DEFAULT_TTL_SECONDS = 900  # pending deadline: 15 minutes
EGRESS_APPROVAL_MAX_TTL_SECONDS = 86400


class EgressAuthorizationRequest(Base, TimestampMixin, WorkspaceScopedMixin):
    """Async user approval before a generic Agent egress host may be used.

    Contract A: the only authorization resource is a canonical exact hostname
    (via ``normalize_hostname``). There is deliberately NO command/argv/prompt/
    URL-path field that participates in authorization; ``request_context`` is
    display/audit context only and must never be used for matching.

    Contract B: a decision only puts a host into the allowlist. It never writes
    an IP, CIDR, ``allow_private`` or classifier exception. The runtime egress
    proxy still resolves and re-classifies every CONNECT, so an approved host
    that later resolves to a private/loopback/metadata address is still denied.

    ``pending`` is a suspension, not a failure: a pending request never blocks
    or fails the caller, and becomes ``expired`` once ``expires_at`` passes.
    """

    __tablename__ = "egress_authorization_requests"
    __table_args__ = (
        # One *pending* request per workspace/host/source (SQLite partial unique
        # index). Terminal rows are allowed to repeat so a later deny ->
        # re-request cycle can start a fresh approval instead of being blocked.
        Index(
            "uq_egress_auth_request_pending",
            "workspace_id",
            "hostname",
            "dedupe_key",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
        Index(
            "ix_egress_auth_requests_workspace_status",
            "workspace_id",
            "status",
        ),
        Index(
            "ix_egress_auth_requests_workspace_requested_by",
            "workspace_id",
            "requested_by",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    hostname: Mapped[str] = mapped_column(String(253), index=True)
    capability: Mapped[str] = mapped_column(
        String(24), default=EGRESS_APPROVAL_CAPABILITY
    )
    requested_by: Mapped[str] = mapped_column(String(64), index=True)
    # Deliberately a plain string, not a foreign key: the chat-session lifecycle
    # is application-validated, and an approval must survive session cleanup.
    chat_session_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    # Display/audit context only (tool name, purpose summary, provenance). Not a
    # matching key and never used for authorization. (Contract A.)
    request_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    decision: Mapped[str | None] = mapped_column(String(24), nullable=True)
    allow_always: Mapped[bool] = mapped_column(Boolean, default=False)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    ttl_seconds: Mapped[int] = mapped_column(
        Integer, default=EGRESS_APPROVAL_DEFAULT_TTL_SECONDS
    )
    # Idempotency/source key: the chat session id (or an explicit caller token).
    # The partial unique index only constrains pending rows.
    dedupe_key: Mapped[str] = mapped_column(String(80), default="")
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Assistant message that carries the durable ``egress_authorization`` card
    # part; the decision endpoint rewrites that part to its terminal state so
    # the transcript stays consistent across reloads. Agent-mode challenges
    # (the model re-invokes the sandbox tool itself) may leave these empty.
    assistant_message_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    user_message_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    # Optional originating tool call id for audit / card correlation.
    tool_call_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Serialized Agent resume checkpoint (tool call, assistant message/version
    # ids, allowed domains) so a decision can resume the exact suspended tool
    # call server-side instead of asking the user to re-send. Agent-mode
    # challenges that rely on the model re-invoking the tool leave this empty.
    resume_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )


class HostAuthorizationGrant(Base, TimestampMixin, WorkspaceScopedMixin):
    """Persistent, auditable host-level allowlist row for one capability.

    One row is one exact canonical hostname granted to one subject
    (``workspace`` or ``user``) for one capability. Capability isolation means a
    ``web_fetch`` approval can never widen generic Agent egress and vice-versa
    (D2.1 namespace isolation). ``allow_always`` in the egress approval card
    upserts a ``subject_type='workspace'`` row here instead of writing to
    ``web_fetch.policy`` or ``UserWebFetchPolicy``.
    """

    __tablename__ = "host_authorization_grants"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "capability",
            "subject_type",
            "subject_id",
            "hostname",
            name="uq_host_authorization_grant",
        ),
        Index(
            "ix_host_authorization_grants_workspace_capability",
            "workspace_id",
            "capability",
            "subject_type",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capability: Mapped[str] = mapped_column(
        String(24), default=EGRESS_APPROVAL_CAPABILITY, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(16), default="workspace")
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    hostname: Mapped[str] = mapped_column(String(253), index=True)
    # v1 fixed shape, server-generated, kept for future extension and display.
    ports: Mapped[list[int]] = mapped_column(JSON, default=lambda: [443])
    protocols: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["https"])
    source_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    granted_by: Mapped[str] = mapped_column(String(64))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

class AdvisoryLock(Base, TimestampMixin):
    """Cross-process scheduler lease (B1-7).

    One row per named sweep; ``token`` identifies the owning process and
    ``expires_at`` bounds the lease so a crashed worker cannot block the sweep.
    """

    __tablename__ = "advisory_locks"
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
