"""SUIP 0.1: bounded declarative UI, never HTML or executable expressions."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from app.domain.safe_svg import validate_illustration_svg

def safe_id(value: str) -> str:
    if value in {"__proto__", "prototype", "constructor"}:
        raise ValueError("Reserved identifier")
    return value


Id = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$"), AfterValidator(safe_id)]
Text = Annotated[str, Field(max_length=2000)]
Scalar = Annotated[str, Field(max_length=2000)] | float | bool | None
Component = Literal["chart", "graph", "timeline", "table", "metric", "document"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Series(StrictModel):
    key: Id
    label: Annotated[str, Field(min_length=1, max_length=100)]


class Props(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=240)]
    description: Text = ""
    chart_type: Literal["line", "bar"] = "line"
    columns: Annotated[list[Series], Field(max_length=12)] = Field(default_factory=list)
    unit: Annotated[str, Field(max_length=40)] = ""
    # Real-world sources or an explicit example label are displayed with the UI.
    source_note: Annotated[str, Field(max_length=500)] = ""


class Row(StrictModel):
    id: Id
    label: Annotated[str, Field(max_length=240)] = ""
    values: dict[Id, Scalar] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.values) > 12:
            raise ValueError("At most 12 columns per row")
        return self


class Node(StrictModel):
    id: Id
    label: Annotated[str, Field(min_length=1, max_length=120)]
    description: Text = ""
    group: Annotated[str, Field(max_length=80)] = ""


class Edge(StrictModel):
    id: Id
    source: Id
    target: Id
    label: Annotated[str, Field(max_length=120)] = ""


class Item(StrictModel):
    id: Id
    label: Annotated[str, Field(min_length=1, max_length=120)]
    value: Scalar = None
    description: Text = ""
    date: Annotated[str, Field(max_length=80)] = ""


class Media(StrictModel):
    """Server-resolved search results. Never accepted in model commands."""
    url: Annotated[str, Field(max_length=2000)]
    file_id: Id | None = None
    source_url: Annotated[str, Field(max_length=2000)] = ""
    title: Annotated[str, Field(max_length=500)] = ""
    attribution: Annotated[str, Field(max_length=500)] = ""


class DocumentNode(StrictModel):
    id: Id
    parent_id: Id | None = None
    state: Literal["pending", "streaming", "completed", "error"] = "completed"
    revision: Annotated[int, Field(ge=0)] = 0
    type: Literal[
        "paragraph", "heading", "list", "quote", "code", "math", "table", "divider",
        "image", "image_group", "gallery", "figure", "chart", "diagram", "graph",
        "timeline", "simulation", "interactive", "section", "stack", "row", "columns",
        "grid", "media_text", "tabs", "accordion",
    ]
    text: Annotated[str, Field(max_length=16000)] = ""
    label: Annotated[str, Field(max_length=240)] = ""
    level: Annotated[int, Field(ge=2, le=6)] = 3
    columns: Annotated[int, Field(ge=1, le=3)] = 2
    align: Literal["start", "center", "end"] = "start"
    width: Literal["reading", "full"] = "reading"
    language: Annotated[str, Field(max_length=40, pattern=r"^[a-zA-Z0-9_+#.-]*$")] = ""
    query: Annotated[str, Field(max_length=500)] = ""
    query_alternatives: Annotated[list[Annotated[str, Field(min_length=1, max_length=150)]], Field(max_length=2)] = Field(default_factory=list)
    alt: Annotated[str, Field(max_length=500)] = ""
    caption: Annotated[str, Field(max_length=1000)] = ""
    fallback_svg: Annotated[str, Field(max_length=24000), AfterValidator(validate_illustration_svg)] = ""
    # Only registered, bounded local calculations; no expressions or callbacks.
    engine: Literal["chase", "quadratic", "sort_steps"] = "chase"
    inline: bool = False
    values: Annotated[list[Annotated[float, Field(ge=-1000, le=1000)]], Field(max_length=30)] = Field(default_factory=list)
    visual: dict[str, object] | None = None
    media: Annotated[list[Media], Field(max_length=6)] = Field(default_factory=list)
    resource_state: Literal["pending", "ready", "unavailable"] = "pending"
    resource_reason: Literal["", "ready", "no_results", "search_failed", "download_failed", "timeout", "disabled", "provider_unavailable", "capacity", "storage_failed"] = ""

    @model_validator(mode="after")
    def valid_visual(self):
        if self.type in {"image", "image_group", "gallery"} and self.media:
            # A pre-search caption describes a requested picture, not the
            # picture actually returned. Source metadata owns resolved labels.
            self.alt = self.media[0].title or "检索图片"
            self.caption = ""
        if self.fallback_svg and self.type not in {"image", "image_group", "gallery"}:
            raise ValueError("fallback_svg belongs to an image node")
        if self.visual is not None:
            if set(self.visual) - {"props", "rows", "nodes", "edges", "items"}:
                raise ValueError("Unexpected embedded visual fields")
            if self.type not in {"chart", "table", "graph", "diagram", "timeline"}:
                raise ValueError("Visual data requires a visual node")
            component = "graph" if self.type == "diagram" else self.type
            Snapshot.model_validate({**self.visual, "component": component, "block_id": self.id})
        return self


LAYOUT_TYPES = {"section", "stack", "row", "columns", "grid", "media_text", "tabs", "accordion", "figure"}


def validate_document(blocks: list[DocumentNode]):
    by_id = {node.id: node for node in blocks}
    if len(by_id) != len(blocks):
        raise ValueError("Duplicate document ids")
    for node in blocks:
        seen = {node.id}
        parent = node.parent_id
        while parent is not None:
            if parent in seen or parent not in by_id or len(seen) >= 7:
                raise ValueError("Missing parent, cycle, or document depth exceeds 6")
            seen.add(parent)
            if by_id[parent].type not in LAYOUT_TYPES:
                raise ValueError("Only layout nodes can contain children")
            parent = by_id[parent].parent_id
    for parent in {node.parent_id for node in blocks}:
        if sum(node.parent_id == parent for node in blocks) > 40:
            raise ValueError("At most 40 children per container")
    for node in blocks:
        if node.type in {"image", "image_group", "gallery"} and node.media:
            parent = node.parent_id
            while parent is not None:
                container = by_id[parent]
                if container.type == "figure":
                    container.caption = ""
                parent = container.parent_id


class Collections(StrictModel):
    rows: Annotated[list[Row], Field(max_length=500)] = Field(default_factory=list)
    nodes: Annotated[list[Node], Field(max_length=100)] = Field(default_factory=list)
    edges: Annotated[list[Edge], Field(max_length=300)] = Field(default_factory=list)
    items: Annotated[list[Item], Field(max_length=100)] = Field(default_factory=list)
    blocks: Annotated[list[DocumentNode], Field(max_length=160)] = Field(default_factory=list)


class InputPreview(StrictModel):
    tool_call_id: Annotated[str, Field(min_length=1, max_length=200)]
    sequence: Annotated[int, Field(ge=1)]
    # The authoritative snapshot is retained for rollback and final validation.
    base: dict | None = None


class Snapshot(Collections):
    protocol: Literal["suip/0.1"] = "suip/0.1"
    block_id: Id
    component: Component
    revision: Annotated[int, Field(ge=0)] = 0
    resource_revision: Annotated[int, Field(ge=0)] = 0
    state: Literal["streaming", "completed", "removed"] = "streaming"
    props: Props
    layout: Literal["inline", "block", "side"] | None = None
    preview: InputPreview | None = None

    @model_validator(mode="after")
    def consistent(self):
        for field in ("rows", "nodes", "edges", "items", "blocks"):
            values = getattr(self, field)
            if len({value.id for value in values}) != len(values):
                raise ValueError(f"Duplicate {field} ids")
        if len({column.key for column in self.props.columns}) != len(self.props.columns):
            raise ValueError("Duplicate column keys")
        allowed = {"chart": {"rows"}, "table": {"rows"}, "graph": {"nodes", "edges"},
                   "timeline": {"items"}, "metric": {"items"}, "document": {"blocks"}}[self.component]
        if any(getattr(self, key) for key in {"rows", "nodes", "edges", "items", "blocks"} - allowed):
            raise ValueError("Collection does not belong to this component")
        validate_document(self.blocks)
        node_ids = {node.id for node in self.nodes}
        if any(edge.source not in node_ids or edge.target not in node_ids for edge in self.edges):
            raise ValueError("Edges must reference existing nodes")
        if self.component == "chart":
            keys = {column.key for column in self.props.columns}
            if not keys:
                raise ValueError("Charts require columns")
            for row in self.rows:
                if any(key not in keys or isinstance(value, bool) or not isinstance(value, (float, int))
                       or not math.isfinite(value) or abs(value) > 1e15 for key, value in row.values.items()):
                    raise ValueError("Chart values must be bounded finite numbers in declared columns")
        return self


class Command(StrictModel):
    protocol: Literal["suip/0.1"] = "suip/0.1"
    op: Literal["mount", "patch", "complete", "remove"]
    block_id: Id
    base_revision: Annotated[int, Field(ge=0)] = 0
    component: Component | None = None
    props: dict[str, object] = Field(default_factory=dict)
    upsert: Collections = Field(default_factory=Collections)
    delete: dict[Literal["rows", "nodes", "edges", "items", "blocks"], list[Id]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_operation(self):
        if any(node.media or node.resource_state != "pending" or node.resource_reason for node in self.upsert.blocks):
            raise ValueError("Media must be resolved by the server, not supplied by a model")
        if any((node.query or node.query_alternatives) and node.type not in {"image", "image_group", "gallery"}
               for node in self.upsert.blocks):
            raise ValueError("Image queries require an image/image_group/gallery node. media_text is a layout: put query on a child image node and link it with parent_id.")
        if self.op == "mount" and (not self.component or self.base_revision != 0 or self.delete):
            raise ValueError("Mount requires component, base_revision=0, and no delete")
        if self.op != "mount" and self.component is not None:
            raise ValueError("Component can only be set on mount")
        if self.op in {"complete", "remove"} and (self.props or self.delete or any(self.upsert.model_dump().values())):
            raise ValueError("Terminal commands cannot modify props or collections")
        if any(len(ids) > 500 for ids in self.delete.values()):
            raise ValueError("Delete batch too large")
        return self


class UIAction(StrictModel):
    part_id: Annotated[str, Field(min_length=1, max_length=100)]
    revision: Annotated[int, Field(ge=0)]
    action: Literal["explain", "graph.node.explain"]
    node_id: Id | None = None
