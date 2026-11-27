"""Incremental Express-style compact DSL compiler for inline SUIP 0.1 documents."""
from __future__ import annotations

import json
import re
from copy import deepcopy

from app.services.genui_inline import canonical_inline_document
from app.services.stream_ui import MAX_UI_BYTES, compile_command

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
_RESERVED_IDS = {"__proto__", "prototype", "constructor"}

_LAYOUT_TYPES = {
    "section", "stack", "row", "column", "columns", "grid", "card", "media_text",
    "tabs", "accordion", "figure", "feature_list", "step_list", "status_list",
}
_VISUAL_TYPES = {"chart", "table", "graph", "diagram", "timeline", "metric"}
_CONTENT_TYPES = {
    "paragraph", "heading", "caption", "list", "quote", "code", "math", "divider",
    "image", "image_group", "gallery", "svg", "simulation", "interactive",
    "rail_item", "icon",
}
_ALL_NODE_TYPES = _LAYOUT_TYPES | _VISUAL_TYPES | _CONTENT_TYPES

_ALLOWED_DOC_PROPS = {
    "title", "icon", "description", "density", "width", "toc",
    "source_note", "data_kind", "time_range",
}
_ALLOWED_NODE_ATTRS = {
    "id", "parent", "parent_id", "text", "label", "level", "columns",
    "align", "width", "weight", "image_variant", "fit",
    "language", "query", "query_alternatives", "alt",
    "caption", "svg", "display_mode", "engine", "inline", "values", "icon", "item_status",
    # Visual props shorthand on chart/table/graph/timeline/metric nodes
    "title", "description", "chart_type", "unit", "source_note",
    "data_kind", "time_range", "interaction", "background", "graph_layout",
    "layout",
}


def _validate_id(value: str) -> str:
    if not _ID_RE.fullmatch(value) or value in _RESERVED_IDS:
        raise ValueError(f"Invalid Express DSL identifier: {value!r}")
    return value


def _unescape_text(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _parse_scalar(raw: str):
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "null":
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        num = float(raw)
        if not (-1e15 <= num <= 1e15):
            raise ValueError("Numeric value out of bounds")
        return num
    return raw


def _tokenize_head(head: str) -> tuple[str, dict[str, object]]:
    """Parse `<token>[#id][@parent] [k=v ...]` safely without eval."""
    tokens: list[str] = []
    i = 0
    n = len(head)
    while i < n:
        while i < n and head[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        in_quote = False
        escape = False
        while i < n:
            ch = head[i]
            if in_quote:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_quote = False
                i += 1
            else:
                if ch == '"':
                    in_quote = True
                    i += 1
                elif ch.isspace():
                    break
                else:
                    i += 1
        if in_quote:
            raise ValueError("Unterminated quoted attribute in Express DSL")
        tokens.append(head[start:i])
    if not tokens:
        raise ValueError("Empty Express DSL statement")

    keyword = tokens[0]
    attrs: dict[str, object] = {}
    for token in tokens[1:]:
        if "=" not in token:
            raise ValueError(f"Unexpected bare token in Express DSL: {token!r}")
        key, raw_val = token.split("=", 1)
        if not key or key in _RESERVED_IDS:
            raise ValueError(f"Invalid attribute key: {key!r}")
        if raw_val.startswith('"') and raw_val.endswith('"') and len(raw_val) >= 2:
            val: object = _unescape_text(raw_val[1:-1])
        elif raw_val.startswith("[") and raw_val.endswith("]"):
            val = json.loads(raw_val)
        else:
            val = _parse_scalar(raw_val)
        attrs[key] = val
    return keyword, attrs


def _split_head_and_content(stripped: str) -> tuple[str, str | None]:
    """Split `head: content` outside quotes/brackets."""
    in_quote = False
    escape = False
    depth = 0
    for idx, ch in enumerate(stripped):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            # A head/content separator colon is followed by whitespace (or EOL),
            # distinguishing `icon=fluent:light-bulb: text` from `fluent:light-bulb`.
            if idx + 1 == len(stripped) or stripped[idx + 1].isspace():
                return stripped[:idx].strip(), stripped[idx + 1 :].strip()
    return stripped, None


def _parse_columns_spec(spec: object) -> list[dict[str, str]]:
    if isinstance(spec, list):
        return spec
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("columns must be a non-empty string or list")
    columns: list[dict[str, str]] = []
    for part in spec.split(","):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            key, label = item.split(":", 1)
            key = _validate_id(key.strip())
            label = label.strip() or key
        else:
            key = _validate_id(item)
            label = key
        columns.append({"key": key, "label": label})
    if not columns or len(columns) > 12:
        raise ValueError("Invalid Express columns count")
    return columns


class ExpressUIParser:
    """Line-incremental compiler from compact Express DSL to SUIP 0.1 commands."""

    def __init__(self, block_id: str, *, initial_revision: int = 0):
        self.block_id = block_id
        self.revision = initial_revision
        self.props: dict[str, object] = {"title": "内容概览"}
        self.props_dirty = False
        self.data_model: dict[str, object] = {}
        self.checks: list[dict] = []
        self.blocks: list[dict] = []
        self.block_index: dict[str, int] = {}
        self.indent_stack: list[tuple[int, str]] = []
        self.auto_seq = 0
        self.active_visual: dict | None = None
        self.active_visual_indent: int = -1
        self.last_text_node_id: str | None = None
        self.byte_size = 0

    def _next_id(self, prefix: str = "n") -> str:
        self.auto_seq += 1
        return f"{prefix}{self.auto_seq}"

    def _emit_patch(self, *, props: dict | None = None, blocks: list[dict] | None = None, checks: list[dict] | None = None) -> dict:
        cmd: dict = {
            "op": "patch",
            "block_id": self.block_id,
            "base_revision": self.revision,
        }
        if props:
            cmd["props"] = deepcopy(props)
        if blocks:
            cmd["upsert"] = {"blocks": deepcopy(blocks)}
        if checks:
            cmd["checks"] = deepcopy(checks)
        self.revision += 1
        return cmd

    def _flush_visual(self) -> list[dict]:
        if self.active_visual is None:
            return []
        builder = self.active_visual
        self.active_visual = None
        self.active_visual_indent = -1
        node_idx = self.block_index[builder["id"]]
        node = self.blocks[node_idx]
        node["visual"] = builder["visual"]
        node["state"] = "completed"
        return [self._emit_patch(blocks=[node])]

    def _emit_data(self, data_model: dict[str, object]) -> dict:
        cmd: dict = {
            "op": "data",
            "block_id": self.block_id,
            "base_revision": self.revision,
            "data_model": deepcopy(data_model),
        }
        self.revision += 1
        return cmd

    def _parse_directive(self, stripped: str) -> list[dict]:
        body = stripped[1:].strip()
        if not body:
            raise ValueError("Empty directive")
        if body.startswith("data ") or body.startswith("data:"):
            payload_str = body[5:].strip()
            if not payload_str:
                raise ValueError("Empty @data directive")
            if payload_str.startswith("{"):
                patch_data = json.loads(payload_str)
                if not isinstance(patch_data, dict):
                    raise ValueError("@data JSON payload must be an object")
            else:
                _, patch_data = _tokenize_head("data " + payload_str)
            from app.services.stream_ui import merge_data_model
            self.data_model = merge_data_model(self.data_model, patch_data)
            return [self._emit_data(patch_data)]
        if body.startswith("checks ") or body.startswith("checks:"):
            payload_str = body[7:].strip()
            if not payload_str:
                raise ValueError("Empty @checks directive")
            parsed_checks = json.loads(payload_str)
            if not isinstance(parsed_checks, list) or not parsed_checks:
                raise ValueError("@checks JSON payload must be a non-empty list")
            from app.domain.schemas.stream_ui import CheckRule
            validated = [CheckRule.model_validate(item).model_dump() for item in parsed_checks]
            self.checks.extend(validated)
            if len(self.checks) > 40:
                raise ValueError("Too many check rules")
            return [self._emit_patch(checks=validated)]
        if body.startswith("check"):
            head, content = _split_head_and_content(body)
            keyword, attrs = _tokenize_head(head)
            if keyword == "check" or keyword.startswith("check#"):
                _, _, explicit_id = keyword.partition("#")
                rule_id = _validate_id(explicit_id) if explicit_id else (
                    _validate_id(str(attrs.pop("id"))) if "id" in attrs else self._next_id("chk")
                )
                if content is not None and "message" not in attrs:
                    attrs["message"] = _unescape_text(content)
                from app.domain.schemas.stream_ui import CheckRule
                validated_rule = CheckRule.model_validate({"id": rule_id, **attrs}).model_dump()
                self.checks.append(validated_rule)
                if len(self.checks) > 40:
                    raise ValueError("Too many check rules")
                return [self._emit_patch(checks=[validated_rule])]
        if body.startswith("props "):
            _, attrs = _tokenize_head(body)
            for key, val in attrs.items():
                if key not in _ALLOWED_DOC_PROPS:
                    raise ValueError(f"Unsupported Express document prop: {key!r}")
                self.props[key] = val
            self.props_dirty = True
            return [self._emit_patch(props=self.props)]

        parts = body.split(None, 1)
        key = parts[0]
        if key.endswith(":"):
            key = key[:-1]
        if key not in _ALLOWED_DOC_PROPS:
            raise ValueError(f"Unsupported Express directive: @{key}")
        raw_val = parts[1].strip() if len(parts) > 1 else ""
        if raw_val.startswith('"') and raw_val.endswith('"') and len(raw_val) >= 2:
            val: object = _unescape_text(raw_val[1:-1])
        else:
            val = _parse_scalar(raw_val)
        self.props[key] = val
        self.props_dirty = True
        return [self._emit_patch(props=self.props)]

    def _parse_visual_child(self, indent: int, stripped: str) -> list[dict]:
        builder = self.active_visual
        assert builder is not None
        head, content = _split_head_and_content(stripped)
        keyword, attrs = _tokenize_head(head)
        kind_part, _, explicit_id = keyword.partition("#")
        item_id = _validate_id(explicit_id) if explicit_id else (
            _validate_id(str(attrs.pop("id"))) if "id" in attrs else self._next_id(kind_part[0])
        )
        vtype = builder["type"]
        visual = builder["visual"]

        if kind_part == "item" and vtype in {"metric", "timeline"}:
            entry: dict[str, object] = {"id": item_id}
            for k in ("label", "icon", "value", "description", "date"):
                if k in attrs:
                    entry[k] = attrs.pop(k)
            if attrs:
                raise ValueError(f"Unknown item attributes: {sorted(attrs)}")
            if content is not None:
                if vtype == "metric":
                    if "value" not in entry and "|" in content:
                        val_part, desc_part = content.split("|", 1)
                        entry["value"] = _parse_scalar(val_part.strip())
                        entry.setdefault("description", _unescape_text(desc_part.strip()))
                    elif "value" not in entry:
                        entry["value"] = _parse_scalar(content)
                    else:
                        entry.setdefault("description", _unescape_text(content))
                else:
                    entry.setdefault("description", _unescape_text(content))
            if "label" not in entry:
                entry["label"] = item_id
            visual.setdefault("items", []).append(entry)
            return []

        if kind_part == "row" and vtype in {"chart", "table"}:
            label = str(attrs.pop("label", content or ""))
            values = {k: v for k, v in attrs.items()}
            visual.setdefault("rows", []).append({"id": item_id, "label": label, "values": values})
            return []

        if kind_part == "node" and vtype in {"graph", "diagram"}:
            entry = {"id": item_id, "label": str(attrs.pop("label", item_id))}
            if "group" in attrs:
                entry["group"] = str(attrs.pop("group"))
            if "description" in attrs:
                entry["description"] = str(attrs.pop("description"))
            if attrs:
                raise ValueError(f"Unknown graph node attributes: {sorted(attrs)}")
            if content is not None and "description" not in entry:
                entry["description"] = _unescape_text(content)
            visual.setdefault("nodes", []).append(entry)
            return []

        if kind_part == "edge" and vtype in {"graph", "diagram"}:
            src = attrs.pop("from", attrs.pop("source", None))
            dst = attrs.pop("to", attrs.pop("target", None))
            if not isinstance(src, str) or not isinstance(dst, str):
                raise ValueError("Graph edge requires from/to or source/target")
            entry = {
                "id": item_id,
                "source": _validate_id(src),
                "target": _validate_id(dst),
                "label": str(attrs.pop("label", _unescape_text(content) if content else "")),
            }
            if attrs:
                raise ValueError(f"Unknown edge attributes: {sorted(attrs)}")
            visual.setdefault("edges", []).append(entry)
            return []

        raise ValueError(f"Invalid child {kind_part!r} inside {vtype!r}")

    def feed_line(self, raw_line: str) -> list[dict]:
        self.byte_size += len(raw_line.encode("utf-8"))
        if self.byte_size > MAX_UI_BYTES:
            raise ValueError("Express UI declaration too large")

        stripped_right = raw_line.rstrip("\r\n")
        stripped = stripped_right.strip()
        if not stripped or stripped.startswith("//"):
            return []
        if stripped.startswith("#"):
            # Comment line (only `# ...`, since node IDs always start with `<type>#id`)
            return []

        indent = len(stripped_right) - len(stripped_right.lstrip(" \t"))
        events: list[dict] = []

        # Text continuation line: `| some text`
        if stripped.startswith("|") and self.last_text_node_id is not None:
            idx = self.block_index[self.last_text_node_id]
            node = self.blocks[idx]
            addition = _unescape_text(stripped[1:].lstrip())
            node["text"] = f"{node.get('text', '')}\n{addition}" if node.get("text") else addition
            return [self._emit_patch(blocks=[node])]

        # Document-level directives
        if stripped.startswith("@"):
            events.extend(self._flush_visual())
            events.extend(self._parse_directive(stripped))
            self.last_text_node_id = None
            return events

        # Check if still inside an active visual builder
        if self.active_visual is not None:
            if indent > self.active_visual_indent:
                events.extend(self._parse_visual_child(indent, stripped))
                return events
            events.extend(self._flush_visual())

        # Normal node declaration
        head, content = _split_head_and_content(stripped)
        keyword, attrs = _tokenize_head(head)

        # Parse `<type>[#<id>][@<parent>]`
        explicit_parent: str | None = None
        if "@" in keyword:
            keyword, explicit_parent = keyword.split("@", 1)
            explicit_parent = _validate_id(explicit_parent)
        node_type, has_hash, explicit_id = keyword.partition("#")
        if re.fullmatch(r"h([2-6])", node_type):
            attrs.setdefault("level", int(node_type[1]))
            node_type = "heading"
        if node_type not in _ALL_NODE_TYPES:
            raise ValueError(f"Unsupported Express node type: {node_type!r}")

        for attr_key in attrs:
            if attr_key not in _ALLOWED_NODE_ATTRS:
                raise ValueError(f"Unsupported Express node attribute: {attr_key!r}")

        if "id" in attrs:
            node_id = _validate_id(str(attrs.pop("id")))
        elif has_hash and explicit_id:
            node_id = _validate_id(explicit_id)
        else:
            node_id = self._next_id("b")
        if node_id in self.block_index:
            raise ValueError(f"Duplicate Express block id: {node_id!r}")

        # Resolve parent via explicit @parent / parent_id= / parent= or indent stack
        parent_id_attr = attrs.pop("parent_id", attrs.pop("parent", None))
        if explicit_parent and parent_id_attr and explicit_parent != parent_id_attr:
            raise ValueError("Conflicting parent references")
        parent_id = explicit_parent or (str(parent_id_attr) if parent_id_attr is not None else None)
        if parent_id is not None:
            parent_id = _validate_id(parent_id)

        while self.indent_stack and self.indent_stack[-1][0] >= indent:
            self.indent_stack.pop()
        if parent_id is None and self.indent_stack:
            parent_id = self.indent_stack[-1][1]

        if node_type in _VISUAL_TYPES:
            visual_props: dict[str, object] = {}
            for vkey in ("title", "description", "chart_type", "unit", "source_note",
                         "data_kind", "time_range", "interaction", "background", "graph_layout"):
                if vkey in attrs:
                    visual_props[vkey] = attrs.pop(vkey)
            if "layout" in attrs:
                visual_props["graph_layout"] = attrs.pop("layout")
            if "columns" in attrs:
                visual_props["columns"] = _parse_columns_spec(attrs.pop("columns"))

            # Inline JSON visual payload shorthand: `chart#c: {"props":...}`
            if content is not None and content.startswith("{"):
                inline_visual = json.loads(content)
                if not isinstance(inline_visual, dict):
                    raise ValueError("Inline visual JSON must be an object")
                if visual_props:
                    inline_visual = {**inline_visual, "props": {**visual_props, **(inline_visual.get("props") or {})}}
                node: dict[str, object] = {
                    "id": node_id,
                    "type": node_type,
                    "state": "completed",
                    "visual": inline_visual,
                }
                if parent_id is not None:
                    node["parent_id"] = parent_id
                for k in ("label", "width", "weight", "align", "icon"):
                    if k in attrs:
                        node[k] = attrs.pop(k)
                if attrs:
                    raise ValueError(f"Unsupported attributes on visual node: {sorted(attrs)}")
                self.block_index[node_id] = len(self.blocks)
                self.blocks.append(node)
                self.last_text_node_id = None
                events.append(self._emit_patch(blocks=[node]))
                return events

            visual_props.setdefault("title", str(attrs.get("label") or content or node_id))
            placeholder: dict[str, object] = {
                "id": node_id,
                "type": node_type,
                "state": "pending",
            }
            if parent_id is not None:
                placeholder["parent_id"] = parent_id
            for k in ("label", "width", "weight", "align", "icon"):
                if k in attrs:
                    placeholder[k] = attrs.pop(k)
            if attrs:
                raise ValueError(f"Unsupported attributes on visual node: {sorted(attrs)}")

            self.block_index[node_id] = len(self.blocks)
            self.blocks.append(placeholder)
            self.active_visual = {
                "id": node_id,
                "type": node_type,
                "visual": {"props": visual_props},
            }
            self.active_visual_indent = indent
            self.last_text_node_id = None
            events.append(self._emit_patch(blocks=[placeholder]))
            return events

        # Standard document / layout / content node
        node = {
            "id": node_id,
            "type": node_type,
            "state": "streaming",
        }
        if parent_id is not None:
            node["parent_id"] = parent_id
        for k, v in attrs.items():
            node[k] = v

        if content is not None:
            if node_type == "rail_item":
                if "label" not in node and "|" in content:
                    parts = [p.strip() for p in content.split("|", 2)]
                    node["label"] = _unescape_text(parts[0])
                    if len(parts) > 1 and parts[1]:
                        node.setdefault("text", _unescape_text(parts[1]))
                    if len(parts) > 2 and parts[2]:
                        node.setdefault("caption", _unescape_text(parts[2]))
                elif "label" not in node:
                    node["label"] = _unescape_text(content)
                else:
                    node.setdefault("text", _unescape_text(content))
            elif node_type in _LAYOUT_TYPES:
                if node_type == "figure" and "caption" not in node:
                    node["caption"] = _unescape_text(content)
                elif "label" not in node:
                    node["label"] = _unescape_text(content)
                else:
                    node.setdefault("text", _unescape_text(content))
            elif node_type in {"image", "image_group", "gallery"}:
                node.setdefault("query", _unescape_text(content))
            elif node_type == "svg":
                node.setdefault("svg", _unescape_text(content))
            elif node_type == "icon":
                node.setdefault("icon", content)
            else:
                node.setdefault("text", _unescape_text(content))

        if len(self.blocks) >= 160:
            raise ValueError("Too many document nodes")

        self.block_index[node_id] = len(self.blocks)
        self.blocks.append(node)
        if node_type in _LAYOUT_TYPES:
            self.indent_stack.append((indent, node_id))
        self.last_text_node_id = (
            node_id
            if node_type in {"paragraph", "list", "quote", "code", "math", "rail_item"}
            else None
        )
        events.append(self._emit_patch(blocks=[node]))
        return events

    def finish(self) -> list[dict]:
        events: list[dict] = []
        events.extend(self._flush_visual())

        changed_blocks: list[dict] = []
        for node in self.blocks:
            if node.get("state") != "completed":
                node["state"] = "completed"
                changed_blocks.append(node)
        if changed_blocks:
            events.append(self._emit_patch(blocks=changed_blocks))

        raw = canonical_inline_document({
            "props": deepcopy(self.props),
            **({"data_model": deepcopy(self.data_model)} if self.data_model else {}),
            **({"checks": deepcopy(self.checks)} if self.checks else {}),
            "upsert": {"blocks": deepcopy(self.blocks)},
        })
        mount = {"op": "mount", "block_id": self.block_id, "component": "document", **raw}
        snapshot = compile_command(mount)
        if not any(
            node.text.strip()
            or (node.type == "caption" and bool(node.caption.strip()))
            or (node.type == "svg" and bool(node.svg))
            or (node.type in {"image", "image_group", "gallery"} and (node.query or node.fallback_svg))
            or node.type in {"simulation", "interactive", "icon"}
            or (node.visual and any(node.visual.get(key) for key in ("rows", "nodes", "items")))
            for node in snapshot.blocks
        ):
            raise ValueError("A finished inline document must contain useful content")

        events.append({"op": "complete", "block_id": self.block_id, "base_revision": self.revision})
        return events
