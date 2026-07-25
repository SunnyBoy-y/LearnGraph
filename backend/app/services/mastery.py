from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.models import (
    Evidence,
    GraphNode,
    MasteryMessageActivity,
    MasteryReviewJob,
    MasterySchedule,
    MasterySessionState,
    utc_now,
)
from app.domain.schemas.learning import MasterySchedulerTickView
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    EvidenceRepository,
    GraphNodeRepository,
    MasteryMessageActivityRepository,
    MasteryReviewJobRepository,
    MasteryScheduleRepository,
    MasterySessionStateRepository,
)


REVIEW_INTERVAL_DAYS = (1, 3, 7, 14, 30, 45, 60, 90)
SCHEDULER_TICK_LOCK = RLock()


class MasteryService:
    """Deterministic milestone and durable review scheduling boundary.

    The process-local timer is only a wake-up mechanism. Session activity,
    queue state, retry attempts, due timestamps, and completion reports live in
    SQLite, so a later process can resume queued or lease-expired work.
    """

    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings or get_settings()
        self.nodes = GraphNodeRepository(db, workspace_id)
        self.evidence = EvidenceRepository(db, workspace_id)
        self.schedules = MasteryScheduleRepository(db, workspace_id)
        self.message_activities = MasteryMessageActivityRepository(db, workspace_id)
        self.session_states = MasterySessionStateRepository(db, workspace_id)
        self.jobs = MasteryReviewJobRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def ensure_schedule(self, node: GraphNode) -> MasterySchedule:
        schedule = self.db.scalar(
            self.schedules.query().where(MasterySchedule.node_id == node.id)
        )
        if schedule is None:
            schedule = self.schedules.add(
                MasterySchedule(
                    workspace_id=self.workspace_id,
                    node_id=node.id,
                    next_review_at=None,
                )
            )
        return schedule

    def ensure_session_state(
        self,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> MasterySessionState:
        state = self.db.scalar(
            self.session_states.query().where(
                MasterySessionState.session_id == session_id
            )
        )
        if state is None:
            current = self._as_utc(now or utc_now())
            state = self.session_states.add(
                MasterySessionState(
                    workspace_id=self.workspace_id,
                    session_id=session_id,
                    last_activity_at=current,
                )
            )
        return state

    def apply_evidence(self, evidence: Evidence, node: GraphNode) -> bool:
        """Apply one accepted evidence item once; growth stars never decrease."""

        schedule = self.ensure_schedule(node)
        metadata = dict(evidence.metadata_json or {})
        if metadata.get("mastery_event_applied"):
            return False
        old_rank = max(0, min(15, int(node.mastery_stars)))
        awarded = False
        if evidence.status == "accepted":
            high_quality = (
                (evidence.source_type == "exercise" and evidence.confidence >= 0.8)
                or (
                    evidence.source_type in {"artifact", "user_correction"}
                    and evidence.confidence >= 0.85
                )
                or metadata.get("first_effective_inquiry") is True
            )
            candidate_rank = min(15, old_rank + 1) if high_quality else old_rank
            node.mastery_stars = max(old_rank, candidate_rank)
            awarded = node.mastery_stars > old_rank
            self._mark_success(node, schedule)
            metadata["mastery_event_applied"] = True
            metadata["mastery_awarded_star"] = awarded
        elif evidence.source_type == "exercise" and evidence.confidence < 0.5:
            node.mastery_stars = old_rank
            self._mark_conflict(node, schedule)
            metadata["mastery_event_applied"] = True
        else:
            node.mastery_stars = old_rank
        evidence.metadata_json = metadata
        return awarded

    def record_message(
        self,
        *,
        message_id: str,
        session_id: str,
        node_ids: Iterable[str],
        occurred_at: datetime | None = None,
    ) -> list[str]:
        """Persist activity and explicit-node inquiry evidence idempotently."""

        now = self._as_utc(occurred_at or utc_now())
        unique_node_ids = list(dict.fromkeys(node_ids))
        existing_activity = self.db.scalar(
            self.message_activities.query().where(
                MasteryMessageActivity.message_id == message_id
            )
        )
        if existing_activity is not None:
            return []
        state = self.ensure_session_state(session_id, now=now)
        state.pending_message_count += 1
        state.activity_version += 1
        self.message_activities.add(
            MasteryMessageActivity(
                workspace_id=self.workspace_id,
                message_id=message_id,
                session_id=session_id,
                activity_version=state.activity_version,
                node_ids=unique_node_ids,
                recorded_at=now,
            )
        )
        state.last_message_id = message_id
        state.last_activity_at = now
        state.idle_due_at = now + timedelta(
            seconds=max(1, self.settings.mastery_idle_seconds)
        )
        node_counts = dict(state.pending_node_counts or {})
        for node_id in unique_node_ids:
            node_counts[node_id] = int(node_counts.get(node_id, 0)) + 1
        state.pending_node_counts = node_counts
        state.pending_node_ids = list(node_counts)

        awarded_nodes: list[str] = []
        for node_id in unique_node_ids:
            node = self.nodes.require(node_id, "graph node")
            existing = self._evidence_for_message(message_id, node.id)
            if existing is not None:
                continue
            first_inquiry = node.mastery_stars == 0
            evidence = self.evidence.add(
                Evidence(
                    workspace_id=self.workspace_id,
                    node_id=node.id,
                    source_type="conversation",
                    summary="用户对该节点发起了有效学习询问。",
                    confidence=0.5,
                    status="accepted",
                    metadata_json={
                        "message_id": message_id,
                        "session_id": session_id,
                        "first_effective_inquiry": first_inquiry,
                        "attribution": "explicit_node_selection",
                    },
                )
            )
            schedule = self.ensure_schedule(node)
            schedule.pending_message_count += 1
            if self.apply_evidence(evidence, node):
                awarded_nodes.append(node.id)

        threshold = max(1, self.settings.mastery_message_threshold)
        if (
            state.pending_message_count >= threshold
            and state.enqueued_version <= state.processed_version
        ):
            self._enqueue_session_job(
                state,
                trigger="message_threshold",
                now=now,
            )
        return awarded_nodes

    def record_exercise_result(self, evidence: Evidence, node: GraphNode) -> bool:
        return self.apply_evidence(evidence, node)

    def run_review(
        self,
        *,
        trigger: str,
        node_ids: list[str] | None = None,
        now: datetime | None = None,
    ) -> MasteryReviewJob:
        """Run an explicit review through the same persisted job executor."""

        current = self._as_utc(now or utc_now())
        if node_ids:
            nodes = [
                self.nodes.require(node_id, "graph node")
                for node_id in dict.fromkeys(node_ids)
            ]
        else:
            nodes = self._due_nodes(current)
        job, _ = self._enqueue_job(
            trigger=trigger,
            node_ids=[node.id for node in nodes],
            report={"kind": "node_review", "requested_at": current.isoformat()},
            dedupe_key=None,
        )
        self.db.commit()
        self._execute_job(job.id, current)
        return self.jobs.require(job.id, "mastery review job")

    def run_session_review(
        self,
        *,
        session_id: str,
        trigger: str,
        node_ids: list[str] | None = None,
        now: datetime | None = None,
    ) -> MasteryReviewJob:
        """Immediately persist and execute an idle/close-equivalent session job."""

        current = self._as_utc(now or utc_now())
        state = self.ensure_session_state(session_id, now=current)
        if node_ids:
            counts = dict(state.pending_node_counts or {})
            for node_id in dict.fromkeys(node_ids):
                self.nodes.require(node_id, "graph node")
                counts.setdefault(node_id, 0)
            state.pending_node_counts = counts
            state.pending_node_ids = list(counts)
        job, _ = self._enqueue_session_job(
            state,
            trigger=trigger,
            now=current,
            force=True,
        )
        self.db.commit()
        self._execute_job(job.id, current)
        return self.jobs.require(job.id, "mastery review job")

    def scheduler_tick(
        self,
        *,
        now: datetime | None = None,
        execute: bool = True,
    ) -> MasterySchedulerTickView:
        with SCHEDULER_TICK_LOCK:
            return self._scheduler_tick(now=now, execute=execute)

    def _scheduler_tick(
        self,
        *,
        now: datetime | None = None,
        execute: bool = True,
    ) -> MasterySchedulerTickView:
        """Recover, enqueue, and optionally drain one workspace's durable work."""

        current = self._as_utc(now or utc_now())
        recovered_job_ids, exhausted_job_ids = self._recover_expired_jobs(current)
        enqueued_job_ids: list[str] = []
        threshold_session_ids: list[str] = []
        idle_session_ids: list[str] = []

        threshold = max(1, self.settings.mastery_message_threshold)
        threshold_states = list(
            self.db.scalars(
                self.session_states.query().where(
                    MasterySessionState.pending_message_count >= threshold,
                    MasterySessionState.enqueued_version
                    <= MasterySessionState.processed_version,
                )
            ).all()
        )
        threshold_state_ids: set[str] = set()
        for state in threshold_states:
            job, created = self._enqueue_session_job(
                state,
                trigger="message_threshold",
                now=current,
            )
            if created:
                enqueued_job_ids.append(job.id)
                threshold_session_ids.append(state.session_id)
                threshold_state_ids.add(state.id)

        idle_states = list(
            self.db.scalars(
                self.session_states.query().where(
                    MasterySessionState.pending_message_count > 0,
                    MasterySessionState.idle_due_at.is_not(None),
                    MasterySessionState.idle_due_at <= current,
                    MasterySessionState.enqueued_version
                    <= MasterySessionState.processed_version,
                )
            ).all()
        )
        for state in idle_states:
            if state.id in threshold_state_ids:
                continue
            job, created = self._enqueue_session_job(
                state,
                trigger="idle",
                now=current,
            )
            if created:
                enqueued_job_ids.append(job.id)
                idle_session_ids.append(state.session_id)

        due_nodes = self._due_nodes(current)
        due_node_ids = [node.id for node in due_nodes]
        if due_nodes:
            due_snapshot = self._due_snapshot(due_nodes)
            digest = hashlib.sha256(
                "\0".join(
                    f"{node_id}@{due_snapshot[node_id]}"
                    for node_id in sorted(due_snapshot)
                ).encode("utf-8")
            ).hexdigest()[:20]
            job, created = self._enqueue_job(
                trigger="periodic",
                node_ids=due_node_ids,
                report={
                    "kind": "due_schedule",
                    "due_snapshot": due_snapshot,
                    "requested_at": current.isoformat(),
                },
                dedupe_key=f"periodic:{digest}",
            )
            if created:
                enqueued_job_ids.append(job.id)

        self.db.commit()
        completed_job_ids: list[str] = []
        failed_job_ids: list[str] = list(exhausted_job_ids)
        if execute:
            queued_ids = list(
                self.db.scalars(
                    self.jobs.query()
                    .where(MasteryReviewJob.status == "queued")
                    .order_by(MasteryReviewJob.created_at, MasteryReviewJob.id)
                    .with_only_columns(MasteryReviewJob.id)
                ).all()
            )
            for job_id in queued_ids:
                status = self._execute_job(job_id, current)
                if status == "completed":
                    completed_job_ids.append(job_id)
                elif status == "failed":
                    failed_job_ids.append(job_id)

        return MasterySchedulerTickView(
            workspace_id=self.workspace_id,
            recovered_job_ids=recovered_job_ids,
            enqueued_job_ids=enqueued_job_ids,
            completed_job_ids=completed_job_ids,
            failed_job_ids=failed_job_ids,
            threshold_session_ids=threshold_session_ids,
            idle_session_ids=idle_session_ids,
            due_node_ids=due_node_ids,
        )

    def list_schedules(self) -> list[MasterySchedule]:
        return list(
            self.db.scalars(
                self.schedules.query().order_by(
                    MasterySchedule.next_review_at.is_(None),
                    MasterySchedule.next_review_at,
                )
            ).all()
        )

    def list_session_states(self) -> list[MasterySessionState]:
        return list(
            self.db.scalars(
                self.session_states.query().order_by(
                    MasterySessionState.idle_due_at.is_(None),
                    MasterySessionState.idle_due_at,
                )
            ).all()
        )

    def list_review_jobs(self) -> list[MasteryReviewJob]:
        return list(
            self.db.scalars(
                self.jobs.query()
                .order_by(MasteryReviewJob.created_at.desc())
                .limit(100)
            ).all()
        )

    def _enqueue_session_job(
        self,
        state: MasterySessionState,
        *,
        trigger: str,
        now: datetime,
        force: bool = False,
    ) -> tuple[MasteryReviewJob, bool]:
        version = state.activity_version
        dedupe_key = f"session:{state.session_id}:v{version}"
        node_counts = {
            key: int(value)
            for key, value in dict(state.pending_node_counts or {}).items()
        }
        activities = list(
            self.db.scalars(
                self.message_activities.query()
                .where(
                    MasteryMessageActivity.session_id == state.session_id,
                    MasteryMessageActivity.activity_version
                    > state.processed_version,
                    MasteryMessageActivity.activity_version <= version,
                )
                .order_by(MasteryMessageActivity.activity_version)
            ).all()
        )
        job, created = self._enqueue_job(
            trigger=trigger,
            node_ids=list(node_counts),
            report={
                "kind": "session_activity",
                "session_id": state.session_id,
                "activity_version": version,
                "pending_message_count": state.pending_message_count,
                "node_message_counts": node_counts,
                "message_ids": [activity.message_id for activity in activities],
                "last_message_id": state.last_message_id,
                "last_activity_at": self._as_utc(state.last_activity_at).isoformat(),
                "requested_at": now.isoformat(),
                "requested_triggers": [trigger],
                "analysis_deferred": not bool(node_counts),
            },
            dedupe_key=dedupe_key,
        )
        if not created:
            report = dict(job.report or {})
            requested_triggers = list(report.get("requested_triggers") or [job.trigger])
            if trigger not in requested_triggers:
                requested_triggers.append(trigger)
                report["requested_triggers"] = requested_triggers
                job.report = report
            if force and job.status == "queued":
                job.trigger = trigger
        if created or force:
            state.enqueued_version = max(state.enqueued_version, version)
        return job, created

    def _enqueue_job(
        self,
        *,
        trigger: str,
        node_ids: list[str],
        report: dict,
        dedupe_key: str | None,
    ) -> tuple[MasteryReviewJob, bool]:
        if dedupe_key:
            existing = self.db.scalar(
                self.jobs.query().where(MasteryReviewJob.dedupe_key == dedupe_key)
            )
            if existing is not None:
                return existing, False
        job = self.jobs.add(
            MasteryReviewJob(
                workspace_id=self.workspace_id,
                trigger=trigger,
                status="queued",
                dedupe_key=dedupe_key,
                node_ids=node_ids,
                report=report,
            )
        )
        self.db.flush()
        return job, True

    def _execute_job(self, job_id: str, now: datetime) -> str:
        job = self.jobs.require(job_id, "mastery review job")
        if job.status == "completed":
            return "completed"
        if job.status == "failed":
            return "failed"
        job.status = "running"
        job.attempt_count += 1
        job.started_at = now
        job.last_error = ""
        self.db.commit()

        try:
            job = self.jobs.require(job_id, "mastery review job")
            report = dict(job.report or {})
            marked_due, missing_nodes = self._mark_nodes_due(job.node_ids, now)
            if report.get("kind") == "session_activity":
                self._complete_session_cursor(report, now)
            report.update(
                {
                    "marked_due_node_ids": marked_due,
                    "missing_node_ids": missing_nodes,
                    "awarded_star_count": 0,
                    "policy": "mastery-v1",
                    "completed_at": now.isoformat(),
                }
            )
            job.report = report
            job.status = "completed"
            job.completed_at = now
            job.last_error = ""
            self.audit.record(
                actor_id=self.actor_id,
                action="mastery.review_sweep",
                resource_type="mastery_review_job",
                resource_id=job.id,
                details={
                    "trigger": job.trigger,
                    "marked_due_count": len(marked_due),
                    "attempt_count": job.attempt_count,
                },
            )
            self.db.commit()
            return "completed"
        except Exception as error:
            self.db.rollback()
            job = self.jobs.require(job_id, "mastery review job")
            max_attempts = max(1, self.settings.mastery_job_max_attempts)
            job.status = "queued" if job.attempt_count < max_attempts else "failed"
            job.started_at = None if job.status == "queued" else job.started_at
            job.last_error = f"{type(error).__name__}: scheduler execution failed"
            self.audit.record(
                actor_id=self.actor_id,
                action="mastery.review_sweep",
                resource_type="mastery_review_job",
                resource_id=job.id,
                outcome="failed",
                details={
                    "trigger": job.trigger,
                    "attempt_count": job.attempt_count,
                    "will_retry": job.status == "queued",
                },
            )
            self.db.commit()
            return job.status

    def _recover_expired_jobs(self, now: datetime) -> tuple[list[str], list[str]]:
        lease_cutoff = now - timedelta(
            seconds=max(1, self.settings.mastery_job_lease_seconds)
        )
        expired = list(
            self.db.scalars(
                self.jobs.query().where(
                    MasteryReviewJob.status == "running",
                    MasteryReviewJob.started_at.is_not(None),
                    MasteryReviewJob.started_at <= lease_cutoff,
                )
            ).all()
        )
        recovered: list[str] = []
        exhausted: list[str] = []
        for job in expired:
            if job.attempt_count >= max(1, self.settings.mastery_job_max_attempts):
                job.status = "failed"
                job.last_error = "Scheduler lease expired after maximum attempts"
                exhausted.append(job.id)
            else:
                job.status = "queued"
                job.started_at = None
                job.last_error = "Recovered after scheduler lease expiry"
                recovered.append(job.id)
        return recovered, exhausted

    def _complete_session_cursor(self, report: dict, now: datetime) -> None:
        session_id = str(report.get("session_id") or "")
        state = self.db.scalar(
            self.session_states.query().where(
                MasterySessionState.session_id == session_id
            )
        )
        if state is None:
            return
        processed_version = int(report.get("activity_version") or 0)
        processed_messages = int(report.get("pending_message_count") or 0)
        state.processed_version = max(state.processed_version, processed_version)
        state.pending_message_count = max(
            0,
            state.pending_message_count - processed_messages,
        )
        remaining_counts = dict(state.pending_node_counts or {})
        for node_id, count in dict(report.get("node_message_counts") or {}).items():
            remaining = int(remaining_counts.get(node_id, 0)) - int(count)
            if remaining > 0:
                remaining_counts[node_id] = remaining
            else:
                remaining_counts.pop(node_id, None)
            schedule = self.db.scalar(
                self.schedules.query().where(MasterySchedule.node_id == node_id)
            )
            if schedule is not None:
                schedule.pending_message_count = max(
                    0,
                    schedule.pending_message_count - int(count),
                )
        state.pending_node_counts = remaining_counts
        state.pending_node_ids = list(remaining_counts)
        state.last_processed_at = now
        state.enqueued_version = 0
        if state.pending_message_count == 0:
            state.idle_due_at = None
        else:
            state.idle_due_at = self._as_utc(state.last_activity_at) + timedelta(
                seconds=max(1, self.settings.mastery_idle_seconds)
            )

    def _mark_nodes_due(
        self,
        node_ids: list[str],
        now: datetime,
    ) -> tuple[list[str], list[str]]:
        unique_ids = list(dict.fromkeys(node_ids))
        if not unique_ids:
            return [], []
        nodes = list(
            self.db.scalars(
                self.nodes.query().where(GraphNode.id.in_(unique_ids))
            ).all()
        )
        by_id = {node.id: node for node in nodes}
        marked_due: list[str] = []
        for node_id in unique_ids:
            node = by_id.get(node_id)
            if node is None:
                continue
            schedule = self.db.scalar(
                self.schedules.query().where(MasterySchedule.node_id == node.id)
            )
            old_rank = node.mastery_stars
            if (
                schedule is not None
                and schedule.next_review_at is not None
                and self._as_utc(schedule.next_review_at) <= now
                and node.retrieval_state != "relearning"
            ):
                node.retrieval_state = "due"
                marked_due.append(node.id)
            node.mastery_stars = max(old_rank, node.mastery_stars)
        return marked_due, [node_id for node_id in unique_ids if node_id not in by_id]

    def _due_nodes(self, now: datetime) -> list[GraphNode]:
        schedules = list(
            self.db.scalars(
                self.schedules.query().where(
                    MasterySchedule.next_review_at.is_not(None),
                    MasterySchedule.next_review_at <= now,
                )
            ).all()
        )
        node_ids = [schedule.node_id for schedule in schedules]
        if not node_ids:
            return []
        nodes = list(
            self.db.scalars(
                self.nodes.query().where(GraphNode.id.in_(node_ids))
            ).all()
        )
        by_id = {node.id: node for node in nodes}
        return [by_id[node_id] for node_id in node_ids if node_id in by_id]

    def _due_snapshot(self, nodes: list[GraphNode]) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for node in nodes:
            schedule = self.db.scalar(
                self.schedules.query().where(MasterySchedule.node_id == node.id)
            )
            if schedule is not None and schedule.next_review_at is not None:
                snapshot[node.id] = self._as_utc(schedule.next_review_at).isoformat()
        return snapshot

    def _mark_success(self, node: GraphNode, schedule: MasterySchedule) -> None:
        now = utc_now()
        interval_index = min(
            max(node.mastery_stars - 1, 0),
            len(REVIEW_INTERVAL_DAYS) - 1,
        )
        node.retrieval_state = "fresh"
        accepted_count = len(
            list(
                self.db.scalars(
                    select(Evidence.id)
                    .where(
                        Evidence.workspace_id == self.workspace_id,
                        Evidence.node_id == node.id,
                        Evidence.status == "accepted",
                    )
                    .limit(3)
                ).all()
            )
        )
        node.evidence_state = "multi" if accepted_count >= 2 else "single"
        schedule.last_qualified_recall_at = now
        schedule.next_review_at = now + timedelta(
            days=REVIEW_INTERVAL_DAYS[interval_index]
        )

    @staticmethod
    def _mark_conflict(node: GraphNode, schedule: MasterySchedule) -> None:
        node.retrieval_state = "relearning"
        node.evidence_state = "conflicted"
        schedule.next_review_at = utc_now()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    def _evidence_for_message(
        self,
        message_id: str,
        node_id: str,
    ) -> Evidence | None:
        candidates = self.db.scalars(
            self.evidence.query().where(
                Evidence.node_id == node_id,
                Evidence.source_type == "conversation",
            )
        ).all()
        return next(
            (
                item
                for item in candidates
                if isinstance(item.metadata_json, dict)
                and item.metadata_json.get("message_id") == message_id
            ),
            None,
        )
