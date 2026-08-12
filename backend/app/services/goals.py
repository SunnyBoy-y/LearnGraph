from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

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
    AVAILABILITY_FIELDS,
    PREFERENCES_FIELDS,
    CandidateGraphRequest,
    CandidateGraphStreamRequest,
    ClarificationQuestion,
    GoalClarifyRequest,
    GoalClarifyResponse,
    GoalConfirmRequest,
    GoalPlanningUpdate,
    GoalView,
    ModelGoalPlan,
    ModelGraphChunk,
    ModelGraphChunkEdge,
    ModelGraphDraft,
    ModelGraphNode,
    ModelGraphRoot,
    PublishGoalRequest,
    PublishGoalResponse,
    sanitize_goal_nested_dict,
)
from app.domain.schemas.files import FileReferenceCreate
from app.domain.schemas.workflow import DeleteImpact, ImpactItem
from app.providers.ports.model import ModelProviderPort
from app.repositories.audit import AuditRepository

# 分支展开的并发度：主干各分支的模型调用彼此独立，用线程池并行以显著缩短
# 总等待时间（思考模式 5-6 个主干时约节省一半以上）。并发上限保持克制，
# 兼顾上游 API 限流与返回质量；需要时可整体调大。
_GRAPH_BRANCH_PARALLEL_WORKERS = 3
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
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        model_provider: ModelProviderPort,
        provider_factory: Callable[[], ModelProviderPort] | None = None,
    ) -> None:
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
        # Optional builder for per-worker provider clones used by the parallel
        # branch expansion. When omitted, _fork_model_provider reconstructs the
        # workspace provider from the request payload instead.
        self.provider_factory = provider_factory

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
            # Release preflight writes (catalog price seed / audit) BEFORE the
            # long generate_json call; a dirty session would hold the single
            # SQLite write lock across the whole remote call.
            self.db.commit()
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
                    cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
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
                # T1-3: tolerate a legacy/corrupt non-dict JSON cell (written as
                # a plain string) instead of failing the whole confirm with 500.
                current = getattr(goal, nested_field)
                if not isinstance(current, dict):
                    current = {}
                incoming = values[nested_field]
                if not isinstance(incoming, dict):
                    incoming = {}
                merged = {**current, **incoming}
                # Drop legacy unknown keys (e.g. weekly_hours) and null values
                # so strict GoalAvailability/GoalPreferences never reject a row
                # that predates the current schema.
                merged = sanitize_goal_nested_dict(
                    merged,
                    allowed_fields=(
                        AVAILABILITY_FIELDS
                        if nested_field == "availability"
                        else PREFERENCES_FIELDS
                    ),
                )
                values[nested_field] = merged
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
                merged = {
                    **dict(getattr(goal, nested_field) or {}),
                    **nested_patch,
                }
                values[nested_field] = sanitize_goal_nested_dict(
                    merged,
                    allowed_fields=(
                        AVAILABILITY_FIELDS
                        if nested_field == "availability"
                        else PREFERENCES_FIELDS
                    ),
                )
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

    # ------------------------------------------------------------------
    # Streaming candidate generation (root preview first, then updates)
    # ------------------------------------------------------------------

    @staticmethod
    def _node_snapshot(node: GraphNode) -> dict[str, Any]:
        return {
            "id": node.id,
            "label": node.label,
            "description": node.description,
            "node_type": node.node_type,
            "target_weight": node.target_weight,
            "teaching_strategy": (node.teaching_strategy or "")[:400],
        }

    @staticmethod
    def _edge_snapshot(edge: GraphEdge) -> dict[str, Any]:
        return {
            "id": edge.id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "relation": edge.relation,
        }

    def _graph_full_snapshot(self, graph: Graph) -> dict[str, Any]:
        nodes = list(
            self.db.scalars(
                self.nodes.query()
                .where(GraphNode.graph_id == graph.id)
                .order_by(GraphNode.created_at)
            ).all()
        )
        edges = list(
            self.db.scalars(
                self.edges.query().where(GraphEdge.graph_id == graph.id)
            ).all()
        )
        return {
            "graph_id": graph.id,
            "title": graph.title,
            "revision": graph.revision,
            "status": graph.status,
            "nodes": [self._node_snapshot(node) for node in nodes],
            "edges": [self._edge_snapshot(edge) for edge in edges],
        }

    def _goal_context_json(self, goal: Goal) -> str:
        return json.dumps(
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

    def _create_root_node(self, graph: Graph, spec: ModelGraphNode) -> GraphNode:
        teaching_strategy = (spec.teaching_strategy or "").strip() or (
            f"以百科词条方式讲解「{spec.label}」：先给出准确定义与边界，"
            "再给出 1–2 个典型例子与常见误区，最后给出可自测的掌握标准。"
        )
        return self.nodes.add(
            GraphNode(
                workspace_id=self.workspace_id,
                graph_id=graph.id,
                label=spec.label[:200],
                description=spec.description,
                node_type="root",
                target_weight=spec.target_weight,
                teaching_strategy=teaching_strategy[:4_000],
            )
        )

    @staticmethod
    def _filter_chunk_keep(chunk: ModelGraphChunk, keep: set[int]) -> ModelGraphChunk:
        """Keep only nodes whose original index is in ``keep``, remapping edges.

        Label dedup drops model-repeated nodes; without index remapping the
        remaining edges would point at the wrong (shifted) nodes or be dropped,
        silently corrupting the generated structure.
        """
        old_to_new: dict[int, int] = {}
        nodes: list[ModelGraphNode] = []
        for old_index, node in enumerate(chunk.nodes):
            if old_index in keep:
                old_to_new[old_index] = len(nodes)
                nodes.append(node)
        edges: list[ModelGraphChunkEdge] = []
        for edge in chunk.edges:
            new_source = (
                -1 if edge.source_index == -1 else old_to_new.get(edge.source_index)
            )
            new_target = (
                -1 if edge.target_index == -1 else old_to_new.get(edge.target_index)
            )
            if new_source is None or new_target is None:
                continue
            edges.append(
                edge.model_copy(
                    update={"source_index": new_source, "target_index": new_target}
                )
            )
        return chunk.model_copy(update={"nodes": nodes, "edges": edges})

    def _append_parent_chunk(
        self,
        graph: Graph,
        parent: GraphNode,
        chunk: ModelGraphChunk,
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Persist one chunk hung under ``parent`` (edge ``-1`` = this parent).

        ``layer=1`` nodes are direct children of ``parent`` and get an automatic
        ``contains`` edge; ``layer=2`` nodes are grandchildren attached to a
        layer-1 node through the chunk's own edges, or auto-attached to the
        first layer-1 node so nothing is ever persisted as an orphan. Any
        model-issued ``contains`` edge from the parent is normalized away
        (containment is derived from the layer field, not the model).
        """
        created: list[GraphNode] = []
        layer_by_index: dict[int, int] = {}
        for index, spec in enumerate(chunk.nodes):
            node_type = spec.node_type
            if node_type == "root":
                node_type = "concept"
            teaching_strategy = (spec.teaching_strategy or "").strip() or (
                f"以百科词条方式讲解「{spec.label}」：先给出准确定义与边界，"
                "再给出 1–2 个典型例子与常见误区，最后给出可自测的掌握标准。"
            )
            created.append(
                self.nodes.add(
                    GraphNode(
                        workspace_id=self.workspace_id,
                        graph_id=graph.id,
                        label=spec.label[:200],
                        description=spec.description,
                        node_type=node_type,
                        target_weight=spec.target_weight,
                        teaching_strategy=teaching_strategy[:4_000],
                    )
                )
            )
            layer_by_index[index] = max(1, min(2, spec.layer))
        node_by_index = {index: node for index, node in enumerate(created)}
        layer1_ids = {
            node.id for index, node in enumerate(created) if layer_by_index.get(index) == 1
        }
        first_layer1 = next(
            (
                node
                for index, node in enumerate(created)
                if layer_by_index.get(index) == 1
            ),
            created[0] if created else None,
        )
        added_edges: list[GraphEdge] = []
        for edge_spec in chunk.edges:
            source = (
                parent
                if edge_spec.source_index == -1
                else node_by_index.get(edge_spec.source_index)
            )
            target = (
                parent
                if edge_spec.target_index == -1
                else node_by_index.get(edge_spec.target_index)
            )
            if source is None or target is None or source.id == target.id:
                continue
            # Containment is derived from the layer field: skip any contains
            # edge issued from the parent (the guaranteed edges below win).
            if source.id == parent.id and edge_spec.relation == "contains":
                continue
            added_edges.append(
                self.edges.add(
                    GraphEdge(
                        workspace_id=self.workspace_id,
                        graph_id=graph.id,
                        source_node_id=source.id,
                        target_node_id=target.id,
                        relation=edge_spec.relation,
                    )
                )
            )
        for index, node in enumerate(created):
            if layer_by_index.get(index) == 1:
                added_edges.append(
                    self.edges.add(
                        GraphEdge(
                            workspace_id=self.workspace_id,
                            graph_id=graph.id,
                            source_node_id=parent.id,
                            target_node_id=node.id,
                            relation="contains",
                        )
                    )
                )
        # Layer-2 nodes with no chunk edge from a layer-1 node hang under the
        # first layer-1 node so no grandchild is persisted as an orphan.
        incoming_layer1 = {
            edge_spec.target_index
            for edge_spec in chunk.edges
            if layer_by_index.get(edge_spec.target_index) == 2
            and layer_by_index.get(edge_spec.source_index) == 1
        }
        if first_layer1 is not None:
            for index, node in enumerate(created):
                if layer_by_index.get(index) == 2 and index not in incoming_layer1:
                    added_edges.append(
                        self.edges.add(
                            GraphEdge(
                                workspace_id=self.workspace_id,
                                graph_id=graph.id,
                                source_node_id=first_layer1.id,
                                target_node_id=node.id,
                                relation="contains",
                            )
                        )
                    )
        return created, added_edges

    def _fork_model_provider(self, payload: CandidateGraphStreamRequest, mode: str):
        """Build an independent provider instance for one branch worker.

        Remote providers keep per-call metadata (``last_usage``,
        ``last_request_id``) on the instance, so concurrent branch calls must
        never share a single instance.  Prefer the injected ``provider_factory``
        (set by the router with the exact payload/mode the request used);
        otherwise rebuild from the same payload/mode, replicating the
        fast→thinking-off default.
        """
        if self.provider_factory is not None:
            return self.provider_factory()
        from app.core.config import get_settings
        from app.providers.factory import model_provider_for_workspace

        thinking_mode = payload.thinking_mode
        if thinking_mode is None and mode == "fast":
            thinking_mode = "off"
        return model_provider_for_workspace(
            self.db,
            self.workspace_id,
            get_settings(),
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            thinking_mode=thinking_mode,
        )

    @staticmethod
    def _branch_generate_job(
        provider: ModelProviderPort,
        prompt: str,
        schema: dict[str, Any],
        attempts: int = 3,
    ) -> tuple[ModelGraphChunk | None, dict[str, Any] | None, bool]:
        """Generate one branch expansion inside a worker thread.

        The provider instance is owned by this worker (``last_usage`` is
        per-instance), so parallel branches never race on usage metadata.
        Mirrors ``_structured``'s retry budget: returns ``(chunk, usage,
        provider_returned)`` where ``provider_returned`` is True whenever the
        provider answered, even when the payload failed validation.
        """
        errors: list[str] = []
        for _ in range(attempts):
            try:
                raw = provider.generate_json(
                    prompt, "learngraph_graph_chunk", schema
                )
                usage = dict(getattr(provider, "last_usage", {}) or {})
                return ModelGraphChunk.model_validate(raw), usage, True
            except Exception as exc:  # noqa: BLE001 -- same retry budget as _structured
                errors.append(type(exc).__name__)
        return None, None, False

    def _emit_branch_layers(
        self,
        emit: Any,
        trunk_node: GraphNode,
        branch_chunk: ModelGraphChunk,
        branch_nodes: list[GraphNode],
        branch_edges: list[GraphEdge],
    ) -> None:
        """Emit one branch as two SSE batches: layer-1 children, then layer-2.

        ``branch_nodes`` and ``branch_chunk.nodes`` share index order, so the
        layer split derives from the spec ``layer`` field (1 = direct child,
        2 = grandchild).  The tree therefore grows one level at a time even
        though both layers came from a single model call.
        """
        layer1_ids = {
            branch_nodes[i].id
            for i, spec in enumerate(branch_chunk.nodes)
            if spec.layer == 1
        }
        if not layer1_ids:
            layer1_ids = {branch_nodes[0].id} if branch_nodes else set()
        layer2_ids = {node.id for node in branch_nodes if node.id not in layer1_ids}
        layer1_edges = [
            edge
            for edge in branch_edges
            if (
                edge.source_node_id in layer1_ids
                and edge.target_node_id in layer1_ids
            )
            or (
                edge.source_node_id == trunk_node.id
                and edge.target_node_id in layer1_ids
            )
        ]
        layer1_edge_ids = {edge.id for edge in layer1_edges}
        layer2_edges = [
            edge for edge in branch_edges if edge.id not in layer1_edge_ids
        ]
        layer1_nodes = [node for node in branch_nodes if node.id in layer1_ids]
        layer2_nodes = [node for node in branch_nodes if node.id in layer2_ids]
        emit(
            "graph.nodes_added",
            {
                "nodes": [self._node_snapshot(node) for node in layer1_nodes],
                "edges": [self._edge_snapshot(edge) for edge in layer1_edges],
            },
        )
        if layer2_nodes or layer2_edges:
            emit(
                "graph.nodes_added",
                {
                    "nodes": [self._node_snapshot(node) for node in layer2_nodes],
                    "edges": [self._edge_snapshot(edge) for edge in layer2_edges],
                },
            )

    def stream_candidate_graph(
        self,
        goal_id: str,
        payload: CandidateGraphStreamRequest,
        emit: Any,
    ) -> Graph:
        """Generate a candidate graph trunk-first, emitting SSE events per stage.

        Stage ``root`` persists and emits the single root node immediately so
        the UI can render the root preview while the rest still generates.
        Stage ``nodes_added`` first carries the trunk (level-1 backbone under
        the root), then every trunk node expands concurrently (each branch in
        its own worker thread with a dedicated provider instance) and emits
        two incremental batches when it completes: its layer-1 children and
        its layer-2 grandchildren (two-layer expansion). Stage ``complete``
        carries the final full snapshot. ``mode`` selects the budget: ``fast``
        keeps thinking off with a compact trunk and narrow branches;
        ``thinking`` allows a fuller trunk and wider branches.
        """
        from typing import Callable

        _emit: Callable[[str, dict[str, Any]], None] = (
            emit if isinstance(emit, Callable) else (lambda _event, _data: None)
        )
        self._ensure_model_provider_available()
        goal = self.goals.require(goal_id, "goal")
        if goal.status not in {"confirmed", "candidate_ready"}:
            raise AppError(409, "goal_not_confirmed", "Confirm the GoalDraft before graph generation")
        existing = self.db.scalar(
            self.graphs.query().where(Graph.goal_id == goal.id, Graph.status == "candidate")
        )
        if existing is not None:
            # A previous (possibly streaming) run already created a candidate:
            # normalize and re-emit its full snapshot so the client converges.
            self._normalize_candidate_roots(existing)
            snapshot = self._graph_full_snapshot(existing)
            _emit("graph.root", {"graph_id": existing.id, "title": existing.title, "root": snapshot["nodes"][0] if snapshot["nodes"] else None})
            _emit("graph.nodes_added", {"nodes": snapshot["nodes"][1:], "edges": snapshot["edges"]})
            _emit("graph.complete", snapshot)
            return existing

        concepts = [item.strip() for item in payload.seed_concepts if item.strip()]
        remote = self.model_provider.remote_capability
        goal_context = self._goal_context_json(goal)
        mode = payload.mode or "thinking"

        # Stage 1 — root preview.
        root_spec: ModelGraphNode | None = None
        model_title: str | None = None
        if remote:
            try:
                root_draft = self._structured(
                    "为已确认的学习目标生成图谱根节点（root）。"
                    "title 必须是干净的学科/主题短语（如「数据库原理与应用」「离散数学」），"
                    "不得包含天数、速通、计划、路径规划等过程修饰。"
                    "root 是该学科的唯一顶层主题节点：label 简洁聚焦知识本体，"
                    "description 概括学科边界与学习范围，node_type 必须为 root，"
                    "并给出针对该学科的 teaching_strategy。\n"
                    "已确认 Goal：" + goal_context[:12_000]
                    + "\n种子概念：" + ", ".join(concepts),
                    "learngraph_graph_root",
                    ModelGraphRoot,
                )
                root_spec = root_draft.root
                model_title = root_draft.title
            except Exception:
                root_spec = None
                model_title = None
        if root_spec is None:
            root_label = " ".join((goal.title or "").split()) or "未命名主题"
            root_spec = ModelGraphNode(
                label=root_label[:200],
                description=f"「{root_label}」的完整学习目标：涵盖基础概念、核心机制、实践练习与综合验收。",
                node_type="root",
                target_weight=50,
                teaching_strategy="",
            )
        graph_title = self._graph_title_from_goal(goal.title, model_title)
        graph = self.graphs.add(
            Graph(workspace_id=self.workspace_id, goal_id=goal.id, title=graph_title, status="candidate")
        )
        root = self._create_root_node(graph, root_spec)
        self.db.commit()
        self.db.refresh(graph)
        _emit(
            "graph.root",
            {
                "graph_id": graph.id,
                "title": graph.title,
                "root": self._node_snapshot(root),
            },
        )

        # Stage 2 — 主干（trunk）：根节点下第一层主干模块，先整体成形再逐分支展开。
        # 每个批次边索引 -1 指向本批次的挂载父节点：主干批次挂 root，分支批次挂主干节点。
        fallback_children = ["基础概念", "核心机制", "实践练习", "综合验收"]
        trunk_min, trunk_max = (3, 4) if mode == "fast" else (5, 6)
        seen_labels: set[str] = set()
        trunk_chunk: ModelGraphChunk | None = None
        if remote:
            try:
                trunk_chunk = self._structured(
                    "为图谱生成「主干」（trunk）：根节点之下的第一层主干模块，"
                    "构成整张图谱的主干骨架，覆盖该学科的主要模块并按学习顺序排列。"
                    "node_type 使用 concept（知识点）、practice（练习）或 assessment（验收），layer 全部填 1。"
                    "edges 里 source_index=-1 表示根节点；主干模块之间的先后关系用 prerequisite。"
                    "本批次生成 "
                    + f"{trunk_min}-{trunk_max} 个"
                    + "左右主干节点，不要生成与根节点同名的节点。"
                    + "每个节点必须并发生成 teaching_strategy（百科式定义切入、关键例子、常见误区、可验证掌握标准）。"
                    + "\n图谱标题：" + graph_title
                    + "\n根节点：" + root.label + " — " + (root.description or "")[:300]
                    + "\n已确认 Goal：" + goal_context[:12_000]
                    + "\n种子概念：" + ", ".join(concepts),
                    "learngraph_graph_chunk",
                    ModelGraphChunk,
                )
            except Exception:
                trunk_chunk = None
        if trunk_chunk is None or not trunk_chunk.nodes:
            # 模型不可用或主干生成失败：退化为本地规则主干，保证图谱可审核。
            remaining = [label for label in fallback_children if label not in seen_labels]
            trunk_chunk = ModelGraphChunk(
                nodes=[
                    ModelGraphNode(
                        label=label,
                        description=f"「{label}」：围绕根节点组织的学习模块，发布前需审核。",
                        node_type="concept",
                        target_weight=50,
                        teaching_strategy="",
                    )
                    for label in remaining[:4]
                ],
                edges=[],
            )
        fresh_indexes = {
            index
            for index, node in enumerate(trunk_chunk.nodes)
            if node.label not in seen_labels and node.label != root.label
        }
        if not fresh_indexes:
            fresh_indexes = {0}
        for node in trunk_chunk.nodes:
            if node.label not in seen_labels and node.label != root.label:
                seen_labels.add(node.label)
        trunk_chunk = self._filter_chunk_keep(trunk_chunk, fresh_indexes)
        trunk_nodes, trunk_edges = self._append_parent_chunk(graph, root, trunk_chunk)
        self.db.commit()
        _emit(
            "graph.nodes_added",
            {
                "nodes": [self._node_snapshot(node) for node in trunk_nodes],
                "edges": [self._edge_snapshot(edge) for edge in trunk_edges],
            },
        )

        # Stage 3 — 各主干节点的两层分层展开：模型调用彼此独立且耗时最长，
        # 用线程池并发执行（每个 worker 持有独立的 provider 实例，避免
        # last_usage 竞态）；记账、标签去重、持久化与 SSE 事件全部回到主线程
        # 串行处理，SQLite 写锁只在主线程短暂持有。分支按完成顺序逐个发出，
        # 前端即可看到图谱逐分支、逐层「长出来」。
        expanded_branches = 0
        if remote and trunk_nodes:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            layer1_count, layer2_count = (
                (2, 1) if mode == "fast" else (3, 2)
            )
            chunk_schema = ModelGraphChunk.model_json_schema()
            # 先在主线程完成全部记账预检（可能产生少量写入），一次提交释放
            # SQLite 写锁，再并发发起模型调用。
            pending: list[tuple[GraphNode, Any, str]] = []
            for trunk_node in trunk_nodes:
                prompt = (
                    "为图谱主干节点生成「两层分层展开」（layer=1 + layer=2）。"
                    "layer=1：该主干节点下的直接子节点，"
                    + f"生成 {layer1_count}-{layer1_count + 1} 个；"
                    + "layer=2：挂在 layer=1 节点之下的孙节点，每个 layer=1 节点下"
                    + f" {layer2_count}-{layer2_count + 1} 个。"
                    + "每个节点的 layer 字段必须填写：直接子节点填 1，孙节点填 2。"
                    + "node_type 使用 concept（知识点）、practice（练习）或 assessment（验收）。"
                    + "edges：source_index=-1 表示挂载父节点（本主干节点）；"
                    + "layer=1→layer=2 用本批 nodes 索引（先列 layer=1 节点，再列 layer=2 节点）；"
                    + "本批次只属于这一个主干分支，不要重复已生成的标签；已生成的节点标签："
                    + ("、".join(sorted(seen_labels)) or "无")
                    + "\n主干节点：" + trunk_node.label + " — " + (trunk_node.description or "")[:300]
                    + "\n图谱标题：" + graph_title
                    + "\n根节点：" + root.label
                    + "\n已确认 Goal：" + goal_context[:12_000]
                    + "\n种子概念：" + ", ".join(concepts)
                )
                quote = self.billing.preflight_model_call(
                    provider_id=self.model_provider.provider_id,
                    model_id=getattr(self.model_provider, "model_id", "unknown"),
                    feature="learngraph_graph_chunk",
                    estimated_input_tokens=max(1, (len(prompt) + 3) // 4),
                    estimated_output_tokens=max(
                        0,
                        int(getattr(self.model_provider, "max_output_tokens", 0)),
                    ),
                    remote_capability=True,
                )
                pending.append((trunk_node, quote, prompt))
            self.db.commit()
            providers = [
                self._fork_model_provider(payload, mode)
                for _ in range(min(_GRAPH_BRANCH_PARALLEL_WORKERS, len(pending)))
            ]
            future_to_branch: dict[Any, tuple[GraphNode, Any]] = {}
            with ThreadPoolExecutor(
                max_workers=len(providers),
                thread_name_prefix="lg-graph-branch",
            ) as pool:
                for index, (trunk_node, quote, prompt) in enumerate(pending):
                    future = pool.submit(
                        self._branch_generate_job,
                        providers[index % len(providers)],
                        prompt,
                        chunk_schema,
                    )
                    future_to_branch[future] = (trunk_node, quote)
                for future in as_completed(future_to_branch):
                    trunk_node, quote = future_to_branch[future]
                    chunk: ModelGraphChunk | None = None
                    usage: dict[str, Any] = {}
                    provider_returned = False
                    try:
                        chunk, usage, provider_returned = future.result()
                    except BaseException:  # noqa: BLE001 -- one branch must not kill the stream
                        chunk, usage, provider_returned = None, {}, False
                    if provider_returned:
                        self.billing.record_usage(
                            quote,
                            input_tokens=int(usage.get("input_tokens") or 0),
                            output_tokens=int(usage.get("output_tokens") or 0),
                            cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
                            cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                            reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                            attempt=1,
                            usage_reported=bool(usage),
                        )
                        self.db.commit()
                    if chunk is None or not chunk.nodes:
                        continue
                    fresh_indexes = {
                        index
                        for index, node in enumerate(chunk.nodes)
                        if node.label not in seen_labels and node.label != root.label
                    }
                    if not fresh_indexes:
                        continue
                    for node in chunk.nodes:
                        if node.label not in seen_labels and node.label != root.label:
                            seen_labels.add(node.label)
                    branch_chunk = self._filter_chunk_keep(chunk, fresh_indexes)
                    branch_nodes, branch_edges = self._append_parent_chunk(
                        graph, trunk_node, branch_chunk
                    )
                    self.db.commit()
                    self._emit_branch_layers(
                        _emit, trunk_node, branch_chunk, branch_nodes, branch_edges
                    )
                    expanded_branches += 1

        self._normalize_candidate_roots(graph)
        goal.status = "candidate_ready"
        self.audit.record(
            actor_id=self.actor_id,
            action="graph.generate_candidate_stream",
            resource_type="graph",
            resource_id=graph.id,
            details={
                "provider": self.model_provider.provider_id,
                "remote_model_used": remote,
                "mode": mode,
                "trunk_nodes": len(trunk_nodes),
                "branches_expanded": expanded_branches,
            },
        )
        self.db.commit()
        self.db.refresh(graph)
        _emit("graph.complete", self._graph_full_snapshot(graph))
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

    def _bind_sessions_to_published_graph(self, graph: Graph) -> None:
        """Bind active creator sessions once their graph is published.

        A learning Session can only bind a published Graph, so the agent-mode
        (and chat) proposal flow never binds a candidate graph to its session.
        When the graph is published, sessions that confirmed a change set
        against it get the binding so Goal/Graph tooling can act on it. The
        WHERE clause is a no-op when the session is already bound elsewhere.
        """

        session_ids = set(
            self.db.scalars(
                select(GraphChangeSet.session_id).where(
                    GraphChangeSet.workspace_id == self.workspace_id,
                    GraphChangeSet.graph_id == graph.id,
                    GraphChangeSet.status == "confirmed",
                )
            ).all()
        )
        if not session_ids:
            return
        self.db.execute(
            update(ChatSession)
            .where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.id.in_(session_ids),
                ChatSession.goal_id == graph.goal_id,
                ChatSession.graph_id.is_(None),
                ChatSession.status == "active",
            )
            .values(graph_id=graph.id)
            .execution_options(synchronize_session=False)
        )

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
            self._bind_sessions_to_published_graph(graph)
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
        # The graph is now published: bind the sessions that confirmed a change
        # set against it (a learning Session can only bind a published Graph).
        self._bind_sessions_to_published_graph(graph)
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
