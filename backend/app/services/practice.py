"""Practice & Review center service.

Closed loop implemented here:

    Schedule → Practice Session → Answer/Evaluation → Evidence
    → Mastery/Review State → Session Report → Reschedule

Everything is a projection of durable rows (``PracticeSession``,
``AnswerRecord``, ``Exercise``, ``Evidence``, ``MasterySchedule``,
``LearningNodeState``). The scheduler rules live in this module so the UI never
owns weights, and no number is ever invented for display.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.domain.memory_event_models import LearningNodeState
from app.domain.models import (
    AnswerRecord,
    Evidence,
    Exercise,
    GraphNode,
    MasterySchedule,
    PracticeSession,
    new_id,
    utc_now,
)
from app.domain.schemas.learning import AnswerRequest
from app.domain.schemas.practice import (
    PracticeAnswerRequest,
    PracticeAnswerResult,
    PracticeDelayedRecallView,
    PracticeHintView,
    PracticeLearningReportView,
    PracticeNodePerformanceView,
    PracticeOverviewView,
    PracticePlanItemView,
    PracticeReportNodeView,
    PracticeRevealView,
    PracticeSessionCreateRequest,
    PracticeSessionItemView,
    PracticeSessionReportView,
    PracticeSessionSummaryView,
    PracticeSessionView,
    PracticeStatsView,
    PracticeTodayPlanView,
    PracticeTrendPointView,
    WrongBookExerciseView,
    WrongBookNodeView,
    WrongBookView,
)
from app.providers.ports.model import ModelProviderPort
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    AnswerRepository,
    EvidenceRepository,
    ExerciseRepository,
    GraphNodeRepository,
    MasteryScheduleRepository,
    PracticeSessionRepository,
)
from app.services.learning import ExerciseService
from app.services.mastery import MasteryService

# Per-question-type fallback time budget in seconds. Used only when the
# workspace has no measured duration history for that question type.
QUESTION_SECONDS: dict[str, int] = {
    "single_choice": 50,
    "true_false": 40,
    "multiple_choice": 60,
    "fill_blank": 60,
    "short_answer": 120,
}
MIN_PLAN_TARGET = 5
MAX_PLAN_TARGET = 10
MAX_QUESTIONS_PER_NODE = 3
CONSECUTIVE_WRONG_LIMIT = 2
STALE_RECALL_DAYS = 7
DELAYED_RECALL_MIN_SAMPLE = 5
DELAYED_RECALL_WINDOWS_HOURS = (24, 24 * 7)

# 这两类 AppError 都是「模型选择/可用性」问题，用户能在「设置 → 功能模型」
# 换模型或改掉 Provider 的默认模型后重试成功；其它错误（网络、超时、schema
# 不合法）不归这里，不能把人误导到设置页。
MODEL_SETTING_ERROR_CODES = frozenset(
    {"remote_model_required", "remote_model_rejected_request"}
)

REASON_LABELS = {
    "overdue": "到期",
    "due_today": "今天到期",
    "consecutive_wrong": "连续答错",
    "relearning": "重新学习",
    "weak_mastery": "掌握度较低",
    "low_confidence": "掌握置信度较低",
    "stale_recall": "久未主动回忆",
    "new_node": "新加入节点",
    "goal_weight": "目标关键节点",
    "struggling": "反复出错",
}


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def local_day(value: datetime, tz_offset_minutes: int) -> date:
    current = as_utc(value) or utc_now()
    return (current + timedelta(minutes=tz_offset_minutes)).date()


def accuracy(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


class PracticeService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        user_id: str,
        model_provider: ModelProviderPort | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.settings = settings or get_settings()
        self.sessions = PracticeSessionRepository(db, workspace_id)
        self.answers = AnswerRepository(db, workspace_id)
        self.exercises = ExerciseRepository(db, workspace_id)
        self.nodes = GraphNodeRepository(db, workspace_id)
        self.evidence = EvidenceRepository(db, workspace_id)
        self.schedules = MasteryScheduleRepository(db, workspace_id)
        self.mastery = MasteryService(db, workspace_id, user_id, self.settings)
        self.audit = AuditRepository(db, workspace_id)
        self.exercise_service = ExerciseService(
            db, workspace_id, user_id, model_provider, self.settings
        )
        self.model_provider = model_provider

    # ------------------------------------------------------------------ #
    # shared lookups
    # ------------------------------------------------------------------ #
    def _node_map(self) -> dict[str, GraphNode]:
        return {node.id: node for node in self.db.scalars(self.nodes.query()).all()}

    def _schedule_map(self) -> dict[str, MasterySchedule]:
        return {
            item.node_id: item
            for item in self.db.scalars(self.schedules.query()).all()
        }

    def _learning_state_map(self) -> dict[str, LearningNodeState]:
        rows = self.db.scalars(
            select(LearningNodeState).where(
                LearningNodeState.workspace_id == self.workspace_id,
                LearningNodeState.subject_user_id == self.user_id,
            )
        ).all()
        return {item.knowledge_node_id: item for item in rows}

    def _exercise_stats(self) -> tuple[dict[str, int], dict[str, list[Exercise]]]:
        items = list(
            self.db.scalars(
                self.exercises.query().order_by(Exercise.created_at.desc())
            ).all()
        )
        by_node: dict[str, list[Exercise]] = defaultdict(list)
        for item in items:
            by_node[item.node_id].append(item)
        counts = {node_id: len(values) for node_id, values in by_node.items()}
        return counts, by_node

    def _provider_available(self) -> bool:
        provider = self.model_provider
        return bool(
            provider is not None
            and getattr(provider, "available", False)
            and getattr(provider, "remote_capability", False)
        )

    def _answer_history(self) -> list[tuple[AnswerRecord, str]]:
        """(answer, node_id) for this workspace ordered oldest → newest."""

        rows = self.db.execute(
            select(AnswerRecord, Exercise.node_id)
            .join(Exercise, Exercise.id == AnswerRecord.exercise_id)
            .where(
                AnswerRecord.workspace_id == self.workspace_id,
                Exercise.workspace_id == self.workspace_id,
            )
            .order_by(AnswerRecord.created_at.asc())
        ).all()
        return [(row[0], str(row[1])) for row in rows]

    def _node_practice_stats(
        self, history: list[tuple[AnswerRecord, str]] | None = None
    ) -> dict[str, dict[str, Any]]:
        rows = history if history is not None else self._answer_history()
        stats: dict[str, dict[str, Any]] = {}
        per_exercise: dict[str, list[tuple[AnswerRecord, str]]] = defaultdict(list)
        for answer, node_id in rows:
            per_exercise[answer.exercise_id].append((answer, node_id))
        for exercise_id, items in per_exercise.items():
            node_id = items[0][1]
            bucket = stats.setdefault(
                node_id,
                {
                    "attempts": 0,
                    "correct": 0,
                    "first_try_correct": 0,
                    "questions": 0,
                    "last_attempt_at": None,
                    "last_correct": None,
                    "recent": [],
                    "consecutive_wrong": 0,
                    "last_score_ratio": None,
                    "last_feedback": "",
                    "last_error_type": None,
                    "missing_points": [],
                    "wrong_exercise_ids": set(),
                    "timeline": [],
                },
            )
            bucket["questions"] += 1
            for answer, _ in items:
                bucket["attempts"] += 1
                if answer.is_correct:
                    bucket["correct"] += 1
                if int(answer.attempt_index or 1) <= 1 and answer.is_correct:
                    bucket["first_try_correct"] += 1
            last = max(items, key=lambda item: as_utc(item[0].created_at) or utc_now())
            bucket["last_attempt_at"] = last[0].created_at
            bucket["last_correct"] = bool(last[0].is_correct)
            bucket["last_score_ratio"] = last[0].score_ratio
            bucket["last_feedback"] = last[0].feedback
            evaluation = dict(last[0].evaluation_json or {})
            bucket["last_error_type"] = evaluation.get("error_type")
            if not last[0].is_correct:
                bucket["wrong_exercise_ids"].add(exercise_id)
                for point in evaluation.get("missing_points") or []:
                    bucket["missing_points"].append(str(point))
            bucket["timeline"].append(
                (as_utc(last[0].created_at) or utc_now(), bool(last[0].is_correct))
            )
        for bucket in stats.values():
            timeline = sorted(bucket["timeline"], key=lambda item: item[0])
            bucket["recent"] = [flag for _, flag in timeline[-5:]]
            consecutive = 0
            for _, flag in reversed(timeline):
                if flag:
                    break
                consecutive += 1
            bucket["consecutive_wrong"] = consecutive
            del bucket["timeline"]
        return stats

    def _question_medians(self, tz_offset_minutes: int = 0) -> dict[str, float]:
        rows = self.db.execute(
            select(Exercise.question_type, AnswerRecord.duration_ms)
            .join(Exercise, Exercise.id == AnswerRecord.exercise_id)
            .where(
                AnswerRecord.workspace_id == self.workspace_id,
                Exercise.workspace_id == self.workspace_id,
                AnswerRecord.duration_ms > 3_000,
            )
        ).all()
        buckets: dict[str, list[int]] = defaultdict(list)
        for question_type, duration_ms in rows:
            buckets[str(question_type)].append(int(duration_ms or 0))
        return {
            question_type: statistics.median(values) / 1000.0
            for question_type, values in buckets.items()
            if len(values) >= 5
        }

    def _estimate_minutes(
        self,
        question_types: Sequence[str],
        medians: dict[str, float] | None = None,
    ) -> int:
        if not question_types:
            return 0
        measured = medians or {}
        total = 0.0
        for question_type in question_types:
            total += measured.get(question_type, float(QUESTION_SECONDS.get(question_type, 60)))
        return max(1, int(round(total / 60.0)))

    # ------------------------------------------------------------------ #
    # scheduler V1 — explainable priority
    # ------------------------------------------------------------------ #
    def _score_node(
        self,
        node: GraphNode,
        *,
        schedule: MasterySchedule | None,
        state: LearningNodeState | None,
        stats: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        score = 0.0
        reasons: list[tuple[float, str, str, str]] = []
        due_in_days: int | None = None
        overdue_days: int | None = None
        next_review_at = as_utc(schedule.next_review_at) if schedule is not None else None
        if next_review_at is not None:
            delta_days = (next_review_at - now).total_seconds() / 86_400
            due_in_days = int(round(delta_days))
            if delta_days <= 0:
                overdue_days = max(0, int(abs(delta_days)))
                weight = 3.0 + min(2.0, overdue_days * 0.4)
                score += weight
                reasons.append(
                    (
                        weight,
                        "overdue" if overdue_days > 0 else "due_today",
                        f"到期 {overdue_days} 天" if overdue_days > 0 else "今天到期",
                        "复习计划已到期，优先安排主动回忆",
                    )
                )
            elif delta_days <= 1:
                score += 1.2
                reasons.append((1.2, "overdue", "明天到期", "即将到期，提前安排一次回忆"))
        consecutive_wrong = int(stats.get("consecutive_wrong") or 0)
        if consecutive_wrong >= CONSECUTIVE_WRONG_LIMIT:
            weight = 2.5 + min(1.5, (consecutive_wrong - CONSECUTIVE_WRONG_LIMIT) * 0.5)
            score += weight
            reasons.append(
                (
                    weight,
                    "consecutive_wrong",
                    f"连续答错 {consecutive_wrong} 次",
                    "连续错误说明当前理解存在缺口，先补概念再练变式",
                )
            )
        if node.retrieval_state == "relearning" or stats.get("last_correct") is False:
            score += 2.0
            reasons.append(
                (
                    2.0,
                    "relearning",
                    "上次练习答错",
                    "答错后已进入重新学习状态，需要一次成功回忆来修复排期",
                )
            )
        mastery = float(state.mastery_score) if state is not None else None
        confidence = float(state.confidence) if state is not None else None
        if mastery is not None and mastery < 0.5:
            weight = round((0.5 - mastery) * 4.0, 2)
            score += weight
            reasons.append(
                (
                    weight,
                    "weak_mastery",
                    f"掌握度较低（{round(mastery * 100)}%）",
                    "证据加权掌握分低于 50%，属于薄弱知识点",
                )
            )
        if confidence is not None and confidence < 0.4:
            weight = round((0.4 - confidence) * 2.5, 2)
            score += weight
            reasons.append(
                (
                    weight,
                    "low_confidence",
                    "掌握置信度较低",
                    "该知识点的证据量还不足以确认掌握，需要更多独立作答",
                )
            )
        last_attempt = as_utc(stats.get("last_attempt_at"))
        last_recall = as_utc(schedule.last_qualified_recall_at) if schedule is not None else None
        reference = last_attempt or last_recall
        if reference is not None:
            idle_days = int((now - reference).total_seconds() // 86_400)
            if idle_days >= STALE_RECALL_DAYS:
                weight = 1.0 + min(1.5, (idle_days - STALE_RECALL_DAYS) * 0.1)
                score += weight
                reasons.append(
                    (
                        weight,
                        "stale_recall",
                        f"{idle_days} 天未主动回忆",
                        "距离上一次主动回忆太久，遗忘风险升高",
                    )
                )
        if not stats.get("attempts") and (state is not None or schedule is not None):
            score += 1.0
            reasons.append(
                (
                    1.0,
                    "new_node",
                    "新加入节点",
                    "该知识点尚未产生练习证据，需要建立第一次基线",
                )
            )
        if int(node.target_weight or 0) >= 80:
            score += 0.5
            reasons.append(
                (
                    0.5,
                    "goal_weight",
                    "目标关键节点",
                    "在当前目标图谱中权重较高",
                )
            )
        very_recent = None
        if last_attempt is not None:
            hours = (now - last_attempt).total_seconds() / 3_600
            if hours < 20:
                penalty = 3.0 if hours < 6 else 1.0
                score -= penalty
                very_recent = hours
        reasons.sort(key=lambda item: item[0], reverse=True)
        top = reasons[0] if reasons else (0.0, "maintenance", "维持性复习", "")
        return {
            "priority": round(score, 2),
            "reason_code": top[1],
            "reason": top[2],
            "detail": top[3],
            "reasons": reasons,
            "due_in_days": due_in_days,
            "overdue_days": overdue_days,
            "very_recent_hours": very_recent,
            "mastery_score": mastery,
            "confidence": confidence,
            "consecutive_wrong": consecutive_wrong,
            "last_practiced_at": last_attempt,
            "next_review_at": next_review_at,
            "retrieval_state": node.retrieval_state,
            "evidence_state": node.evidence_state,
        }

    def _planned_questions(self, priority: float, *, overdue: bool) -> int:
        if priority >= 5:
            return 3
        if priority >= 3:
            return 2
        if priority >= 1.5 and overdue:
            return 2
        return 1

    def build_today_plan(
        self, *, tz_offset_minutes: int = 0
    ) -> PracticeTodayPlanView:
        now = utc_now()
        nodes = self._node_map()
        schedules = self._schedule_map()
        states = self._learning_state_map()
        stats = self._node_practice_stats()
        exercise_counts, exercises_by_node = self._exercise_stats()
        medians = self._question_medians(tz_offset_minutes)

        candidates: list[tuple[GraphNode, dict[str, Any]]] = []
        for node in nodes.values():
            schedule = schedules.get(node.id)
            state = states.get(node.id)
            node_stats = stats.get(node.id, {})
            scored = self._score_node(
                node,
                schedule=schedule,
                state=state,
                stats=node_stats,
                now=now,
            )
            if scored["priority"] <= 0:
                continue
            candidates.append((node, scored))
        candidates.sort(
            key=lambda item: (
                item[1]["priority"],
                item[1]["overdue_days"] or 0,
                item[1]["consecutive_wrong"] or 0,
            ),
            reverse=True,
        )

        target = min(
            MAX_PLAN_TARGET,
            max(MIN_PLAN_TARGET, len(candidates) * 2),
        )
        provider_available = self._provider_available()
        items: list[PracticePlanItemView] = []
        skipped: list[PracticePlanItemView] = []
        planned_question_types: list[str] = []
        remaining = target
        for node, scored in candidates:
            if remaining <= 0:
                break
            available = exercises_by_node.get(node.id, [])
            wanted = min(
                self._planned_questions(
                    scored["priority"], overdue=(scored["overdue_days"] is not None)
                ),
                remaining,
                MAX_QUESTIONS_PER_NODE,
            )
            if not available:
                # "还没有题库"不代表今天练不了：只要远程模型可用，开始练习时会
                # 真的出题，所以它必须留在计划里（否则 create_session 只遍历
                # plan.items，这条出题路径永远走不到）。
                # 保留调度器给出的真实原因（到期/答错/薄弱…），"尚无题库"通过
                # available_exercises=0 + detail 表达，UI 才能同时看到"为什么今天
                # 要练它"和"题目从哪来"。
                no_bank = PracticePlanItemView(
                    node_id=node.id,
                    label=node.label,
                    graph_id=node.graph_id,
                    reason_code=scored["reason_code"],
                    reason=scored["reason"],
                    detail=(
                        "尚无题库，开始练习时会用远程模型出题"
                        if provider_available
                        else "尚无题库，需要配置远程模型后才能出题"
                    ),
                    planned_questions=wanted,
                    priority=scored["priority"],
                    available_exercises=0,
                    due_in_days=scored["due_in_days"],
                    overdue_days=scored["overdue_days"],
                    last_practiced_at=scored["last_practiced_at"],
                    next_review_at=scored["next_review_at"],
                    retrieval_state=scored["retrieval_state"],
                    evidence_state=scored["evidence_state"],
                    mastery_score=scored["mastery_score"],
                    confidence=scored["confidence"],
                    consecutive_wrong=scored["consecutive_wrong"],
                )
                if not provider_available:
                    skipped.append(no_bank)
                    continue
                items.append(no_bank)
                remaining -= wanted
                # 题型未知（题库为空）：按默认题量预算估算用时。
                planned_question_types.extend(["mixed"] * wanted)
                continue
            item = PracticePlanItemView(
                node_id=node.id,
                label=node.label,
                graph_id=node.graph_id,
                reason_code=scored["reason_code"],
                reason=scored["reason"],
                detail=scored["detail"],
                planned_questions=wanted,
                priority=scored["priority"],
                available_exercises=exercise_counts.get(node.id, 0),
                due_in_days=scored["due_in_days"],
                overdue_days=scored["overdue_days"],
                last_practiced_at=scored["last_practiced_at"],
                next_review_at=scored["next_review_at"],
                retrieval_state=scored["retrieval_state"],
                evidence_state=scored["evidence_state"],
                mastery_score=scored["mastery_score"],
                confidence=scored["confidence"],
                consecutive_wrong=scored["consecutive_wrong"],
            )
            items.append(item)
            remaining -= wanted
            for exercise in available[:wanted]:
                planned_question_types.append(exercise.question_type)

        question_count = sum(item.planned_questions for item in items)
        if not items and not skipped:
            return PracticeTodayPlanView(
                mode="empty",
                message="今天没有必须复习的内容",
            )
        mode = "scheduled" if any(
            item.reason_code in {"overdue", "due_today", "relearning"} for item in items
        ) else "maintenance"
        if not items and skipped:
            # 只有"没有题库 + 没有远程模型"才会走到这里（模型可用时上面的节点
            # 已经作为可出题的计划项留在 items 里）。
            message = (
                "计划中的知识点还没有题库，且当前没有可用的远程模型，无法出题。"
                if not provider_available
                else "计划中的知识点还没有题库，且远程模型未能出题。"
            )
        else:
            message = ""
        return PracticeTodayPlanView(
            mode=mode,
            message=message,
            question_count=question_count,
            estimated_minutes=self._estimate_minutes(planned_question_types, medians),
            node_count=len(items),
            node_ids=[item.node_id for item in items],
            question_types=sorted(set(planned_question_types)),
            provider_available=provider_available,
            # 走到这里的 skipped 只可能是「没有题库 + 模型不可用」，也就是模型侧
            # 的阻塞；UI 需要据此提供「去设置换模型」的出口。
            model_setting_required=not provider_available,
            items=items,
            skipped_nodes=skipped,
        )

    def _focus_nodes(
        self, plan: PracticeTodayPlanView, limit: int = 3
    ) -> list[PracticePlanItemView]:
        flagged = [item for item in plan.items if item.priority > 0]
        return flagged[:limit]

    # ------------------------------------------------------------------ #
    # stats / trend
    # ------------------------------------------------------------------ #
    def _trend(
        self,
        *,
        days: int,
        now: datetime,
        tz_offset_minutes: int,
        history: list[tuple[AnswerRecord, str]] | None = None,
    ) -> list[PracticeTrendPointView]:
        rows = history if history is not None else self._answer_history()
        start_day = local_day(now - timedelta(days=days - 1), tz_offset_minutes)
        buckets: dict[date, dict[str, Any]] = {}
        per_exercise: dict[tuple[str, date], list[AnswerRecord]] = defaultdict(list)
        for answer, _node_id in rows:
            day = local_day(answer.created_at, tz_offset_minutes)
            if day < start_day:
                continue
            bucket = buckets.setdefault(
                day,
                {
                    "answered": 0,
                    "session_ids": set(),
                    "duration_ms": 0,
                    "first_try_correct": 0,
                    "first_try_total": 0,
                },
            )
            bucket["answered"] += 1
            bucket["duration_ms"] += max(0, int(answer.duration_ms or 0))
            if answer.practice_session_id:
                bucket["session_ids"].add(answer.practice_session_id)
            if int(answer.attempt_index or 1) <= 1:
                bucket["first_try_total"] += 1
                if answer.is_correct:
                    bucket["first_try_correct"] += 1
            per_exercise[(answer.exercise_id, day)].append(answer)
        final_correct: dict[date, tuple[int, int]] = {}
        counters: dict[date, list[int]] = defaultdict(lambda: [0, 0])
        for (_exercise_id, day), items in per_exercise.items():
            last = max(items, key=lambda item: as_utc(item.created_at) or utc_now())
            counters[day][1] += 1
            if last.is_correct:
                counters[day][0] += 1
        for day, (correct, total) in counters.items():
            final_correct[day] = (correct, total)
        points: list[PracticeTrendPointView] = []
        for offset in range(days):
            day = start_day + timedelta(days=offset)
            bucket = buckets.get(day)
            if bucket is None:
                points.append(PracticeTrendPointView(date=day.isoformat()))
                continue
            final = final_correct.get(day, (0, 0))
            points.append(
                PracticeTrendPointView(
                    date=day.isoformat(),
                    answered=int(bucket["answered"]),
                    sessions=len(bucket["session_ids"]),
                    minutes=int(round(bucket["duration_ms"] / 60_000)),
                    first_try_accuracy=accuracy(
                        int(bucket["first_try_correct"]), int(bucket["first_try_total"])
                    ),
                    final_accuracy=accuracy(final[0], final[1]),
                )
            )
        return points

    def _window_accuracy(
        self,
        history: Sequence[tuple[AnswerRecord, str]],
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[int, int, int]:
        first_total = 0
        first_correct = 0
        answered = 0
        per_exercise: dict[str, list[AnswerRecord]] = defaultdict(list)
        for answer, _node_id in history:
            created = as_utc(answer.created_at) or utc_now()
            if created < start or created >= end:
                continue
            answered += 1
            if int(answer.attempt_index or 1) <= 1:
                first_total += 1
                if answer.is_correct:
                    first_correct += 1
            per_exercise[answer.exercise_id].append(answer)
        return first_correct, first_total, answered

    def _stats(
        self,
        plan: PracticeTodayPlanView,
        *,
        now: datetime,
        tz_offset_minutes: int,
        stats_by_node: dict[str, dict[str, Any]],
        history: list[tuple[AnswerRecord, str]],
    ) -> PracticeStatsView:
        schedules = self._schedule_map()
        states = self._learning_state_map()
        nodes = self._node_map()
        due = 0
        consolidated = 0
        weak: set[str] = set()
        for node_id, schedule in schedules.items():
            next_review = as_utc(schedule.next_review_at)
            if next_review is not None and next_review <= now:
                due += 1
            if schedule.last_qualified_recall_at is not None:
                consolidated += 1
        for node_id, state in states.items():
            if state.status == "weak" or float(state.mastery_score) < 0.5:
                weak.add(node_id)
        attention: set[str] = set()
        for node_id, state in states.items():
            if state.status in {"weak", "needs_review"}:
                attention.add(node_id)
        for node_id, stats in stats_by_node.items():
            if int(stats.get("consecutive_wrong") or 0) >= CONSECUTIVE_WRONG_LIMIT:
                attention.add(node_id)
        for node_id, node in nodes.items():
            if node.evidence_state == "conflicted" or node.retrieval_state == "relearning":
                attention.add(node_id)
        window_end = now
        window_start = now - timedelta(days=7)
        prev_start = now - timedelta(days=14)
        first_correct, first_total, answered = self._window_accuracy(
            history, start=window_start, end=window_end
        )
        prev_correct, prev_total, prev_answered = self._window_accuracy(
            history, start=prev_start, end=window_start
        )
        current_accuracy = accuracy(first_correct, first_total)
        previous_accuracy = accuracy(prev_correct, prev_total)
        delta = (
            round(current_accuracy - previous_accuracy, 4)
            if current_accuracy is not None and previous_accuracy is not None
            else None
        )
        del tz_offset_minutes
        return PracticeStatsView(
            pending_node_count=plan.node_count,
            due_node_count=due,
            weak_node_count=len(weak),
            attention_node_count=len(attention),
            consolidated_node_count=consolidated,
            estimated_minutes=plan.estimated_minutes,
            answered_7d=answered,
            answered_7d_previous=prev_answered,
            first_try_correct_7d=first_correct,
            first_try_accuracy_7d=current_accuracy,
            first_try_accuracy_previous_7d=previous_accuracy,
            first_try_accuracy_delta_7d=delta,
        )

    # ------------------------------------------------------------------ #
    # overview
    # ------------------------------------------------------------------ #
    def overview(self, *, tz_offset_minutes: int = 0) -> PracticeOverviewView:
        now = utc_now()
        history = self._answer_history()
        stats_by_node = self._node_practice_stats(history)
        plan = self.build_today_plan(tz_offset_minutes=tz_offset_minutes)
        stats = self._stats(
            plan,
            now=now,
            tz_offset_minutes=tz_offset_minutes,
            stats_by_node=stats_by_node,
            history=history,
        )
        recent = self.db.scalar(
            self.sessions.query()
            .where(PracticeSession.status == "completed")
            .order_by(PracticeSession.completed_at.desc())
            .limit(1)
        )
        active = self.db.scalar(
            self.sessions.query()
            .where(PracticeSession.status.in_(("active", "planned")))
            .order_by(PracticeSession.created_at.desc())
            .limit(1)
        )
        return PracticeOverviewView(
            generated_at=now,
            stats=stats,
            today_plan=plan,
            focus_nodes=self._focus_nodes(plan),
            recent_session=self._summary(recent) if recent is not None else None,
            active_session=self._summary(active) if active is not None else None,
            trend=self._trend(
                days=7,
                now=now,
                tz_offset_minutes=tz_offset_minutes,
                history=history,
            ),
        )

    # ------------------------------------------------------------------ #
    # session lifecycle
    # ------------------------------------------------------------------ #
    def _summary(self, session: PracticeSession) -> PracticeSessionSummaryView:
        node_map = self._node_map()
        labels = [
            node_map[node_id].label
            for node_id in (session.node_ids or [])
            if node_id in node_map
        ]
        # 分母 = 本次真正作答过的题数（未作答不计入，也不按 0 分拉低），
        # 与学习报告 `first_try_correct / first_total` 的口径一致。
        answered = int(session.completed_question_count or 0)
        return PracticeSessionSummaryView(
            id=session.id,
            mode=session.mode,
            status=session.status,
            title=session.title,
            started_at=session.started_at,
            completed_at=session.completed_at,
            created_at=session.created_at,
            duration_seconds=int(session.duration_seconds or 0),
            planned_question_count=int(session.planned_question_count or 0),
            completed_question_count=int(session.completed_question_count or 0),
            total_attempt_count=int(session.total_attempt_count or 0),
            first_try_correct_count=int(session.first_try_correct_count or 0),
            final_correct_count=int(session.final_correct_count or 0),
            first_try_accuracy=accuracy(
                int(session.first_try_correct_count or 0),
                answered,
            ),
            final_accuracy=accuracy(
                int(session.final_correct_count or 0),
                answered,
            ),
            node_count=len({node_id for node_id in (session.node_ids or [])}),
            node_labels=labels,
        )

    def _session_nodes(self, session: PracticeSession) -> dict[str, Exercise]:
        exercise_ids = list((session.source_metadata or {}).get("question_order") or [])
        if not exercise_ids:
            return {}
        rows = self.db.scalars(
            self.exercises.query().where(Exercise.id.in_(exercise_ids))
        ).all()
        by_id = {item.id: item for item in rows}
        return {
            exercise_id: by_id[exercise_id]
            for exercise_id in exercise_ids
            if exercise_id in by_id
        }

    def _session_answers(self, session_id: str) -> dict[str, list[AnswerRecord]]:
        rows = self.db.scalars(
            self.answers.query()
            .where(AnswerRecord.practice_session_id == session_id)
            .order_by(AnswerRecord.created_at.asc())
        ).all()
        grouped: dict[str, list[AnswerRecord]] = defaultdict(list)
        for item in rows:
            grouped[item.exercise_id].append(item)
        return grouped

    def _items(
        self,
        session: PracticeSession,
        *,
        exercises: dict[str, Exercise],
        answers: dict[str, list[AnswerRecord]],
    ) -> list[PracticeSessionItemView]:
        node_map = self._node_map()
        hints = dict((session.source_metadata or {}).get("hints") or {})
        items: list[PracticeSessionItemView] = []
        for position, exercise_id in enumerate(
            (session.source_metadata or {}).get("question_order") or []
        ):
            exercise = exercises.get(exercise_id)
            if exercise is None:
                continue
            node = node_map.get(exercise.node_id)
            attempts = answers.get(exercise_id, [])
            last = attempts[-1] if attempts else None
            evaluation = dict((last.evaluation_json or {}) if last is not None else {})
            if last is None:
                state = "unanswered"
            elif last.is_correct and float(last.score_ratio if last.score_ratio is not None else 1.0) >= 0.999:
                state = "correct"
            elif last.is_correct:
                state = "partial"
            else:
                state = "incorrect"
            hint_texts = [str(item) for item in (hints.get(exercise_id) or [])]
            items.append(
                PracticeSessionItemView(
                    position=position,
                    exercise_id=exercise.id,
                    node_id=exercise.node_id,
                    node_label=node.label if node is not None else exercise.node_id,
                    graph_id=node.graph_id if node is not None else None,
                    question_type=exercise.question_type,
                    prompt=exercise.prompt,
                    options=list(exercise.options or []),
                    difficulty=exercise.difficulty or "medium",
                    explanation_available=bool((exercise.explanation or "").strip()),
                    source_refs=list(exercise.source_refs or []),
                    state=state,
                    attempts=len(attempts),
                    hint_count=len(hint_texts),
                    hints=hint_texts,
                    last_answer=last.answer if last is not None else None,
                    last_feedback=last.feedback if last is not None else "",
                    score_ratio=last.score_ratio if last is not None else None,
                    covered_points=[str(item) for item in evaluation.get("covered_points") or []],
                    missing_points=[str(item) for item in evaluation.get("missing_points") or []],
                    error_type=evaluation.get("error_type"),
                    answered_at=last.created_at if last is not None else None,
                    duration_ms=int(last.duration_ms or 0) if last is not None else 0,
                )
            )
        return items

    def _sync_counters(self, session: PracticeSession) -> None:
        rows = self.db.scalars(
            self.answers.query().where(AnswerRecord.practice_session_id == session.id)
        ).all()
        grouped: dict[str, list[AnswerRecord]] = defaultdict(list)
        for item in rows:
            grouped[item.exercise_id].append(item)
        first_try_correct = 0
        final_correct = 0
        for items in grouped.values():
            first = min(
                items,
                key=lambda item: (
                    int(item.attempt_index or 1),
                    as_utc(item.created_at) or utc_now(),
                ),
            )
            last = max(items, key=lambda item: as_utc(item.created_at) or utc_now())
            if int(first.attempt_index or 1) <= 1 and first.is_correct:
                first_try_correct += 1
            if last.is_correct:
                final_correct += 1
        session.completed_question_count = len(grouped)
        session.total_attempt_count = len(rows)
        session.first_try_correct_count = first_try_correct
        session.final_correct_count = final_correct
        total_ms = sum(max(0, int(item.duration_ms or 0)) for item in rows)
        started = as_utc(session.started_at)
        if total_ms:
            session.duration_seconds = int(round(total_ms / 1000))
        elif started is not None:
            session.duration_seconds = max(
                0, int((utc_now() - started).total_seconds())
            )

    def _snapshot_mastery(self, node_ids: Iterable[str]) -> dict[str, Any]:
        states = self._learning_state_map()
        schedules = self._schedule_map()
        node_map = self._node_map()
        snapshot: dict[str, Any] = {}
        for node_id in dict.fromkeys(node_ids):
            state = states.get(node_id)
            schedule = schedules.get(node_id)
            node = node_map.get(node_id)
            snapshot[node_id] = {
                "mastery_score": float(state.mastery_score) if state is not None else None,
                "confidence": float(state.confidence) if state is not None else None,
                "mastery_stars": int(node.mastery_stars) if node is not None else 0,
                "next_review_at": (
                    as_utc(schedule.next_review_at).isoformat()
                    if schedule is not None and schedule.next_review_at is not None
                    else None
                ),
            }
        return snapshot

    def _pick_exercises(
        self,
        node_id: str,
        *,
        wanted: int,
        question_type: str,
        available: list[Exercise],
        history_by_exercise: dict[str, int],
    ) -> list[Exercise]:
        """Pick the least-practised questions for one node.

        Ordering by practice count means a second question from the same node is
        a genuine variant rather than the identical question again.
        """

        def matches(exercise: Exercise) -> bool:
            if question_type == "mixed":
                return True
            return exercise.question_type == question_type

        pool = [item for item in available if matches(item)]
        if not pool:
            return []
        pool.sort(key=lambda item: (history_by_exercise.get(item.id, 0), item.created_at))
        return pool[:wanted]

    def create_session(
        self,
        payload: PracticeSessionCreateRequest,
        *,
        tz_offset_minutes: int = 0,
    ) -> PracticeSessionView:
        now = utc_now()
        exercise_counts, exercises_by_node = self._exercise_stats()
        history = self._answer_history()
        history_by_exercise: dict[str, int] = defaultdict(int)
        answer_count_by_node: dict[str, int] = defaultdict(int)
        # 错题重练必须只练"真的答错过"的题，否则「错题重练」名不副实。
        wrong_exercise_ids: set[str] = set()
        for answer, node_id in history:
            history_by_exercise[answer.exercise_id] += 1
            answer_count_by_node[node_id] += 1
            if not answer.is_correct:
                wrong_exercise_ids.add(answer.exercise_id)

        plan: PracticeTodayPlanView | None = None
        selected: list[Exercise] = []
        hints: dict[str, list[str]] = {}
        warnings: list[str] = []
        node_ids: list[str] = []
        # 本场组卷是否被模型侧问题挡住（换了模型就能重试成功），随 session 落库，
        # 这样详情页刷新后仍能给出「去设置」入口。
        model_setting_required = False

        if payload.mode == "scheduled":
            plan = self.build_today_plan(tz_offset_minutes=tz_offset_minutes)
            provider_available = self._provider_available()
            model_setting_required = not provider_available
            generation_error: AppError | None = None
            for item in plan.items:
                available = exercises_by_node.get(item.node_id, [])
                picked = self._pick_exercises(
                    item.node_id,
                    wanted=item.planned_questions,
                    question_type=payload.question_type,
                    available=available,
                    history_by_exercise=history_by_exercise,
                )
                generation_failed = False
                if len(picked) < item.planned_questions and provider_available:
                    missing = item.planned_questions - len(picked)
                    try:
                        generated = self._generate_for_node(
                            item.node_id,
                            count=missing,
                            question_type=payload.question_type,
                            difficulty=payload.difficulty,
                        )
                    except AppError as exc:
                        # 单个知识点出题失败不该让整场「今日练习」泡汤：记录真实原因
                        # 后继续安排其它知识点；如果最后一题都没排出来，下面会把原始
                        # 错误重新抛出，绝不降级成假题。
                        generation_error = generation_error or exc
                        generation_failed = True
                        if exc.code in MODEL_SETTING_ERROR_CODES:
                            model_setting_required = True
                        warnings.append(f"「{item.label}」出题失败：{exc.message}")
                        generated = []
                    picked = list(picked) + list(generated)
                    exercise_counts[item.node_id] = exercise_counts.get(item.node_id, 0) + len(
                        generated
                    )
                if not picked:
                    if not generation_failed:
                        warnings.append(f"「{item.label}」没有可用题目")
                    continue
                selected.extend(picked)
                node_ids.append(item.node_id)
            if not selected:
                if generation_error is not None:
                    raise generation_error
                raise AppError(
                    409,
                    "practice_plan_unavailable",
                    "今天没有可以真正作答的题目：计划中的知识点还没有题库，"
                    + (
                        "且当前没有可用的远程模型可以出题。请先配置并启用远程模型。"
                        if not provider_available
                        else "远程模型也未能为它们出题，请检查模型配置后重试。"
                    ),
                    {
                        "skipped_nodes": [item.node_id for item in plan.skipped_nodes],
                        "provider_available": provider_available,
                        "model_setting_required": model_setting_required,
                        "warnings": warnings,
                    },
                )
        else:
            node_ids = list(dict.fromkeys(payload.node_ids))
            if payload.mode == "wrong_book" and not node_ids:
                node_ids = [item.node_id for item in self.wrong_book().nodes]
            if not node_ids:
                raise AppError(422, "practice_nodes_required", "请选择至少一个知识点")
            for node_id in node_ids:
                self.nodes.require(node_id, "graph node")
            wanted = payload.count or 5
            per_node = max(1, min(MAX_QUESTIONS_PER_NODE, -(-wanted // max(1, len(node_ids)))))
            provider_available = self._provider_available()
            model_setting_required = not provider_available
            generation_error: AppError | None = None
            for node_id in node_ids:
                available = exercises_by_node.get(node_id, [])
                if payload.mode == "wrong_book":
                    wrong_pool = [
                        item for item in available if item.id in wrong_exercise_ids
                    ]
                    if wrong_pool:
                        available = wrong_pool
                    else:
                        # 该知识点没有历史错题（例如从其它入口传了任意节点）：
                        # 退回普通练习，但必须如实说明，不能假装是错题重练。
                        warnings.append(
                            f"「{self._node_label(node_id)}」没有历史错题，已按普通练习出题"
                        )
                picked = self._pick_exercises(
                    node_id,
                    wanted=per_node,
                    question_type=payload.question_type,
                    available=available,
                    history_by_exercise=history_by_exercise,
                )
                if payload.generation_batch_id:
                    batch_items = [
                        item
                        for item in available
                        if item.generation_batch_id == payload.generation_batch_id
                    ]
                    if batch_items:
                        picked = batch_items[:per_node]
                if len(picked) < per_node:
                    if not provider_available:
                        warnings.append(
                            self._unavailable_reason(payload.mode, node_id, picked)
                        )
                    else:
                        try:
                            generated = self._generate_for_node(
                                node_id,
                                count=per_node - len(picked),
                                question_type=payload.question_type,
                                difficulty=payload.difficulty,
                                file_ids=payload.file_ids,
                                collection_ids=payload.collection_ids,
                            )
                        except AppError as exc:
                            # 多知识点自由练习：一个节点出题失败时保留其它节点的题目，
                            # 并把模型侧的真实原因带回去（而不是笼统的"没有可用题目"）。
                            generation_error = generation_error or exc
                            if exc.code in MODEL_SETTING_ERROR_CODES:
                                model_setting_required = True
                            warnings.append(f"「{self._node_label(node_id)}」出题失败：{exc.message}")
                            generated = []
                        picked = list(picked) + list(generated)
                if not picked:
                    continue
                selected.extend(picked)
            if payload.count:
                selected = selected[: payload.count]
            if not selected:
                if generation_error is not None:
                    raise generation_error
                raise AppError(
                    409,
                    "practice_questions_unavailable",
                    "所选知识点没有可用题目，且当前没有可用的远程模型可以出题。"
                    if not self._provider_available()
                    else "所选知识点没有可用题目。",
                    {"warnings": warnings, "model_setting_required": model_setting_required},
                )

        # Order: round-robin across nodes so a wrong answer meets the same
        # knowledge point again a few questions later instead of repeating the
        # identical question back-to-back.
        ordered = self._interleave(selected)
        session = self.sessions.add(
            PracticeSession(
                workspace_id=self.workspace_id,
                user_id=self.user_id,
                mode=payload.mode,
                status="planned",
                title=self._session_title(payload.mode, ordered, node_ids),
                planned_question_count=len(ordered),
                node_ids=list(dict.fromkeys(item.node_id for item in ordered)),
                source_metadata={
                    "question_order": [item.id for item in ordered],
                    "plan": plan.model_dump(mode="json") if plan is not None else None,
                    "reasons": {
                        item.node_id: {
                            "reason_code": item.reason_code,
                            "reason": item.reason,
                            "detail": item.detail,
                        }
                        for item in (plan.items if plan is not None else [])
                    },
                    "hints": hints,
                    "hint_counts": {},
                    "mastery_snapshot": self._snapshot_mastery(
                        item.node_id for item in ordered
                    ),
                    "warnings": warnings,
                    "model_setting_required": model_setting_required,
                    "tz_offset_minutes": tz_offset_minutes,
                    "requested": payload.model_dump(mode="json"),
                },
            )
        )
        self.audit.record(
            actor_id=self.user_id,
            action="practice.session_created",
            resource_type="practice_session",
            resource_id=session.id,
            details={
                "mode": payload.mode,
                "question_count": len(ordered),
                "node_ids": list(session.node_ids or []),
            },
        )
        self.db.commit()
        self.db.refresh(session)
        return self.get_session(session.id)

    def _node_label(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        return node.label if node is not None else node_id

    def _unavailable_reason(
        self, mode: str, node_id: str, picked: Sequence[Exercise]
    ) -> str:
        del mode
        label = self._node_label(node_id)
        if picked:
            return f"「{label}」题目不足，已按现有题目安排"
        return f"「{label}」没有可用题目，且当前没有可用的远程模型可以出题"

    def _interleave(self, items: Sequence[Exercise]) -> list[Exercise]:
        queues: dict[str, list[Exercise]] = defaultdict(list)
        order: list[str] = []
        for item in items:
            if item.node_id not in queues:
                order.append(item.node_id)
            queues[item.node_id].append(item)
        # Nodes with more planned questions first, so their variants spread out.
        order.sort(key=lambda node_id: len(queues[node_id]), reverse=True)
        result: list[Exercise] = []
        while any(queues[node_id] for node_id in order):
            for node_id in order:
                if queues[node_id]:
                    result.append(queues[node_id].pop(0))
        return result

    def _session_title(
        self, mode: str, items: Sequence[Exercise], node_ids: Sequence[str]
    ) -> str:
        base = {
            "scheduled": "今日练习",
            "custom": "自由练习",
            "wrong_book": "错题重练",
            "node": "知识点练习",
            "material": "资料出题练习",
        }.get(mode, "练习")
        node_map = self._node_map()
        labels = [node_map[node_id].label for node_id in node_ids if node_id in node_map]
        if not labels:
            return base
        preview = "、".join(labels[:3])
        suffix = " 等" if len(labels) > 3 else ""
        return f"{base} · {preview}{suffix}"

    def _generate_for_node(
        self,
        node_id: str,
        *,
        count: int,
        question_type: str,
        difficulty: str | None,
        file_ids: list[str] | None = None,
        collection_ids: list[str] | None = None,
    ) -> list[Exercise]:
        from app.domain.schemas.learning import ExerciseGenerateRequest

        payload = ExerciseGenerateRequest(
            node_id=node_id,
            question_type=question_type,
            count=max(1, min(10, count)),
            file_ids=list(file_ids or []),
            collection_ids=list(collection_ids or []),
            difficulty=difficulty or "medium",
        )
        return self.exercise_service.generate(payload)

    def get_session(self, session_id: str) -> PracticeSessionView:
        session = self.sessions.require(session_id, "practice session")
        exercises = self._session_nodes(session)
        answers = self._session_answers(session.id)
        items = self._items(session, exercises=exercises, answers=answers)
        current = next((item for item in items if item.state == "unanswered"), None)
        stored_plan = (session.source_metadata or {}).get("plan")
        return PracticeSessionView(
            session=self._summary(session),
            items=items,
            current_position=current.position if current is not None else max(0, len(items) - 1),
            current_exercise_id=current.exercise_id if current is not None else None,
            remaining_count=sum(1 for item in items if item.state == "unanswered"),
            report_available=bool(session.report_json),
            plan=PracticeTodayPlanView.model_validate(stored_plan) if stored_plan else None,
            warnings=[
                str(item)
                for item in (session.source_metadata or {}).get("warnings") or []
            ],
            model_setting_required=bool(
                (session.source_metadata or {}).get("model_setting_required")
            ),
        )

    def list_sessions(
        self, *, limit: int = 20, status: str | None = "completed"
    ) -> list[PracticeSessionSummaryView]:
        query = self.sessions.query().order_by(PracticeSession.created_at.desc())
        if status:
            query = query.where(PracticeSession.status == status)
        rows = self.db.scalars(query.limit(max(1, min(limit, 100)))).all()
        return [self._summary(item) for item in rows]

    def answer_question(
        self, session_id: str, payload: PracticeAnswerRequest
    ) -> PracticeAnswerResult:
        session = self.sessions.require(session_id, "practice session")
        if session.status in {"completed", "abandoned"}:
            raise AppError(
                409,
                "practice_session_closed",
                "本次练习已经结束，请查看练习报告或开始新的练习",
            )
        order = list((session.source_metadata or {}).get("question_order") or [])
        if payload.exercise_id not in order:
            raise AppError(
                404,
                "practice_item_not_found",
                "该题目不属于本次练习",
            )
        if session.status == "planned":
            session.status = "active"
            session.started_at = utc_now()
        hints = dict((session.source_metadata or {}).get("hint_counts") or {})
        hint_count = int(hints.get(payload.exercise_id) or 0)
        existing = self._session_answers(session.id).get(payload.exercise_id, [])
        attempt_index = len(existing) + 1
        result = self.exercise_service.submit_answer(
            payload.exercise_id,
            AnswerRequest(answer=payload.answer),
            practice_session_id=session.id,
            duration_ms=payload.duration_ms,
            hint_count=hint_count,
            attempt_index=attempt_index,
        )
        self._sync_counters(session)
        node = self.nodes.get(result.node_id or "")
        items_answered = session.completed_question_count
        remaining = max(0, int(session.planned_question_count or 0) - int(items_answered))
        self.db.commit()
        self.db.refresh(session)
        return PracticeAnswerResult(
            answer_record_id=result.answer_record_id,
            exercise_id=payload.exercise_id,
            node_id=result.node_id or "",
            node_label=node.label if node is not None else (result.node_id or ""),
            graph_id=node.graph_id if node is not None else None,
            is_correct=result.is_correct,
            score_ratio=float(result.score_ratio),
            covered_points=list(result.covered_points),
            missing_points=list(result.missing_points),
            error_type=result.error_type,
            feedback=result.feedback,
            explanation=None,
            attempt_index=attempt_index,
            is_first_attempt=attempt_index <= 1,
            is_first_try_correct=attempt_index <= 1 and result.is_correct,
            hint_count=hint_count,
            evidence_signal_id=result.evidence_signal_id,
            mastery_star_awarded=result.mastery_star_awarded,
            next_review_at=result.next_review_at,
            schedule_reason=result.schedule_reason,
            retry_allowed=not result.is_correct,
            reveal_available=True,
            session_completed=False,
            remaining_count=remaining,
        )

    def reveal_item(self, session_id: str, exercise_id: str) -> PracticeRevealView:
        session = self.sessions.require(session_id, "practice session")
        exercises = self._session_nodes(session)
        exercise = exercises.get(exercise_id)
        if exercise is None:
            raise AppError(404, "practice_item_not_found", "该题目不属于本次练习")
        answers = self._session_answers(session.id).get(exercise_id, [])
        if not answers:
            raise AppError(
                409,
                "practice_item_unanswered",
                "先作答一次再查看讲解",
            )
        last = answers[-1]
        if exercise.question_type == "multiple_choice":
            import json as _json

            try:
                display = " / ".join(_json.loads(exercise.answer_key))
            except (TypeError, ValueError):
                display = exercise.answer_key
        else:
            display = (exercise.answer_key or "").strip()[:600]
        evaluation = dict(last.evaluation_json or {})
        return PracticeRevealView(
            exercise_id=exercise_id,
            explanation=(exercise.explanation or "").strip(),
            answer_display=display,
            covered_points=[str(item) for item in evaluation.get("covered_points") or []],
            missing_points=[str(item) for item in evaluation.get("missing_points") or []],
        )

    def hint_item(self, session_id: str, exercise_id: str) -> PracticeHintView:
        session = self.sessions.require(session_id, "practice session")
        if session.status in {"completed", "abandoned"}:
            raise AppError(409, "practice_session_closed", "本次练习已经结束")
        exercises = self._session_nodes(session)
        exercise = exercises.get(exercise_id)
        if exercise is None:
            raise AppError(404, "practice_item_not_found", "该题目不属于本次练习")
        metadata = dict(session.source_metadata or {})
        hints = dict(metadata.get("hints") or {})
        counts = dict(metadata.get("hint_counts") or {})
        stored = [str(item) for item in (hints.get(exercise_id) or [])]
        text = stored[-1] if stored else ""
        source = "generated"
        if not text:
            text, source = self.exercise_service.hint_for_exercise(exercise)
            if not text:
                raise AppError(
                    409,
                    "practice_hint_unavailable",
                    "这道题暂时没有可用的提示：它既没有生成提示，也无法从节点描述推导。",
                )
            stored.append(text)
            hints[exercise_id] = stored
        counts[exercise_id] = int(counts.get(exercise_id) or 0) + 1
        metadata["hints"] = hints
        metadata["hint_counts"] = counts
        session.source_metadata = metadata
        self.db.commit()
        return PracticeHintView(
            exercise_id=exercise_id,
            hint=text,
            hint_count=int(counts[exercise_id]),
            source=source,  # type: ignore[arg-type]
        )

    def complete_session(
        self, session_id: str, *, abandon: bool = False
    ) -> PracticeSessionReportView:
        session = self.sessions.require(session_id, "practice session")
        if session.status == "abandoned":
            raise AppError(409, "practice_session_closed", "本次练习已放弃")
        if abandon:
            session.status = "abandoned"
            session.completed_at = utc_now()
            self.audit.record(
                actor_id=self.user_id,
                action="practice.session_abandoned",
                resource_type="practice_session",
                resource_id=session.id,
                details={"answered": int(session.completed_question_count or 0)},
            )
            self.db.commit()
            return PracticeSessionReportView(session=self._summary(session))
        if session.status != "completed":
            self._sync_counters(session)
            started = as_utc(session.started_at)
            if started is not None and not session.duration_seconds:
                session.duration_seconds = max(
                    0, int((utc_now() - started).total_seconds())
                )
            session.status = "completed"
            session.completed_at = utc_now()
            report = self._build_report(session)
            session.report_json = report.model_dump(mode="json")
            self.audit.record(
                actor_id=self.user_id,
                action="practice.session_completed",
                resource_type="practice_session",
                resource_id=session.id,
                details={
                    "planned": int(session.planned_question_count or 0),
                    "completed": int(session.completed_question_count or 0),
                    "first_try_correct": int(session.first_try_correct_count or 0),
                    "final_correct": int(session.final_correct_count or 0),
                },
            )
            self.db.commit()
            self.db.refresh(session)
            return report
        if session.report_json:
            return PracticeSessionReportView.model_validate(session.report_json)
        report = self._build_report(session)
        session.report_json = report.model_dump(mode="json")
        self.db.commit()
        return report

    # ------------------------------------------------------------------ #
    # session report
    # ------------------------------------------------------------------ #
    def _build_report(self, session: PracticeSession) -> PracticeSessionReportView:
        exercises = self._session_nodes(session)
        answers = self._session_answers(session.id)
        items = self._items(session, exercises=exercises, answers=answers)
        node_map = self._node_map()
        schedules = self._schedule_map()
        states = self._learning_state_map()
        snapshot = dict((session.source_metadata or {}).get("mastery_snapshot") or {})

        grouped: dict[str, list[PracticeSessionItemView]] = defaultdict(list)
        for item in items:
            grouped[item.node_id].append(item)

        consolidated: list[PracticeReportNodeView] = []
        attention: list[PracticeReportNodeView] = []
        mastery_changes: list[dict[str, Any]] = []
        misconceptions: list[dict[str, Any]] = []

        # Misconceptions come from every wrong attempt in this session — not only
        # from questions that are still wrong at the end — so a retried question
        # still contributes its real diagnosis.
        for exercise_id, records in answers.items():
            exercise = exercises.get(exercise_id)
            if exercise is None:
                continue
            node = node_map.get(exercise.node_id)
            for record in records:
                if record.is_correct:
                    continue
                evaluation = dict(record.evaluation_json or {})
                missing = [str(item) for item in evaluation.get("missing_points") or []]
                summary = (
                    f"遗漏：{'、'.join(missing[:3])}"
                    if missing
                    else (record.feedback or self._error_label(evaluation.get("error_type")))
                )
                misconceptions.append(
                    {
                        "node_id": exercise.node_id,
                        "node_label": node.label if node is not None else exercise.node_id,
                        "graph_id": node.graph_id if node is not None else None,
                        "summary": summary[:240],
                        "error_type": evaluation.get("error_type"),
                        "exercise_id": exercise_id,
                        "attempt_index": int(record.attempt_index or 1),
                    }
                )

        for node_id, node_items in grouped.items():
            node = node_map.get(node_id)
            label = node.label if node is not None else node_id
            schedule = schedules.get(node_id)
            state = states.get(node_id)
            before = dict(snapshot.get(node_id) or {})
            next_review_at = as_utc(schedule.next_review_at) if schedule is not None else None
            previous_next_review_at = (
                datetime.fromisoformat(before["next_review_at"])
                if before.get("next_review_at")
                else None
            )
            planned = len(node_items)
            unanswered = [item for item in node_items if item.state == "unanswered"]
            first_try = sum(
                1
                for item in node_items
                if item.attempts == 1 and item.state in {"correct", "partial"}
            )
            final = sum(1 for item in node_items if item.state in {"correct", "partial"})
            attempts = sum(item.attempts for item in node_items)
            missed: list[str] = []
            for item in node_items:
                missed.extend(item.missing_points)
            repeated = [
                point
                for point in dict.fromkeys(missed)
                if missed.count(point) >= 2
            ]
            retried = any(item.attempts > 1 for item in node_items)
            needs_retry = any(item.hint_count > 0 for item in node_items)
            schedule_extended = (
                next_review_at is not None
                and (
                    previous_next_review_at is None
                    or next_review_at > previous_next_review_at
                )
            )
            # 「已巩固」以学习报告的口径为准：窗口内存在"合格回忆"时间
            # （mastery_schedules.last_qualified_recall_at，由 _mark_success 写入）。
            # 会话报告的窗口 = 本次会话；额外要求本次没有仍未答对的题，避免同一节点
            # 同时出现在「已巩固」与「仍需加强」两个列表里。
            qualified_recall_at = (
                as_utc(schedule.last_qualified_recall_at) if schedule is not None else None
            )
            session_start = as_utc(session.started_at) or as_utc(session.created_at)
            qualified_recall = bool(
                qualified_recall_at is not None
                and session_start is not None
                and qualified_recall_at >= session_start
            )
            view_kwargs = {
                "node_id": node_id,
                "label": label,
                "graph_id": node.graph_id if node is not None else None,
                "planned": planned,
                "first_try_correct": first_try,
                "final_correct": final,
                "attempts": attempts,
                "next_review_at": next_review_at,
                "previous_next_review_at": previous_next_review_at,
                "mastery_before": before.get("mastery_score"),
                "mastery_after": float(state.mastery_score) if state is not None else None,
                "confidence_before": before.get("confidence"),
                "confidence_after": float(state.confidence) if state is not None else None,
            }
            still_wrong = any(item.state == "incorrect" for item in node_items)
            mastery_after = float(state.mastery_score) if state is not None else None
            mastery_before = before.get("mastery_score")
            stars_before = before.get("mastery_stars")
            stars_after = int(node.mastery_stars) if node is not None else None
            if (
                (mastery_before is None) != (mastery_after is None)
                or (
                    mastery_before is not None
                    and mastery_after is not None
                    and abs(mastery_after - mastery_before) > 1e-6
                )
                or (
                    stars_before is not None
                    and stars_after is not None
                    and stars_after != stars_before
                )
            ):
                mastery_changes.append(
                    {
                        "node_id": node_id,
                        "node_label": label,
                        "graph_id": node.graph_id if node is not None else None,
                        "mastery_before": mastery_before,
                        "mastery_after": mastery_after,
                        "stars_before": stars_before,
                        "stars_after": stars_after,
                        "changed": True,
                    }
                )
            if qualified_recall:
                # 与会话报告严格同源：只要本次产生了"合格回忆"（学习报告用的就是
                # 这个字段），这里就是「已巩固」，不再附加"本次首次全对"等额外门槛，
                # 否则同一节点可能在一个报告里已巩固、另一个报告里仍需加强。
                # 本次仍有未答对的题时如实标注（误区清单与逐题状态另有展示）。
                next_review_text = (
                    f"下一次复习：{next_review_at.astimezone(timezone.utc).strftime('%m月%d日')}"
                    if next_review_at is not None
                    else ""
                )
                if still_wrong:
                    next_review_text = (
                        f"{next_review_text}（注意：本次仍有题目未答对）"
                        if next_review_text
                        else "（注意：本次仍有题目未答对）"
                    )
                consolidated.append(
                    PracticeReportNodeView(
                        **view_kwargs,
                        requires_attention=False,
                        reason="本次产生了合格回忆",
                        detail=next_review_text,
                    )
                )
                continue
            if unanswered and len(unanswered) == planned:
                continue
            if still_wrong:
                reason = "仍有题目未答对"
                detail = (
                    f"连续遗漏：{'、'.join(repeated[:3])}"
                    if repeated
                    else (
                        next(
                            (
                                item.last_feedback
                                for item in reversed(node_items)
                                if item.state == "incorrect"
                            ),
                            "",
                        )
                        or "建议先看讲解补齐概念，再练同知识点的变式题。"
                    )
                )
            elif retried or needs_retry:
                reason = "首次未答对，重试后才正确"
                detail = (
                    f"本次 {first_try}/{planned} 首次正确，"
                    f"使用了 {sum(item.hint_count for item in node_items)} 次提示；"
                    "复习间隔只做较短延长，建议明天再做一次无选项主动回忆。"
                )
            elif not schedule_extended:
                reason = "本次未形成可延长的复习间隔"
                detail = "证据强度不足以延长间隔，保持原有复习节奏。"
            else:
                reason = "部分作答仍需复习"
                detail = f"本次 {first_try}/{planned} 首次正确。"
            if attempts >= planned + 2:
                detail = f"{detail}（本题组尝试 {attempts} 次，建议先回到图谱重新学习该节点）"
            attention.append(
                PracticeReportNodeView(
                    **view_kwargs,
                    requires_attention=True,
                    reason=reason,
                    detail=detail,
                    misconceptions=repeated,
                )
            )

        # Deduplicate misconception rows by (node, summary).
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for row in misconceptions:
            key = (str(row["node_id"]), str(row["summary"]))
            counts[key] += 1
        for row in misconceptions:
            key = (str(row["node_id"]), str(row["summary"]))
            if key in seen:
                continue
            seen.add(key)
            deduped.append({**row, "count": counts[key]})

        due_nodes: list[datetime] = []
        for node_id in grouped:
            schedule = schedules.get(node_id)
            if schedule is not None and schedule.next_review_at is not None:
                due_nodes.append(as_utc(schedule.next_review_at))  # type: ignore[arg-type]
        next_due = min(due_nodes) if due_nodes else None
        next_review_payload: dict[str, Any] = {
            "due_at": next_due.isoformat() if next_due is not None else None,
            "node_count": len(due_nodes),
        }
        if next_due is not None:
            pending_types = [
                item.question_type
                for item in items
                if item.node_id in grouped
            ]
            next_review_payload["estimated_minutes"] = self._estimate_minutes(
                pending_types[: max(1, len(grouped))], self._question_medians()
            )

        return PracticeSessionReportView(
            session=self._summary(session),
            consolidated=consolidated,
            attention=attention,
            mastery_changes=mastery_changes,
            misconceptions=deduped,
            next_review=next_review_payload,
            items=items,
        )

    @staticmethod
    def _error_label(error_type: str | None) -> str:
        return {
            "incomplete_selection": "多选漏选",
            "incorrect_selection": "多选错选",
            "wrong_judgement": "判断错误",
            "wrong_answer": "答案错误",
            "missing_points": "要点遗漏",
            "off_target": "偏离考点",
            "no_attempt": "未作答",
            "missing_answer_key": "缺少参考答案",
        }.get(str(error_type or ""), "作答错误")

    def session_report(self, session_id: str) -> PracticeSessionReportView:
        session = self.sessions.require(session_id, "practice session")
        if session.status in {"completed"} and session.report_json:
            return PracticeSessionReportView.model_validate(session.report_json)
        if session.status == "abandoned":
            return PracticeSessionReportView(session=self._summary(session))
        raise AppError(
            409,
            "practice_session_not_completed",
            "练习尚未完成，无法生成报告",
        )

    # ------------------------------------------------------------------ #
    # wrong book
    # ------------------------------------------------------------------ #
    def wrong_book(self, *, limit: int = 50) -> WrongBookView:
        history = self._answer_history()
        node_map = self._node_map()
        wrong_by_exercise: dict[str, dict[str, Any]] = {}
        per_node: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "exercises": set(),
                "attempts": 0,
                "missing": [],
                "error_types": [],
                "recent": [],
                "last_attempt_at": None,
            }
        )
        for answer, node_id in history:
            bucket = per_node[node_id]
            bucket["attempts"] += 1
            if answer.created_at and (
                bucket["last_attempt_at"] is None
                or (as_utc(answer.created_at) or utc_now())
                > (as_utc(bucket["last_attempt_at"]) or utc_now())
            ):
                bucket["last_attempt_at"] = answer.created_at
            if answer.is_correct:
                continue
            bucket["exercises"].add(answer.exercise_id)
            evaluation = dict(answer.evaluation_json or {})
            for point in evaluation.get("missing_points") or []:
                bucket["missing"].append(str(point))
            if evaluation.get("error_type"):
                bucket["error_types"].append(str(evaluation["error_type"]))
            entry = wrong_by_exercise.setdefault(
                answer.exercise_id,
                {
                    "node_id": node_id,
                    "wrong_count": 0,
                    "attempt_count": 0,
                    "last_wrong_at": None,
                    "last_feedback": "",
                    "error_type": None,
                },
            )
            entry["wrong_count"] += 1
            if entry["last_wrong_at"] is None or (
                as_utc(answer.created_at) or utc_now()
            ) > (as_utc(entry["last_wrong_at"]) or utc_now()):
                entry["last_wrong_at"] = answer.created_at
                entry["last_feedback"] = answer.feedback
                entry["error_type"] = evaluation.get("error_type")
        for answer, node_id in history:
            if answer.exercise_id in wrong_by_exercise:
                wrong_by_exercise[answer.exercise_id]["attempt_count"] += 1
                per_node[node_id]["recent"].append(
                    (as_utc(answer.created_at) or utc_now(), bool(answer.is_correct))
                )

        exercise_map = {
            item.id: item
            for item in self.db.scalars(
                self.exercises.query().where(Exercise.id.in_(list(wrong_by_exercise.keys())))
            ).all()
        } if wrong_by_exercise else {}
        nodes: list[WrongBookNodeView] = []
        per_node_exercises: dict[str, list[WrongBookExerciseView]] = defaultdict(list)
        for exercise_id, entry in wrong_by_exercise.items():
            exercise = exercise_map.get(exercise_id)
            if exercise is None:
                continue
            per_node_exercises[entry["node_id"]].append(
                WrongBookExerciseView(
                    exercise_id=exercise_id,
                    node_id=entry["node_id"],
                    question_type=exercise.question_type,
                    prompt=exercise.prompt,
                    wrong_count=int(entry["wrong_count"]),
                    attempt_count=int(entry["attempt_count"]),
                    last_wrong_at=entry["last_wrong_at"],
                    last_feedback=entry["last_feedback"],
                    error_type=entry["error_type"],
                )
            )
        for node_id, bucket in per_node.items():
            wrong_exercises = per_node_exercises.get(node_id) or []
            if not wrong_exercises:
                continue
            node = node_map.get(node_id)
            recent = [
                flag
                for _, flag in sorted(bucket["recent"], key=lambda item: item[0])[-5:]
            ]
            patterns: list[str] = []
            missing_counts: dict[str, int] = defaultdict(int)
            for point in bucket["missing"]:
                missing_counts[point] += 1
            patterns.extend(
                f"反复遗漏：{point}"
                for point, count in sorted(
                    missing_counts.items(), key=lambda item: item[1], reverse=True
                )
                if count >= 2
            )
            error_counts: dict[str, int] = defaultdict(int)
            for error_type in bucket["error_types"]:
                error_counts[error_type] += 1
            patterns.extend(
                f"重复问题：{self._error_label(error_type)}（{count} 次）"
                for error_type, count in sorted(
                    error_counts.items(), key=lambda item: item[1], reverse=True
                )
                if count >= 2
            )
            if len(wrong_exercises) >= 3:
                patterns.append(f"同一知识点累计 {len(wrong_exercises)} 道错题")
            nodes.append(
                WrongBookNodeView(
                    node_id=node_id,
                    label=node.label if node is not None else node_id,
                    graph_id=node.graph_id if node is not None else None,
                    wrong_question_count=len(wrong_exercises),
                    attempt_count=int(bucket["attempts"]),
                    recent_results=recent,
                    last_attempt_at=bucket["last_attempt_at"],
                    repeated_patterns=patterns,
                    exercises=sorted(
                        wrong_exercises,
                        key=lambda item: item.last_wrong_at or utc_now(),
                        reverse=True,
                    )[:10],
                )
            )
        nodes.sort(
            key=lambda item: (item.wrong_question_count, item.attempt_count), reverse=True
        )
        return WrongBookView(
            generated_at=utc_now(),
            total_wrong_questions=len(wrong_by_exercise),
            node_count=len(nodes),
            nodes=nodes[:limit],
        )

    # ------------------------------------------------------------------ #
    # learning report
    # ------------------------------------------------------------------ #
    def learning_report(
        self,
        *,
        window: str = "7d",
        tz_offset_minutes: int = 0,
    ) -> PracticeLearningReportView:
        now = utc_now()
        history = self._answer_history()
        if window == "30d":
            days = 30
            start = now - timedelta(days=30)
        elif window == "all":
            first = min((as_utc(answer.created_at) for answer, _ in history), default=None)
            # "全部"就是全部：按真实跨度取窗口，不再截到 90 天（截断会让
            # answered/正确率/趋势 与"全部"这个标签互相矛盾）。
            days = (
                max(
                    1,
                    (
                        local_day(now, tz_offset_minutes)
                        - local_day(first, tz_offset_minutes)
                    ).days
                    + 1,
                )
                if first is not None
                else 7
            )
            start = now - timedelta(days=days)
        else:
            days = 7
            start = now - timedelta(days=7)

        first_correct = 0
        first_total = 0
        answered = 0
        duration_ms = 0
        session_ids: set[str] = set()
        per_exercise: dict[str, list[tuple[datetime, AnswerRecord, str]]] = defaultdict(list)
        node_first: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        node_final: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        node_recent: dict[str, list[tuple[datetime, bool]]] = defaultdict(list)
        for answer, node_id in history:
            created = as_utc(answer.created_at) or now
            if created < start:
                continue
            answered += 1
            duration_ms += max(0, int(answer.duration_ms or 0))
            if answer.practice_session_id:
                session_ids.add(answer.practice_session_id)
            if int(answer.attempt_index or 1) <= 1:
                first_total += 1
                node_first[node_id][1] += 1
                if answer.is_correct:
                    first_correct += 1
                    node_first[node_id][0] += 1
            per_exercise[answer.exercise_id].append((created, answer, node_id))
            node_recent[node_id].append((created, bool(answer.is_correct)))
        final_correct = 0
        for items in per_exercise.values():
            _created, last, node_id = max(items, key=lambda item: item[0])
            node_final[node_id][1] += 1
            if last.is_correct:
                final_correct += 1
                node_final[node_id][0] += 1

        schedules = self._schedule_map()
        states = self._learning_state_map()
        node_map = self._node_map()
        stats_by_node = self._node_practice_stats(history)
        consolidated = 0
        for node_id, schedule in schedules.items():
            recall = as_utc(schedule.last_qualified_recall_at)
            if recall is not None and recall >= start:
                consolidated += 1
        attention_nodes = [
            node_id
            for node_id in set(list(states.keys()) + list(schedules.keys()))
            if (
                (states.get(node_id) is not None and states[node_id].status in {"weak", "needs_review"})
                or int((stats_by_node.get(node_id) or {}).get("consecutive_wrong") or 0) >= CONSECUTIVE_WRONG_LIMIT
                or (
                    node_map.get(node_id) is not None
                    and node_map[node_id].evidence_state == "conflicted"
                )
            )
        ]

        nodes: list[PracticeNodePerformanceView] = []
        for node_id, values in node_first.items():
            node = node_map.get(node_id)
            state = states.get(node_id)
            final_values = node_final.get(node_id, [0, 0])
            node_stats = stats_by_node.get(node_id) or {}
            status = state.status if state is not None else (
                node.retrieval_state if node is not None else "unseen"
            )
            nodes.append(
                PracticeNodePerformanceView(
                    node_id=node_id,
                    label=node.label if node is not None else node_id,
                    graph_id=node.graph_id if node is not None else None,
                    answered=int(values[1]),
                    first_try_accuracy=accuracy(values[0], values[1]),
                    final_accuracy=accuracy(final_values[0], final_values[1]),
                    recent_results=[
                        flag
                        for _, flag in sorted(
                            node_recent.get(node_id, []), key=lambda item: item[0]
                        )[-5:]
                    ],
                    status=status,
                    status_label=self._status_label(status, node_stats),
                    misconceptions=[
                        str(item.get("summary"))
                        for item in (
                            state.misconceptions_json if state is not None else []
                        )[:3]
                    ],
                    consecutive_wrong=int(node_stats.get("consecutive_wrong") or 0),
                )
            )
        nodes.sort(
            key=lambda item: (
                item.first_try_accuracy if item.first_try_accuracy is not None else 1.0
            )
        )

        return PracticeLearningReportView(
            window=window,
            window_start=start,
            generated_at=now,
            answered=answered,
            sessions=len(session_ids),
            minutes=int(round(duration_ms / 60_000)),
            first_try_correct=first_correct,
            final_correct=final_correct,
            first_try_accuracy=accuracy(first_correct, first_total),
            final_accuracy=accuracy(final_correct, len(per_exercise)),
            consolidated_node_count=consolidated,
            attention_node_count=len(set(attention_nodes)),
            trend=self._trend(
                days=days,
                now=now,
                tz_offset_minutes=tz_offset_minutes,
                history=history,
            ),
            nodes=nodes,
            delayed_recall=self._delayed_recall(history, now=now),
            calendar=self._trend(
                days=30,
                now=now,
                tz_offset_minutes=tz_offset_minutes,
                history=history,
            ),
        )

    def _status_label(self, status: str, node_stats: dict[str, Any]) -> str:
        if int(node_stats.get("consecutive_wrong") or 0) >= CONSECUTIVE_WRONG_LIMIT:
            return "需要关注"
        return {
            # LearningNodeState.status
            "mastered": "已掌握",
            "familiar": "较熟练",
            "learning": "学习中",
            "weak": "需要关注",
            "needs_review": "需要复习",
            "unseen": "尚未练习",
            # GraphNode.retrieval_state：没有该成员的掌握状态行时（例如历史作答或
            # 状态行属于家庭里其他成员）会回落到这些值，必须同样给中文，不能把内部码
            # 直接显示给用户；文案与图谱画布保持一致。
            "unverified": "未学习",
            "due": "待复习",
            "due_soon": "即将复习",
            "fresh": "掌握稳定",
            "relearning": "重新学习",
        }.get(status, "其他状态")

    def _delayed_recall(
        self, history: Sequence[tuple[AnswerRecord, str]], *, now: datetime
    ) -> PracticeDelayedRecallView:
        """Measure recall success on answers that follow an earlier answer by 24h/7d.

        This is computed strictly from stored attempts; when the workspace has
        too few cross-day follow-ups the metric is reported as unavailable
        instead of being estimated.
        """

        by_node: dict[str, list[AnswerRecord]] = defaultdict(list)
        for answer, node_id in history:
            by_node[node_id].append(answer)
        samples = {hours: [0, 0] for hours in DELAYED_RECALL_WINDOWS_HOURS}
        for items in by_node.values():
            ordered = sorted(items, key=lambda item: as_utc(item.created_at) or now)
            for index, answer in enumerate(ordered):
                created = as_utc(answer.created_at) or now
                previous = [
                    item
                    for item in ordered[:index]
                    if (created - (as_utc(item.created_at) or created)).total_seconds()
                    >= 24 * 3_600
                ]
                if not previous:
                    continue
                for hours in DELAYED_RECALL_WINDOWS_HOURS:
                    window_previous = [
                        item
                        for item in previous
                        if (created - (as_utc(item.created_at) or created)).total_seconds()
                        >= hours * 3_600
                    ]
                    if not window_previous:
                        continue
                    samples[hours][1] += 1
                    if answer.is_correct:
                        samples[hours][0] += 1
        total_samples = samples[24][1]
        if total_samples < DELAYED_RECALL_MIN_SAMPLE:
            return PracticeDelayedRecallView(
                available=False,
                reason=(
                    f"样本不足（只有 {total_samples} 次跨天复习记录，"
                    f"至少需要 {DELAYED_RECALL_MIN_SAMPLE} 次）"
                ),
                sample_size=total_samples,
            )
        day7 = samples[24 * 7]
        return PracticeDelayedRecallView(
            available=True,
            sample_size=total_samples,
            recall_24h=accuracy(samples[24][0], samples[24][1]),
            recall_7d=accuracy(day7[0], day7[1]) if day7[1] else None,
        )
