"""Durable, version-pinned learning packages; never synthesized chat messages."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.models import GraphNode, TimestampMixin, WorkspaceScopedMixin, new_id


class LearningPolicy(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "learning_package_policies"
    graph_id: Mapped[str] = mapped_column(ForeignKey("graphs.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    mode: Mapped[str] = mapped_column(String(20), default="path")
    image_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    actor_id: Mapped[str] = mapped_column(String(64))


class LearningEligibility(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "learning_package_eligibility"
    node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), primary_key=True)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graphs.id", ondelete="CASCADE"), index=True)
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)
    first_engaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_package_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class LearningBuild(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "learning_package_builds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(String(36), unique=True)
    trigger: Mapped[str] = mapped_column(String(20))
    epoch: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    stage: Mapped[int] = mapped_column(Integer, default=0)
    checkpoints: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LearningPackage(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "learning_packages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    build_id: Mapped[str] = mapped_column(String(36), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON)
    private_assessment: Mapped[dict] = mapped_column(JSON)
    private_activity: Mapped[dict] = mapped_column(JSON)


class LearningEnrollment(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "learning_package_enrollments"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", "package_id", name="uq_learning_enrollment"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("learning_packages.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)


class LearningAttempt(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "learning_package_attempts"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", "request_key", name="uq_learning_attempt_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("learning_packages.id", ondelete="CASCADE"))
    request_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    activity: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class LearningAchievement(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "learning_package_achievements"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", "node_id", name="uq_learning_achievement"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("graph_nodes.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[str] = mapped_column(String(36))
    score: Mapped[int] = mapped_column(Integer)


# Durable inquiry evidence is captured in the same transaction as the chat
# write. No timestamps/counters inferred from localStorage, and deleting chat
# never clears eligibility. Legacy nodes have no eligible epoch and are never
# swept.
#
# The eligibility guard row for a node is deliberately NOT created here. It must
# be opened only after the node row exists (see ``open_eligibility_guard``),
# because ``learning_package_eligibility`` references ``graph_nodes(id)`` and
# ``graphs(id)`` by foreign key while these mappers declare no relationship: the
# unit of work then orders them by ``mapper._sort_key``, i.e. by the *alphabetical
# order of ``module.ClassName``* -- a guard added in the same flush as its node is
# emitted first and rejected by SQLite (PRAGMA foreign_keys=ON). Node creation
# therefore goes through ``GraphNodeRepository.add``, which flushes the node and
# opens the guard explicitly instead of relying on ORM ordering.
from sqlalchemy import event, update
from sqlalchemy.orm import Session


def open_eligibility_guard(db: Session, node: GraphNode) -> LearningEligibility:
    """Create the eligibility guard row for a node whose row already exists.

    The caller MUST have flushed ``node`` first so that both foreign keys it
    points at (``graph_nodes``, ``graphs``) are present. The returned row is
    pending and is inserted by the next flush.
    """

    policy = db.get(LearningPolicy, node.graph_id) if node.graph_id else None
    guard = LearningEligibility(
        node_id=node.id,
        graph_id=node.graph_id,
        workspace_id=node.workspace_id,
        epoch=policy.epoch if policy and policy.enabled else 0,
    )
    db.add(guard)
    return guard


@event.listens_for(Session, "before_flush")
def capture_learning_lifecycle(db, _context, _instances):
    from app.domain.models import Evidence, MessagePartRecord
    pending = {row.node_id: row for row in db.new if isinstance(row, LearningEligibility)}
    for item in list(db.new):
        ids = []
        if isinstance(item, Evidence) and item.node_id:
            ids = [item.node_id]
        if isinstance(item, MessagePartRecord) and isinstance(item.data, dict) and item.data.get("tool_name") == "resolve_learning_context":
            ids = item.data.get("node_ids", [])
        elif isinstance(item, MessagePartRecord) and isinstance(item.data, dict):
            ids = item.data.get("learning_node_ids", [])
        if ids:
            from app.domain.models import utc_now
            for node_id in ids:
                if node_id in pending and pending[node_id].workspace_id == item.workspace_id:
                    pending[node_id].first_engaged_at = utc_now()
            db.execute(update(LearningEligibility).where(
                LearningEligibility.workspace_id == item.workspace_id,
                LearningEligibility.node_id.in_(ids),
                LearningEligibility.first_engaged_at.is_(None),
            ).values(first_engaged_at=utc_now(), version=LearningEligibility.version + 1))
