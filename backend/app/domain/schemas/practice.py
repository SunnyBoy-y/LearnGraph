"""Schemas for the Practice & Review center.

Every field here is a projection of durable records (Exercise, AnswerRecord,
Evidence, MasterySchedule, LearningNodeState). Nothing in this file may be
filled with placeholder numbers: when real data is missing the field is ``None``
or an empty list and the UI renders an empty state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.domain.schemas.learning import AnswerSelections

AnswerText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)]

PracticeSessionMode = Literal["scheduled", "custom", "wrong_book", "node", "material"]
PracticeSessionStatus = Literal["planned", "active", "completed", "abandoned"]
PracticeItemState = Literal["unanswered", "correct", "partial", "incorrect"]
PracticeReportWindow = Literal["7d", "30d", "all"]


# --------------------------------------------------------------------------- #
# 今日练习计划
# --------------------------------------------------------------------------- #
class PracticePlanItemView(BaseModel):
    node_id: str
    label: str
    graph_id: str | None = None
    reason_code: str
    reason: str
    detail: str = ""
    planned_questions: int = 0
    priority: float = 0.0
    available_exercises: int = 0
    due_in_days: int | None = None
    overdue_days: int | None = None
    last_practiced_at: datetime | None = None
    next_review_at: datetime | None = None
    retrieval_state: str = "unverified"
    evidence_state: str = "none"
    mastery_score: float | None = None
    confidence: float | None = None
    consecutive_wrong: int = 0


class PracticeTodayPlanView(BaseModel):
    mode: Literal["scheduled", "maintenance", "empty"] = "scheduled"
    message: str = ""
    question_count: int = 0
    estimated_minutes: int = 0
    node_count: int = 0
    node_ids: list[str] = Field(default_factory=list)
    question_types: list[str] = Field(default_factory=list)
    items: list[PracticePlanItemView] = Field(default_factory=list)
    # 缺少题库时是否可以现场出题（工作区已有可用的远程结构化模型）。
    provider_available: bool = False
    # 计划被模型侧问题挡住（没有可用模型，或选中的模型不可用）。UI 据此给出
    # 直达「设置 → 功能模型」的入口，而不是只留一句错误文案让用户自己找。
    model_setting_required: bool = False
    # Nodes the scheduler wanted to include but cannot practise right now
    # (no exercise and no remote model to generate one).
    skipped_nodes: list[PracticePlanItemView] = Field(default_factory=list)


class PracticeStatsView(BaseModel):
    pending_node_count: int = 0
    due_node_count: int = 0
    weak_node_count: int = 0
    attention_node_count: int = 0
    # 已巩固：存在真实“合格回忆”时间（复习间隔被延长过）的知识点。
    consolidated_node_count: int = 0
    estimated_minutes: int = 0
    answered_7d: int = 0
    answered_7d_previous: int = 0
    first_try_correct_7d: int = 0
    first_try_accuracy_7d: float | None = None
    first_try_accuracy_previous_7d: float | None = None
    first_try_accuracy_delta_7d: float | None = None


class PracticeTrendPointView(BaseModel):
    date: str
    answered: int = 0
    sessions: int = 0
    minutes: int = 0
    first_try_accuracy: float | None = None
    final_accuracy: float | None = None


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #
class PracticeSessionSummaryView(BaseModel):
    id: str
    mode: str
    status: str
    title: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    duration_seconds: int = 0
    planned_question_count: int = 0
    completed_question_count: int = 0
    total_attempt_count: int = 0
    first_try_correct_count: int = 0
    final_correct_count: int = 0
    first_try_accuracy: float | None = None
    final_accuracy: float | None = None
    node_count: int = 0
    node_labels: list[str] = Field(default_factory=list)


class PracticeSessionItemView(BaseModel):
    position: int
    exercise_id: str
    node_id: str
    node_label: str
    graph_id: str | None = None
    question_type: str
    prompt: str
    options: list[str] = Field(default_factory=list)
    difficulty: str = "medium"
    explanation_available: bool = False
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    state: PracticeItemState = "unanswered"
    attempts: int = 0
    hint_count: int = 0
    hints: list[str] = Field(default_factory=list)
    last_answer: str | None = None
    last_feedback: str = ""
    score_ratio: float | None = None
    covered_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    error_type: str | None = None
    answered_at: datetime | None = None
    duration_ms: int = 0


class PracticeSessionView(BaseModel):
    session: PracticeSessionSummaryView
    items: list[PracticeSessionItemView] = Field(default_factory=list)
    current_position: int = 0
    current_exercise_id: str | None = None
    remaining_count: int = 0
    report_available: bool = False
    plan: PracticeTodayPlanView | None = None
    # 组卷时遇到的问题（例如某个知识点出题失败）。必须回传，否则少题会变成静默行为。
    warnings: list[str] = Field(default_factory=list)
    # 本场组卷是否因模型侧问题少题（换了模型就能重试成功）：UI 用它在
    # 警告块旁给出直达「设置 → 功能模型」的入口。
    model_setting_required: bool = False


class PracticeSessionCreateRequest(BaseModel):
    mode: PracticeSessionMode = "scheduled"
    node_ids: list[str] = Field(default_factory=list, max_length=30)
    question_type: Literal[
        "single_choice",
        "multiple_choice",
        "true_false",
        "fill_blank",
        "short_answer",
        "mixed",
    ] = "mixed"
    count: int | None = Field(default=None, ge=1, le=30)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    collection_ids: list[str] = Field(default_factory=list, max_length=10)
    # Free practise can reuse an existing generated batch instead of paying for
    # a new generation.
    generation_batch_id: str | None = None


class PracticeAnswerRequest(BaseModel):
    exercise_id: str
    answer: AnswerText | AnswerSelections
    duration_ms: int = Field(default=0, ge=0, le=3_600_000)


class PracticeAnswerResult(BaseModel):
    answer_record_id: str
    exercise_id: str
    node_id: str
    node_label: str
    graph_id: str | None = None
    is_correct: bool
    score_ratio: float = 1.0
    covered_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    error_type: str | None = None
    feedback: str = ""
    # Only returned after a correct answer (or after an explicit reveal).
    explanation: str | None = None
    attempt_index: int = 1
    is_first_attempt: bool = True
    is_first_try_correct: bool = False
    hint_count: int = 0
    evidence_signal_id: str
    mastery_star_awarded: bool = False
    next_review_at: datetime | None = None
    schedule_reason: str = ""
    retry_allowed: bool = False
    reveal_available: bool = False
    session_completed: bool = False
    remaining_count: int = 0


class PracticeRevealView(BaseModel):
    exercise_id: str
    explanation: str = ""
    answer_display: str = ""
    covered_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)


class PracticeHintView(BaseModel):
    exercise_id: str
    hint: str
    hint_count: int = 0
    source: Literal["generated", "source_material", "node_description"] = "node_description"


class PracticeSessionReportView(BaseModel):
    session: PracticeSessionSummaryView
    consolidated: list["PracticeReportNodeView"] = Field(default_factory=list)
    attention: list["PracticeReportNodeView"] = Field(default_factory=list)
    mastery_changes: list[dict[str, Any]] = Field(default_factory=list)
    misconceptions: list[dict[str, Any]] = Field(default_factory=list)
    next_review: dict[str, Any] = Field(default_factory=dict)
    items: list[PracticeSessionItemView] = Field(default_factory=list)


class PracticeReportNodeView(BaseModel):
    node_id: str
    label: str
    graph_id: str | None = None
    planned: int = 0
    first_try_correct: int = 0
    final_correct: int = 0
    attempts: int = 0
    requires_attention: bool = False
    reason: str = ""
    detail: str = ""
    next_review_at: datetime | None = None
    previous_next_review_at: datetime | None = None
    mastery_before: float | None = None
    mastery_after: float | None = None
    confidence_before: float | None = None
    confidence_after: float | None = None
    misconceptions: list[str] = Field(default_factory=list)


PracticeSessionReportView.model_rebuild()


# --------------------------------------------------------------------------- #
# 错题本 / 学习报告
# --------------------------------------------------------------------------- #
class WrongBookExerciseView(BaseModel):
    exercise_id: str
    node_id: str
    question_type: str
    prompt: str
    wrong_count: int = 0
    attempt_count: int = 0
    last_wrong_at: datetime | None = None
    last_feedback: str = ""
    error_type: str | None = None


class WrongBookNodeView(BaseModel):
    node_id: str
    label: str
    graph_id: str | None = None
    wrong_question_count: int = 0
    attempt_count: int = 0
    recent_results: list[bool] = Field(default_factory=list)
    last_attempt_at: datetime | None = None
    repeated_patterns: list[str] = Field(default_factory=list)
    exercises: list[WrongBookExerciseView] = Field(default_factory=list)


class WrongBookView(BaseModel):
    generated_at: datetime
    total_wrong_questions: int = 0
    node_count: int = 0
    nodes: list[WrongBookNodeView] = Field(default_factory=list)


class PracticeNodePerformanceView(BaseModel):
    node_id: str
    label: str
    graph_id: str | None = None
    answered: int = 0
    first_try_accuracy: float | None = None
    final_accuracy: float | None = None
    recent_results: list[bool] = Field(default_factory=list)
    status: str = "unseen"
    status_label: str = ""
    misconceptions: list[str] = Field(default_factory=list)
    consecutive_wrong: int = 0


class PracticeDelayedRecallView(BaseModel):
    available: bool = False
    reason: str = ""
    sample_size: int = 0
    recall_24h: float | None = None
    recall_7d: float | None = None


class PracticeLearningReportView(BaseModel):
    window: str
    window_start: datetime | None = None
    generated_at: datetime
    answered: int = 0
    sessions: int = 0
    minutes: int = 0
    first_try_correct: int = 0
    final_correct: int = 0
    first_try_accuracy: float | None = None
    final_accuracy: float | None = None
    consolidated_node_count: int = 0
    attention_node_count: int = 0
    trend: list[PracticeTrendPointView] = Field(default_factory=list)
    nodes: list[PracticeNodePerformanceView] = Field(default_factory=list)
    delayed_recall: PracticeDelayedRecallView = Field(default_factory=PracticeDelayedRecallView)
    calendar: list[PracticeTrendPointView] = Field(default_factory=list)


class PracticeOverviewView(BaseModel):
    generated_at: datetime
    stats: PracticeStatsView = Field(default_factory=PracticeStatsView)
    today_plan: PracticeTodayPlanView = Field(default_factory=PracticeTodayPlanView)
    focus_nodes: list[PracticePlanItemView] = Field(default_factory=list)
    recent_session: PracticeSessionSummaryView | None = None
    active_session: PracticeSessionSummaryView | None = None
    trend: list[PracticeTrendPointView] = Field(default_factory=list)
