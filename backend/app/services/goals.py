from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, distinct, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ActionItem,
    AnswerRecord,
    ChatSession,
    Evidence,
    Exercise,
    FileReference,
    Goal,
    Graph,
    GraphChangeSet,
    GraphEdge,
    GraphNode,
    GraphNodeMerge,
    GraphRevision,
    MasteryReviewJob,
    MasterySchedule,
    Project,
    Roadmap,
    SourceLink,
    utc_now,
)
from app.domain.schemas.goals import (
    CandidateGraphRequest,
    ClarificationQuestion,
    GoalClarifyRequest,
    GoalClarifyResponse,
    GoalConfirmRequest,
    GoalPlanningUpdate,
    GoalView,
    ModelGoalPlan,
    ModelGraphDraft,
    PublishGoalRequest,
    PublishGoalResponse,
)
from app.domain.schemas.files import FileReferenceCreate
from app.domain.schemas.workflow import DeleteImpact, ImpactItem
from app.providers.ports.model import ModelProviderPort
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    FileRepository,
    GoalRepository,
    GraphEdgeRepository,
    GraphNodeRepository,
    GraphRepository,
)
from app.services.file_references import FileReferenceService
from app.services.billing import BillingService


class GoalService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str, model_provider: ModelProviderPort) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.goals = GoalRepository(db, workspace_id)
        self.graphs = GraphRepository(db, workspace_id)
        self.files = FileRepository(db, workspace_id)
        self.nodes = GraphNodeRepository(db, workspace_id)
        self.edges = GraphEdgeRepository(db, workspace_id)
        self.file_references = FileReferenceService(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.model_provider = model_provider
        self.billing = BillingService(db, workspace_id, actor_id)

    def _ensure_model_provider_available(self) -> None:
        if getattr(self.model_provider, "available", True):
            return
        raise AppError(
            503,
            "model_provider_unavailable",
            getattr(
                self.model_provider,
                "reason",
                "No usable model provider is configured for this workspace",
            ),
            {"provider_id": self.model_provider.provider_id},
        )

    @staticmethod
    def _store_deadline_as_utc(value: datetime | None) -> datetime | None:
        """Preserve an offset deadline through SQLite's offset-less storage."""

        if value is None:
            return None
        if value.tzinfo is None:
            # Request schemas reject this branch; retain it for older direct
            # service callers without guessing a non-UTC local timezone.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _structured(self, prompt: str, schema_name: str, model_type):
        self._ensure_model_provider_available()
        errors: list[str] = []
        for attempt in range(1, 4):
            quote = self.billing.preflight_model_call(
                provider_id=self.model_provider.provider_id,
                model_id=getattr(self.model_provider, "model_id", "unknown"),
                feature=schema_name,
                estimated_input_tokens=max(1, (len(prompt) + 3) // 4),
                estimated_output_tokens=max(
                    0,
                    int(getattr(self.model_provider, "max_output_tokens", 0)),
                ),
                remote_capability=self.model_provider.remote_capability,
            )
            provider_returned = False
            result = None
            try:
                raw = self.model_provider.generate_json(prompt, schema_name, model_type.model_json_schema())
                provider_returned = True
                result = model_type.model_validate(raw)
            except Exception as exc:
                errors.append(type(exc).__name__)
            if provider_returned:
                usage = dict(getattr(self.model_provider, "last_usage", {}) or {})
                self.billing.record_usage(
                    quote,
                    input_tokens=int(usage.get("input_tokens") or 0),
                    output_tokens=int(usage.get("output_tokens") or 0),
                    cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
                    reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                    attempt=attempt,
                    usage_reported=bool(usage),
                )
                self.db.commit()
            if result is not None:
                return result
        raise AppError(502, "structured_generation_failed", "Model structured generation failed after 3 attempts", {"attempts": 3, "errors": errors})

    def list(self) -> list[Goal]:
        return list(self.db.scalars(self.goals.query().order_by(Goal.updated_at.desc())).all())

    def _deletion_scope(self, goal_id: str) -> dict[str, list[str]]:
        graph_ids = list(
            self.db.scalars(
                select(Graph.id).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.goal_id == goal_id,
                )
            )
        )
        node_ids = (
            list(
                self.db.scalars(
                    select(GraphNode.id).where(
                        GraphNode.workspace_id == self.workspace_id,
                        GraphNode.graph_id.in_(graph_ids),
                    )
                )
            )
            if graph_ids
            else []
        )
        roadmap_ids = list(
            self.db.scalars(
                select(Roadmap.id).where(
                    Roadmap.workspace_id == self.workspace_id,
                    Roadmap.goal_id == goal_id,
                )
            )
        )
        exercise_ids = (
            list(
                self.db.scalars(
                    select(Exercise.id).where(
                        Exercise.workspace_id == self.workspace_id,
                        Exercise.node_id.in_(node_ids),
                    )
                )
            )
            if node_ids
            else []
        )
        generated_action_ids = list(
            self.db.scalars(
                select(ActionItem.id).where(
                    ActionItem.workspace_id == self.workspace_id,
                    or_(
                        ActionItem.roadmap_id.in_(roadmap_ids),
                        and_(ActionItem.source == "roadmap", ActionItem.goal_id == goal_id),
                    ),
                )
            )
        )
        return {
            "graph_ids": graph_ids,
            "node_ids": node_ids,
            "roadmap_ids": roadmap_ids,
            "exercise_ids": exercise_ids,
            "generated_action_ids": generated_action_ids,
        }

    @staticmethod
    def _count(db: Session, model, *criteria) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*criteria)) or 0)

    def _goal_reference_conditions(self, goal_id: str, graph_ids: list[str], node_ids: list[str]):
        project_reference = or_(
            Project.primary_goal_id == goal_id,
            Project.primary_graph_id.in_(graph_ids),
        )
        session_reference = or_(
            ChatSession.goal_id == goal_id,
            ChatSession.graph_id.in_(graph_ids),
        )
        action_reference = or_(
            ActionItem.goal_id == goal_id,
            ActionItem.graph_id.in_(graph_ids),
            ActionItem.node_id.in_(node_ids),
        )
        return project_reference, session_reference, action_reference

    def goal_impact(self, goal_id: str) -> DeleteImpact:
        goal = self.goals.require(goal_id, "goal")
        scope = self._deletion_scope(goal.id)
        graph_ids = scope["graph_ids"]
        node_ids = scope["node_ids"]
        roadmap_ids = scope["roadmap_ids"]
        exercise_ids = scope["exercise_ids"]
        generated_action_ids = scope["generated_action_ids"]
        project_reference, session_reference, action_reference = self._goal_reference_conditions(
            goal.id, graph_ids, node_ids
        )
        source_link_condition = or_(
            and_(SourceLink.target_type == "goal", SourceLink.target_id == goal.id),
            and_(SourceLink.target_type == "graph", SourceLink.target_id.in_(graph_ids)),
            and_(SourceLink.target_type == "node", SourceLink.target_id.in_(node_ids)),
        )

        review_jobs = list(
            self.db.scalars(
                select(MasteryReviewJob).where(MasteryReviewJob.workspace_id == self.workspace_id)
            )
        )
        review_job_count = sum(
            1 for job in review_jobs if set(job.node_ids or []).intersection(node_ids)
        )
        referencing_goals = list(
            self.db.scalars(
                select(Goal).where(
                    Goal.workspace_id == self.workspace_id,
                    Goal.id != goal.id,
                )
            )
        )
        goal_constraint_count = sum(
            1
            for item in referencing_goals
            if set((item.constraints or {}).get("graph_context_ids") or []).intersection(graph_ids)
        )
        source_record_count = int(
            self.db.scalar(
                select(func.count(distinct(SourceLink.source_id))).where(
                    SourceLink.workspace_id == self.workspace_id,
                    source_link_condition,
                )
            )
            or 0
        )
        file_reference_condition = or_(
            and_(FileReference.target_type == "goal", FileReference.target_id == goal.id),
            and_(FileReference.target_type == "graph", FileReference.target_id.in_(graph_ids)),
            and_(FileReference.target_type == "node", FileReference.target_id.in_(node_ids)),
            and_(
                FileReference.target_type == "evidence",
                FileReference.target_id.in_(
                    select(Evidence.id).where(
                        Evidence.workspace_id == self.workspace_id,
                        Evidence.node_id.in_(node_ids),
                    )
                ),
            ),
            and_(
                FileReference.target_type == "source_link",
                FileReference.target_id.in_(
                    select(SourceLink.id).where(
                        SourceLink.workspace_id == self.workspace_id,
                        source_link_condition,
                    )
                ),
            ),
        )

        impacts = [
            ImpactItem(resource_type="graph", count=len(graph_ids), action="delete"),
            ImpactItem(
                resource_type="graph_node",
                count=len(node_ids),
                action="delete",
            ),
            ImpactItem(
                resource_type="graph_edge",
                count=self._count(
                    self.db,
                    GraphEdge,
                    GraphEdge.workspace_id == self.workspace_id,
                    GraphEdge.graph_id.in_(graph_ids),
                ),
                action="delete",
            ),
            ImpactItem(
                resource_type="graph_revision",
                count=self._count(
                    self.db,
                    GraphRevision,
                    GraphRevision.workspace_id == self.workspace_id,
                    GraphRevision.graph_id.in_(graph_ids),
                ),
                action="delete",
            ),
            ImpactItem(
                resource_type="graph_node_merge",
                count=self._count(
                    self.db,
                    GraphNodeMerge,
                    GraphNodeMerge.workspace_id == self.workspace_id,
                    or_(
                        GraphNodeMerge.source_node_id.in_(node_ids),
                        GraphNodeMerge.target_node_id.in_(node_ids),
                    ),
                ),
                action="delete",
            ),
            ImpactItem(
                resource_type="evidence",
                count=self._count(
                    self.db,
                    Evidence,
                    Evidence.workspace_id == self.workspace_id,
                    Evidence.node_id.in_(node_ids),
                ),
                action="delete",
            ),
            ImpactItem(
                resource_type="mastery_schedule",
                count=self._count(
                    self.db,
                    MasterySchedule,
                    MasterySchedule.workspace_id == self.workspace_id,
                    MasterySchedule.node_id.in_(node_ids),
                ),
                action="delete",
            ),
            ImpactItem(resource_type="exercise", count=len(exercise_ids), action="delete"),
            ImpactItem(
                resource_type="answer_record",
                count=self._count(
                    self.db,
                    AnswerRecord,
                    AnswerRecord.workspace_id == self.workspace_id,
                    AnswerRecord.exercise_id.in_(exercise_ids),
                ),
                action="delete",
            ),
            ImpactItem(
                resource_type="roadmap",
                count=len(roadmap_ids),
                action="delete",
            ),
            ImpactItem(
                resource_type="roadmap_action",
                count=len(generated_action_ids),
                action="delete",
            ),
            ImpactItem(
                resource_type="source_link",
                count=self._count(
                    self.db,
                    SourceLink,
                    SourceLink.workspace_id == self.workspace_id,
                    source_link_condition,
                ),
                action="delete",
            ),
            ImpactItem(
                resource_type="graph_change_set",
                count=self._count(
                    self.db,
                    GraphChangeSet,
                    GraphChangeSet.workspace_id == self.workspace_id,
                    GraphChangeSet.goal_id == goal.id,
                ),
                action="delete",
            ),
            ImpactItem(
                resource_type="project",
                count=self._count(
                    self.db,
                    Project,
                    Project.workspace_id == self.workspace_id,
                    project_reference,
                ),
                action="detach",
            ),
            ImpactItem(
                resource_type="chat_session",
                count=self._count(
                    self.db,
                    ChatSession,
                    ChatSession.workspace_id == self.workspace_id,
                    session_reference,
                ),
                action="detach",
            ),
            ImpactItem(
                resource_type="action_item",
                count=self._count(
                    self.db,
                    ActionItem,
                    ActionItem.workspace_id == self.workspace_id,
                    action_reference,
                    ActionItem.id.not_in(generated_action_ids),
                ),
                action="detach",
            ),
            ImpactItem(
                resource_type="mastery_review_job",
                count=review_job_count,
                action="detach",
            ),
            ImpactItem(
                resource_type="goal_constraint",
                count=goal_constraint_count,
                action="detach",
            ),
            ImpactItem(
                resource_type="source_record",
                count=source_record_count,
                action="preserve",
            ),
            ImpactItem(
                resource_type="file_reference",
                count=self._count(
                    self.db,
                    FileReference,
                    FileReference.workspace_id == self.workspace_id,
                    file_reference_condition,
                ),
                action="delete",
            ),
        ]
        return DeleteImpact(
            resource_type="goal",
            resource_id=goal.id,
            title=goal.title,
            impacts=impacts,
            confirmation_text=goal.title,
        )

    def delete_goal(self, goal_id: str, confirmation: str) -> None:
        impact = self.goal_impact(goal_id)
        if confirmation != impact.confirmation_text:
            raise AppError(
                409,
                "confirmation_mismatch",
                "Confirmation text does not match the goal title",
            )

        scope = self._deletion_scope(goal_id)
        graph_ids = scope["graph_ids"]
        node_ids = scope["node_ids"]
        roadmap_ids = scope["roadmap_ids"]
        exercise_ids = scope["exercise_ids"]
        generated_action_ids = scope["generated_action_ids"]
        graph_id_set = set(graph_ids)
        node_id_set = set(node_ids)
        project_reference, session_reference, action_reference = self._goal_reference_conditions(
            goal_id, graph_ids, node_ids
        )
        source_link_condition = or_(
            and_(SourceLink.target_type == "goal", SourceLink.target_id == goal_id),
            and_(SourceLink.target_type == "graph", SourceLink.target_id.in_(graph_ids)),
            and_(SourceLink.target_type == "node", SourceLink.target_id.in_(node_ids)),
        )
        evidence_ids = list(
            self.db.scalars(
                select(Evidence.id).where(
                    Evidence.workspace_id == self.workspace_id,
                    Evidence.node_id.in_(node_ids),
                )
            )
        )
        source_link_ids = list(
            self.db.scalars(
                select(SourceLink.id).where(
                    SourceLink.workspace_id == self.workspace_id,
                    source_link_condition,
                )
            )
        )
        self.db.execute(
            delete(FileReference).where(
                FileReference.workspace_id == self.workspace_id,
                or_(
                    and_(FileReference.target_type == "goal", FileReference.target_id == goal_id),
                    and_(FileReference.target_type == "graph", FileReference.target_id.in_(graph_ids)),
                    and_(FileReference.target_type == "node", FileReference.target_id.in_(node_ids)),
                    and_(FileReference.target_type == "evidence", FileReference.target_id.in_(evidence_ids)),
                    and_(FileReference.target_type == "source_link", FileReference.target_id.in_(source_link_ids)),
                ),
            )
        )

        # JSON references are not protected by foreign keys, so remove only the
        # deleted node/graph identifiers and preserve every unrelated entry.
        for job in self.db.scalars(
            select(MasteryReviewJob).where(MasteryReviewJob.workspace_id == self.workspace_id)
        ):
            filtered_node_ids = [node_id for node_id in (job.node_ids or []) if node_id not in node_id_set]
            if filtered_node_ids != (job.node_ids or []):
                job.node_ids = filtered_node_ids
                report = dict(job.report or {})
                for key in ("marked_due_node_ids", "awarded_node_ids"):
                    if isinstance(report.get(key), list):
                        report[key] = [node_id for node_id in report[key] if node_id not in node_id_set]
                job.report = report
        for item in self.db.scalars(
            select(Goal).where(
                Goal.workspace_id == self.workspace_id,
                Goal.id != goal_id,
            )
        ):
            constraints = dict(item.constraints or {})
            context_ids = list(constraints.get("graph_context_ids") or [])
            filtered_context_ids = [graph_id for graph_id in context_ids if graph_id not in graph_id_set]
            if filtered_context_ids != context_ids:
                constraints["graph_context_ids"] = filtered_context_ids
                item.constraints = constraints

        projects = list(
            self.db.scalars(
                select(Project).where(
                    Project.workspace_id == self.workspace_id,
                    project_reference,
                )
            )
        )
        for project in projects:
            if project.primary_goal_id == goal_id:
                project.primary_goal_id = None
            if project.primary_graph_id in graph_id_set:
                project.primary_graph_id = None

        sessions = list(
            self.db.scalars(
                select(ChatSession).where(
                    ChatSession.workspace_id == self.workspace_id,
                    session_reference,
                )
            )
        )
        for session in sessions:
            if session.goal_id == goal_id:
                session.goal_id = None
            if session.graph_id in graph_id_set:
                session.graph_id = None

        preserved_actions = list(
            self.db.scalars(
                select(ActionItem).where(
                    ActionItem.workspace_id == self.workspace_id,
                    action_reference,
                    ActionItem.id.not_in(generated_action_ids),
                )
            )
        )
        for action in preserved_actions:
            if action.goal_id == goal_id:
                action.goal_id = None
            if action.graph_id in graph_id_set:
                action.graph_id = None
            if action.node_id in node_id_set:
                action.node_id = None

        if generated_action_ids:
            self.db.execute(
                delete(ActionItem).where(
                    ActionItem.workspace_id == self.workspace_id,
                    ActionItem.id.in_(generated_action_ids),
                )
            )
        self.db.execute(
            delete(SourceLink).where(
                SourceLink.workspace_id == self.workspace_id,
                source_link_condition,
            )
        )
        if exercise_ids:
            self.db.execute(
                delete(AnswerRecord).where(
                    AnswerRecord.workspace_id == self.workspace_id,
                    AnswerRecord.exercise_id.in_(exercise_ids),
                )
            )
            self.db.execute(
                delete(Exercise).where(
                    Exercise.workspace_id == self.workspace_id,
                    Exercise.id.in_(exercise_ids),
                )
            )
        if node_ids:
            self.db.execute(
                delete(Evidence).where(
                    Evidence.workspace_id == self.workspace_id,
                    Evidence.node_id.in_(node_ids),
                )
            )
            self.db.execute(
                delete(MasterySchedule).where(
                    MasterySchedule.workspace_id == self.workspace_id,
                    MasterySchedule.node_id.in_(node_ids),
                )
            )
            self.db.execute(
                delete(GraphNodeMerge).where(
                    GraphNodeMerge.workspace_id == self.workspace_id,
                    or_(
                        GraphNodeMerge.source_node_id.in_(node_ids),
                        GraphNodeMerge.target_node_id.in_(node_ids),
                    ),
                )
            )
        if graph_ids:
            self.db.execute(
                delete(GraphEdge).where(
                    GraphEdge.workspace_id == self.workspace_id,
                    GraphEdge.graph_id.in_(graph_ids),
                )
            )
            self.db.execute(
                delete(GraphRevision).where(
                    GraphRevision.workspace_id == self.workspace_id,
                    GraphRevision.graph_id.in_(graph_ids),
                )
            )
        if roadmap_ids:
            self.db.execute(
                delete(Roadmap).where(
                    Roadmap.workspace_id == self.workspace_id,
                    Roadmap.id.in_(roadmap_ids),
                )
            )
        if node_ids:
            self.db.execute(
                delete(GraphNode).where(
                    GraphNode.workspace_id == self.workspace_id,
                    GraphNode.id.in_(node_ids),
                )
            )
        if graph_ids:
            self.db.execute(
                delete(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.id.in_(graph_ids),
                )
            )
        self.db.execute(
            delete(Goal).where(
                Goal.workspace_id == self.workspace_id,
                Goal.id == goal_id,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="goal.delete",
            resource_type="goal",
            resource_id=goal_id,
            details={"impacts": [item.model_dump() for item in impact.impacts]},
        )
        self.db.commit()

    def clarify(self, payload: GoalClarifyRequest) -> GoalClarifyResponse:
        self._ensure_model_provider_available()
        for file_id in dict.fromkeys(payload.file_ids):
            self.files.require(file_id, "referenced file")
        for graph_id in dict.fromkeys(payload.graph_context_ids):
            self.graphs.require(graph_id, "referenced graph")

        plan = None
        if self.model_provider.remote_capability:
            plan = self._structured(
                "为 LearnGraph 目标生成动态澄清问题。"
                "title 必须是干净的学科/主题短语（如「数据库原理与应用」「离散数学」），"
                "聚焦知识本体，不得输出「学习xxx」「xxx学习计划」「xxx路径规划」等过程套话。"
                "只询问会改变图谱边界、深度或验收方式的信息；每题必须允许跳过和自定义，不得询问密码或 API Key。\n"
                "用户目标：" + payload.prompt,
                "learngraph_goal_plan",
                ModelGoalPlan,
            )
        title = plan.title if plan else self._graph_title_from_goal(
            payload.prompt.strip().split("。", 1)[0][:80],
            None,
        )
        questions = plan.questions if plan else [
            ClarificationQuestion(
                key="intent",
                prompt="主要学习场景是什么？",
                options=["考试", "项目", "面试", "自定义"],
            ),
            ClarificationQuestion(
                key="time_limit",
                prompt="每天可投入多长时间？",
                options=["1 小时", "2-3 小时", "5 小时以上"],
            ),
            ClarificationQuestion(
                key="desired_outcome",
                prompt="希望通过什么方式验收？",
                options=["选择题", "简答题", "项目产出"],
            ),
        ]
        goal = self.goals.add(
            Goal(
                workspace_id=self.workspace_id,
                title=title,
                raw_prompt=payload.prompt,
                status="clarifying",
                constraints={
                    "file_ids": payload.file_ids,
                    "graph_context_ids": payload.graph_context_ids,
                    "clarification_questions": [
                        question.model_dump() for question in questions
                    ],
                },
            )
        )
        for file_id in dict.fromkeys(payload.file_ids):
            self.file_references.add(
                file_id,
                FileReferenceCreate(
                    target_type="goal",
                    target_id=goal.id,
                    relation="goal_material",
                ),
            )
        self.audit.record(actor_id=self.actor_id, action="goal.clarify", resource_type="goal", resource_id=goal.id)
        self.db.commit()
        self.db.refresh(goal)
        return GoalClarifyResponse(
            goal=GoalView.model_validate(goal),
            questions=questions,
            provider=self.model_provider.provider_id,
            remote_model_used=self.model_provider.remote_capability,
        )

    def confirm(self, goal_id: str, payload: GoalConfirmRequest) -> Goal:
        goal = self.goals.require(goal_id, "goal")
        if goal.status == "approved":
            raise AppError(409, "goal_already_published", "Published goals cannot be silently rewritten")
        values = payload.model_dump(exclude_unset=True)
        for nested_field in ("availability", "preferences"):
            if nested_field in values:
                values[nested_field] = {
                    **dict(getattr(goal, nested_field) or {}),
                    **dict(values[nested_field]),
                }
        for field, value in values.items():
            if field == "deadline_at":
                value = self._store_deadline_as_utc(value)
            setattr(goal, field, value)
        goal.status = "confirmed"
        self.audit.record(
            actor_id=self.actor_id,
            action="goal.confirm",
            resource_type="goal",
            resource_id=goal.id,
            details={"fields": sorted(values)},
        )
        self.db.commit()
        self.db.refresh(goal)
        return goal

    def update_planning(self, goal_id: str, payload: GoalPlanningUpdate) -> Goal:
        """Update explicit planning facts without rewriting the approved Goal.

        A deadline, available time, or preferred study mode changes the next
        draft's ranking inputs.  It deliberately does not mutate an existing
        Roadmap: users must ask for a new version and review that version.
        """

        goal = self.goals.require(goal_id, "goal")
        values = payload.model_dump(exclude_unset=True)
        if not values:
            return goal
        for nested_field in ("availability", "preferences"):
            nested_patch = values.get(nested_field)
            if nested_patch is not None:
                values[nested_field] = {
                    **dict(getattr(goal, nested_field) or {}),
                    **nested_patch,
                }
        for field, value in values.items():
            if field == "deadline_at":
                value = self._store_deadline_as_utc(value)
            setattr(goal, field, value)
        self.audit.record(
            actor_id=self.actor_id,
            action="goal.planning_update",
            resource_type="goal",
            resource_id=goal.id,
            details={"fields": sorted(values)},
        )
        self.db.commit()
        self.db.refresh(goal)
        return goal

    def generate_candidate_graph(self, goal_id: str, payload: CandidateGraphRequest) -> Graph:
        self._ensure_model_provider_available()
        goal = self.goals.require(goal_id, "goal")
        if goal.status not in {"confirmed", "candidate_ready"}:
            raise AppError(409, "goal_not_confirmed", "Confirm the GoalDraft before graph generation")
        existing = self.db.scalar(
            self.graphs.query().where(Graph.goal_id == goal.id, Graph.status == "candidate")
        )
        if existing is not None:
            self._normalize_candidate_roots(existing)
            return existing
        concepts = [item.strip() for item in payload.seed_concepts if item.strip()]
        draft = None
        if self.model_provider.remote_capability:
            goal_context = json.dumps(
                {
                    "title": goal.title,
                    "intent": goal.intent,
                    "time_limit": goal.time_limit,
                    "desired_outcome": goal.desired_outcome,
                    "constraints": {
                        key: value
                        for key, value in (goal.constraints or {}).items()
                        if key not in {"file_ids", "graph_context_ids"}
                    },
                    "assumptions": goal.assumptions or [],
                },
                ensure_ascii=False,
                default=str,
            )
            draft = self._structured(
                "生成待用户审核的目标知识图谱。"
                "title 必须是干净的学科/主题短语（如「数据库原理与应用」「离散数学」「算法与数据结构」），"
                "聚焦知识本体，不得输出「学习xxx」「xxx学习图谱」「xxx知识图谱」这类模板套话，"
                "不得包含天数、速通、计划、路径规划等过程修饰。"
                "每个节点必须并发生成 teaching_strategy：针对该学科的讲解/教学策略，"
                "包括百科式定义切入、关键例子、常见误区与可验证的掌握标准，便于后续学习该节点时注入。"
                "节点索引从 0 开始；边只能引用本次 nodes；不得发布或声称用户已掌握。\n"
                + "已确认 Goal："
                + goal_context[:16_000]
                + "\n种子概念："
                + ", ".join(concepts),
                "learngraph_graph_draft",
                ModelGraphDraft,
            )
        if not concepts and draft is None:
            concepts = [goal.title, "基础概念", "核心机制", "实践练习", "综合验收"]
        graph_title = self._graph_title_from_goal(goal.title, draft.title if draft else None)
        graph = self.graphs.add(
            Graph(workspace_id=self.workspace_id, goal_id=goal.id, title=graph_title, status="candidate")
        )
        created_nodes: list[GraphNode] = []
        node_specs = draft.nodes if draft else None
        canonical_root_index = 0
        if node_specs:
            canonical_root_index = next(
                (
                    index
                    for index, node_spec in enumerate(node_specs)
                    if node_spec.node_type == "root"
                ),
                0,
            )
        for index, concept in enumerate(node_specs or dict.fromkeys(concepts)):
            label = concept.label if node_specs else concept
            teaching_strategy = (
                concept.teaching_strategy.strip()
                if node_specs and getattr(concept, "teaching_strategy", None)
                else (
                    f"以百科词条方式讲解「{label}」：先给出准确定义与边界，"
                    "再给出 1–2 个典型例子与常见误区，最后给出可自测的掌握标准。"
                )
            )
            created_nodes.append(
                self.nodes.add(
                    GraphNode(
                        workspace_id=self.workspace_id,
                        graph_id=graph.id,
                        label=label[:200],
                        description=concept.description if node_specs else "本地规则生成的候选节点，发布前必须审核。",
                        # Model output is advisory. A target graph must always
                        # have exactly one root, so promote the first node when
                        # none was supplied and demote any additional roots.
                        node_type=(
                            "root"
                            if index == canonical_root_index
                            else (
                                "concept"
                                if node_specs and concept.node_type == "root"
                                else concept.node_type
                            )
                            if node_specs
                            else "concept"
                        ),
                        target_weight=concept.target_weight if node_specs else 50,
                        teaching_strategy=teaching_strategy[:4_000],
                    )
                )
            )
        edge_specs = draft.edges if draft else [None] * max(0, len(created_nodes) - 1)
        for edge_index, edge_spec in enumerate(edge_specs):
            source_index = edge_spec.source_index if edge_spec else edge_index
            target_index = edge_spec.target_index if edge_spec else edge_index + 1
            if source_index >= len(created_nodes) or target_index >= len(created_nodes) or source_index == target_index:
                raise AppError(502, "graph_edge_out_of_scope", "Model graph edge references an invalid node index")
            self.edges.add(
                GraphEdge(
                    workspace_id=self.workspace_id,
                    graph_id=graph.id,
                    source_node_id=created_nodes[source_index].id,
                    target_node_id=created_nodes[target_index].id,
                    relation=edge_spec.relation if edge_spec else "prerequisite",
                )
            )
        for file_id in dict.fromkeys((goal.constraints or {}).get("file_ids") or []):
            self.file_references.add(
                file_id,
                FileReferenceCreate(
                    target_type="graph",
                    target_id=graph.id,
                    relation="generation_material",
                ),
            )
            for node in created_nodes:
                self.file_references.add(
                    file_id,
                    FileReferenceCreate(
                        target_type="node",
                        target_id=node.id,
                        relation="generation_material",
                    ),
                )
        goal.status = "candidate_ready"
        self.audit.record(
            actor_id=self.actor_id,
            action="graph.generate_candidate",
            resource_type="graph",
            resource_id=graph.id,
            details={"provider": self.model_provider.provider_id, "remote_model_used": self.model_provider.remote_capability},
        )
        self.db.commit()
        self.db.refresh(graph)
        return graph

    def _normalize_candidate_roots(self, graph: Graph) -> bool:
        """Repair model-created candidate graphs that do not have one root.

        Older candidates may already contain multiple roots because structured
        model output used to be persisted verbatim. Prefer the root that is not
        contained by another node, then the one with the broadest containment
        subtree. The repair is revisioned so an open reviewer is forced to
        reload instead of publishing a structure it has not seen.
        """

        if graph.status != "candidate":
            return False
        nodes = list(
            self.db.scalars(
                self.nodes.query().where(GraphNode.graph_id == graph.id)
            ).all()
        )
        if not nodes:
            return False
        roots = [node for node in nodes if node.node_type == "root"]
        if len(roots) == 1:
            return False
        edges = list(
            self.db.scalars(
                self.edges.query().where(
                    GraphEdge.graph_id == graph.id,
                    GraphEdge.relation == "contains",
                )
            ).all()
        )
        contained_ids = {edge.target_node_id for edge in edges}
        outgoing_counts: dict[str, int] = {}
        for edge in edges:
            outgoing_counts[edge.source_node_id] = (
                outgoing_counts.get(edge.source_node_id, 0) + 1
            )
        candidates = roots or nodes
        canonical = min(
            candidates,
            key=lambda node: (
                node.id in contained_ids,
                -outgoing_counts.get(node.id, 0),
                node.created_at,
                node.id,
            ),
        )
        changed_node_ids: list[str] = []
        for node in nodes:
            next_type = (
                "root"
                if node.id == canonical.id
                else "concept"
                if node.node_type == "root"
                else node.node_type
            )
            if node.node_type != next_type:
                node.node_type = next_type
                changed_node_ids.append(node.id)
        if not changed_node_ids:
            return False
        graph.revision += 1
        self.audit.record(
            actor_id=self.actor_id,
            action="graph.normalize_candidate_roots",
            resource_type="graph",
            resource_id=graph.id,
            details={
                "canonical_root_id": canonical.id,
                "changed_node_ids": sorted(changed_node_ids),
                "revision": graph.revision,
            },
        )
        self.db.commit()
        self.db.refresh(graph)
        return True

    @staticmethod
    def _graph_title_from_goal(goal_title: str, model_title: str | None) -> str:
        """Prefer a model subject phrase; fall back to a cleaned Goal title."""

        if model_title:
            cleaned = ModelGraphDraft.normalize_graph_title(model_title)
            if cleaned:
                return cleaned
        source = " ".join((goal_title or "").split())
        if not source:
            return "未命名主题"
        try:
            return ModelGraphDraft.normalize_graph_title(source)
        except ValueError:
            return source[:80] or "未命名主题"

    @staticmethod
    def _find_directed_cycle(adjacency: dict[str, set[str]]) -> list[str]:
        state: dict[str, int] = {}
        stack: list[str] = []
        stack_indexes: dict[str, int] = {}

        def visit(node_id: str) -> list[str]:
            state[node_id] = 1
            stack_indexes[node_id] = len(stack)
            stack.append(node_id)
            for target_id in sorted(adjacency.get(node_id, set())):
                target_state = state.get(target_id, 0)
                if target_state == 0:
                    cycle = visit(target_id)
                    if cycle:
                        return cycle
                elif target_state == 1:
                    return stack[stack_indexes[target_id] :] + [target_id]
            stack.pop()
            stack_indexes.pop(node_id, None)
            state[node_id] = 2
            return []

        for node_id in sorted(adjacency):
            if state.get(node_id, 0) == 0:
                cycle = visit(node_id)
                if cycle:
                    return cycle
        return []

    def _publishability_violations(
        self,
        graph: Graph,
    ) -> tuple[list[dict[str, Any]], int, int]:
        nodes = list(
            self.db.scalars(
                self.nodes.query().where(GraphNode.graph_id == graph.id)
            ).all()
        )
        edges = list(
            self.db.scalars(
                self.edges.query().where(GraphEdge.graph_id == graph.id)
            ).all()
        )
        node_ids = {node.id for node in nodes}
        violations: list[dict[str, Any]] = []
        allowed_node_types = {"root", "concept", "practice", "assessment"}
        allowed_relations = {
            "contains",
            "prerequisite",
            "related",
            "contrast",
            "application",
        }

        if len(nodes) < 2:
            violations.append(
                {
                    "code": "graph_node_count_invalid",
                    "minimum": 2,
                    "actual": len(nodes),
                }
            )
        root_ids = sorted(node.id for node in nodes if node.node_type == "root")
        if len(root_ids) != 1:
            violations.append(
                {
                    "code": "graph_root_count_invalid",
                    "expected": 1,
                    "actual": len(root_ids),
                    "root_node_ids": root_ids,
                }
            )
        invalid_node_ids = sorted(
            node.id for node in nodes if node.node_type not in allowed_node_types
        )
        if invalid_node_ids:
            violations.append(
                {
                    "code": "graph_node_type_invalid",
                    "node_ids": invalid_node_ids,
                }
            )

        valid_edges: list[GraphEdge] = []
        invalid_endpoints: list[dict[str, Any]] = []
        self_loop_edge_ids: list[str] = []
        invalid_relation_edge_ids: list[str] = []
        duplicate_edge_ids: list[str] = []
        edge_keys: dict[tuple[str, str, str], str] = {}
        for edge in edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                invalid_endpoints.append(
                    {
                        "edge_id": edge.id,
                        "invalid_endpoints": [
                            endpoint
                            for endpoint, node_id in (
                                ("source", edge.source_node_id),
                                ("target", edge.target_node_id),
                            )
                            if node_id not in node_ids
                        ],
                    }
                )
                continue
            if edge.source_node_id == edge.target_node_id:
                self_loop_edge_ids.append(edge.id)
                continue
            if edge.relation not in allowed_relations:
                invalid_relation_edge_ids.append(edge.id)
                continue
            key = (edge.source_node_id, edge.target_node_id, edge.relation)
            if key in edge_keys:
                duplicate_edge_ids.append(edge.id)
                continue
            edge_keys[key] = edge.id
            valid_edges.append(edge)
        if invalid_endpoints:
            violations.append(
                {
                    "code": "graph_edge_endpoint_invalid",
                    "edges": invalid_endpoints,
                }
            )
        if self_loop_edge_ids:
            violations.append(
                {
                    "code": "graph_edge_self_loop",
                    "edge_ids": sorted(self_loop_edge_ids),
                }
            )
        if invalid_relation_edge_ids:
            violations.append(
                {
                    "code": "graph_edge_relation_invalid",
                    "edge_ids": sorted(invalid_relation_edge_ids),
                }
            )
        if duplicate_edge_ids:
            violations.append(
                {
                    "code": "graph_edge_duplicate",
                    "edge_ids": sorted(duplicate_edge_ids),
                }
            )

        if len(root_ids) == 1:
            undirected: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
            for edge in valid_edges:
                undirected[edge.source_node_id].add(edge.target_node_id)
                undirected[edge.target_node_id].add(edge.source_node_id)
            visited: set[str] = set()
            pending = [root_ids[0]]
            while pending:
                node_id = pending.pop()
                if node_id in visited:
                    continue
                visited.add(node_id)
                pending.extend(sorted(undirected[node_id] - visited, reverse=True))
            disconnected_node_ids = sorted(node_ids - visited)
            if disconnected_node_ids:
                violations.append(
                    {
                        "code": "graph_disconnected",
                        "root_node_id": root_ids[0],
                        "node_ids": disconnected_node_ids,
                    }
                )

        structural_adjacency: dict[str, set[str]] = {
            node_id: set() for node_id in node_ids
        }
        for edge in valid_edges:
            if edge.relation in {"contains", "prerequisite"}:
                structural_adjacency[edge.source_node_id].add(edge.target_node_id)
        cycle = self._find_directed_cycle(structural_adjacency)
        if cycle:
            violations.append(
                {
                    "code": "graph_structural_cycle",
                    "relation_types": ["contains", "prerequisite"],
                    "node_ids": cycle,
                }
            )
        return violations, len(nodes), len(edges)

    def publish(self, goal_id: str, payload: PublishGoalRequest) -> PublishGoalResponse:
        goal = self.goals.require(goal_id, "goal")
        graph = self.graphs.require(payload.graph_id, "graph")
        if graph.goal_id != goal.id:
            raise AppError(
                409,
                "graph_goal_mismatch",
                "The reviewed graph does not belong to this Goal",
            )
        # Repair legacy model output before validating the review revision.
        # Because normalization increments the revision, a reviewer looking at
        # the old two-root graph will receive the normal conflict response and
        # must review the corrected graph once before publishing.
        self._normalize_candidate_roots(graph)
        if graph.revision != payload.expected_revision:
            raise AppError(
                409,
                "graph_revision_conflict",
                "The candidate graph changed after this review; reload before publishing",
                {
                    "expected_revision": payload.expected_revision,
                    "current_revision": graph.revision,
                },
            )
        if graph.status == "published":
            if goal.status != "approved":
                raise AppError(
                    409,
                    "published_graph_goal_state_conflict",
                    "Published Graph and Goal approval state are inconsistent",
                )
            return PublishGoalResponse(
                goal=GoalView.model_validate(goal),
                graph_id=graph.id,
                graph_revision=graph.revision,
                status=graph.status,
            )
        if graph.status != "candidate":
            raise AppError(
                409,
                "candidate_graph_missing",
                "Generate and review a candidate graph first",
            )
        if goal.status != "candidate_ready":
            raise AppError(
                409,
                "goal_not_ready_to_publish",
                "Confirm the Goal and review its candidate graph before publishing",
            )

        violations, node_count, edge_count = self._publishability_violations(graph)
        if violations:
            raise AppError(
                409,
                "graph_not_publishable",
                "The candidate graph failed structural validation",
                {
                    "graph_id": graph.id,
                    "revision": graph.revision,
                    "violations": violations,
                },
            )

        published_at = utc_now()
        result = self.db.execute(
            update(Graph)
            .where(
                Graph.workspace_id == self.workspace_id,
                Graph.id == graph.id,
                Graph.status == "candidate",
                Graph.revision == payload.expected_revision,
            )
            .values(status="published", published_at=published_at)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.db.rollback()
            current_graph = self.graphs.require(payload.graph_id, "graph")
            current_goal = self.goals.require(goal_id, "goal")
            if current_graph.revision != payload.expected_revision:
                raise AppError(
                    409,
                    "graph_revision_conflict",
                    "The candidate graph changed after this review; reload before publishing",
                    {
                        "expected_revision": payload.expected_revision,
                        "current_revision": current_graph.revision,
                    },
                )
            if current_graph.status == "published" and current_goal.status == "approved":
                return PublishGoalResponse(
                    goal=GoalView.model_validate(current_goal),
                    graph_id=current_graph.id,
                    graph_revision=current_graph.revision,
                    status=current_graph.status,
                )
            raise AppError(
                409,
                "candidate_graph_missing",
                "Generate and review a candidate graph first",
            )

        # Synchronize the identity-map instance with the conditional UPDATE so
        # the ORM flush cannot emit a second, unconditional Graph write.
        self.db.refresh(graph)
        goal.status = "approved"
        self.audit.record(
            actor_id=self.actor_id,
            action="graph.publish",
            resource_type="graph",
            resource_id=graph.id,
            details={
                "goal_id": goal.id,
                "revision": graph.revision,
                "node_count": node_count,
                "edge_count": edge_count,
                "structure_validated": True,
            },
        )
        self.db.commit()
        self.db.refresh(goal)
        self.db.refresh(graph)
        return PublishGoalResponse(
            goal=GoalView.model_validate(goal), graph_id=graph.id, graph_revision=graph.revision, status=graph.status
        )
