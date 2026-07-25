from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.domain.schemas.common import ORMModel


AnswerText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
]
AnswerSelections = Annotated[list[AnswerText], Field(min_length=1, max_length=20)]


class EvidenceCreateRequest(BaseModel):
    node_id: str
    source_type: Literal["conversation", "exercise", "file", "user_correction", "artifact"]
    summary: str = Field(min_length=1, max_length=4_000)
    confidence: float = Field(default=0.5, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    file_id: str | None = Field(default=None, min_length=1, max_length=36)
    locator: str = Field(default="", max_length=255)

    @model_validator(mode="after")
    def validate_file_source(self):
        if self.source_type == "file" and self.file_id is None:
            raise ValueError("file evidence requires file_id")
        if self.source_type != "file" and (self.file_id is not None or self.locator):
            raise ValueError("file_id and locator are only valid for file evidence")
        return self


class EvidenceDecisionRequest(BaseModel):
    decision: Literal["accepted", "rejected"]
    reason: str = Field(default="", max_length=1_000)


class EvidenceView(ORMModel):
    id: str
    workspace_id: str
    node_id: str
    source_type: str
    summary: str
    confidence: float
    status: str
    metadata_json: dict[str, Any]
    created_at: datetime


class MasteryNodeView(BaseModel):
    node_id: str
    label: str
    mastery_stars: int
    retrieval_state: str
    evidence_state: str
    attention_state: str
    accepted_evidence_count: int
    next_review_at: datetime | None = None
    # Practice stats are explanatory only — never used as a mastery percentage.
    exercise_attempt_count: int = 0
    exercise_correct_count: int = 0


class MasteryGoalOccurrenceView(BaseModel):
    goal_id: str
    goal_title: str
    graph_id: str
    graph_title: str
    graph_status: str


class MasteryAlignmentView(BaseModel):
    node_id: str
    label: str
    external_concept_id: str | None
    occurrences: list[MasteryGoalOccurrenceView]
    explanation: str


class CapabilityReportSummary(BaseModel):
    concept_count: int
    accepted_evidence_count: int
    mastered_concept_count: int
    review_due_count: int
    exercise_attempt_count: int = 0
    exercise_correct_count: int = 0


class CapabilityReportView(BaseModel):
    workspace_id: str
    generated_at: datetime
    summary: CapabilityReportSummary
    nodes: list[MasteryNodeView]


ExerciseQuestionType = Literal[
    "single_choice",
    "multiple_choice",
    "true_false",
    "fill_blank",
    "short_answer",
    "mixed",
]


class ExerciseGenerateRequest(BaseModel):
    node_id: str
    question_type: ExerciseQuestionType = "mixed"
    count: int = Field(default=5, ge=1, le=10)
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    collection_ids: list[str] = Field(default_factory=list, max_length=10)
    difficulty: Literal["easy", "medium", "hard"] = "medium"


class ModelGeneratedExerciseItem(BaseModel):
    """Structured model output for one exercise. Server-only — never returned to clients."""

    question_type: Literal[
        "single_choice",
        "multiple_choice",
        "true_false",
        "fill_blank",
        "short_answer",
    ]
    prompt: str = Field(min_length=1, max_length=4_000)
    options: list[str] = Field(default_factory=list, max_length=12)
    answer_key: str | list[str] = Field(min_length=1)
    explanation: str = Field(default="", max_length=4_000)
    rubric_points: list[str] = Field(default_factory=list, max_length=12)
    source_chunk_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("options")
    @classmethod
    def normalize_options(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("answer_key")
    @classmethod
    def normalize_answer_key(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list):
            cleaned = [item.strip() for item in value if item and str(item).strip()]
            if not cleaned:
                raise ValueError("answer_key list must not be empty")
            return cleaned
        text = value.strip()
        if not text:
            raise ValueError("answer_key must not be empty")
        return text


class ModelGeneratedExerciseSet(BaseModel):
    items: list[ModelGeneratedExerciseItem] = Field(min_length=1, max_length=10)


class ExerciseView(ORMModel):
    id: str
    workspace_id: str
    node_id: str
    question_type: str
    prompt: str
    options: list[str]
    explanation: str
    difficulty: str = "medium"
    generation_batch_id: str | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class ExerciseBankItemView(ExerciseView):
    attempt_count: int = 0
    correct_count: int = 0
    last_is_correct: bool | None = None


class AnswerRequest(BaseModel):
    answer: AnswerText | AnswerSelections

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list) and len({item.casefold() for item in value}) != len(value):
            raise ValueError("answer selections must not contain duplicates")
        return value


class AnswerResult(BaseModel):
    answer_record_id: str
    is_correct: bool
    feedback: str
    evidence_signal_id: str
    mastery_star_awarded: bool = False


class MasteryScheduleView(ORMModel):
    id: str
    workspace_id: str
    node_id: str
    next_review_at: datetime | None
    last_qualified_recall_at: datetime | None
    pending_message_count: int
    active_rule_version: str
    updated_at: datetime


class MasteryReviewRunRequest(BaseModel):
    trigger: Literal["manual", "session_closed", "idle", "weekend", "periodic"] = "manual"
    node_ids: list[str] = Field(default_factory=list, max_length=50)


class MasteryReviewJobView(ORMModel):
    id: str
    workspace_id: str
    trigger: str
    status: str
    dedupe_key: str | None
    attempt_count: int
    started_at: datetime | None
    completed_at: datetime | None
    last_error: str
    node_ids: list[str]
    report: dict[str, Any]
    created_at: datetime


class MasterySessionStateView(ORMModel):
    id: str
    workspace_id: str
    session_id: str
    pending_message_count: int
    pending_node_ids: list[str]
    pending_node_counts: dict[str, int]
    activity_version: int
    processed_version: int
    enqueued_version: int
    last_message_id: str | None
    last_activity_at: datetime
    idle_due_at: datetime | None
    last_processed_at: datetime | None
    updated_at: datetime


class MasterySchedulerTickView(BaseModel):
    workspace_id: str
    recovered_job_ids: list[str]
    enqueued_job_ids: list[str]
    completed_job_ids: list[str]
    failed_job_ids: list[str]
    threshold_session_ids: list[str]
    idle_session_ids: list[str]
    due_node_ids: list[str]
