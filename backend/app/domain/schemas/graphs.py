from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.schemas.common import ORMModel


class GraphNodeView(ORMModel):
    id: str
    graph_id: str
    workspace_id: str
    label: str
    description: str
    node_type: str
    target_weight: int
    teaching_strategy: str = ""
    external_concept_id: str | None
    mastery_stars: int
    achievement_score: int | None = None
    retrieval_state: str
    evidence_state: str
    attention_state: str
    # A generated node learning page (教材 / 互动实验 / 闯关测评) is a durable,
    # version-pinned package. The canvas only needs to know whether one already
    # exists, so it can decorate the node without opening every learning page.
    has_learning_page: bool = False


class GraphEdgeView(ORMModel):
    id: str
    graph_id: str
    workspace_id: str
    source_node_id: str
    target_node_id: str
    relation: str


class GraphSummary(ORMModel):
    id: str
    goal_id: str
    workspace_id: str
    title: str
    status: str
    revision: int
    published_at: datetime | None
    cover_svg: str | None = None
    # An AI cover generation is running for this graph. The bookshelf needs it to
    # keep the 「生成中」 badge after a reload, when no dialog is open to poll.
    cover_ai_active: bool = False


class GraphCoverUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["generated", "template", "image", "svg"]
    template: Literal["ancient", "literature", "history", "science", "chemistry", "paper", "midnight", "sunrise"] | None = None
    image_data_url: str | None = Field(default=None, max_length=2_800_000)
    svg: str | None = Field(default=None, max_length=48_000)

    @model_validator(mode="after")
    def require_cover_content(self):
        if self.mode == "image" and not self.image_data_url:
            raise ValueError("image_data_url is required for image mode")
        if self.mode == "svg" and not self.svg:
            raise ValueError("svg is required for svg mode")
        if self.mode == "template" and not self.template:
            raise ValueError("template is required for template mode")
        return self


class GraphCoverTemplate(BaseModel):
    id: Literal["ancient", "literature", "history", "science", "chemistry", "paper", "midnight", "sunrise"]
    name: str
    cover_svg: str


class GraphCoverDraftRequest(BaseModel):
    """Phase 1 of the AI cover flow: draft the brief, draw nothing."""

    model_config = ConfigDict(extra="forbid")

    engine: Literal["svg", "image"] = "svg"
    hint: str = Field(default="", max_length=500)
    provider_id: str | None = Field(default=None, max_length=120)
    model_id: str | None = Field(default=None, max_length=200)


class GraphCoverDraftView(BaseModel):
    engine: Literal["svg", "image"]
    prompt: str
    # ``model`` = drafted by the model, ``fallback`` = template text because the
    # drafting model was unavailable (the user can still edit it).
    prompt_source: Literal["model", "fallback"]


class GraphCoverAIRequest(BaseModel):
    """Phase 2: the confirmed brief, submitted to the background worker."""

    model_config = ConfigDict(extra="forbid")

    engine: Literal["svg", "image"]
    prompt: str = Field(min_length=1, max_length=2000)
    prompt_source: Literal["model", "fallback", "user_edited"] = "user_edited"
    provider_id: str | None = Field(default=None, max_length=120)
    model_id: str | None = Field(default=None, max_length=200)


class GraphCoverAIJobView(BaseModel):
    id: str | None = None
    graph_id: str | None = None
    engine: str | None = None
    status: Literal["idle", "queued", "running", "ready", "failed", "cancelled"]
    prompt: str = ""
    prompt_source: str = ""
    provider_id: str | None = None
    model_id: str | None = None
    file_id: str | None = None
    error: str | None = None
    active: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GraphCoverView(BaseModel):
    graph_id: str
    title: str
    graph_revision: int
    node_count: int
    cover_svg: str
    templates: list[GraphCoverTemplate]
    used_default: bool = False


class GraphView(GraphSummary):
    nodes: list[GraphNodeView]
    edges: list[GraphEdgeView]


class GraphRevisionView(ORMModel):
    id: str
    graph_id: str
    revision: int
    change_type: str
    resource_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    actor_id: str
    created_at: datetime


class ConversationGraphNodeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    change: Literal["add", "update"]
    node_id: str | None = Field(default=None, min_length=1, max_length=36)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    node_type: Literal["root", "concept", "practice", "assessment"] = "concept"
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_change_target(self):
        if self.change == "add" and self.node_id is not None:
            raise ValueError("An added node cannot already have a node_id")
        if self.change == "update" and self.node_id is None:
            raise ValueError("An updated node must identify an existing node_id")
        return self


class ConversationGraphEdgeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str = Field(min_length=1, max_length=80)
    target_ref: str = Field(min_length=1, max_length=80)
    relation: Literal["contains", "prerequisite", "related", "contrast", "application"]
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_distinct_endpoints(self):
        if self.source_ref == self.target_ref:
            raise ValueError("A graph edge cannot connect a node to itself")
        return self


class ModelConversationGraphProposal(BaseModel):
    """Bounded structured output for an ordinary-chat graph proposal."""

    model_config = ConfigDict(extra="forbid")

    graph_title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=2_000)
    nodes: list[ConversationGraphNodeChange] = Field(min_length=1, max_length=16)
    edges: list[ConversationGraphEdgeChange] = Field(default_factory=list, max_length=32)

    @field_validator("graph_title")
    @classmethod
    def normalize_graph_title(cls, value: str) -> str:
        """Keep proposal titles as clean subject phrases, not template wrappers."""

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

    @model_validator(mode="after")
    def validate_unique_refs(self):
        refs = [node.ref for node in self.nodes]
        if len(refs) != len(set(refs)):
            raise ValueError("Node refs must be unique inside one graph proposal")
        return self


class GraphChangeSetView(ORMModel):
    id: str
    workspace_id: str
    session_id: str
    goal_id: str
    graph_id: str | None
    source_user_message_id: str
    source_assistant_message_id: str
    mode: Literal["create", "update"]
    status: Literal["proposed", "confirmed", "rejected", "undone"]
    base_revision: int
    confirmed_revision: int | None
    proposal: ModelConversationGraphProposal
    result: dict[str, Any]
    provider_trace: dict[str, Any]
    reviewed_by: str | None
    reviewed_at: datetime | None
    rejection_reason: str
    created_at: datetime
    updated_at: datetime


class RejectGraphChangeSetRequest(BaseModel):
    reason: str = Field(default="", max_length=2_000)


class NodeQuestionView(BaseModel):
    id: str
    content: str
    created_at: datetime


class UpdateNodeRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    target_weight: int | None = Field(default=None, ge=1, le=100)
    attention_state: str | None = Field(default=None, max_length=40)
    external_concept_id: str | None = Field(default=None, max_length=255)


class RetryNodeRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    instruction: str = Field(min_length=2, max_length=2_000)


class ModelNodePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(replace_node|no_change)$")
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4_000)


class MultiNodeStudyRequest(BaseModel):
    node_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(
        min_length=2,
        max_length=8,
    )

    @field_validator("node_ids")
    @classmethod
    def require_unique_node_ids(cls, node_ids: list[str]) -> list[str]:
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node_ids must contain 2 to 8 unique node IDs")
        return node_ids


class MultiNodeStudyEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation: str


class MultiNodeSharedPrerequisite(BaseModel):
    node_id: str
    label: str
    target_node_ids: list[str] = Field(min_length=2, max_length=8)
    edge_ids: list[str] = Field(min_length=2)


class MultiNodeStudyResponse(BaseModel):
    graph_revision: int = Field(ge=1)
    selected_edges: list[MultiNodeStudyEdge]
    shared_prerequisites: list[MultiNodeSharedPrerequisite]
    context_basis: Literal["graph_structure_only"]
    source_materials_queried: Literal[False]
    related: bool
    rationale: str
    roles: dict[str, str]
    next_actions: list[str]
    relationship: Literal["related", "weakly_related", "unrelated"] = "related"
    study_outline: str = ""
    comparison_points: list[str] = Field(default_factory=list)
    exercise_prompt: str | None = None
    provider: str = "local_rule_based"


class ModelMultiNodeStudy(BaseModel):
    """The bounded semantic decision a remote model may return for selected nodes."""

    model_config = ConfigDict(extra="forbid")
    relationship: Literal["related", "weakly_related", "unrelated"]
    rationale: str = Field(min_length=1, max_length=2_000)
    roles: dict[str, str] = Field(default_factory=dict)
    study_outline: str = Field(default="", max_length=4_000)
    comparison_points: list[str] = Field(default_factory=list, max_length=8)
    exercise_prompt: str | None = Field(default=None, max_length=2_000)


class NodeMergePreviewRequest(BaseModel):
    source_node_id: str = Field(min_length=1, max_length=36)
    target_node_id: str = Field(min_length=1, max_length=36)


class NodeMergePreview(BaseModel):
    source_node_id: str
    target_node_id: str
    recommendation: Literal["merge", "review", "related", "do_not_merge"]
    decision: Literal["same", "related_not_same", "different", "insufficient"]
    can_auto_merge: bool
    requires_review: bool
    rationale: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    provider: str


class ModelNodeMergeDecision(BaseModel):
    """A model may classify a pair but never authorize an automatic merge."""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["same", "related_not_same", "different", "insufficient"]
    rationale: str = Field(min_length=1, max_length=2_000)
    supporting_spans: list[str] = Field(default_factory=list, max_length=12)
    contradiction_spans: list[str] = Field(default_factory=list, max_length=12)
    context_used: list[str] = Field(default_factory=list, max_length=12)
    model_version: str = Field(default="", max_length=160)
    prompt_version: str = Field(default="merge-v1", max_length=80)


class NodeMergeDecisionRequest(NodeMergePreviewRequest):
    action: Literal["merge", "related", "do_not_merge"]
    rationale: str = Field(default="", max_length=2_000)
    user_confirmed: bool = False


class NodeMergeView(ORMModel):
    id: str
    workspace_id: str
    source_node_id: str
    target_node_id: str
    status: str
    decision_source: str
    rationale: str
    evidence: dict[str, Any]
    snapshot: dict[str, Any]
    reverted_at: datetime | None
    created_at: datetime
