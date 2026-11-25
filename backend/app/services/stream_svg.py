"""Bounded, append-only diagram declarations in the answer stream."""
from __future__ import annotations

import json
import re
from xml.etree import ElementTree as ET

from app.domain.safe_svg import SVG_NS, TAGS, validate_illustration_svg

ID = re.compile(r"[a-zA-Z0-9_-]{1,70}\Z")
DRAWABLE = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text"}
PARENTS = {"g", "text", "tspan"}
MAX_LINE = 12_000


class SvgDeclaration:
    def __init__(self, block_id: str):
        self.block_id = block_id
        self.view_box = ""
        self.title = ""
        self.nodes: dict[str, tuple[str, int, ET.Element]] = {}
        self.bytes = 256
        self.text_size = 0
        self.attempted_header = False
        self.has_content = False
        self.skipped = False

    def feed_line(self, line: str) -> list[dict]:
        if not line.strip():
            return []
        try:
            if len(line.encode("utf-8")) > MAX_LINE:
                raise ValueError("SVG line too large")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("Expected an SVG object")
            if not self.attempted_header:
                self.attempted_header = True
                box, title = value.get("viewBox"), value.get("title")
                if (set(value) != {"viewBox", "title"} or not isinstance(box, list)
                    or len(box) != 4 or any(type(n) not in {int, float} or not -10000 <= n <= 10000 for n in box)
                    or not isinstance(title, str) or not title.strip() or len(title) > 240):
                    raise ValueError("Invalid SVG header")
                view_box = " ".join(str(n) for n in box)
                validate_illustration_svg(f'<svg viewBox="{view_box}"><rect/></svg>')
                self.view_box, self.title = view_box, title.strip()
                return [{"op": "start", "block_id": self.block_id, "view_box": self.view_box, "title": self.title}]
            if not self.view_box:
                return []
            node = self._node(value)
            return [{"op": "node", "block_id": self.block_id, "node": node}]
        except (ValueError, TypeError, RecursionError, UnicodeError, ET.ParseError):
            self.attempted_header = True
            self.skipped = True
            return []

    def _node(self, value: dict) -> dict:
        node_id, parent, tag = value.get("id"), value.get("parent_id"), value.get("tag")
        attrs, text = value.get("attrs", {}), value.get("text", "")
        if (set(value) - {"id", "parent_id", "tag", "attrs", "text"}
            or not isinstance(node_id, str) or not ID.fullmatch(node_id) or node_id in self.nodes
            or not isinstance(tag, str) or tag not in TAGS or tag == "svg"
            or not isinstance(attrs, dict) or len(attrs) > 24
            or not isinstance(text, str) or self.text_size + len(text) > 4000
            or len(self.nodes) >= 95):
            raise ValueError("Invalid SVG node")
        parent_entry = None
        if parent is not None:
            if not isinstance(parent, str) or parent not in self.nodes:
                raise ValueError("Missing SVG parent")
            parent_entry = self.nodes[parent]
            if parent_entry[0] not in PARENTS or (parent_entry[0] in {"text", "tspan"} and tag != "tspan"):
                raise ValueError("Invalid SVG parent")
        depth = parent_entry[1] + 1 if parent_entry else 1
        if depth > 8 or any(not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]*", k) or k in {"id", "xmlns"}
                           or type(v) not in {str, int, float} for k, v in attrs.items()):
            raise ValueError("Invalid SVG attributes")
        node_attrs = {k: str(v).strip() for k, v in attrs.items()}
        element = ET.Element(tag, node_attrs)
        element.text = text
        markup = ET.tostring(element, encoding="unicode")
        cost = len(markup.encode("utf-8")) + len(tag) + 3
        if self.bytes + cost > 24000:
            raise ValueError("SVG budget exceeded")
        # Reuse the static allowlist; the sentinel only makes isolated groups
        # and labels valid during validation and is never emitted or stored.
        validate_illustration_svg(f'<svg viewBox="{self.view_box}"><rect/>{markup}</svg>')
        self.nodes[node_id] = (tag, depth, element)
        self.bytes += cost
        self.text_size += len(text)
        self.has_content |= tag in DRAWABLE
        return {"id": node_id, "parent_id": parent, "tag": tag, "attrs": node_attrs, "text": text}

    def finish(self, *, interrupted: bool = False) -> dict:
        return {"op": "end", "block_id": self.block_id,
                "state": ("removed" if not self.has_content else
                          "partial" if interrupted or self.skipped else "complete")}


def render_svg_markup(data: dict) -> str:
    """Serialize validated nodes for content-based artifact comparison only."""
    root = ET.Element("svg", {"xmlns": SVG_NS, "viewBox": data["view_box"]})
    elements = {None: root}
    for node in data["nodes"]:
        element = ET.SubElement(elements[node["parent_id"]], node["tag"], node["attrs"])
        element.text = node["text"]
        elements[node["id"]] = element
    return ET.tostring(root, encoding="unicode")
