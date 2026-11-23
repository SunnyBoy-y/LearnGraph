"""SUIP 0.1: bounded declarative UI, never HTML or executable expressions."""
from __future__ import annotations

import math
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from app.domain.safe_svg import validate_illustration_svg

MAX_DOCUMENT_NODES = 160
MAX_DOCUMENT_DEPTH = 6  # Number of parent links from a root node.
MAX_CONTAINER_CHILDREN = 40
MAX_RAIL_CHILDREN = 12
MAX_CHART_ABS_VALUE = 10**15


class UIFieldError(ValueError):
    def __init__(self, message: str, *, code: str = "value_error", **details):
        super().__init__(message)
        self.code = code
        self.details = details


def ui_issue(path: tuple, message: str, *, code: str = "value_error", **details) -> dict:
    return {"type": "value_error", "loc": path,
            "ctx": {"error": UIFieldError(message, code=code, **details)}}

RESERVED_IDS = {"__proto__", "prototype", "constructor"}
DATA_BINDING_RE = re.compile(r"^@/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*$")
DATA_PATH_RE = re.compile(r"^@?/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*$")
DATA_TEMPLATE_RE = re.compile(r"@\{(/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*)\}")


def safe_id(value: str) -> str:
    if value in RESERVED_IDS:
        raise ValueError("Reserved identifier")
    return value


def is_data_binding(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 200 and bool(DATA_BINDING_RE.fullmatch(value))


def parse_data_path(expr: str) -> list[str] | None:
    if not isinstance(expr, str) or len(expr) > 200 or not DATA_PATH_RE.fullmatch(expr):
        return None
    raw = expr[1:] if expr.startswith("@") else expr
    parts = [part for part in raw.split("/") if part]
    if not parts or len(parts) > 8 or any(part in RESERVED_IDS for part in parts):
        return None
    return parts


def resolve_data_path(data_model: dict[str, object], expr: str) -> tuple[bool, object]:
    parts = parse_data_path(expr)
    if not parts:
        return False, None
    current: object = data_model
    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            if idx < 0 or idx >= len(current):
                return False, None
            current = current[idx]
        else:
            return False, None
    return True, current


def validate_data_model(data: object, *, max_depth: int = 8, max_nodes: int = 1000) -> dict[str, object]:
    if not isinstance(data, dict):
        raise ValueError("data_model must be an object")
    count = 0

    def walk(value: object, depth: int):
        nonlocal count
        count += 1
        if count > max_nodes:
            raise ValueError("data_model exceeds node limit")
        if depth > max_depth:
            raise ValueError("data_model exceeds nesting depth")
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if not math.isfinite(value) or abs(value) > 1e15:
                raise ValueError("data_model numbers must be bounded and finite")
            return value
        if isinstance(value, str):
            if len(value) > 16000:
                raise ValueError("data_model string too long")
            return value
        if isinstance(value, list):
            if len(value) > 500:
                raise ValueError("data_model array too large")
            return [walk(item, depth + 1) for item in value]
        if isinstance(value, dict):
            if len(value) > 200:
                raise ValueError("data_model object too large")
            out: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key or len(key) > 100 or key in RESERVED_IDS:
                    raise ValueError("Invalid data_model key")
                out[key] = walk(item, depth + 1)
            return out
        raise ValueError("Unsupported data_model value type")

    result = walk(data, 0)
    assert isinstance(result, dict)
    return result


Id = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$"), AfterValidator(safe_id)]
SourceKey = Annotated[str, Field(min_length=1, max_length=100), AfterValidator(safe_id)]
Text = Annotated[str, Field(max_length=2000)]
Scalar = Annotated[str, Field(max_length=2000)] | float | bool | None
Component = Literal["chart", "graph", "timeline", "table", "metric", "document"]
LegacyRailIcon = Literal["zap", "brain-circuit", "search", "layers", "shield", "lightbulb", "circle-dot", "check-check", "blocks", "sliders", "file-text", "database", "chart", "book-open", "target", "code"]
# Identifiers only: the frontend resolves bundled, same-origin Microsoft 3D assets.
FluentIcon = Annotated[str, Field(min_length=8, max_length=100, pattern=r"^fluent:[a-z0-9]+(?:-[a-z0-9]+)*$")]
RailIcon = LegacyRailIcon | FluentIcon


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Series(StrictModel):
    key: Id
    label: Annotated[str, Field(min_length=1, max_length=100)]


class DataReference(StrictModel):
    """A collection in a saved tool result, resolved within this conversation."""
    part_id: Id
    path: Annotated[list[SourceKey | Annotated[int, Field(ge=0, le=10000)]], Field(max_length=6)] = Field(default_factory=list)
    label_key: SourceKey | None = None
    id_key: SourceKey | None = None
    value_keys: Annotated[dict[Id, SourceKey], Field(max_length=12)] = Field(default_factory=dict)


class Props(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=240)]
    icon: RailIcon | None = None
    description: Text = ""
    chart_type: Literal["line", "bar"] = "line"
    columns: Annotated[list[Series], Field(max_length=12)] = Field(default_factory=list)
    unit: Annotated[str, Field(max_length=40)] = ""
    # Real-world sources or an explicit example label are displayed with the UI.
    source_note: Annotated[str, Field(max_length=500)] = ""
    density: Literal["compact", "comfortable", "editorial"] = "comfortable"
    width: Literal["reading", "full"] = "reading"
    toc: bool = False
    interaction: Literal["auto", "static", "explore"] = "auto"
    background: Literal["auto", "paper", "warm", "cool", "dark"] = "auto"
    graph_layout: Literal["flow", "mindmap"] = "flow"
    data_ref: DataReference | None = None
    # Example data must remain visibly identified after replay and export.
    data_kind: Literal["provided", "example"] = "provided"
    time_range: Annotated[str, Field(max_length=120)] = ""


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
    icon: RailIcon | None = None
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
        "timeline", "metric", "simulation", "interactive", "section", "stack", "row", "columns",
        "grid", "media_text", "tabs", "accordion",
        "feature_list", "step_list", "status_list", "rail_item", "icon",
    ]
    text: Annotated[str, Field(max_length=16000)] = ""
    label: Annotated[str, Field(max_length=240)] = ""
    level: Annotated[int, Field(ge=2, le=6)] = 3
    columns: Annotated[int, Field(ge=1, le=3)] = 2
    align: Literal["start", "center", "end"] = "start"
    width: Literal["reading", "full"] = "reading"
    weight: Annotated[int, Field(ge=1, le=6)] = 1
    image_variant: Literal["default", "hero", "thumbnail", "compact", "wide"] = "default"
    fit: Literal["contain", "cover", "scale-down"] = "contain"
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
    icon: RailIcon | None = None
    item_status: Literal["pending", "active", "completed", "error"] | None = None
    media: Annotated[list[Media], Field(max_length=6)] = Field(default_factory=list)
    resource_state: Literal["pending", "ready", "unavailable"] = "pending"
    resource_reason: Literal["", "ready", "no_results", "search_failed", "download_failed", "timeout", "disabled", "provider_unavailable", "capacity", "storage_failed"] = ""

    @model_validator(mode="after")
    def valid_visual(self):
        if self.type == "rail_item" and not self.label.strip():
            raise ValueError("Rail items require a title in label")
        if self.type == "icon" and self.icon is None:
            raise ValueError("Standalone icon nodes require an icon")
        if self.type != "rail_item" and self.item_status is not None:
            raise ValueError("item_status belongs to a rail_item")
        if self.type in {"image", "image_group", "gallery"} and self.media:
            # A pre-search caption describes a requested picture, not the
            # picture actually returned. Source metadata owns resolved labels.
            self.alt = self.media[0].title or "检索图片"
            self.caption = ""
        if self.fallback_svg and self.type not in {"image", "image_group", "gallery"}:
            raise ValueError("fallback_svg belongs to an image node")
        if self.image_variant != "default" and self.type not in {"image", "image_group", "gallery", "figure"}:
            raise ValueError("image_variant belongs to an image or figure node")
        if self.fit != "contain" and self.type not in {"image", "image_group", "gallery", "figure"}:
            raise ValueError("fit belongs to an image or figure node")
        if self.visual is not None:
            if set(self.visual) - {"props", "rows", "nodes", "edges", "items"}:
                raise ValidationError.from_exception_data(type(self).__name__, [
                    ui_issue(("visual",), "Unexpected embedded visual fields")])
            if self.type not in {"chart", "table", "graph", "diagram", "timeline", "metric"}:
                raise ValidationError.from_exception_data(type(self).__name__, [
                    ui_issue(("visual",), "Visual data requires a visual node", node_type=self.type)])
            component = "graph" if self.type == "diagram" else self.type
            try:
                Snapshot.model_validate({**self.visual, "component": component, "block_id": self.id})
            except ValidationError as exc:
                errors = [{**error, "loc": ("visual", *error["loc"])} for error in exc.errors(include_url=False)]
                raise ValidationError.from_exception_data(type(self).__name__, errors) from exc
        return self


RAIL_TYPES = {"feature_list", "step_list", "status_list"}
LAYOUT_TYPES = {"section", "stack", "row", "columns", "grid", "media_text", "tabs", "accordion", "figure"} | RAIL_TYPES


def container_child_limit(parent_type: str | None) -> int:
    return MAX_RAIL_CHILDREN if parent_type in RAIL_TYPES else MAX_CONTAINER_CHILDREN


def validate_document(blocks: list[DocumentNode]):
    by_id = {node.id: node for node in blocks}
    if len(by_id) != len(blocks):
        raise ValueError("Duplicate document ids")
    issues = []
    children: dict[str | None, list[int]] = {}
    for index, node in enumerate(blocks):
        parent_node = by_id.get(node.parent_id)
        path = ("blocks", index, "parent_id")
        details = {"node_id": node.id, "parent_id": node.parent_id}
        children.setdefault(node.parent_id, []).append(index)
        if node.type == "rail_item" and (parent_node is None or parent_node.type not in RAIL_TYPES):
            issues.append(ui_issue(path, "Rail items require a feature_list, step_list or status_list parent", **details))
        elif parent_node and parent_node.type in RAIL_TYPES and node.type != "rail_item":
            issues.append(ui_issue(path, "Rail lists only accept rail_item children", **details))
        elif parent_node and parent_node.type not in LAYOUT_TYPES:
            issues.append(ui_issue(path, "Only layout nodes can contain children", parent_type=parent_node.type, **details))
        seen = {node.id}
        parent = node.parent_id
        while parent is not None:
            if parent in seen or parent not in by_id or len(seen) > MAX_DOCUMENT_DEPTH:
                issues.append(ui_issue(path, f"Missing parent, cycle, or document depth exceeds {MAX_DOCUMENT_DEPTH}", **details))
                break
            seen.add(parent)
            parent = by_id[parent].parent_id
    for parent, indices in children.items():
        limit = container_child_limit(by_id[parent].type if parent in by_id else None)
        if len(indices) > limit:
            issues.append(ui_issue(("blocks", indices[limit], "parent_id"),
                f"At most {limit} children per container. Choose a layout node with room or move related siblings under a section.",
                code="ui_container_limit", parent_id=parent, count=len(indices), limit=limit,
                node_id=blocks[indices[limit]].id))
    if issues:
        raise ValidationError.from_exception_data("Document", issues)
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
    blocks: Annotated[list[DocumentNode], Field(max_length=MAX_DOCUMENT_NODES)] = Field(default_factory=list)


class InputPreview(StrictModel):
    tool_call_id: Annotated[str, Field(min_length=1, max_length=200)]
    sequence: Annotated[int, Field(ge=1)]
    # The authoritative snapshot is retained for rollback and final validation.
    base: dict | None = None


CheckFunction = Literal[
    "required", "non_empty", "min_length", "max_length",
    "min_value", "max_value", "range", "one_of",
    "gt", "gte", "lt", "lte", "eq", "ne",
    "is_finite_number",
]


class CheckRule(StrictModel):
    id: Id
    fn: CheckFunction
    path: Annotated[str, Field(min_length=2, max_length=200)]
    args: Annotated[list[Scalar], Field(max_length=8)] = Field(default_factory=list)
    message: Annotated[str, Field(min_length=1, max_length=240)]
    severity: Literal["error", "warning", "info"] = "warning"
    target_id: Id | None = None

    @model_validator(mode="after")
    def valid_rule(self):
        if parse_data_path(self.path) is None:
            raise ValueError("Invalid data path in check rule")
        num_fns_1 = {"min_value", "max_value", "gt", "gte", "lt", "lte"}
        len_fns_1 = {"min_length", "max_length"}
        if self.fn in num_fns_1:
            if len(self.args) != 1 or isinstance(self.args[0], bool) or not isinstance(self.args[0], (int, float)) or not math.isfinite(self.args[0]):
                raise ValueError(f"{self.fn} requires 1 finite number argument")
        elif self.fn in len_fns_1:
            if len(self.args) != 1 or type(self.args[0]) is not int or self.args[0] < 0:
                raise ValueError(f"{self.fn} requires 1 non-negative integer argument")
        elif self.fn == "range":
            if (
                len(self.args) != 2
                or any(isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a) for a in self.args)
                or float(self.args[0]) > float(self.args[1])  # type: ignore[arg-type]
            ):
                raise ValueError("range requires [min, max] finite numbers with min <= max")
        elif self.fn == "one_of":
            if not self.args:
                raise ValueError("one_of requires at least 1 option argument")
        elif self.fn in {"eq", "ne"}:
            if len(self.args) != 1:
                raise ValueError(f"{self.fn} requires 1 argument")
        elif self.args:
            raise ValueError(f"{self.fn} takes no arguments")
        return self


class Snapshot(Collections):
    protocol: Literal["suip/0.1"] = "suip/0.1"
    block_id: Id
    component: Component
    revision: Annotated[int, Field(ge=0)] = 0
    resource_revision: Annotated[int, Field(ge=0)] = 0
    state: Literal["streaming", "completed", "removed"] = "streaming"
    props: Props
    data_model: dict[str, object] = Field(default_factory=dict)
    checks: Annotated[list[CheckRule], Field(max_length=40)] = Field(default_factory=list)
    layout: Literal["inline", "block", "side"] | None = None
    preview: InputPreview | None = None

    @model_validator(mode="after")
    def consistent(self):
        self.data_model = validate_data_model(self.data_model)
        if len({rule.id for rule in self.checks}) != len(self.checks):
            raise ValueError("Duplicate check rule ids")
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
            if not keys and self.rows:
                raise ValidationError.from_exception_data("Chart", [
                    ui_issue(("props", "columns"), "Charts require columns")])
            issues = []
            for index, row in enumerate(self.rows):
                for key, value in row.values.items():
                    path = ("rows", index, "values", key)
                    if key not in keys:
                        issues.append(ui_issue(path, "Chart values must use declared columns", row_id=row.id))
                        continue
                    binding = is_data_binding(value)
                    if binding:
                        found, value = resolve_data_path(self.data_model, value)
                        if not found:
                            continue
                    if (isinstance(value, bool) or not isinstance(value, (float, int))
                            or abs(value) > MAX_CHART_ABS_VALUE or not math.isfinite(value)):
                        message = ("Chart data binding must resolve to a bounded finite number" if binding
                                   else "Chart values must be bounded finite numbers, without quotes")
                        issues.append(ui_issue(path, message, code="ui_chart_number", row_id=row.id))
            if issues:
                raise ValidationError.from_exception_data("Chart", issues)
        return self


class Command(StrictModel):
    protocol: Literal["suip/0.1"] = "suip/0.1"
    op: Literal["mount", "patch", "revise", "data", "complete", "remove"]
    block_id: Id
    base_revision: Annotated[int, Field(ge=0)] = 0
    component: Component | None = None
    props: dict[str, object] = Field(default_factory=dict)
    data_model: dict[str, object] = Field(default_factory=dict)
    checks: Annotated[list[CheckRule], Field(max_length=40)] = Field(default_factory=list)
    delete_paths: Annotated[list[Annotated[str, Field(min_length=2, max_length=200)]], Field(max_length=100)] = Field(default_factory=list)
    upsert: Collections = Field(default_factory=Collections)
    delete: dict[Literal["rows", "nodes", "edges", "items", "blocks"], list[Id]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_operation(self):
        self.data_model = validate_data_model(self.data_model)
        if len({rule.id for rule in self.checks}) != len(self.checks):
            raise ValueError("Duplicate check rule ids")
        for path in self.delete_paths:
            if parse_data_path(path) is None:
                raise ValueError("Invalid data path in delete_paths")
        if any(node.media or node.resource_state != "pending" or node.resource_reason for node in self.upsert.blocks):
            raise ValueError("Media must be resolved by the server, not supplied by a model")
        if any((node.query or node.query_alternatives) and node.type not in {"image", "image_group", "gallery"}
               for node in self.upsert.blocks):
            raise ValueError("Image queries require an image/image_group/gallery node. media_text is a layout: put query on a child image node and link it with parent_id.")
        if self.op == "mount" and (not self.component or self.base_revision != 0 or self.delete or self.delete_paths):
            raise ValueError("Mount requires component, base_revision=0, and no delete")
        if self.op != "mount" and self.component is not None:
            raise ValueError("Component can only be set on mount")
        if self.op == "data":
            if self.props or self.checks or self.delete or any(self.upsert.model_dump().values()):
                raise ValueError("Data commands can only update data_model")
            if not self.data_model and not self.delete_paths:
                raise ValueError("Data commands require data_model or delete_paths")
        if self.op in {"complete", "remove"} and (
            self.props or self.checks or self.delete or self.data_model or self.delete_paths or any(self.upsert.model_dump().values())
        ):
            raise ValueError("Terminal commands cannot modify props, data_model, checks, or collections")
        if any(len(ids) > 500 for ids in self.delete.values()):
            raise ValueError("Delete batch too large")
        return self


class UIAction(StrictModel):
    part_id: Annotated[str, Field(min_length=1, max_length=100)]
    revision: Annotated[int, Field(ge=0)]
    action: Literal["explain", "graph.node.explain", "document.block.explain"]
    node_id: Id | None = None
    block_id: Id | None = None
    view_state: dict[Literal["mode", "hidden", "limit", "query", "sort"], object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_view(self):
        import json
        if len(json.dumps(self.view_state, allow_nan=False)) > 4000:
            raise ValueError("View state too large")
        for key, value in self.view_state.items():
            if key == "mode" and value not in (None, "line", "bar"):
                raise ValueError("Invalid chart mode")
            if key == "limit" and (type(value) is not int or value not in {3, 5, 10, 500}):
                raise ValueError("Invalid range")
            if key == "query" and (not isinstance(value, str) or len(value) > 500):
                raise ValueError("Invalid filter")
            if key == "hidden" and (not isinstance(value, list) or len(value) > 12 or any(not isinstance(item, str) or len(item) > 100 for item in value)):
                raise ValueError("Invalid series selection")
            if key == "sort" and value is not None and (not isinstance(value, dict) or set(value) != {"key", "descending"} or not isinstance(value["key"], str) or len(value["key"]) > 100 or type(value["descending"]) is not bool):
                raise ValueError("Invalid sort")
        return self
