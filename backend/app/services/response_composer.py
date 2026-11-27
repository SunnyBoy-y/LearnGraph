"""Unified Artifact Registry and Response Composer for agent chat responses.

Tools produce raw artifacts (sandbox files, magic cards, generated images,
declarative components, Stream UI blocks). The Artifact Registry assigns
canonical identities, types, capabilities, and content fingerprints so duplicate
representations of the same deliverable (for example, ``sandbox_publish_file``
publishing ``outputs/svg-demo.svg`` alongside ``canvas_emit_magic_card``
wrapping the identical ``<svg>``) are merged into a single deliverable.

The Response Composer then decides final ordering and layout hints so primary
visual deliverables appear ahead of explanatory prose rather than sinking below
walls of text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html import unescape
from xml.etree import ElementTree as ET
from typing import Any, Callable

ARTIFACT_PART_KINDS = frozenset(
    {
        "sticker",
        "image",
        "sandbox",
        "sandbox_artifact",
        "subapp_artifact",
        "magic_card",
        "component",
        "chart",
        "svg",
        "ui",
    }
)

_SVG_BLOCK_RE = re.compile(r"<svg\b[^>]*>[\s\S]*?</svg>", re.IGNORECASE)
_INTERACTIVE_HTML_RE = re.compile(
    r"<script\b|<canvas\b|<form\b|<input\b|<button\b|<select\b|<textarea\b|"
    r"\bon(?:click|change|input|submit|pointerdown|keydown)\s*=",
    re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_SVG_VIEWBOX_RE = re.compile(r"""viewBox\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_SVG_TEXT_NODE_RE = re.compile(r"<text\b[^>]*>([\s\S]*?)</text>", re.IGNORECASE)
_SVG_SHAPE_TAG_RE = re.compile(
    r"<(rect|circle|ellipse|line|polygon|polyline|path|text)\b", re.IGNORECASE
)
_WS_RE = re.compile(r"\s+")
DISPLAY_MODES = frozenset({"inline", "artifact"})
HEIGHT_MODES = frozenset({"auto", "aspect-ratio", "fixed", "bounded"})


def normalize_height_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in HEIGHT_MODES else None


def resolve_height_mode(data: dict[str, Any], *, artifact_type: str) -> str:
    """Resolve sizing strategy ('auto' | 'aspect-ratio' | 'fixed' | 'bounded')."""
    reg = data.get("artifact_registry")
    viewport = data.get("viewport")
    explicit = (
        normalize_height_mode(data.get("height_mode"))
        or (
            normalize_height_mode(viewport.get("height_mode"))
            or normalize_height_mode(viewport.get("mode"))
            if isinstance(viewport, dict)
            else None
        )
        or (
            normalize_height_mode(reg.get("height_mode"))
            if isinstance(reg, dict)
            else None
        )
    )
    if explicit is not None:
        return explicit
    if artifact_type == "svg":
        return "aspect-ratio"
    return "auto"


def normalize_display_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in DISPLAY_MODES else None


def resolve_display_mode(
    part_type: str,
    data: dict[str, Any],
    *,
    artifact_type: str,
    is_static_svg_card: bool,
) -> tuple[str, bool]:
    """Resolve explicit or purpose-based display mode ('inline' | 'artifact')."""
    reg = data.get("artifact_registry")
    explicit = (
        normalize_display_mode(data.get("display_mode"))
        or normalize_display_mode(data.get("presentation_mode"))
        or normalize_display_mode(data.get("render_mode"))
        or (
            normalize_display_mode(reg.get("display_mode"))
            if isinstance(reg, dict)
            else None
        )
    )
    if explicit is not None:
        return explicit, True
    if part_type == "svg":
        return "inline", False
    if part_type == "magic_card" and is_static_svg_card:
        return "inline", False
    if part_type == "ui" or artifact_type == "ui":
        return "inline", False
    return "artifact", False


def extract_svg_markup(source: str | None) -> str | None:
    if not isinstance(source, str) or "<svg" not in source.lower():
        return None
    match = _SVG_BLOCK_RE.search(source)
    return match.group(0).strip() if match else None


def is_static_svg_html(source: str | None) -> bool:
    if not isinstance(source, str):
        return False
    svg = extract_svg_markup(source)
    if not svg:
        return False
    return not bool(_INTERACTIVE_HTML_RE.search(source))


def svg_content_fingerprint(source: str | None) -> str | None:
    svg = extract_svg_markup(source)
    if not svg or len(svg) > 256_000 or "<!" in svg or "<?" in svg:
        return None
    try:
        root = ET.fromstring(svg)
        def canonical(node, depth=0):
            if depth > 32:
                raise ValueError("SVG too deep")
            tag = node.tag.removeprefix("{http://www.w3.org/2000/svg}")
            attrs = sorted((k, v) for k, v in node.attrib.items()
                           if k != "font-family" and not (depth == 0 and k in {"width", "height"}))
            text = (node.text or "").strip()
            tail = (node.tail or "").strip()
            return (tag, attrs, text, tail, [canonical(child, depth + 1) for child in node])
        return hashlib.sha256(repr(canonical(root)).encode("utf-8")).hexdigest()[:24]
    except (ET.ParseError, ValueError, UnicodeError):
        return None


def _file_extension(path_or_title: str | None) -> str:
    if not isinstance(path_or_title, str):
        return ""
    name = path_or_title.strip().rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _basename(path: str | None) -> str:
    if not isinstance(path, str):
        return ""
    return path.strip().rsplit("/", 1)[-1].lower()


def _is_svg_file_data(data: dict[str, Any]) -> bool:
    mime = str(data.get("mime_type") or "").strip().lower()
    if mime == "image/svg+xml" or mime.startswith("image/svg"):
        return True
    return (
        _file_extension(data.get("path")) == "svg"
        or _file_extension(data.get("title")) == "svg"
    )


def _is_html_file_data(data: dict[str, Any]) -> bool:
    mime = str(data.get("mime_type") or "").strip().lower()
    if mime in {"text/html", "application/xhtml+xml"}:
        return True
    ext = _file_extension(data.get("path")) or _file_extension(data.get("title"))
    return ext in {"html", "htm"}


def _is_image_file_data(data: dict[str, Any]) -> bool:
    mime = str(data.get("mime_type") or "").strip().lower()
    if mime.startswith("image/"):
        return True
    ext = _file_extension(data.get("path")) or _file_extension(data.get("title"))
    return ext in {"png", "jpg", "jpeg", "webp", "gif", "svg", "avif"}


def _collect_tool_sources(parts: list[Any]) -> dict[str, str]:
    """Extract written SVG/HTML file contents from tool_call parts by basename."""
    by_name: dict[str, str] = {}
    for part in parts:
        part_type = getattr(part, "part_type", None) or getattr(part, "type", None)
        if part_type != "tool_call":
            continue
        data = getattr(part, "data", None) or {}
        if not isinstance(data, dict):
            continue
        tool_name = str(data.get("tool_name") or "")
        if tool_name not in {"sandbox_write_file", "sandbox_create_file"}:
            continue
        tool_input = data.get("input")
        if not isinstance(tool_input, dict):
            continue
        path = tool_input.get("path")
        content = tool_input.get("content")
        if isinstance(path, str) and isinstance(content, str) and content.strip():
            by_name[_basename(path)] = content
            by_name[path.strip()] = content
    return by_name


@dataclass
class ArtifactEntry:
    part: Any
    canonical_id: str
    artifact_type: str
    title: str
    file_id: str | None = None
    path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    card_id: str | None = None
    card_instance_id: str | None = None
    bundle_id: str | None = None
    block_id: str | None = None
    preview_html: str | None = None
    svg_markup: str | None = None
    content_fingerprint: str | None = None
    is_static_svg_card: bool = False
    display_mode: str = "artifact"
    explicit_display_mode: bool = False
    height_mode: str = "auto"
    primary_deliverable: bool = True
    capabilities: dict[str, bool] = field(default_factory=dict)
    merged_from_ids: list[str] = field(default_factory=list)
    suppressed: bool = False
    merged_into: str | None = None


@dataclass
class ArtifactReconciliationResult:
    entries: list[ArtifactEntry]
    suppressed_part_ids: set[str]
    suppressed_records: list[Any]
    modified_records: list[Any]


class ArtifactRegistry:
    """Builds canonical artifact records and reconciles duplicate deliverables."""

    def register_parts(self, parts: list[Any]) -> list[ArtifactEntry]:
        tool_sources = _collect_tool_sources(parts)
        entries: list[ArtifactEntry] = []
        for part in parts:
            part_type = getattr(part, "part_type", None) or getattr(part, "type", None)
            status = getattr(part, "status", None) or "completed"
            if part_type not in ARTIFACT_PART_KINDS or status == "failed":
                continue
            data = dict(getattr(part, "data", None) or {})
            entry = self._build_entry(part, part_type, data, tool_sources)
            entries.append(entry)
        return entries

    def _build_entry(
        self,
        part: Any,
        part_type: str,
        data: dict[str, Any],
        tool_sources: dict[str, str],
    ) -> ArtifactEntry:
        file_id = str(data["file_id"]) if isinstance(data.get("file_id"), str) and data.get("file_id") else None
        path = str(data["path"]) if isinstance(data.get("path"), str) and data.get("path") else None
        mime_type = str(data["mime_type"]) if isinstance(data.get("mime_type"), str) and data.get("mime_type") else None
        size_bytes = data.get("size_bytes") if isinstance(data.get("size_bytes"), int) else None
        sha256 = (
            str(data.get("sha256") or data.get("blob_sha256") or "") or None
        )
        card_id = str(data["card_id"]) if isinstance(data.get("card_id"), str) and data.get("card_id") else None
        card_instance_id = (
            str(data["card_instance_id"])
            if isinstance(data.get("card_instance_id"), str) and data.get("card_instance_id")
            else None
        )
        bundle_id = str(data["bundle_id"]) if isinstance(data.get("bundle_id"), str) and data.get("bundle_id") else None
        block_id = str(data["block_id"]) if isinstance(data.get("block_id"), str) and data.get("block_id") else None
        preview_html = str(data["preview_html"]) if isinstance(data.get("preview_html"), str) and data.get("preview_html") else None

        title = (
            str(data.get("title") or "").strip()
            or (path.rsplit("/", 1)[-1] if path else "")
            or str(getattr(part, "content", None) or "").strip()
            or "产物"
        )

        svg_markup: str | None = None
        is_static_svg_card = False
        if part_type == "svg":
            artifact_type, svg_markup = "svg", data.get("svg_markup")
        elif part_type in {"sandbox_artifact", "sandbox"} and _is_svg_file_data(data):
            raw_source = (path and (tool_sources.get(path) or tool_sources.get(_basename(path)))) or None
            svg_markup = extract_svg_markup(raw_source) or extract_svg_markup(preview_html)
            artifact_type = "svg"
        elif part_type == "magic_card":
            if is_static_svg_html(preview_html):
                svg_markup = extract_svg_markup(preview_html)
                is_static_svg_card = True
                artifact_type = "svg"
            else:
                artifact_type = "magic_card"
        elif part_type == "image" or (part_type in {"sandbox_artifact", "sandbox"} and _is_image_file_data(data)):
            artifact_type = "image"
        elif part_type in {"sandbox_artifact", "sandbox"} and _is_html_file_data(data):
            artifact_type = "html"
        elif part_type in {"sandbox_artifact", "sandbox"}:
            artifact_type = "file"
        elif part_type == "component":
            component_type = str(data.get("component_type") or "")
            artifact_type = "component"
            primary_deliverable = component_type not in {
                "graph_update_proposal",
                "option_group",
                "single_choice",
                "multiple_choice",
                "fill_blank",
                "short_answer_table",
            }
        else:
            artifact_type = part_type
            primary_deliverable = artifact_type in {
                "chart",
                "subapp_artifact",
                "ui",
            }

        if part_type != "component":
            primary_deliverable = artifact_type in {
                "svg",
                "image",
                "html",
                "magic_card",
                "chart",
                "subapp_artifact",
                "ui",
            }

        fingerprint = svg_content_fingerprint(svg_markup or preview_html)
        if not fingerprint and sha256:
            fingerprint = f"sha256:{sha256[:24]}"

        canonical_id = (
            file_id
            or card_id
            or bundle_id
            or block_id
            or fingerprint
            or str(getattr(part, "id", ""))
        )

        capabilities = {
            "inline_preview": artifact_type in {"svg", "image", "magic_card", "html", "chart", "subapp_artifact", "ui"},
            "download": bool(file_id) or artifact_type in {"svg", "image", "html"},
            "fullscreen": artifact_type in {"svg", "image", "magic_card", "html", "subapp_artifact"},
            "wide_layout": artifact_type in {"svg", "magic_card", "html", "chart", "subapp_artifact"},
        }
        display_mode, explicit_display_mode = resolve_display_mode(
            part_type,
            data,
            artifact_type=artifact_type,
            is_static_svg_card=is_static_svg_card,
        )
        height_mode = resolve_height_mode(data, artifact_type=artifact_type)

        return ArtifactEntry(
            part=part,
            canonical_id=canonical_id,
            artifact_type=artifact_type,
            title=title,
            file_id=file_id,
            path=path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            card_id=card_id,
            card_instance_id=card_instance_id,
            bundle_id=bundle_id,
            block_id=block_id,
            preview_html=preview_html,
            svg_markup=svg_markup,
            content_fingerprint=fingerprint,
            is_static_svg_card=is_static_svg_card,
            display_mode=display_mode,
            explicit_display_mode=explicit_display_mode,
            height_mode=height_mode,
            primary_deliverable=primary_deliverable,
            capabilities=capabilities,
        )

    def _can_merge(self, left: ArtifactEntry, right: ArtifactEntry) -> bool:
        if left.suppressed or right.suppressed:
            return False
        if left.file_id and right.file_id and left.file_id == right.file_id:
            return True
        if left.sha256 and right.sha256 and left.sha256 == right.sha256:
            return True
        if left.bundle_id and right.bundle_id and left.bundle_id == right.bundle_id:
            return True
        if left.card_id and right.card_id and left.card_id == right.card_id:
            return True
        if getattr(left.part, "part_type", None) == getattr(right.part, "part_type", None) == "svg":
            return False
        return bool(left.artifact_type == right.artifact_type == "svg" and left.content_fingerprint
                    and left.content_fingerprint == right.content_fingerprint)

    def reconcile(self, parts: list[Any]) -> ArtifactReconciliationResult:
        entries = self.register_parts(parts)
        suppressed_ids: set[str] = set()
        suppressed_records: list[Any] = []
        modified_records: list[Any] = []

        for idx, primary in enumerate(entries):
            if primary.suppressed:
                continue
            for candidate in entries[idx + 1 :]:
                if not self._can_merge(primary, candidate):
                    continue
                # Prefer keeping the richer file-backed entry or magic_card merged cleanly
                # onto the surviving record (preserving file_id + preview + title).
                winner, loser = self._pick_winner(primary, candidate)
                self._merge_entries(winner, loser)
                loser.suppressed = True
                loser.merged_into = str(getattr(winner.part, "id", ""))
                loser_id = str(getattr(loser.part, "id", ""))
                if loser_id:
                    suppressed_ids.add(loser_id)
                suppressed_records.append(loser.part)
                if winner.part not in modified_records:
                    modified_records.append(winner.part)
                # Update primary pointer if candidate won
                if winner is candidate:
                    primary = candidate

        # Stamp artifact_registry metadata onto surviving entries
        for entry in entries:
            if entry.suppressed:
                continue
            data = dict(getattr(entry.part, "data", None) or {})
            data["artifact_registry"] = {
                "canonical_id": entry.canonical_id,
                "artifact_type": entry.artifact_type,
                "display_mode": entry.display_mode,
                "height_mode": entry.height_mode,
                "title": entry.title,
                "capabilities": entry.capabilities,
                "merged_from_ids": entry.merged_from_ids,
                "primary_deliverable": entry.primary_deliverable,
                "layout": "wide" if entry.capabilities.get("wide_layout") else data.get("layout", "block"),
            }
            if entry.explicit_display_mode and not normalize_display_mode(data.get("display_mode")):
                data["display_mode"] = entry.display_mode
            if entry.file_id and not data.get("file_id"):
                data["file_id"] = entry.file_id
            if entry.path and not data.get("path"):
                data["path"] = entry.path
            if entry.size_bytes is not None and data.get("size_bytes") is None:
                data["size_bytes"] = entry.size_bytes
            if entry.mime_type and not data.get("mime_type"):
                data["mime_type"] = entry.mime_type
            if entry.sha256 and not data.get("sha256"):
                data["sha256"] = entry.sha256
            if entry.preview_html and not data.get("preview_html"):
                data["preview_html"] = entry.preview_html
            if entry.svg_markup and not data.get("svg_markup"):
                data["svg_markup"] = entry.svg_markup
            if entry.title and (not data.get("title") or _file_extension(str(data.get("title"))) == "svg"):
                data["title"] = entry.title
            entry.part.data = data

        for entry in entries:
            if not entry.suppressed:
                continue
            data = dict(getattr(entry.part, "data", None) or {})
            data["artifact_registry"] = {
                "canonical_id": entry.canonical_id,
                "artifact_type": entry.artifact_type,
                "suppressed": True,
                "merged_into": entry.merged_into,
            }
            entry.part.data = data

        return ArtifactReconciliationResult(
            entries=entries,
            suppressed_part_ids=suppressed_ids,
            suppressed_records=suppressed_records,
            modified_records=modified_records,
        )

    def _pick_winner(self, left: ArtifactEntry, right: ArtifactEntry) -> tuple[ArtifactEntry, ArtifactEntry]:
        if getattr(left.part, "part_type", None) == "svg":
            return left, right
        if getattr(right.part, "part_type", None) == "svg":
            return right, left
        # Prefer file-backed sandbox_artifact when merging with a static-SVG magic_card
        # so file_id/path/download stay first-class, while adopting the card's title & preview.
        left_score = (2 if left.file_id else 0) + (1 if left.preview_html else 0)
        right_score = (2 if right.file_id else 0) + (1 if right.preview_html else 0)
        if right_score > left_score:
            return right, left
        return left, right

    def _merge_entries(self, winner: ArtifactEntry, loser: ArtifactEntry) -> None:
        loser_id = str(getattr(loser.part, "id", ""))
        if loser_id and loser_id not in winner.merged_from_ids:
            winner.merged_from_ids.append(loser_id)
        if not winner.file_id and loser.file_id:
            winner.file_id = loser.file_id
        if not winner.path and loser.path:
            winner.path = loser.path
        if winner.size_bytes is None and loser.size_bytes is not None:
            winner.size_bytes = loser.size_bytes
        if not winner.mime_type and loser.mime_type:
            winner.mime_type = loser.mime_type
        if not winner.sha256 and loser.sha256:
            winner.sha256 = loser.sha256
        if not winner.preview_html and loser.preview_html:
            winner.preview_html = loser.preview_html
        if not winner.svg_markup and loser.svg_markup:
            winner.svg_markup = loser.svg_markup
        if not winner.card_id and loser.card_id:
            winner.card_id = loser.card_id
        if not winner.card_instance_id and loser.card_instance_id:
            winner.card_instance_id = loser.card_instance_id
        if not winner.bundle_id and loser.bundle_id:
            winner.bundle_id = loser.bundle_id
        # Prefer human-readable non-filename title (e.g. "SVG 示例图 · 三段式流程")
        if _file_extension(winner.title) and not _file_extension(loser.title) and loser.title:
            winner.title = loser.title
        for key, enabled in loser.capabilities.items():
            if enabled:
                winner.capabilities[key] = True
        if not winner.explicit_display_mode and loser.explicit_display_mode:
            winner.display_mode = loser.display_mode
            winner.explicit_display_mode = True
        winner.primary_deliverable = winner.primary_deliverable or loser.primary_deliverable


@dataclass
class ComposePlan:
    new_sequence: list[Any]
    extra_records: list[Any]
    moved_records: list[tuple[Any, str]]
    reconciliation: ArtifactReconciliationResult


class ResponseComposer:
    """Composes final answer part order and metadata from ArtifactRegistry."""

    def __init__(self, registry: ArtifactRegistry | None = None) -> None:
        self.registry = registry or ArtifactRegistry()

    def is_primary_deliverable(self, part: Any, entry_by_part_id: dict[str, ArtifactEntry]) -> bool:
        part_id = str(getattr(part, "id", ""))
        entry = entry_by_part_id.get(part_id)
        if entry is not None:
            return entry.primary_deliverable and not entry.suppressed
        part_type = getattr(part, "part_type", None) or getattr(part, "type", None)
        if part_type in {"image", "magic_card", "chart", "subapp_artifact", "ui"}:
            return True
        if part_type == "component":
            data = getattr(part, "data", None) or {}
            return data.get("component_type") not in {
                "graph_update_proposal",
                "option_group",
                "single_choice",
                "multiple_choice",
                "fill_blank",
                "short_answer_table",
            }
        return False

    @staticmethod
    def _stamp_placement(part: Any, placement: str) -> None:
        data = dict(getattr(part, "data", None) or {})
        reg = dict(data.get("artifact_registry") or {})
        reg["placement"] = placement
        data["artifact_registry"] = reg
        part.data = data

    def plan_answer_sequence(
        self,
        *,
        all_parts: list[Any],
        text_record: Any,
        markers: list[dict[str, Any]],
        segments: list[str],
        is_implicitly_anchored: Callable[[Any], bool],
        create_image_record: Callable[[dict[str, Any], int], Any],
        create_segment_record: Callable[[str, int], Any],
    ) -> ComposePlan:
        """Deduplicate artifacts and compose deliverable-first answer sequence."""
        reconciliation = self.registry.reconcile(all_parts)
        entry_by_part_id: dict[str, ArtifactEntry] = {
            str(getattr(entry.part, "id", "")): entry
            for entry in reconciliation.entries
            if getattr(entry.part, "id", None) is not None
        }
        part_by_id: dict[str, Any] = {
            str(getattr(part, "id", "")): part
            for part in all_parts
            if getattr(part, "id", None) is not None
        }
        redirect_by_id: dict[str, Any] = {}
        for entry in reconciliation.entries:
            if entry.suppressed and entry.merged_into and entry.merged_into in part_by_id:
                redirect_by_id[str(getattr(entry.part, "id", ""))] = part_by_id[entry.merged_into]

        suppressed_ids = set(reconciliation.suppressed_part_ids)
        referenced_ids: set[str] = set()
        referenced_parts: list[Any] = []
        resolved_markers: list[dict[str, Any]] = []
        for marker in markers:
            if marker.get("kind") != "artifact":
                resolved_markers.append(marker)
                continue
            part = marker["part"]
            part_id = str(getattr(part, "id", ""))
            if part_id in suppressed_ids and part_id in redirect_by_id:
                part = redirect_by_id[part_id]
                part_id = str(getattr(part, "id", ""))
            resolved_markers.append({**marker, "part": part})
            if part_id in referenced_ids:
                continue
            referenced_ids.add(part_id)
            referenced_parts.append(part)
            layout = marker.get("layout")
            display_mode = normalize_display_mode(marker.get("display_mode"))
            if layout or display_mode:
                updated = dict(getattr(part, "data", None) or {})
                if layout:
                    updated["layout"] = layout
                if display_mode:
                    updated["display_mode"] = display_mode
                    reg = dict(updated.get("artifact_registry") or {})
                    reg["display_mode"] = display_mode
                    updated["artifact_registry"] = reg
                part.data = updated
            self._stamp_placement(part, "referenced")

        surviving_artifacts = [
            part
            for part in all_parts
            if getattr(part, "id", None) != getattr(text_record, "id", None)
            and (getattr(part, "part_type", None) or getattr(part, "type", None)) in ARTIFACT_PART_KINDS
            and str(getattr(part, "id", "")) not in suppressed_ids
        ]
        anchored_ids: set[str] = set()
        primary_deliverable_parts: list[Any] = []
        sink_parts: list[Any] = []
        for part in surviving_artifacts:
            part_id = str(getattr(part, "id", ""))
            if part_id in referenced_ids:
                continue
            if is_implicitly_anchored(part):
                anchored_ids.add(part_id)
                self._stamp_placement(part, "anchored")
            elif self.is_primary_deliverable(part, entry_by_part_id):
                primary_deliverable_parts.append(part)
                self._stamp_placement(part, "primary_first")
            else:
                sink_parts.append(part)
                self._stamp_placement(part, "sink")

        primary_ids = {str(getattr(part, "id", "")) for part in primary_deliverable_parts}
        sink_ids = {str(getattr(part, "id", "")) for part in sink_parts}
        text_id = getattr(text_record, "id", None)

        new_sequence: list[Any] = []
        for part in all_parts:
            part_id = str(getattr(part, "id", ""))
            if (
                getattr(part, "id", None) == text_id
                or part_id in referenced_ids
                or part_id in primary_ids
                or part_id in sink_ids
                or part_id in suppressed_ids
            ):
                continue
            new_sequence.append(part)

        # Deliverable-first: place unreferenced primary deliverables immediately
        # before the explanatory answer text so users see the artifact first.
        new_sequence.extend(primary_deliverable_parts)
        new_sequence.append(text_record)

        extra_records: list[Any] = []
        appended_referenced: set[str] = set()
        for index, marker in enumerate(resolved_markers):
            if marker.get("kind") == "image":
                temp_ordinal = -1_000_000 - len(new_sequence) - len(extra_records)
                image_record = create_image_record(marker["data"], temp_ordinal)
                extra_records.append(image_record)
                new_sequence.append(image_record)
            elif marker.get("kind") == "artifact":
                part = marker["part"]
                part_id = str(getattr(part, "id", ""))
                if part_id not in appended_referenced:
                    appended_referenced.add(part_id)
                    new_sequence.append(part)
            segment = segments[index + 1]
            if segment:
                temp_ordinal = -1_000_000 - len(new_sequence) - len(extra_records)
                segment_record = create_segment_record(segment, temp_ordinal)
                extra_records.append(segment_record)
                new_sequence.append(segment_record)

        new_sequence.extend(sink_parts)
        suppressed_parts = [
            part for part in all_parts if str(getattr(part, "id", "")) in suppressed_ids
        ]
        new_sequence.extend(suppressed_parts)

        moved_records: list[tuple[Any, str]] = []
        seen_moved: set[str] = set()

        def _add_moved(part: Any, reason: str) -> None:
            pid = str(getattr(part, "id", ""))
            if pid and pid not in seen_moved:
                seen_moved.add(pid)
                moved_records.append((part, reason))

        for part in referenced_parts:
            _add_moved(part, "artifact_reference")
        for part in primary_deliverable_parts:
            _add_moved(part, "artifact_primary")
        for part in sink_parts:
            _add_moved(part, "artifact_sink")
        for part in suppressed_parts:
            _add_moved(part, "artifact_deduplicated")
        for part in reconciliation.modified_records:
            if str(getattr(part, "id", "")) not in suppressed_ids:
                _add_moved(part, "artifact_reconciled")

        return ComposePlan(
            new_sequence=new_sequence,
            extra_records=extra_records,
            moved_records=moved_records,
            reconciliation=reconciliation,
        )
