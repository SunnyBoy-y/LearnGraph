"""Deterministic SVG covers for the graph bookshelf.

The cover skill is deliberately side-effect free.  It turns graph metadata
into a data URL so the API can return it as part of the graph summary and the
web client can embed it directly without an image upload or a second asset
service.
"""
from __future__ import annotations

from html import escape
import base64
import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote
from xml.etree import ElementTree


logger = logging.getLogger(__name__)


def _safe_title(title: str) -> str:
    # XML 1.0 forbids control characters and lone surrogates, even in text.
    return "".join(
        char for char in title
        if char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF
    )


def _artwork_cover(title: str, *, kind: str) -> str:
    """Keep the illustration full-bleed; the bookshelf already shows its title."""
    title = _safe_title(title) if isinstance(title, str) else ""
    label = escape(title, quote=True)
    artwork = _default_artwork_data_url(title)
    # This vector layer also renders if a bundled image is missing or fails to
    # decode. Never fall back to the retired pastel curve illustration.
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 300" data-cover-kind="{kind}" data-cover-version="2">
<title>{label}</title>
<defs><radialGradient id="glow"><stop stop-color="#234d58"/><stop offset="1" stop-color="#091b26"/></radialGradient></defs>
<rect width="640" height="300" fill="url(#glow)"/>
<g fill="none" stroke="#c6ae7a" stroke-opacity=".4">
<ellipse cx="430" cy="145" rx="166" ry="92" transform="rotate(-24 430 145)"/>
<circle cx="430" cy="145" r="65"/><circle cx="430" cy="145" r="111"/>
<path d="M72 225L180 148L294 196L430 145L563 67M180 148L248 66L430 145"/>
</g><g fill="#dfc896"><circle cx="72" cy="225" r="3"/><circle cx="180" cy="148" r="5"/>
<circle cx="294" cy="196" r="4"/><circle cx="430" cy="145" r="12"/><circle cx="563" cy="67" r="4"/>
<circle cx="248" cy="66" r="3"/></g>
{f'<image href="{artwork}" width="640" height="300" preserveAspectRatio="xMidYMid slice"/>' if artwork else ''}
</svg>'''
    return f"data:image/svg+xml,{quote(svg)}"


def default_graph_cover(title: str = "") -> str:
    """Return an independent, offline-safe illustration for decoration failures."""
    return _artwork_cover(title, kind="fallback")


def _default_artwork_data_url(title: str = "") -> str:
    """Match an illustration to the title without model calls or network access."""
    subjects = (
        ("wen", ("文学", "语文", "写作", "小说", "诗")),
        ("shi", ("历史", "史学", "朝代", "文明")),
        ("zhe", ("哲学", "伦理", "逻辑")),
        ("si", ("思维", "思考", "认知", "心理")),
        ("shu", ("数学", "数理", "算法", "几何", "概率")),
        ("li", ("物理", "力学", "光学", "电磁")),
        ("hua", ("化学", "化工", "分子", "有机")),
        ("zheng", ("政治", "政史", "社会", "公共")),
        ("stars", ("星辰", "天文", "宇宙", "星空", "太空")),
    )
    key = next((key for key, words in subjects if any(word in title for word in words)), "default")
    return _load_artwork_data_url(key)


@lru_cache(maxsize=16)
def _load_artwork_data_url(key: str) -> str:
    """Cache by asset rather than title so a large shelf reuses encoded bytes."""
    assets = Path(__file__).resolve().parent.parent / "assets"
    asset = assets / "graph-covers" / f"{key}.jpg" if key != "default" else assets / "graph-cover-default.jpg"
    if not asset.exists():
        asset = assets / "graph-cover-default.jpg"
    try:
        encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
    except OSError:
        logger.warning("Default graph cover artwork is unavailable; using vector fallback")
        return ""
    return f"data:image/jpeg;base64,{encoded}"


def generate_graph_cover(
    title: str,
    *,
    progress: float = 0.0,
    node_labels: list[str] | None = None,
) -> str:
    """Generate a cover without allowing decoration failures to break a graph."""
    try:
        cover = _render_graph_cover(title, progress=progress, node_labels=node_labels)
        prefix = "data:image/svg+xml,"
        if not cover.startswith(prefix):
            raise ValueError("Invalid cover data URL")
        root = ElementTree.fromstring(unquote(cover[len(prefix):]))
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("Invalid cover SVG root")
        return cover
    except Exception:
        logger.warning("Graph cover generation failed; using default cover", exc_info=True)
        return default_graph_cover(title)


def generate_template_cover(title: str, template: str = "paper") -> str:
    """Return one of the user-selectable deterministic cover templates."""
    palettes = {
        "ancient": ("#eee7d8", "#c9a66b", "#7a4e2d", "#2d241e"),
        "literature": ("#f5f0ea", "#d9b7a2", "#9b4d54", "#352b2c"),
        "history": ("#e9edf1", "#8ea4b5", "#31556b", "#182b36"),
        "science": ("#e7f3f1", "#9fd4ca", "#277f78", "#163f45"),
        "chemistry": ("#eef0fa", "#b6b9e8", "#5a56a6", "#292750"),
        "paper": ("#f4f0e8", "#d9d1c2", "#445849", "#25372c"),
        "midnight": ("#171c35", "#30395f", "#a5b4fc", "#f0f1ff"),
        "sunrise": ("#fff0df", "#ffd2ac", "#db754c", "#7c3e2b"),
    }
    template = template if template in palettes else "paper"
    background, accent, line, ink = palettes[template]
    label = escape(_safe_title(title)[:18], quote=True)
    # Art-directed raster studies are used for the five new subject themes.
    if template in {"ancient", "literature", "history", "science", "chemistry"}:
        asset = Path(__file__).resolve().parent.parent / "assets" / "graph-covers" / f"{template}.jpg"
        try:
            artwork = f"data:image/jpeg;base64,{base64.b64encode(asset.read_bytes()).decode('ascii')}"
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 300"><image href="{artwork}" width="640" height="300" preserveAspectRatio="xMidYMid slice"/><rect width="640" height="86" fill="{ink}" opacity=".68"/><text x="30" y="52" font-family="sans-serif" font-size="24" font-weight="600" fill="#fffaf1">{label}</text></svg>'
            return f"data:image/svg+xml,{quote(svg)}"
        except OSError:
            pass
    if template == "ancient":
        decoration = f'<circle cx="505" cy="150" r="105" fill="none" stroke="{accent}" stroke-width="2"/><circle cx="505" cy="150" r="78" fill="none" stroke="{accent}" stroke-width="1"/><path d="M36 238H604M70 218H570" stroke="{line}" stroke-width="3"/><path d="M90 218V116M150 218V98M210 218V116M270 218V98" stroke="{line}" stroke-width="8"/><path d="M70 116Q120 75 150 98Q180 75 210 116Q240 75 270 98" fill="none" stroke="{accent}" stroke-width="4"/>'
    elif template == "literature":
        decoration = f'<path d="M88 220V104Q164 82 240 104V220Q164 198 88 220ZM240 104Q316 82 392 104V220Q316 198 240 220Z" fill="{accent}" opacity=".55" stroke="{line}" stroke-width="3"/><path d="M118 133H214M266 133H362M118 160H214M266 160H362" stroke="{line}" stroke-width="3" stroke-linecap="round"/><circle cx="520" cy="106" r="28" fill="none" stroke="{line}" stroke-width="3"/><path d="M520 78V134M492 106H548" stroke="{line}" stroke-width="2"/>'
    elif template == "history":
        decoration = f'<path d="M56 224H584M92 224V136M160 224V136M228 224V136M296 224V136M364 224V136M432 224V136M500 224V136" stroke="{line}" stroke-width="9"/><path d="M70 136L338 70L570 136Z" fill="{accent}" stroke="{line}" stroke-width="4"/><circle cx="338" cy="70" r="11" fill="{line}"/><path d="M88 248H568" stroke="{accent}" stroke-width="6"/>'
    elif template == "science":
        decoration = f'<circle cx="478" cy="148" r="76" fill="none" stroke="{accent}" stroke-width="3"/><ellipse cx="478" cy="148" rx="112" ry="36" fill="none" stroke="{line}" stroke-width="3" transform="rotate(-28 478 148)"/><ellipse cx="478" cy="148" rx="112" ry="36" fill="none" stroke="{line}" stroke-width="3" transform="rotate(28 478 148)"/><circle cx="478" cy="148" r="16" fill="{line}"/><path d="M72 226Q160 170 250 226T420 220" fill="none" stroke="{line}" stroke-width="4"/>'
    elif template == "chemistry":
        decoration = f'<path d="M142 94V150L78 246Q72 258 88 258H236Q252 258 246 246L182 150V94" fill="{accent}" opacity=".65" stroke="{line}" stroke-width="4"/><path d="M118 94H206M104 210H222" stroke="{line}" stroke-width="5"/><circle cx="426" cy="112" r="24" fill="none" stroke="{line}" stroke-width="4"/><circle cx="530" cy="188" r="34" fill="none" stroke="{line}" stroke-width="4"/><path d="M448 126L505 168" stroke="{line}" stroke-width="4"/><circle cx="402" cy="220" r="14" fill="{accent}" stroke="{line}" stroke-width="3"/>'
    elif template == "paper":
        decoration = f'<path d="M370 88H566M370 120H540M370 152H556M370 184H508" stroke="{line}" stroke-width="3"/><rect x="60" y="120" width="180" height="130" rx="12" fill="{accent}"/><path d="M150 133V237M76 148Q108 136 137 148M163 148Q192 136 224 148" fill="none" stroke="{line}" stroke-width="4"/>'
    elif template == "midnight":
        decoration = f'<path d="M100 208L230 114L350 216L515 98M230 114L515 98" fill="none" stroke="{line}" stroke-width="2"/><g fill="{ink}"><circle cx="100" cy="208" r="8"/><circle cx="230" cy="114" r="13"/><circle cx="350" cy="216" r="8"/><circle cx="515" cy="98" r="9"/></g>'
    else:
        decoration = f'<circle cx="492" cy="155" r="78" fill="{accent}"/><path d="M0 264Q130 166 300 248T640 220V300H0Z" fill="{line}"/><path d="M0 284Q180 230 370 280T640 258" fill="none" stroke="{ink}" stroke-width="2"/>'
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 300"><rect width="640" height="300" fill="{background}"/>{decoration}<text x="36" y="54" font-family="sans-serif" font-size="26" font-weight="600" fill="{ink}">{label}</text></svg>'
    return f"data:image/svg+xml,{quote(svg)}"


def validate_custom_svg(svg: str) -> str:
    """Accept a small static SVG vocabulary; no scripts, embeds, or remote URLs."""
    import re

    if len(svg.encode("utf-8")) > 64 * 1024 or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", svg, re.I):
        raise ValueError("SVG is too large or contains declarations")
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        raise ValueError("SVG markup is invalid") from exc
    namespace = "{http://www.w3.org/2000/svg}"
    tags = {"svg", "g", "defs", "linearGradient", "radialGradient", "stop", "rect", "path", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "clipPath", "mask", "title", "desc"}
    attributes = {"viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity", "fill-opacity", "stroke-opacity", "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline", "transform", "id", "clip-path", "mask", "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod", "fx", "fy", "fr"}
    if root.tag != namespace + "svg":
        raise ValueError("SVG root must declare the SVG namespace")
    elements = list(root.iter())
    if len(elements) > 1000:
        raise ValueError("SVG contains too many elements")
    for element in elements:
        if element.tag not in {namespace + tag for tag in tags}:
            raise ValueError("Unsupported SVG element")
        for key, value in element.attrib.items():
            if key not in attributes:
                raise ValueError("Unsupported SVG attribute")
            if re.search(r"url\s*\(", value, re.I) and not re.fullmatch(r"url\(#[A-Za-z_][A-Za-z0-9_.:-]*\)", value):
                raise ValueError("SVG may reference only local fragment IDs")
    root.set("viewBox", "0 0 640 300")
    root.set("width", "640")
    root.set("height", "300")
    # Serialization discards XML processing instructions and comments.
    return "data:image/svg+xml," + quote(ElementTree.tostring(root, encoding="unicode"))


def _render_graph_cover(
    title: str,
    *,
    progress: float = 0.0,
    node_labels: list[str] | None = None,
) -> str:
    """Use a bundled subject illustration, with progress shown on the card itself."""
    return _artwork_cover(title, kind="generated")


def is_legacy_generated_cover(cover: str) -> bool:
    """Recognize only the retired built-in curve artwork, not custom covers."""
    prefix = "data:image/svg+xml,"
    if not cover.startswith(prefix) or len(cover) > 16_384:
        return False
    try:
        root = ElementTree.fromstring(unquote(cover[len(prefix):]))
    except (ElementTree.ParseError, ValueError):
        return False
    namespace = "{http://www.w3.org/2000/svg}"
    if root.tag != namespace + "svg" or root.get("viewBox") != "0 0 640 300":
        return False
    backgrounds = root.findall(namespace + "rect")
    paths = root.findall(namespace + "path")
    circles = root.findall(namespace + "circle")
    return (
        any(rect.get("width") == "640" and rect.get("height") == "300"
            and rect.get("fill", "").startswith("hsl(") for rect in backgrounds)
        and any(path.get("d") in {
            "M70 230 Q170 90 280 210 T480 180",
            "M80 230 Q180 90 280 210 T470 180",
        } and path.get("stroke-width") == "8" for path in paths)
        and any(circle.get("cx") in {"500", "510"} and circle.get("cy") == "120"
                and circle.get("r") == "82" for circle in circles)
        and any(rect.get("y") == "260" and rect.get("height") == "10"
                and rect.get("fill") == "#d8dadd" for rect in backgrounds)
    )


def resolve_graph_cover(
    stored_cover: str | None,
    title: str,
    *,
    progress: float = 0.0,
    node_labels: list[str] | None = None,
) -> str:
    """Upgrade old automatic artwork on read without overwriting saved choices."""
    if stored_cover and not is_legacy_generated_cover(stored_cover):
        return stored_cover
    return generate_graph_cover(title, progress=progress, node_labels=node_labels)
