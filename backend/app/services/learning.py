from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.config import Settings, get_settings
from app.domain.models import (
    AnswerRecord,
    Evidence,
    Exercise,
    FileReference,
    Goal,
    Graph,
    GraphNode,
    new_id,
    utc_now,
)
from app.domain.schemas.files import DocumentQueryPreviewRequest, FileReferenceCreate
from app.domain.schemas.learning import (
    AnswerRequest,
    AnswerResult,
    CapabilityReportSummary,
    CapabilityReportView,
    EvidenceCreateRequest,
    EvidenceDecisionRequest,
    ExerciseBankItemView,
    ExerciseGenerateRequest,
    ExerciseView,
    MasteryAlignmentView,
    MasteryGoalOccurrenceView,
    MasteryNodeView,
    ModelGeneratedExerciseItem,
    ModelGeneratedExerciseSet,
)
from app.providers.ports.model import ModelProviderPort
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    AnswerRepository,
    EvidenceRepository,
    ExerciseRepository,
    GraphNodeRepository,
)
from app.services.billing import BillingService
from app.services.document_learning import DocumentLearningService
from app.services.file_references import FileReferenceService
from app.services.mastery import MasteryService

TRUE_FALSE_OPTIONS = ["正确", "错误"]
TRUE_FALSE_TRUE = frozenset({"true", "yes", "y", "1", "正确", "对", "是", "t"})
TRUE_FALSE_FALSE = frozenset({"false", "no", "n", "0", "错误", "错", "否", "f"})
ALL_ITEM_TYPES = (
    "single_choice",
    "multiple_choice",
    "true_false",
    "fill_blank",
    "short_answer",
)


class EvidenceService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.evidence = EvidenceRepository(db, workspace_id)
        self.nodes = GraphNodeRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.mastery_scheduler = MasteryService(db, workspace_id, actor_id)
        self.file_references = FileReferenceService(db, workspace_id)

    def list(self) -> list[Evidence]:
        return list(self.db.scalars(self.evidence.query().order_by(Evidence.created_at.desc())).all())

    def create(self, payload: EvidenceCreateRequest) -> Evidence:
        self.nodes.require(payload.node_id, "graph node")
        metadata = dict(payload.metadata)
        if payload.file_id is not None:
            metadata["file_id"] = payload.file_id
            if payload.locator:
                metadata["locator"] = payload.locator
        evidence = self.evidence.add(
            Evidence(
                workspace_id=self.workspace_id,
                node_id=payload.node_id,
                source_type=payload.source_type,
                summary=payload.summary,
                confidence=payload.confidence,
                status="pending",
                metadata_json=metadata,
            )
        )
        if payload.file_id is not None:
            self.file_references.add(
                payload.file_id,
                FileReferenceCreate(
                    target_type="evidence",
                    target_id=evidence.id,
                    relation="evidence_source",
                    locator=payload.locator,
                ),
            )
        self.audit.record(actor_id=self.actor_id, action="evidence.create", resource_type="evidence", resource_id=evidence.id)
        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    def decide(self, evidence_id: str, payload: EvidenceDecisionRequest) -> Evidence:
        evidence = self.evidence.require(evidence_id, "evidence")
        evidence.status = payload.decision
        evidence.metadata_json = {**evidence.metadata_json, "decision_reason": payload.reason}
        node = self.nodes.require(evidence.node_id, "graph node")
        if payload.decision == "accepted":
            self.mastery_scheduler.apply_evidence(evidence, node)
        elif node.evidence_state == "none":
            node.evidence_state = "unverified"
        self.audit.record(
            actor_id=self.actor_id,
            action=f"evidence.{payload.decision}",
            resource_type="evidence",
            resource_id=evidence.id,
            details={"reason": payload.reason},
        )
        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    def mastery(self) -> list[MasteryNodeView]:
        nodes = self.db.scalars(self.nodes.query().order_by(GraphNode.label)).all()
        counts = dict(
            self.db.execute(
                select(Evidence.node_id, func.count(Evidence.id))
                .where(Evidence.workspace_id == self.workspace_id, Evidence.status == "accepted")
                .group_by(Evidence.node_id)
            ).all()
        )
        attempt_rows = self.db.execute(
            select(
                Exercise.node_id,
                func.count(AnswerRecord.id),
                func.coalesce(
                    func.sum(case((AnswerRecord.is_correct.is_(True), 1), else_=0)),
                    0,
                ),
            )
            .select_from(AnswerRecord)
            .join(Exercise, Exercise.id == AnswerRecord.exercise_id)
            .where(
                AnswerRecord.workspace_id == self.workspace_id,
                Exercise.workspace_id == self.workspace_id,
            )
            .group_by(Exercise.node_id)
        ).all()
        attempt_map: dict[str, tuple[int, int]] = {}
        for node_id, attempts, correct_sum in attempt_rows:
            attempt_map[str(node_id)] = (int(attempts or 0), int(correct_sum or 0))
        schedules = {
            schedule.node_id: schedule
            for schedule in self.mastery_scheduler.list_schedules()
        }
        return [
            MasteryNodeView(
                node_id=node.id,
                label=node.label,
                mastery_stars=node.mastery_stars,
                retrieval_state=node.retrieval_state,
                evidence_state=node.evidence_state,
                attention_state=node.attention_state,
                accepted_evidence_count=int(counts.get(node.id, 0)),
                next_review_at=schedules.get(node.id).next_review_at if node.id in schedules else None,
                exercise_attempt_count=attempt_map.get(node.id, (0, 0))[0],
                exercise_correct_count=attempt_map.get(node.id, (0, 0))[1],
            )
            for node in nodes
        ]

    def mastery_alignment(self, node_id: str) -> MasteryAlignmentView:
        node = self.nodes.require(node_id, "graph node")
        concept_filter = (
            GraphNode.external_concept_id == node.external_concept_id
            if node.external_concept_id
            else GraphNode.id == node.id
        )
        rows = self.db.execute(
            select(GraphNode, Graph, Goal)
            .join(Graph, Graph.id == GraphNode.graph_id)
            .join(Goal, Goal.id == Graph.goal_id)
            .where(
                GraphNode.workspace_id == self.workspace_id,
                Graph.workspace_id == self.workspace_id,
                Goal.workspace_id == self.workspace_id,
                concept_filter,
            )
            .order_by(Goal.created_at, Graph.created_at, GraphNode.created_at)
        ).all()
        occurrences = [
            MasteryGoalOccurrenceView(
                goal_id=goal.id,
                goal_title=goal.title,
                graph_id=graph.id,
                graph_title=graph.title,
                graph_status=graph.status,
            )
            for _, graph, goal in rows
        ]
        goal_count = len({item.goal_id for item in occurrences})
        explanation = (
            f"“{node.label}”当前出现在 {goal_count} 个学习目标、"
            f"{len(occurrences)} 张图谱中；这里只说明事实关联，不会据此自动授予成长星级。"
            if occurrences
            else f"“{node.label}”当前没有可访问的目标图谱关联。"
        )
        return MasteryAlignmentView(
            node_id=node.id,
            label=node.label,
            external_concept_id=node.external_concept_id,
            occurrences=occurrences,
            explanation=explanation,
        )

    def capability_report(self) -> CapabilityReportView:
        nodes = self.mastery()
        now = utc_now()

        def is_due(value: datetime | None) -> bool:
            if value is None:
                return False
            comparison_time = now if value.tzinfo is not None else now.replace(tzinfo=None)
            return value <= comparison_time

        report = CapabilityReportView(
            workspace_id=self.workspace_id,
            generated_at=now,
            summary=CapabilityReportSummary(
                concept_count=len(nodes),
                accepted_evidence_count=sum(item.accepted_evidence_count for item in nodes),
                mastered_concept_count=sum(1 for item in nodes if item.mastery_stars > 0),
                review_due_count=sum(1 for item in nodes if is_due(item.next_review_at)),
                exercise_attempt_count=sum(item.exercise_attempt_count for item in nodes),
                exercise_correct_count=sum(item.exercise_correct_count for item in nodes),
            ),
            nodes=nodes,
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="mastery.capability_report_exported",
            resource_type="capability_report",
            resource_id=self.workspace_id,
            details={"concept_count": len(nodes)},
        )
        self.db.commit()
        return report


class ExerciseService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        model_provider: ModelProviderPort | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.exercises = ExerciseRepository(db, workspace_id)
        self.answers = AnswerRepository(db, workspace_id)
        self.evidence = EvidenceRepository(db, workspace_id)
        self.nodes = GraphNodeRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.mastery_scheduler = MasteryService(db, workspace_id, actor_id)
        self.model_provider = model_provider
        self.billing = BillingService(db, workspace_id, actor_id)
        self.settings = settings or get_settings()
        self.documents = DocumentLearningService(
            db, workspace_id, actor_id, self.settings
        )
        self.file_references = FileReferenceService(db, workspace_id)

    def list(
        self,
        *,
        wrong_only: bool = False,
        node_id: str | None = None,
        question_type: str | None = None,
        batch_id: str | None = None,
    ) -> list[ExerciseBankItemView]:
        query = self.exercises.query().order_by(Exercise.created_at.desc())
        if node_id:
            query = query.where(Exercise.node_id == node_id)
        if question_type:
            query = query.where(Exercise.question_type == question_type)
        if batch_id:
            query = query.where(Exercise.generation_batch_id == batch_id)
        if wrong_only:
            query = query.where(
                exists(
                    select(AnswerRecord.id).where(
                        AnswerRecord.workspace_id == self.workspace_id,
                        AnswerRecord.exercise_id == Exercise.id,
                        AnswerRecord.is_correct.is_(False),
                    )
                )
            )
        items = list(self.db.scalars(query).all())
        if not items:
            return []
        exercise_ids = [item.id for item in items]
        stats_rows = self.db.execute(
            select(
                AnswerRecord.exercise_id,
                func.count(AnswerRecord.id),
                func.coalesce(
                    func.sum(case((AnswerRecord.is_correct.is_(True), 1), else_=0)),
                    0,
                ),
            )
            .where(
                AnswerRecord.workspace_id == self.workspace_id,
                AnswerRecord.exercise_id.in_(exercise_ids),
            )
            .group_by(AnswerRecord.exercise_id)
        ).all()
        stats = {
            str(exercise_id): (int(attempts or 0), int(correct or 0))
            for exercise_id, attempts, correct in stats_rows
        }
        last_rows = self.db.execute(
            select(AnswerRecord.exercise_id, AnswerRecord.is_correct, AnswerRecord.created_at)
            .where(
                AnswerRecord.workspace_id == self.workspace_id,
                AnswerRecord.exercise_id.in_(exercise_ids),
            )
            .order_by(AnswerRecord.created_at.desc())
        ).all()
        last_map: dict[str, bool] = {}
        for exercise_id, is_correct, _created in last_rows:
            key = str(exercise_id)
            if key not in last_map:
                last_map[key] = bool(is_correct)
        views: list[ExerciseBankItemView] = []
        for item in items:
            base = ExerciseView.model_validate(item)
            attempt_count, correct_count = stats.get(item.id, (0, 0))
            views.append(
                ExerciseBankItemView(
                    **base.model_dump(),
                    attempt_count=attempt_count,
                    correct_count=correct_count,
                    last_is_correct=last_map.get(item.id),
                )
            )
        return views

    def _ensure_remote_model(self) -> ModelProviderPort:
        provider = self.model_provider
        if provider is None or not getattr(provider, "available", False):
            raise AppError(
                503,
                "remote_model_required",
                getattr(
                    provider,
                    "reason",
                    "Exercise generation requires a configured remote model provider",
                ),
                {
                    "provider_id": getattr(provider, "provider_id", "unavailable"),
                    "feature": "exercise_generate",
                },
            )
        if not getattr(provider, "remote_capability", False):
            raise AppError(
                503,
                "remote_model_required",
                "Exercise generation requires a remote model provider with structured JSON capability; local demo is not used",
                {
                    "provider_id": provider.provider_id,
                    "remote_capability": False,
                    "feature": "exercise_generate",
                },
            )
        return provider

    def _resolve_linked_file_ids(self, node: GraphNode) -> list[str]:
        graph = self.db.scalar(
            select(Graph).where(
                Graph.workspace_id == self.workspace_id,
                Graph.id == node.graph_id,
            )
        )
        target_clauses = [
            (FileReference.target_type == "node") & (FileReference.target_id == node.id),
            (FileReference.target_type == "graph") & (FileReference.target_id == node.graph_id),
        ]
        if graph is not None and graph.goal_id:
            target_clauses.append(
                (FileReference.target_type == "goal")
                & (FileReference.target_id == graph.goal_id)
            )
        rows = self.db.scalars(
            select(FileReference.file_id).where(
                FileReference.workspace_id == self.workspace_id,
                or_(*target_clauses),
            )
        ).all()
        return list(dict.fromkeys(str(item) for item in rows))

    def _resolve_source_file_ids(
        self,
        node: GraphNode,
        file_ids: list[str],
        collection_ids: list[str],
    ) -> tuple[list[str], str]:
        linked = self._resolve_linked_file_ids(node)
        explicit: list[str] = []
        if file_ids or collection_ids:
            explicit = self.documents.resolve_query_file_ids(file_ids, collection_ids)
        resolved = list(dict.fromkeys([*linked, *explicit]))
        if not resolved:
            return [], "node_only"
        if explicit:
            return resolved, "node_and_files"
        return resolved, "linked_files"

    def _grounding_snippets(
        self,
        node: GraphNode,
        file_ids: list[str],
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not file_ids:
            return [], None
        query = " ".join(
            part for part in [node.label, (node.description or "")[:240]] if part
        ).strip() or node.label
        preview = self.documents.preview(
            DocumentQueryPreviewRequest(
                query=query,
                file_ids=file_ids,
                scope="files",
                max_results=8,
            )
        )
        hits: list[dict[str, Any]] = []
        for hit in preview.hits:
            hits.append(
                {
                    "chunk_id": hit.chunk_id,
                    "file_id": hit.file_id,
                    "filename": hit.filename,
                    "locator": hit.locator,
                    "content_hash": hit.content_hash,
                    "quote": (hit.quote or "")[:800],
                }
            )
        return hits, preview.trace_id

    def _structured_generate(self, prompt: str) -> ModelGeneratedExerciseSet:
        provider = self._ensure_remote_model()
        errors: list[str] = []
        for attempt in range(1, 4):
            quote = self.billing.preflight_model_call(
                provider_id=provider.provider_id,
                model_id=getattr(provider, "model_id", "unknown"),
                feature="exercise_generate",
                estimated_input_tokens=max(1, (len(prompt) + 3) // 4),
                estimated_output_tokens=max(
                    0,
                    int(getattr(provider, "max_output_tokens", 0)),
                ),
                remote_capability=True,
            )
            provider_returned = False
            result: ModelGeneratedExerciseSet | None = None
            try:
                raw = provider.generate_json(
                    prompt,
                    "exercise_generate",
                    ModelGeneratedExerciseSet.model_json_schema(),
                )
                provider_returned = True
                result = ModelGeneratedExerciseSet.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)
            if provider_returned:
                usage = dict(getattr(provider, "last_usage", {}) or {})
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
        raise AppError(
            502,
            "structured_generation_failed",
            "Model structured generation failed after 3 attempts",
            {"attempts": 3, "errors": errors, "feature": "exercise_generate"},
        )

    def _build_generation_prompt(
        self,
        node: GraphNode,
        payload: ExerciseGenerateRequest,
        snippets: list[dict[str, Any]],
        grounding: str,
    ) -> str:
        type_hint = (
            f"生成 {payload.count} 道题，题型必须全部为 {payload.question_type}。"
            if payload.question_type != "mixed"
            else (
                f"生成恰好 {payload.count} 道题，题型从 "
                f"{', '.join(ALL_ITEM_TYPES)} 中合理混排，优先覆盖选择与判断。"
            )
        )
        materials = (
            "\n".join(
                f"- chunk_id={item['chunk_id']} file={item['filename']} locator={item['locator']}: "
                f"{item['quote']}"
                for item in snippets
            )
            if snippets
            else "（无检索到的资料片段；仅依据节点信息出题，不要编造外部事实。）"
        )
        return (
            "你是 LearnGraph 的习题命题器。请针对当前知识点设计自测题，输出符合 schema 的 JSON。\n"
            f"{type_hint}\n"
            f"难度：{payload.difficulty}。\n"
            "硬性约束：\n"
            "1. 题干必须可由节点描述或给定资料支撑，禁止编造资料未出现的专有事实。\n"
            "2. single_choice：options 4 项，answer_key 为其中一个选项原文。\n"
            "3. multiple_choice：options 4 项，answer_key 为正确选项原文数组（至少 2 项）。\n"
            "4. true_false：options 必须是 [\"正确\",\"错误\"]，answer_key 为其中之一。\n"
            "5. fill_blank：options 为空数组，answer_key 为标准填空答案字符串。\n"
            "6. short_answer：options 为空，answer_key 为参考要点摘要，rubric_points 为 2～5 条可判分要点。\n"
            "7. source_chunk_ids 只能引用下方资料中的 chunk_id；无资料时返回空数组。\n"
            "8. explanation 用中文给出简短讲解，不要包含未必要的标准答案抄写。\n"
            f"知识点：label={node.label}\n"
            f"描述：{(node.description or '')[:1200]}\n"
            f"教学策略：{(node.teaching_strategy or '')[:800]}\n"
            f"资料 grounding={grounding}：\n{materials}\n"
        )

    @staticmethod
    def _store_answer_key(item: ModelGeneratedExerciseItem) -> str:
        if item.question_type == "multiple_choice":
            if isinstance(item.answer_key, list):
                return json.dumps(item.answer_key, ensure_ascii=False)
            return json.dumps([item.answer_key], ensure_ascii=False)
        if isinstance(item.answer_key, list):
            return item.answer_key[0] if item.answer_key else ""
        return item.answer_key

    def _normalize_item(
        self,
        item: ModelGeneratedExerciseItem,
        snippets: list[dict[str, Any]],
    ) -> tuple[str, list[str], str, dict[str, Any], list[dict[str, Any]]]:
        qtype = item.question_type
        options = list(item.options)
        if qtype == "true_false":
            options = list(TRUE_FALSE_OPTIONS)
            key = self._normalize_true_false_value(self._store_answer_key(item))
            if key is None:
                raise AppError(
                    502,
                    "structured_generation_failed",
                    "Model returned an invalid true/false answer_key",
                )
            answer_key = key
        elif qtype == "single_choice":
            if len(options) < 2:
                raise AppError(
                    502,
                    "structured_generation_failed",
                    "Model single_choice requires at least 2 options",
                )
            answer_key = self._store_answer_key(item)
            if answer_key not in options:
                folded = {opt.casefold(): opt for opt in options}
                mapped = folded.get(answer_key.casefold())
                if mapped is None:
                    raise AppError(
                        502,
                        "structured_generation_failed",
                        "Model single_choice answer_key is not in options",
                    )
                answer_key = mapped
        elif qtype == "multiple_choice":
            if len(options) < 2:
                raise AppError(
                    502,
                    "structured_generation_failed",
                    "Model multiple_choice requires at least 2 options",
                )
            raw = item.answer_key if isinstance(item.answer_key, list) else [item.answer_key]
            folded = {opt.casefold(): opt for opt in options}
            resolved: list[str] = []
            for value in raw:
                mapped = folded.get(str(value).strip().casefold())
                if mapped is None:
                    raise AppError(
                        502,
                        "structured_generation_failed",
                        "Model multiple_choice answer_key is not in options",
                    )
                if mapped not in resolved:
                    resolved.append(mapped)
            if len(resolved) < 1:
                raise AppError(
                    502,
                    "structured_generation_failed",
                    "Model multiple_choice answer_key is empty",
                )
            answer_key = json.dumps(resolved, ensure_ascii=False)
        else:
            options = []
            answer_key = self._store_answer_key(item)
            if not answer_key.strip():
                raise AppError(
                    502,
                    "structured_generation_failed",
                    "Model returned an empty answer_key",
                )
        rubric = {
            "points": [point.strip() for point in item.rubric_points if point and point.strip()]
        }
        chunk_lookup = {snippet["chunk_id"]: snippet for snippet in snippets}
        source_refs: list[dict[str, Any]] = []
        for chunk_id in item.source_chunk_ids:
            hit = chunk_lookup.get(chunk_id)
            if hit is None:
                continue
            source_refs.append(
                {
                    "file_id": hit["file_id"],
                    "chunk_id": hit["chunk_id"],
                    "locator": hit.get("locator") or "",
                    "content_hash": hit.get("content_hash") or "",
                    "filename": hit.get("filename") or "",
                }
            )
        return qtype, options, answer_key, rubric, source_refs

    @staticmethod
    def _normalize_true_false_value(value: str) -> str | None:
        folded = value.strip().casefold()
        if folded in TRUE_FALSE_TRUE or value.strip() == "正确":
            return "正确"
        if folded in TRUE_FALSE_FALSE or value.strip() == "错误":
            return "错误"
        if value.strip() in TRUE_FALSE_OPTIONS:
            return value.strip()
        return None

    def generate(self, payload: ExerciseGenerateRequest) -> list[Exercise]:
        provider = self._ensure_remote_model()
        node = self.nodes.require(payload.node_id, "graph node")
        file_ids, grounding = self._resolve_source_file_ids(
            node, payload.file_ids, payload.collection_ids
        )
        snippets, retrieval_trace_id = self._grounding_snippets(node, file_ids)
        prompt = self._build_generation_prompt(node, payload, snippets, grounding)
        model_set = self._structured_generate(prompt)
        items = list(model_set.items)[: payload.count]
        if not items:
            raise AppError(
                502,
                "structured_generation_failed",
                "Model returned no exercise items",
            )
        if payload.question_type != "mixed":
            mismatched = [item for item in items if item.question_type != payload.question_type]
            if mismatched:
                raise AppError(
                    502,
                    "structured_generation_failed",
                    "Model returned question types that do not match the request",
                    {
                        "expected": payload.question_type,
                        "got": [item.question_type for item in items],
                    },
                )
        batch_id = new_id()
        generated: list[Exercise] = []
        for item in items:
            qtype, options, answer_key, rubric, source_refs = self._normalize_item(
                item, snippets
            )
            generated.append(
                self.exercises.add(
                    Exercise(
                        workspace_id=self.workspace_id,
                        node_id=node.id,
                        question_type=qtype,
                        prompt=item.prompt.strip(),
                        options=options,
                        answer_key=answer_key,
                        explanation=(item.explanation or "").strip(),
                        difficulty=payload.difficulty,
                        generation_batch_id=batch_id,
                        source_refs=source_refs,
                        rubric_json=rubric,
                        metadata_json={
                            "provider_id": provider.provider_id,
                            "model_id": getattr(provider, "model_id", ""),
                            "retrieval_trace_id": retrieval_trace_id,
                            "grounding": grounding,
                            "requested_question_type": payload.question_type,
                            "file_ids": file_ids,
                        },
                    )
                )
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="exercise.generate",
            resource_type="graph_node",
            resource_id=node.id,
            details={
                "count": len(generated),
                "remote_model_used": True,
                "provider_id": provider.provider_id,
                "generation_batch_id": batch_id,
                "grounding": grounding,
                "file_ids": file_ids,
                "retrieval_trace_id": retrieval_trace_id,
            },
        )
        self.db.commit()
        for item in generated:
            self.db.refresh(item)
        return generated

    def _grade(
        self, exercise: Exercise, payload: AnswerRequest
    ) -> tuple[bool, str, str]:
        qtype = exercise.question_type
        if qtype == "multiple_choice":
            if isinstance(payload.answer, str):
                raise AppError(
                    422,
                    "answer_type_mismatch",
                    "Multiple-choice answers must be submitted as a JSON array",
                )
            try:
                expected = json.loads(exercise.answer_key)
            except (TypeError, json.JSONDecodeError):
                raise AppError(
                    500,
                    "exercise_answer_key_invalid",
                    "The stored multiple-choice answer key is invalid",
                ) from None
            if not isinstance(expected, list) or not all(isinstance(item, str) for item in expected):
                raise AppError(
                    500,
                    "exercise_answer_key_invalid",
                    "The stored multiple-choice answer key is invalid",
                )
            allowed = {item.strip().casefold() for item in exercise.options}
            submitted = {item.strip().casefold() for item in payload.answer}
            if not submitted.issubset(allowed):
                raise AppError(
                    422,
                    "invalid_answer_option",
                    "At least one answer is not an option for this exercise",
                )
            correct = submitted == {item.strip().casefold() for item in expected}
            stored_answer = json.dumps(payload.answer, ensure_ascii=False)
            feedback = (
                (exercise.explanation or "回答正确。")
                if correct
                else (exercise.explanation or "多选答案未完全匹配，请复习相关知识点。")
            )
            return correct, stored_answer, feedback

        if isinstance(payload.answer, list):
            raise AppError(
                422,
                "answer_type_mismatch",
                "This exercise requires one text answer",
            )
        answer_text = payload.answer.strip()

        if qtype == "true_false":
            normalized = self._normalize_true_false_value(answer_text)
            if normalized is None:
                allowed = {item.strip().casefold() for item in (exercise.options or TRUE_FALSE_OPTIONS)}
                if answer_text.casefold() not in allowed:
                    raise AppError(
                        422,
                        "invalid_answer_option",
                        "True/false answers must be 正确 or 错误",
                    )
                normalized = answer_text.strip()
            expected = self._normalize_true_false_value(exercise.answer_key) or exercise.answer_key.strip()
            correct = normalized.casefold() == expected.casefold()
            feedback = (
                (exercise.explanation or "回答正确。")
                if correct
                else (exercise.explanation or "判断有误，请结合知识点再看一眼。")
            )
            return correct, answer_text, feedback

        if qtype in {"single_choice", "fill_blank"}:
            if qtype == "single_choice" and exercise.options:
                allowed = {item.strip().casefold() for item in exercise.options}
                if answer_text.casefold() not in allowed:
                    raise AppError(
                        422,
                        "invalid_answer_option",
                        "The answer is not an option for this exercise",
                    )
            correct = answer_text.casefold() == exercise.answer_key.strip().casefold()
            feedback = (
                (exercise.explanation or "回答正确。")
                if correct
                else (exercise.explanation or "答案未命中标准选项/填空，请复习后重试。")
            )
            return correct, answer_text, feedback

        rubric = dict(exercise.rubric_json or {})
        points = [str(point).strip() for point in (rubric.get("points") or []) if str(point).strip()]
        answer_folded = answer_text.casefold()
        if points:
            hits = sum(1 for point in points if point.casefold() in answer_folded)
            ratio = hits / max(1, len(points))
            correct = ratio >= 0.5
            feedback = (
                (exercise.explanation or f"覆盖了 {hits}/{len(points)} 个要点。")
                if correct
                else (
                    exercise.explanation
                    or f"仅覆盖 {hits}/{len(points)} 个要点，请补充关键概念后再答。"
                )
            )
            return correct, answer_text, feedback
        correct = exercise.answer_key.casefold() in answer_folded
        feedback = (
            (exercise.explanation or "回答正确。")
            if correct
            else (exercise.explanation or "回答未覆盖参考要点，请结合资料再组织答案。")
        )
        return correct, answer_text, feedback

    def answer(self, exercise_id: str, payload: AnswerRequest) -> AnswerResult:
        exercise = self.exercises.require(exercise_id, "exercise")
        correct, stored_answer, feedback = self._grade(exercise, payload)
        answer = self.answers.add(
            AnswerRecord(
                workspace_id=self.workspace_id,
                exercise_id=exercise.id,
                answer=stored_answer,
                is_correct=correct,
                feedback=feedback,
                actor_id=self.actor_id,
            )
        )
        signal = self.evidence.add(
            Evidence(
                workspace_id=self.workspace_id,
                node_id=exercise.node_id,
                source_type="exercise",
                summary=f"练习作答：{'正确' if correct else '待改进'}",
                confidence=0.9 if correct else 0.35,
                status="accepted" if correct else "pending",
                metadata_json={
                    "answer_record_id": answer.id,
                    "exercise_id": exercise.id,
                    "question_type": exercise.question_type,
                    "generation_batch_id": exercise.generation_batch_id,
                },
            )
        )
        node = self.nodes.require(exercise.node_id, "graph node")
        awarded = self.mastery_scheduler.record_exercise_result(signal, node)
        self.audit.record(
            actor_id=self.actor_id,
            action="exercise.answer",
            resource_type="answer_record",
            resource_id=answer.id,
            details={
                "mastery_star_awarded": awarded,
                "is_correct": correct,
                "question_type": exercise.question_type,
            },
        )
        self.db.commit()
        return AnswerResult(
            answer_record_id=answer.id,
            is_correct=correct,
            feedback=feedback,
            evidence_signal_id=signal.id,
            mastery_star_awarded=bool(awarded),
        )
