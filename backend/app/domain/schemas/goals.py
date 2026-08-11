from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from app.domain.schemas.common import ORMModel


class GoalClarifyRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=10_000)
    file_ids: list[str] = Field(default_factory=list, max_length=20)
    graph_context_ids: list[str] = Field(default_factory=list, max_length=8)
    # Optional model selection so the Goal wizard uses the same provider/model
    # the user picked in the chat header instead of an implicit workspace default.
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    thinking_mode: Literal["off", "low", "medium", "high", "xhigh"] | None = None


class ClarificationQuestion(BaseModel):
    key: str
    prompt: str
    options: list[str] = Field(default_factory=list)
    required: bool = False
    reason: str = ""
    input_type: str = "single_choice"
    allow_custom: bool = True
    allow_skip: bool = True
    graph_impact: str = "nodes"
    default_assumption: str | None = None


PlannerActionType = Literal["learn", "review", "practice", "assessment"]


class GoalAvailability(BaseModel):
    """Explicit capacity facts for one goal's action plan."""

    model_config = ConfigDict(extra="forbid")

    minutes_per_day: int | None = Field(default=None, ge=15, le=1_440)
    days_per_week: int | None = Field(default=None, ge=1, le=7)

    @field_validator("minutes_per_day", "days_per_week")
    @classmethod
    def supplied_values_cannot_be_null(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("supplied availability fields cannot be null")
        return value

    @model_serializer
    def serialize_supplied_values(self) -> dict[str, int]:
        return {
            key: value
            for key, value in {
                "minutes_per_day": self.minutes_per_day,
                "days_per_week": self.days_per_week,
            }.items()
            if value is not None
        }


AVAILABILITY_FIELDS = frozenset({"minutes_per_day", "days_per_week"})
PREFERENCES_FIELDS = frozenset({"preferred_action_types", "session_minutes"})


def sanitize_goal_nested_dict(
    value: Any,
    *,
    allowed_fields: frozenset[str],
) -> dict[str, Any]:
    """Drop unknown keys and null values from a legacy/corrupt nested dict.

    Historical schema versions wrote extra availability keys (e.g.
    ``weekly_hours``) and the DB may still hold them. Strict input schemas
    (extra="forbid" + non-null validators) must stay strict for new writes, so
    read/write paths sanitize the stored JSON before validation instead of
    weakening the schemas. This mirrors the T1-3 corrupt-JSON tolerance for
    GoalView columns.
    """
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in allowed_fields and item is not None
    }


class GoalPreferences(BaseModel):
    """User choices that can break ties, never fabricate mastery."""

    model_config = ConfigDict(extra="forbid")

    preferred_action_types: list[PlannerActionType] | None = Field(
        default=None,
        max_length=4,
    )
    session_minutes: int | None = Field(default=None, ge=15, le=240)

    @field_validator("preferred_action_types")
    @classmethod
    def unique_action_types(
        cls,
        values: list[PlannerActionType] | None,
    ) -> list[PlannerActionType]:
        if values is None:
            raise ValueError("preferred_action_types cannot be null")
        return list(dict.fromkeys(values))

    @field_validator("session_minutes")
    @classmethod
    def session_minutes_cannot_be_null(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("session_minutes cannot be null")
        return value

    @model_serializer
    def serialize_supplied_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if self.preferred_action_types is not None:
            values["preferred_action_types"] = self.preferred_action_types
        if self.session_minutes is not None:
            values["session_minutes"] = self.session_minutes
        return values


class GoalAvailabilityUpdate(GoalAvailability):
    pass


class GoalPreferencesUpdate(GoalPreferences):
    pass


class GoalView(ORMModel):
    id: str
    workspace_id: str
    title: str
    raw_prompt: str
    status: str
    intent: str
    time_limit: str
    target_weight: int
    deadline_at: datetime | None
    availability: GoalAvailability
    preferences: GoalPreferences
    desired_outcome: str
    constraints: dict[str, Any]
    assumptions: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _tolerate_corrupt_json_columns(cls, data: Any) -> Any:
        """T1-3: tolerate one corrupted JSON column instead of failing the list.

        A single row whose JSON columns were written as plain strings (legacy
        migration, partial write, manual DB edit) previously made the whole
        goals list / dashboard 500. Coerce such cells to their schema defaults
        so the row renders with empty structured fields and the rest of the
        list stays usable.
        """
        if not isinstance(data, dict):
            # Pydantic ORMModel path passes the ORM instance; project its
            # fields so corrupt JSON columns can be corrected before validation.
            projected = {}
            for field in ("id", "workspace_id", "title", "raw_prompt", "status", "intent", "time_limit", "target_weight", "deadline_at", "availability", "preferences", "desired_outcome", "constraints", "assumptions", "created_at", "updated_at"):
                try:
                    projected[field] = getattr(data, field)
                except AttributeError:
                    continue
            data = projected
        for key in ("availability", "preferences", "constraints", "assumptions"):
            value = data.get(key)
            if value is None or isinstance(value, str) or isinstance(value, (int, float, bool)):
                data[key] = (
                    {}
                    if key in ("constraints",)
                    else [] if key == "assumptions" else {}
                )
        # Sanitize legacy nested dicts: unknown keys (e.g. weekly_hours from an
        # older schema) and null values must not fail strict GoalAvailability /
        # GoalPreferences validation and 500 the whole goals list.
        data["availability"] = sanitize_goal_nested_dict(
            data.get("availability"),
            allowed_fields=AVAILABILITY_FIELDS,
        )
        data["preferences"] = sanitize_goal_nested_dict(
            data.get("preferences"),
            allowed_fields=PREFERENCES_FIELDS,
        )
        return data


class GoalClarifyResponse(BaseModel):
    goal: GoalView
    questions: list[ClarificationQuestion]
    provider: str = "local_rule_based"
    remote_model_used: bool = False


class GoalConfirmRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    intent: str = Field(default="", max_length=240)
    time_limit: str = Field(default="", max_length=120)
    target_weight: int = Field(default=50, ge=1, le=100)
    deadline_at: datetime | None = None
    availability: GoalAvailability = Field(default_factory=GoalAvailability)
    preferences: GoalPreferences = Field(default_factory=GoalPreferences)
    desired_outcome: str = Field(default="", max_length=4_000)
    constraints: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("deadline_at")
    @classmethod
    def deadline_requires_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone offset")
        return value


class GoalPlanningUpdate(BaseModel):
    """Partial update for planning facts after a goal has been published."""

    model_config = ConfigDict(extra="forbid")

    target_weight: int | None = Field(default=None, ge=1, le=100)
    deadline_at: datetime | None = None
    availability: GoalAvailabilityUpdate | None = None
    preferences: GoalPreferencesUpdate | None = None

    @field_validator("target_weight", "availability", "preferences")
    @classmethod
    def supplied_planning_values_cannot_be_null(cls, value):
        if value is None:
            raise ValueError("supplied planning fields cannot be null")
        return value

    @field_validator("deadline_at")
    @classmethod
    def deadline_requires_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone offset")
        return value


class CandidateGraphRequest(BaseModel):
    seed_concepts: list[str] = Field(default_factory=list, max_length=24)
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    thinking_mode: Literal["off", "low", "medium", "high", "xhigh"] | None = None


class CandidateGraphStreamRequest(CandidateGraphRequest):
    """Streaming candidate-graph generation (trunk first, then layer expansion).

    ``mode`` selects the generation budget: ``fast`` keeps thinking off and
    produces a compact trunk plus a two-layer expansion per trunk node;
    ``thinking`` allows the provider thinking mode with a fuller trunk and
    wider two-layer branches. SSE event order is ``graph.root`` (root preview),
    then one ``graph.nodes_added`` for the trunk, then per trunk node two
    ``graph.nodes_added`` events (layer-1 children, then layer-2 grandchildren),
    finishing with ``graph.complete`` (full snapshot).
    """

    mode: Literal["fast", "thinking"] = "thinking"


class PublishGoalRequest(BaseModel):
    graph_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1)


class ModelGoalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    questions: list[ClarificationQuestion] = Field(min_length=1, max_length=9)

    @field_validator("title")
    @classmethod
    def normalize_goal_title(cls, value: str) -> str:
        """Prefer subject phrases over process templates like “学习xxx”."""

        return ModelGraphDraft.normalize_graph_title(value)


class ModelGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    node_type: str = Field(pattern="^(root|concept|practice|assessment)$")
    target_weight: int = Field(default=50, ge=1, le=100)
    teaching_strategy: str = Field(
        default="",
        max_length=4_000,
        description=(
            "Subject-aware teaching strategy for this node: encyclopedia-style "
            "entry angle, examples, common pitfalls, and how to verify mastery."
        ),
    )
    layer: int = Field(
        default=1,
        ge=1,
        le=2,
        description=(
            "Expand layer used by streaming chunks: 1 = direct child of the "
            "batch parent, 2 = grandchild hanging under a layer-1 node. The "
            "root node and the full graph draft ignore this field."
        ),
    )


class ModelGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_index: int = Field(ge=0)
    target_index: int = Field(ge=0)
    relation: str = Field(pattern="^(contains|prerequisite|related|contrast|application)$")


class ModelGraphDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    nodes: list[ModelGraphNode] = Field(min_length=2, max_length=24)
    edges: list[ModelGraphEdge] = Field(default_factory=list, max_length=60)

    @field_validator("title")
    @classmethod
    def normalize_graph_title(cls, value: str) -> str:
        """Keep graph titles as clean subject phrases, not template wrappers."""

        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("The generated graph title cannot be blank")
        for suffix in (
            "学习图谱",
            "知识图谱",
            "目标图谱",
            "学习计划",
            "学习路径",
            "路径规划",
            "学习路线",
            "图谱",
            "速通",
            "计划",
        ):
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                candidate = normalized[: -len(suffix)].rstrip(" -—·:：")
                if candidate:
                    normalized = candidate
        for prefix in ("学习", "掌握", "了解", "搞定", "搞定一下"):
            if normalized.startswith(prefix) and len(normalized) > len(prefix):
                rest = normalized[len(prefix) :].lstrip(" ：:·-—")
                if rest:
                    normalized = rest
                    break
        if not normalized:
            raise ValueError("The generated graph title cannot be blank")
        return normalized[:80]


class ModelGraphRoot(BaseModel):
    """First streaming stage: the graph title and its single root node.

    Emitted to the client as soon as it is ready so the user sees the root
    preview instead of a blank spinner while level-1 children still generate.
    """

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    root: ModelGraphNode

    @field_validator("root")
    @classmethod
    def root_must_be_root_type(cls, value: ModelGraphNode) -> ModelGraphNode:
        if value.node_type != "root":
            value = value.model_copy(update={"node_type": "root"})
        return value


class ModelGraphChunkEdge(BaseModel):
    """Chunk-relative edge; ``-1`` refers to this batch's mount parent.

    For the trunk batch the mount parent is the graph root; for a branch
    expansion it is the trunk node being expanded. Positive indexes are
    relative to this chunk's own node list.
    """

    model_config = ConfigDict(extra="forbid")
    source_index: int = Field(ge=-1)
    target_index: int = Field(ge=-1)
    relation: str = Field(pattern="^(contains|prerequisite|related|contrast|application)$")


class ModelGraphChunk(BaseModel):
    """One streaming stage hung under a mount parent (root or a trunk node).

    ``source_index == -1`` in an edge means the mount parent. Nodes may span
    two layers of one branch: ``layer=1`` nodes are direct children of the
    parent (an automatic ``contains`` edge is guaranteed), ``layer=2`` nodes
    are grandchildren attached to a layer-1 node via the chunk's edges (or to
    the first layer-1 node when the model omits the edge, so no orphan is ever
    persisted).
    """

    model_config = ConfigDict(extra="forbid")
    nodes: list[ModelGraphNode] = Field(min_length=1, max_length=10)
    edges: list[ModelGraphChunkEdge] = Field(default_factory=list, max_length=20)


class PublishGoalResponse(BaseModel):
    goal: GoalView
    graph_id: str
    graph_revision: int
    status: str
