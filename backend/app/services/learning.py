from __future__ import annotations

import json
import re
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
    ModelShortAnswerGrade,
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

        return self._grade_short_answer(exercise, answer_text)

    def _grade_short_answer(
        self, exercise: Exercise, answer_text: str
    ) -> tuple[bool, str, str]:
        rubric = dict(exercise.rubric_json or {})
        points = [
            str(point).strip()
            for point in (rubric.get("points") or [])
            if str(point).strip()
        ]
        model_grade = self._model_grade_short_answer(exercise, answer_text, points)
        if model_grade is not None:
            return model_grade.is_correct, answer_text, model_grade.feedback

        return self._heuristic_grade_short_answer(exercise, answer_text, points)

    def _model_grade_short_answer(
        self,
        exercise: Exercise,
        answer_text: str,
        points: list[str],
    ) -> ModelShortAnswerGrade | None:
        provider = self.model_provider
        if provider is None or not getattr(provider, "available", False):
            return None
        if not getattr(provider, "remote_capability", False):
            return None

        points_block = (
            "\n".join(f"- {point}" for point in points)
            if points
            else f"- {exercise.answer_key.strip()}"
        )
        prompt = (
            "你是 LearnGraph 的简答题自动批改器。根据题干、参考答案与评分要点，"
            "判断学生回答是否覆盖关键概念。允许同义改写与不同表述顺序，"
            "不要要求逐字匹配。\n"
            "判分规则：\n"
            "1. covered_points / missing_points 只能来自给定要点（或参考答案拆出的要点），"
            "不要新增要点。\n"
            "2. score_ratio = 已覆盖要点数 / 总要点数，范围 0～1。\n"
            "3. is_correct 在 score_ratio >= 0.5 时为 true，否则 false；"
            "若学生明确表示不知道/空白/拒答，则为 false。\n"
            "4. feedback 用中文，1～3 句：先给覆盖情况，再点出缺失要点或改进建议。"
            "不要复述整段学生原文。\n"
            f"题干：{exercise.prompt.strip()}\n"
            f"参考答案：{exercise.answer_key.strip()}\n"
            f"评分要点：\n{points_block}\n"
            f"学生回答：{answer_text.strip()}\n"
        )
        errors: list[str] = []
        for attempt in range(1, 3):
            try:
                quote = self.billing.preflight_model_call(
                    provider_id=provider.provider_id,
                    model_id=getattr(provider, "model_id", "unknown"),
                    feature="exercise_grade",
                    estimated_input_tokens=max(1, (len(prompt) + 3) // 4),
                    estimated_output_tokens=min(
                        512,
                        max(64, int(getattr(provider, "max_output_tokens", 0) or 512)),
                    ),
                    remote_capability=True,
                )
            except Exception as exc:  # noqa: BLE001 — fall back to heuristic grading
                errors.append(type(exc).__name__)
                break

            provider_returned = False
            result: ModelShortAnswerGrade | None = None
            try:
                raw = provider.generate_json(
                    prompt,
                    "exercise_grade",
                    ModelShortAnswerGrade.model_json_schema(),
                )
                provider_returned = True
                result = ModelShortAnswerGrade.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)
            if provider_returned:
                usage = dict(getattr(provider, "last_usage", {}) or {})
                try:
                    self.billing.record_usage(
                        quote,
                        input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                        cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
                        reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                        attempt=attempt,
                        usage_reported=bool(usage),
                    )
                    # Keep billing durable even if the outer answer transaction later rolls back.
                    self.db.commit()
                except Exception as exc:  # noqa: BLE001
                    errors.append(type(exc).__name__)
            if result is None:
                continue
            return self._normalize_model_short_answer_grade(result, points, exercise)
        return None

    def _normalize_model_short_answer_grade(
        self,
        result: ModelShortAnswerGrade,
        points: list[str],
        exercise: Exercise,
    ) -> ModelShortAnswerGrade:
        total = max(1, len(points) if points else 1)
        covered = list(result.covered_points)
        missing = list(result.missing_points)
        hits: int
        ratio: float

        if points:
            point_lookup = {point.casefold(): point for point in points}

            def resolve_point(item: str) -> str | None:
                folded = item.casefold().strip()
                if folded in point_lookup:
                    return point_lookup[folded]
                # Models often paraphrase the point label; map by keyword overlap.
                best: str | None = None
                best_ratio = 0.0
                for point in points:
                    r = self._point_match_ratio(point, folded)
                    # Also try reverse containment of content keywords.
                    if point.casefold() in folded or folded in point.casefold():
                        r = max(r, 0.9)
                    if r > best_ratio:
                        best_ratio = r
                        best = point
                return best if best is not None and best_ratio >= 0.25 else None

            covered_resolved = []
            for item in covered:
                mapped = resolve_point(item)
                if mapped is not None and mapped not in covered_resolved:
                    covered_resolved.append(mapped)
            missing_resolved = []
            for item in missing:
                mapped = resolve_point(item)
                if mapped is not None and mapped not in missing_resolved:
                    missing_resolved.append(mapped)

            covered = covered_resolved
            missing = missing_resolved

            if not covered and not missing:
                # Model gave a verdict but no usable point lists — trust ratio/flag.
                ratio = max(0.0, min(1.0, float(result.score_ratio)))
                if result.is_correct and ratio < 0.5:
                    ratio = max(ratio, 0.5)
                if not result.is_correct and ratio >= 0.5:
                    ratio = min(ratio, 0.49)
                hits = int(round(ratio * total))
                # Reconstruct covered/missing proportionally for feedback only.
                if hits >= total:
                    covered = list(points)
                    missing = []
                elif hits <= 0:
                    covered = []
                    missing = list(points)
            else:
                if not covered and missing:
                    missing_folded = {item.casefold() for item in missing}
                    covered = [point for point in points if point.casefold() not in missing_folded]
                if not missing:
                    covered_folded = {item.casefold() for item in covered}
                    missing = [point for point in points if point.casefold() not in covered_folded]
                hits = len({item.casefold() for item in covered})
                ratio = hits / total
        else:
            hits = 1 if result.is_correct else 0
            ratio = max(0.0, min(1.0, float(result.score_ratio)))
            if result.is_correct and ratio < 0.5:
                ratio = 1.0
            if not result.is_correct and ratio >= 0.5:
                ratio = min(ratio, 0.49)

        correct = ratio >= 0.5
        # If the model is decisive and we could not map points, prefer its flag.
        if points and not result.covered_points and not result.missing_points:
            correct = bool(result.is_correct) if abs(ratio - 0.5) < 1e-9 else correct
        elif abs(ratio - 0.5) < 1e-9:
            correct = bool(result.is_correct)

        feedback = (result.feedback or "").strip()
        if not feedback:
            if points:
                feedback = (
                    f"覆盖了 {hits}/{total} 个要点。"
                    if correct
                    else f"仅覆盖 {hits}/{total} 个要点，请补充关键概念后再答。"
                )
            else:
                feedback = (
                    exercise.explanation
                    or ("回答正确。" if correct else "回答未覆盖参考要点，请结合资料再组织答案。")
                )
            if missing and not correct:
                feedback = f"{feedback.rstrip('。')}；可补充：{'；'.join(missing[:3])}。"
        return ModelShortAnswerGrade(
            covered_points=covered,
            missing_points=missing,
            is_correct=correct,
            score_ratio=ratio,
            feedback=feedback,
        )

    @staticmethod
    def _strip_rubric_instruction_prefix(text: str) -> str:
        folded = text.casefold().strip()
        return re.sub(
            r"^(正确|准确|简要|请|需要|应当|应该|能够|可以|要求)?"
            r"(说明|描述|解释|阐述|指出|写出|回答|答出|理解|掌握|运用|使用)?"
            r"[:：、，,\s]*",
            "",
            folded,
            count=1,
        ).strip(" 。.;；,，")

    @classmethod
    def _content_keywords(cls, text: str) -> list[str]:
        """Extract content keywords from a rubric point or answer key."""
        content = cls._strip_rubric_instruction_prefix(text)
        if not content:
            return []
        parts = [
            part.strip()
            for part in re.split(r"[并与和且、，,/]|以及|并以|并且", content)
            if part and part.strip()
        ]
        if not parts:
            parts = [content]
        keywords: list[str] = []
        for part in parts:
            part = re.sub(r"^(可|能|要|应|需)", "", part)
            if len(part) < 2:
                continue
            if 2 <= len(part) <= 8:
                keywords.append(part)
            keywords.append(part[:2])
            keywords.append(part[-2:])
            if len(part) >= 4:
                keywords.append(part[:4])
                keywords.append(part[-4:])
            for i in range(0, len(part) - 1, 2):
                keywords.append(part[i : i + 2])
            keywords.extend(re.findall(r"[a-z0-9_]{2,}", part))
        stop = {
            "并且",
            "以及",
            "或者",
            "进行",
            "通过",
            "可以",
            "能够",
            "一个",
            "一种",
            "相关",
            "内容",
            "方面",
            "问题",
            "如下",
            "上述",
            "以下",
            "正确",
            "说明",
            "描述",
            "解释",
        }
        seen: set[str] = set()
        ordered: list[str] = []
        for token in keywords:
            token = token.strip()
            if len(token) < 2 or token in stop or token in seen:
                continue
            seen.add(token)
            ordered.append(token)
        return ordered

    # Backwards-compatible alias.
    _tokenize_for_match = _content_keywords

    def _point_match_ratio(self, point: str, answer_folded: str) -> float:
        if point.casefold() in answer_folded:
            return 1.0
        keywords = self._content_keywords(point)
        if not keywords:
            return 0.0
        weight_total = 0.0
        weight_hit = 0.0
        for keyword in keywords:
            weight = 1.5 if len(keyword) >= 4 else 1.0
            weight_total += weight
            if keyword in answer_folded:
                weight_hit += weight
        return weight_hit / weight_total if weight_total else 0.0

    def _heuristic_grade_short_answer(
        self,
        exercise: Exercise,
        answer_text: str,
        points: list[str],
    ) -> tuple[bool, str, str]:
        answer_folded = answer_text.casefold()
        refusal = re.fullmatch(
            r"(不知道|不太清楚|不会|无|无解|不会做|skip|n/?a|idk|i\s*don'?t\s*know)[。.!！?？]*",
            answer_text.strip(),
            flags=re.IGNORECASE,
        )
        if refusal:
            total = max(1, len(points) if points else 1)
            feedback = (
                exercise.explanation
                or f"仅覆盖 0/{total} 个要点，请补充关键概念后再答。"
            )
            return False, answer_text, feedback

        if points:
            hits = 0
            missing: list[str] = []
            for point in points:
                ratio = self._point_match_ratio(point, answer_folded)
                # Heuristic is a fallback for when the model is unavailable; keep
                # the bar low enough that common paraphrases still hit.
                if ratio >= 0.25:
                    hits += 1
                else:
                    missing.append(point)
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
            if missing and not correct and not exercise.explanation:
                feedback = f"{feedback.rstrip('。')}；可补充：{'；'.join(missing[:3])}。"
            return correct, answer_text, feedback

        key = exercise.answer_key.strip()
        if not key:
            return False, answer_text, exercise.explanation or "缺少参考答案，无法自动批改。"
        if key.casefold() in answer_folded or answer_folded in key.casefold():
            return True, answer_text, exercise.explanation or "回答正确。"
        ratio = self._point_match_ratio(key, answer_folded)
        correct = ratio >= 0.25
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
