"""Validate model-authored static illustrations; no scripts, CSS or references."""
from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "title", "desc"}
NUMBERS = {"x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width", "height", "stroke-width", "font-size", "dx", "dy", "letter-spacing", "opacity", "fill-opacity", "stroke-opacity"}
ENUMS = {"text-anchor": {"start", "middle", "end"}, "dominant-baseline": {"auto", "middle", "hanging", "alphabetic", "central"},
         "font-weight": {"normal", "bold", "400", "500", "600", "700"}, "stroke-linecap": {"butt", "round", "square"},
         "stroke-linejoin": {"miter", "round", "bevel"}, "fill-rule": {"nonzero", "evenodd"}, "vector-effect": {"non-scaling-stroke"}}
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
COLOR = r"(?:none|transparent|currentColor|#[0-9a-fA-F]{3,8}|[a-zA-Z]{1,24}|(?:rgb|rgba|hsl|hsla)\([0-9.,%\s+-]+\))"


def _numbers(value: str) -> list[float]:
    parts = re.split(r"[\s,]+", value.strip())
    if not parts or len(parts) > 256 or any(not re.fullmatch(NUMBER, p) for p in parts):
        raise ValueError("Invalid SVG numbers")
    values = [float(p) for p in parts]
    if any(not math.isfinite(n) or abs(n) > 10000 for n in values):
        raise ValueError("SVG numbers exceed bounds")
    return values


def validate_illustration_svg(value: str) -> str:
    if not value:
        return value
    if len(value.encode("utf-8")) > 24000 or "<!" in value or "<?" in value:
        raise ValueError("SVG must be a small static illustration without declarations")
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise ValueError("Invalid illustration SVG XML") from exc
    count = 0
    drawable = False

    def visit(node, depth=0):
        nonlocal count, drawable
        count += 1
        tag = node.tag.removeprefix("{" + SVG_NS + "}")
        if count > 96 or depth > 8 or tag not in TAGS or (tag == "svg" and depth != 0):
            raise ValueError("Unsupported SVG element or complexity")
        drawable |= tag in {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
        if len(node.attrib) > 24:
            raise ValueError("Too many SVG attributes")
        for key, raw in node.attrib.items():
            v = raw.strip()
            if key in NUMBERS:
                numbers = _numbers(v.removesuffix("%"))
                if len(numbers) != 1 or (v.endswith("%") and not 0 <= numbers[0] <= 100):
                    raise ValueError("Invalid SVG coordinate")
                if key in {"width", "height", "r", "rx", "ry", "stroke-width", "font-size"} and numbers[0] < 0:
                    raise ValueError("Negative SVG size")
                if key.endswith("opacity") and not 0 <= numbers[0] <= 1:
                    raise ValueError("Invalid SVG opacity")
            elif key == "viewBox" and depth == 0:
                box = _numbers(v)
                if len(box) != 4 or not (80 <= box[2] <= 1600 and 80 <= box[3] <= 1200 and .4 <= box[2] / box[3] <= 4):
                    raise ValueError("Invalid SVG viewBox")
            elif key in {"fill", "stroke"}:
                if len(v) > 100 or not re.fullmatch(COLOR, v):
                    raise ValueError("SVG paint must be a plain color, never a reference")
            elif key in ENUMS:
                if v not in ENUMS[key]:
                    raise ValueError("Unsupported SVG attribute value")
            elif key == "d" and tag == "path":
                if not v or len(v) > 6000 or not re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]+", v):
                    raise ValueError("Invalid SVG path")
                _numbers(" ".join(re.findall(NUMBER, v)))
            elif key in {"points", "stroke-dasharray"}:
                if key == "stroke-dasharray" and v == "none":
                    continue
                numbers = _numbers(v)
                if key == "points" and (len(numbers) < 4 or len(numbers) % 2):
                    raise ValueError("Invalid SVG points")
            elif key == "transform":
                pattern = r"(translate|scale|rotate|matrix)\(([^()]*)\)"
                parts = list(re.finditer(pattern, v))
                if not parts or len(parts) > 8 or re.sub(pattern, "", v).strip():
                    raise ValueError("Unsupported SVG transform")
                for part in parts:
                    n = len(_numbers(part[2]))
                    if n not in {"translate": {1, 2}, "scale": {1, 2}, "rotate": {1, 3}, "matrix": {6}}[part[1]]:
                        raise ValueError("Invalid SVG transform")
            elif key == "font-family":
                if len(v) > 100 or not re.fullmatch(r"[a-zA-Z0-9_ ,'-]+", v):
                    raise ValueError("Unsupported SVG font")
            elif key == "preserveAspectRatio" and depth == 0 and v == "xMidYMid meet":
                pass
            elif key == "id" and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,79}", v):
                pass
            elif key == "role" and v == "img":
                pass
            elif key == "aria-label" and len(v) <= 240:
                pass
            else:
                raise ValueError(f"Unsupported SVG attribute: {key}")
        if node.text and node.text.strip() and tag not in {"text", "tspan", "title", "desc"}:
            raise ValueError("SVG text must use text elements")
        for child in node:
            visit(child, depth + 1)
            if child.tail and child.tail.strip() and tag not in {"text", "tspan", "title", "desc"}:
                raise ValueError("SVG text must use text elements")

    if root.tag not in {"svg", "{" + SVG_NS + "}svg"} or "viewBox" not in root.attrib:
        raise ValueError("SVG root requires viewBox")
    visit(root)
    if not drawable or sum(len(t) for t in root.itertext()) > 4000:
        raise ValueError("SVG requires bounded illustration content")
    return value.strip()
