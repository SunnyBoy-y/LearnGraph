"""Agent Skill file packages (SKILL.md trees) — D-077 / D-081."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.domain.extension_models import SkillPackageFile, SkillRecord
from app.domain.schemas.extensions import (
    SkillFileContentView,
    SkillFileEntryView,
    SkillFileTreeView,
    SkillFileWriteRequest,
    SkillMkdirRequest,
    SkillPackageCreateRequest,
    SkillValidateResponse,
    SkillView,
)
from app.domain.models import utc_now
from app.repositories.audit import AuditRepository
from app.repositories.extensions import SkillRepository
from app.services.session_workspace import BlobStore

MAX_SKILL_FILE_BYTES = 2 * 1024 * 1024
MAX_SKILL_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_SKILL_FILES = 200
PATH_RE = re.compile(r"^[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)*$")
RESERVED_NAMES = {".", ".."}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def normalize_skill_relative_path(path: str, *, allow_root: bool = False) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if raw.startswith("/"):
        raise AppError(400, "invalid_skill_path", "Skill paths must be relative")
    if not raw:
        if allow_root:
            return ""
        raise AppError(400, "invalid_skill_path", "Skill path is required")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in RESERVED_NAMES or part == "":
            raise AppError(400, "invalid_skill_path", "Skill path may not contain '.' or '..'")
        if part.startswith("."):
            raise AppError(400, "invalid_skill_path", "Hidden path segments are not allowed")
        parts.append(part)
    joined = "/".join(parts)
    if len(joined) > 500:
        raise AppError(400, "invalid_skill_path", "Skill path is too long")
    if not PATH_RE.match(joined):
        raise AppError(
            400,
            "invalid_skill_path",
            "Skill path may only contain letters, digits, '.', '_', '-' and '/'",
        )
    # Reject Windows drive-like segments
    if ":" in joined:
        raise AppError(400, "invalid_skill_path", "Skill path may not contain ':'")
    return joined


def _strip_scalar_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_skill_md_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Frontmatter reader for the common Agent Skills subset.

    Handles top-level ``key: value`` pairs, matched quotes, and folded/literal
    multi-line scalars (``>`` / ``|`` and their ``-``/``+`` chomping variants).
    Nested mappings and lists (e.g. ``metadata:`` children) are skipped instead
    of being mis-read as top-level keys.  Full YAML is intentionally out of
    scope for this trusted parser.
    """

    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, Any] = {}
    body_start: int | None = None
    index = 1
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped == "---":
            body_start = index + 1
            break
        if not stripped or stripped.startswith("#") or line[:1] in (" ", "\t"):
            index += 1
            continue
        if ":" not in line:
            index += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in {">", ">-", ">+", "|", "|-", "|+"}:
            style = value[0]
            block: list[str] = []
            index += 1
            while index < len(lines):
                nxt = lines[index]
                nxt_stripped = nxt.strip()
                if nxt_stripped == "---" or (nxt_stripped and nxt[:1] not in (" ", "\t")):
                    break
                block.append(nxt_stripped)
                index += 1
            if key:
                if style == ">":
                    meta[key] = " ".join(part for part in block if part).strip()
                else:
                    meta[key] = "\n".join(block).strip("\n")
            continue
        if key:
            meta[key] = _strip_scalar_quotes(value)
        index += 1
    if body_start is None:
        return {}, text
    body = "\n".join(lines[body_start:]).lstrip("\n")
    return meta, body


def guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if path.endswith(".md"):
        return "text/markdown; charset=utf-8"
    if path.endswith((".py", ".ts", ".js", ".json", ".sh", ".txt", ".yaml", ".yml")):
        return "text/plain; charset=utf-8"
    return mime or "application/octet-stream"


# ---------------------------------------------------------------------------
# Official (first-party) skill registry
#
# Official skills are the product's own agent workflows (graph generation,
# roadmap planning, spaced review, canvas components, goal orchestration).
# They ship as SKILL.md files under ``backend/app/skills/<dir>/`` and are
# installed into every workspace with a durable system grant.  Users cannot
# delete, revoke, or shadow them — see ``assert_skill_identity_not_reserved``
# and the guards in ``MCPAndSkillService``.
# ---------------------------------------------------------------------------

OFFICIAL_SKILL_SOURCE = "learngraph_system"

SYSTEM_CANVAS_SKILL_KEY = "canvas-emit-trusted-component"
SYSTEM_CANVAS_SKILL_NAME = "Canvas 可信组件发布"
SYSTEM_CANVAS_SKILL_VERSION = "1.0.0"
SYSTEM_GOAL_ROUTE_SKILL_KEY = "goal-learning-route"
SYSTEM_GOAL_ROUTE_SKILL_NAME = "目标学习路线编排"
SYSTEM_GOAL_ROUTE_SKILL_VERSION = "2.0.1"

_CANVAS_FALLBACK_MD = (
    "---\n"
    f"name: {SYSTEM_CANVAS_SKILL_KEY}\n"
    "description: Teach the Agent how to call canvas_emit_trusted_component with valid channel-A props.\n"
    "---\n\n"
    f"# {SYSTEM_CANVAS_SKILL_NAME}\n\n"
    "## When to use\n"
    "- Publish interactive option / fill-blank / weather / metric cards in chat.\n\n"
    "## Instructions\n"
    "1. Use tool `canvas_emit_trusted_component` with a channel-A `component_type`.\n"
    "2. Never pass JSON null for optional fields — omit them or send real values.\n"
    "3. Option cards need non-empty `options` with `id` + `label` each.\n"
    "4. Prefer `title`/`prompt` strings; do not re-paste the tool JSON as Markdown.\n"
    "5. On schema errors, fix props and retry once.\n\n"
    "## Minimal examples\n"
    "```json\n"
    '{"component_type":"single_choice","props":{"title":"选一项","options":[{"id":"a","label":"A"}]}}\n'
    "```\n"
    "```json\n"
    '{"component_type":"fill_blank","props":{"title":"填空","prompt":"ACID 的 A 是","blank_ids":["answer"]}}\n'
    "```\n"
    "```json\n"
    '{"component_type":"weather_card","props":{"location":"杭州","condition":"多云","temperature_c":27}}\n'
    "```\n"
)

_GOAL_ROUTE_FALLBACK_MD = (
    "---\n"
    f"name: {SYSTEM_GOAL_ROUTE_SKILL_KEY}\n"
    "description: Clarify a learning goal before using authorized graph, file, search, or roadmap tools.\n"
    "---\n\n"
    f"# {SYSTEM_GOAL_ROUTE_SKILL_NAME}\n\n"
    "Only use this skill when Goal mode and Agent mode are both active. "
    "Extract known goal facts first, ask only for consequential missing "
    "information, use only tools actually provided for this turn, and never "
    "emit tool protocol text as the user-facing answer. Graph and roadmap "
    "writes must remain reviewable proposals."
)


@dataclass(frozen=True)
class OfficialSkillSpec:
    """One first-party workflow skill shipped with the product."""

    key: str
    display_name: str
    version: str
    dir_name: str
    description: str
    grant_reason: str
    # When set, the skill is durably installed but its instructions are only
    # injected into turns where the matching composer mode is active.
    contextual_activation: str | None = None
    allowed_components: tuple[str, ...] = ()
    fallback_md: str | None = None
    # --- Built-in sandbox capability skills ---------------------------------
    # Category bucket used for capability-search discovery and the prompt
    # catalog (e.g. "document", "pdf", "pptx", "spreadsheet", "media",
    # "frontend", "data", "archive", "web"). Empty for workflow-only skills.
    category: str = ""
    capability_ids: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    # Runtime prerequisite: "sandbox" for offline scripts, "sandbox+egress"
    # for anything that needs the reviewed outbound proxy (e.g. web fetch).
    requires_runtime: str = ""
    required_tools: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()


OFFICIAL_SKILLS: tuple[OfficialSkillSpec, ...] = (
    OfficialSkillSpec(
        key=SYSTEM_CANVAS_SKILL_KEY,
        display_name=SYSTEM_CANVAS_SKILL_NAME,
        version=SYSTEM_CANVAS_SKILL_VERSION,
        dir_name="canvas_emit_trusted_component",
        description=(
            "Teach the Agent how to call canvas_emit_trusted_component "
            "with valid channel-A props so UI cards render."
        ),
        grant_reason="system_canvas_skill_auto_enable",
        allowed_components=(
            "weather_card",
            "metric_card",
            "option_group",
            "single_choice",
            "multiple_choice",
            "fill_blank",
            "short_answer_table",
            "image_frame",
            "goal_draft_editor",
            "question_batch",
        ),
        fallback_md=_CANVAS_FALLBACK_MD,
    ),
    OfficialSkillSpec(
        key=SYSTEM_GOAL_ROUTE_SKILL_KEY,
        display_name=SYSTEM_GOAL_ROUTE_SKILL_NAME,
        version=SYSTEM_GOAL_ROUTE_SKILL_VERSION,
        dir_name="goal_learning_route",
        description=(
            "Guide Goal + Agent turns to clarify consequential gaps "
            "before selecting authorized graph, file, search, or roadmap tools."
        ),
        grant_reason="system_goal_route_skill_auto_enable",
        contextual_activation="goal_mode+agent_mode",
        fallback_md=_GOAL_ROUTE_FALLBACK_MD,
    ),
    OfficialSkillSpec(
        key="graph-generation",
        display_name="知识图谱生成",
        version="1.0.1",
        dir_name="graph_generation",
        description=(
            "Generate or update a reviewable knowledge-graph proposal from a "
            "learning goal or course material via lg_graph_create (new graph) "
            "or lg_graph_propose_change (update)."
        ),
        grant_reason="official_skill_auto_enable",
    ),
    OfficialSkillSpec(
        key="roadmap-planning",
        display_name="学习路线规划",
        version="1.0.0",
        dir_name="roadmap_planning",
        description=(
            "Plan or replan a learning roadmap and schedule from graph "
            "prerequisites, mastery state, and available time."
        ),
        grant_reason="official_skill_auto_enable",
    ),
    OfficialSkillSpec(
        key="review-coach",
        display_name="间隔复习教练",
        version="1.0.0",
        dir_name="review_coach",
        description=(
            "Run evidence-driven spaced review over due nodes with retrieval "
            "practice and recorded mastery evidence."
        ),
        grant_reason="official_skill_auto_enable",
    ),
    OfficialSkillSpec(
        key="node-learning",
        display_name="节点学习编排",
        version="1.0.0",
        dir_name="node_learning",
        description=(
            "图谱节点学习专用编排：用户选中学习节点、要求讲解某个知识点时，"
            "按成本阶梯组合图文（引用图/搜图/生图）、图表、选项题、双向交互与"
            "动画教学等多维形式讲解，控制成本与效果的平衡；到期复习→review-coach，"
            "图谱结构变更→graph-generation。"
        ),
        grant_reason="official_skill_auto_enable",
        keywords=(
            "学习节点", "节点学习", "讲解", "教教我", "展开讲讲", "图解", "配图",
            "图表", "选择题", "自测", "练习", "交互", "动画", "学习卡片", "一页纸",
            "知识点", "教学",
        ),
        required_tools=(
            "canvas_emit_trusted_component",
            "canvas_emit_magic_card",
            "create_chart",
            "generate_image",
            "search_images",
            "download_external_image",
            "lg_graph_read",
            "lg_learning_mastery_read",
            "lg_learning_evidence_record",
            "sandbox_publish_file",
        ),
    ),
    # ------------------------------------------------------------------
    # Built-in sandbox capability skills. Every workspace gets the same set
    # (idempotent per-workspace install with a durable system grant). The
    # packages ship as SKILL.md + references/ + scripts/ under
    # backend/app/skills/<dir>/; scripts run only inside the offline Docker
    # sandbox via skill.sandbox-run and are never registered as tools.
    # ------------------------------------------------------------------
    OfficialSkillSpec(
        key="document-conversion",
        display_name="文档转换与文本抽取",
        version="1.0.1",
        dir_name="document_conversion",
        description=(
            "DOC/DOCX/RTF/HTML（Word/Office）文档转 HTML、纯文本、PDF、PNG 预览，"
            "并抽取正文供下游分析；PDF/表格/JSON 不属本 Skill"
            "（→ pdf-processing / spreadsheet-analysis / data-processing）。"
        ),
        grant_reason="official_skill_auto_enable",
        category="document",
        capability_ids=("docx.read", "doc.read", "rtf.read", "html.read", "document.convert"),
        keywords=(
            "docx", "doc", "rtf", "html", "word", "office", "转换", "文本抽取",
            "pdf预览", "转word", "正文提取",
        ),
        requires_runtime="sandbox",
        required_tools=("sandbox_exec", "skill.sandbox_run"),
    ),
    OfficialSkillSpec(
        key="pdf-processing",
        display_name="PDF 解析与处理",
        version="1.0.1",
        dir_name="pdf_processing",
        description=(
            "PDF 元信息、正文/页抽取（大文件 --pages 分段）、合并拆分、页面渲染 PNG；"
            "扫描件/图片型 PDF（无文本层）需先渲染为图片走视觉模型识别（本包不做 OCR）；"
            "Word/HTML/表格类不属本 Skill（→ document-conversion / spreadsheet-analysis）。"
        ),
        grant_reason="official_skill_auto_enable",
        category="pdf",
        capability_ids=("pdf.read", "pdf.merge", "pdf.split", "pdf.render"),
        keywords=(
            "pdf", "合并", "拆分", "提取文本", "页数", "渲染", "pdf转图片",
            "ocr", "扫描件", "扫描", "图片型pdf", "文字识别",
        ),
        requires_runtime="sandbox",
        required_tools=("sandbox_exec", "skill.sandbox_run"),
    ),
    OfficialSkillSpec(
        key="pptx-generation",
        display_name="PPT 生成与检查",
        version="1.0.1",
        dir_name="pptx_generation",
        description=(
            "从结构化大纲 JSON 生成 PPTX、抽取幻灯片文本、转换为可打印 HTML 预览；"
            "处理演示文稿/幻灯片/slides/deck 相关需求。"
        ),
        grant_reason="official_skill_auto_enable",
        category="pptx",
        capability_ids=("pptx.build", "pptx.read", "pptx.preview"),
        keywords=("pptx", "ppt", "演示", "幻灯片", "生成", "大纲", "deck", "slides", "演示文稿"),
        requires_runtime="sandbox",
        required_tools=("sandbox_exec", "skill.sandbox_run"),
    ),
    OfficialSkillSpec(
        key="spreadsheet-analysis",
        display_name="表格分析与处理",
        version="1.0.1",
        dir_name="spreadsheet_analysis",
        description=(
            "CSV/XLS/XLSX/XLSB/ODS 表格的读取、探查、清洗、汇总与写出；"
            "CSV 围绕「表」的读/洗/汇总/写出也走本 Skill；"
            "批量管道/报告/重命名走 data-processing；DOC/PDF 不属本 Skill。"
        ),
        grant_reason="official_skill_auto_enable",
        category="spreadsheet",
        capability_ids=("xlsx.read", "csv.read", "xls.read", "sheet.analyze", "xlsx.write"),
        keywords=(
            "xlsx", "xls", "csv", "tsv", "ods", "表格", "数据分析", "pandas",
            "openpyxl", "清洗", "探查", "导出",
        ),
        requires_runtime="sandbox",
        required_tools=("sandbox_exec", "skill.sandbox_run"),
    ),
    OfficialSkillSpec(
        key="media-processing",
        display_name="音视频处理",
        version="1.0.0",
        dir_name="media_processing",
        description="音视频元信息、转码、抽取音频、抽帧与媒体报告。",
        grant_reason="official_skill_auto_enable",
        category="media",
        capability_ids=("media.probe", "audio.transcode", "video.frames", "media.report"),
        keywords=("ffmpeg", "ffprobe", "音视频", "音频", "视频", "转码", "抽帧", "元数据"),
        requires_runtime="sandbox",
        required_tools=("sandbox_exec", "skill.sandbox_run"),
    ),
    OfficialSkillSpec(
        key="frontend-build-preview",
        display_name="前端构建与预览",
        version="1.0.0",
        dir_name="frontend_build_preview",
        description="离线创建 Vite/React/Vue 项目、构建静态产物并渲染 PNG/PDF 预览。",
        grant_reason="official_skill_auto_enable",
        category="frontend",
        capability_ids=("frontend.scaffold", "frontend.build", "frontend.preview", "frontend.publish"),
        keywords=("vite", "react", "vue", "前端", "构建", "预览", "spa", "dist"),
        requires_runtime="sandbox",
        required_tools=("sandbox_exec", "skill.sandbox_run"),
    ),
    OfficialSkillSpec(
        key="data-processing",
        display_name="数据批处理与转换",
        version="1.0.1",
        dir_name="data_processing",
        description=(
            "JSON/CSV/文本批处理、转换、统计与 Markdown 报告生成；"
            "CSV 作为批处理管道一环/产出报告或 JSON → 本 Skill；"
            "围绕「表」的探查清洗写出 → spreadsheet-analysis。"
        ),
        grant_reason="official_skill_auto_enable",
        category="data",
        capability_ids=("json.transform", "csv.profile", "files.rename", "report.generate"),
        keywords=("json", "csv", "批处理", "转换", "统计", "报告", "rename", "清洗", "格式化"),
        requires_runtime="sandbox",
        required_tools=("sandbox_exec", "skill.sandbox_run"),
    ),
    OfficialSkillSpec(
        key="archive-workspace",
        display_name="归档与解压",
        version="1.0.0",
        dir_name="archive_workspace",
        description="安全创建/解压归档并生成成员清单，防止 zip-slip 与路径逃逸。",
        grant_reason="official_skill_auto_enable",
        category="archive",
        capability_ids=("archive.create", "archive.extract", "archive.manifest"),
        keywords=("zip", "解压", "压缩", "归档", "7z", "tar", "打包"),
        requires_runtime="sandbox",
        required_tools=("sandbox_exec", "skill.sandbox_run"),
    ),
    OfficialSkillSpec(
        key="web-fetch-render",
        display_name="受审网页抓取与渲染",
        version="1.0.0",
        dir_name="web_fetch_render",
        description=(
            "在沙箱容器内按统一权限清单抓取网页并可选 chromium 渲染；"
            "仅当 egress 门开启且授权域名非空时使用。"
        ),
        grant_reason="official_skill_auto_enable",
        category="web",
        capability_ids=("web.fetch", "web.render"),
        keywords=("网页", "抓取", "web_fetch", "渲染", "html", "fetch"),
        requires_runtime="sandbox+egress",
        required_tools=("fetch_web_page",),
    ),
    OfficialSkillSpec(
        key="image-generation",
        display_name="文生图 / 图生图",
        version="1.0.0",
        dir_name="image_generation",
        description=(
            "用宿主 generate_image 工具完成文生图与图生图编辑：先解析会话图片 "
            "file_id（list_session_files / read_session_file），再生成或基于原图编辑；"
            "区分搜图（search_images）与生图（generate_image）。"
        ),
        grant_reason="official_skill_auto_enable",
        category="image",
        capability_ids=("image.generate", "image.edit", "image.file.read"),
        keywords=(
            "文生图",
            "图生图",
            "生成图片",
            "generate_image",
            "配图",
            "插画",
            "改图",
            "编辑图片",
            "source_file_ids",
        ),
        requires_runtime="agent",
        required_tools=("generate_image", "list_session_files", "read_session_file"),
    ),
    OfficialSkillSpec(
        key="sandbox-files",
        display_name="沙箱文件处理",
        version="1.0.0",
        dir_name="sandbox_files",
        description=(
            "沙箱工作区文件的定位、检索、分页读取、精确编辑与授权删除 "
            "（sandbox_read_file / sandbox_grep / sandbox_list_files / "
            "sandbox_write_file / sandbox_edit_file / sandbox_delete_file）。"
        ),
        grant_reason="official_skill_auto_enable",
        category="files",
        capability_ids=("files.list", "files.search", "files.read", "files.edit", "files.delete"),
        keywords=(
            "文件",
            "工作区",
            "workspace",
            "grep",
            "搜索",
            "查找",
            "读取",
            "编辑",
            "替换",
            "删除",
            "read_file",
            "list_files",
        ),
        requires_runtime="sandbox",
        required_tools=(
            "sandbox_read_file",
            "sandbox_write_file",
            "sandbox_append_file",
            "sandbox_edit_file",
            "sandbox_list_files",
            "sandbox_grep",
            "sandbox_delete_file",
        ),
    ),
)

OFFICIAL_SKILL_KEYS: frozenset[str] = frozenset(spec.key for spec in OFFICIAL_SKILLS)
CONTEXTUAL_OFFICIAL_SKILL_KEYS: frozenset[str] = frozenset(
    spec.key for spec in OFFICIAL_SKILLS if spec.contextual_activation
)
_OFFICIAL_SKILLS_BY_KEY = {spec.key: spec for spec in OFFICIAL_SKILLS}


def official_skill_spec(key: str) -> OfficialSkillSpec:
    spec = _OFFICIAL_SKILLS_BY_KEY.get(key)
    if spec is None:
        raise AppError(404, "official_skill_unknown", f"Unknown official skill: {key}")
    return spec


def is_official_skill_record(skill: SkillRecord) -> bool:
    """First-class flag with a fallback for rows written before ``is_official``."""

    if bool(getattr(skill, "is_official", False)):
        return True
    return (
        skill.source == OFFICIAL_SKILL_SOURCE
        and (skill.origin_type or "") == "system"
        and skill.skill_key in OFFICIAL_SKILL_KEYS
    )


def assert_skill_identity_not_reserved(skill_key: str, source: str | None = None) -> None:
    """Reject user-supplied skills that impersonate the official namespace.

    Mirrors ``builtin_component_identity_reserved`` on the component plane.
    """

    key = (skill_key or "").strip().casefold()
    src = (source or "").strip().casefold()
    if key in OFFICIAL_SKILL_KEYS or src == OFFICIAL_SKILL_SOURCE:
        raise AppError(
            403,
            "official_skill_identity_reserved",
            "This skill key or source is reserved for official LearnGraph skills",
        )


def official_skill_md(spec: OfficialSkillSpec) -> str:
    """Load the shipped SKILL.md for an official skill, with an inline fallback."""

    candidates = [
        Path(__file__).resolve().parents[1] / "skills" / spec.dir_name / "SKILL.md",
        Path(__file__).resolve().parents[2] / "app" / "skills" / spec.dir_name / "SKILL.md",
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    if spec.fallback_md:
        return spec.fallback_md
    return (
        "---\n"
        f"name: {spec.key}\n"
        f"description: {spec.description}\n"
        "---\n\n"
        f"# {spec.display_name}\n\n"
        f"{spec.description}\n"
    )


def _official_skill_source_dir(spec: OfficialSkillSpec) -> Path | None:
    candidates = [
        Path(__file__).resolve().parents[1] / "skills" / spec.dir_name,
        Path(__file__).resolve().parents[2] / "app" / "skills" / spec.dir_name,
    ]
    for path in candidates:
        try:
            if path.is_dir():
                return path
        except OSError:
            continue
    return None


# Bounded cache so per-turn official-skill refresh does not re-read the tree
# on every Agent stream. Keyed by (resolved dir, newest file mtime); rebuilt
# lazily and capped to a single entry.
_OFFICIAL_PACKAGE_CACHE: dict[tuple[str, int], dict[str, bytes]] = {}


def official_skill_package_files(spec: OfficialSkillSpec) -> dict[str, bytes]:
    """Materialize the versioned source tree for an official skill package.

    Only ``SKILL.md`` and the controlled ``references/``, ``scripts/`` and
    ``examples/`` directories are included; everything else in the source
    directory is ignored. This function never reads an arbitrary host path.
    Each file is UTF-8 text and bounded by ``MAX_SKILL_FILE_BYTES``.
    """

    root = _official_skill_source_dir(spec)
    if root is None:
        return {"SKILL.md": official_skill_md(spec).encode("utf-8")}
    try:
        mtime = max(
            (path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()),
            default=0,
        )
    except OSError:
        mtime = 0
    key = (str(root), mtime)
    cached = _OFFICIAL_PACKAGE_CACHE.get(key)
    if cached is not None:
        return dict(cached)
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel != "SKILL.md" and not rel.startswith(("references/", "scripts/", "examples/")):
            continue
        data = path.read_bytes()
        if len(data) > MAX_SKILL_FILE_BYTES:
            raise AppError(
                400,
                "official_skill_file_too_large",
                f"Official skill file {rel} exceeds the 2 MB limit",
            )
        files[rel] = data
    if "SKILL.md" not in files:
        files["SKILL.md"] = official_skill_md(spec).encode("utf-8")
    _OFFICIAL_PACKAGE_CACHE.clear()
    _OFFICIAL_PACKAGE_CACHE[key] = files
    return dict(files)


def _official_package_hash(files: dict[str, bytes]) -> str:
    payload = [
        {"path": path, "sha256": hashlib.sha256(data).hexdigest()}
        for path, data in files.items()
    ]
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def ensure_official_skill_package(
    db: Session,
    workspace_id: str,
    spec: OfficialSkillSpec | str,
    *,
    actor_id: str = "system-policy",
    settings: Settings | None = None,
) -> SkillRecord:
    """Install/refresh one official skill and keep its durable system grant.

    Idempotent: content refresh is keyed on the package-wide ``origin_hash``
    (sha256 over every shipped file: SKILL.md + references/ + scripts/ +
    examples/). Official packages get an automatic durable ``always`` grant so
    the Agent prompt injection works without a manual authorization step.
    """

    from app.domain.extension_models import ExtensionPermissionGrant
    from app.repositories.extensions import ExtensionPermissionGrantRepository

    if isinstance(spec, str):
        spec = official_skill_spec(spec)
    resolved_settings = settings or get_settings()
    service = SkillPackageService(db, workspace_id, actor_id, resolved_settings)
    files = official_skill_package_files(spec)
    package_hash = _official_package_hash(files)
    script_basenames = sorted(
        path.rsplit("/", 1)[-1]
        for path in files
        if path.startswith("scripts/")
        and path.endswith((".py", ".js", ".mjs", ".cjs"))
    )
    manifest_meta = {
        "category": spec.category,
        "capability_ids": list(spec.capability_ids),
        "keywords": list(spec.keywords),
        "requires_runtime": spec.requires_runtime,
        "required_tools": list(spec.required_tools),
        "scripts": script_basenames,
    }
    existing = db.scalar(
        select(SkillRecord).where(
            SkillRecord.workspace_id == workspace_id,
            SkillRecord.skill_key == spec.key,
        )
    )
    if existing is None:
        skill = service.skills.add(
            SkillRecord(
                workspace_id=workspace_id,
                skill_key=spec.key,
                name=spec.display_name,
                source=OFFICIAL_SKILL_SOURCE,
                version=spec.version,
                generated_by="system",
                kind="agent_skill_package",
                package_format="skill_md_v1",
                origin_type="system",
                origin_ref=f"backend/app/skills/{spec.dir_name}",
                origin_hash=package_hash,
                has_scripts=False,
                locale_source="zh-CN",
                is_official=True,
                content_hash="",
                manifest_json={
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "name": spec.display_name,
                    "description": spec.description,
                    **manifest_meta,
                },
                manifest_hash="",
                instructions_markdown="",
                required_tools=list(spec.required_tools),
                required_permissions=list(spec.required_permissions),
                allowed_components=list(spec.allowed_components),
                validation_report={"system_skill": True, "official_skill": True},
                status="authorization_required",
                enabled=False,
            )
        )
        db.flush()
        service.write_official_package_files(skill, files)
        skill.origin_hash = package_hash
    else:
        skill = existing
        # Refresh only when any shipped file changes or the body was lost.
        if skill.origin_hash != package_hash or not (skill.instructions_markdown or "").strip():
            service.write_official_package_files(skill, files)
            skill.origin_hash = package_hash
            # Merge capability metadata (category/keywords/scripts) so older
            # rows created before these fields existed stay current.
            manifest = dict(skill.manifest_json or {})
            manifest.update({**manifest_meta, "description": spec.description})
            skill.manifest_json = manifest

    # Instruction-only official skill: durable always grant so prompt injection works.
    grants = ExtensionPermissionGrantRepository(db, workspace_id)
    auth_hash = hashlib.sha256(
        json.dumps(
            {
                "subject_type": "skill",
                "subject_id": skill.id,
                "manifest_hash": skill.manifest_hash,
                "permissions": skill.required_permissions or [],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    active = list(
        db.scalars(
            select(ExtensionPermissionGrant).where(
                ExtensionPermissionGrant.workspace_id == workspace_id,
                ExtensionPermissionGrant.subject_type == "skill",
                ExtensionPermissionGrant.subject_id == skill.id,
                ExtensionPermissionGrant.status == "active",
            )
        )
    )
    usable = next(
        (
            grant
            for grant in active
            if grant.decision == "always" and grant.authorization_hash == auth_hash
        ),
        None,
    )
    if usable is None:
        for grant in active:
            grant.status = "superseded"
            grant.revoked_at = utc_now()
        grants.add(
            ExtensionPermissionGrant(
                workspace_id=workspace_id,
                subject_type="skill",
                subject_id=skill.id,
                decision="always",
                status="active",
                permissions=list(skill.required_permissions or []),
                authorization_hash=auth_hash,
                decided_by=actor_id,
                reason=spec.grant_reason,
            )
        )
    skill.status = "enabled"
    skill.enabled = True
    skill.generated_by = "system"
    skill.origin_type = "system"
    skill.is_official = True
    # Package recompute copies the frontmatter name (the key); official skills
    # keep their curated display name.
    skill.name = spec.display_name
    report = dict(skill.validation_report or {})
    report["system_skill"] = True
    report["official_skill"] = True
    report["auto_enabled"] = True
    if spec.contextual_activation:
        report["contextual_activation"] = spec.contextual_activation
    if spec.requires_runtime:
        report["requires_runtime"] = spec.requires_runtime
    if spec.category:
        report["category"] = spec.category
    skill.validation_report = report
    db.flush()
    return skill


def ensure_official_skill_packages(
    db: Session,
    workspace_id: str,
    *,
    actor_id: str = "system-policy",
    settings: Settings | None = None,
) -> list[SkillRecord]:
    """Install/refresh every official skill for a workspace (idempotent)."""

    return [
        ensure_official_skill_package(
            db, workspace_id, spec, actor_id=actor_id, settings=settings
        )
        for spec in OFFICIAL_SKILLS
    ]


class SkillPackageService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.skills = SkillRepository(db, workspace_id)
        self.blobs = BlobStore(db, workspace_id, settings)
        self.audit = AuditRepository(db, workspace_id)

    def require_skill(self, skill_id: str) -> SkillRecord:
        return self.skills.require(skill_id, "Skill")

    def skill_view(self, skill: SkillRecord) -> SkillView:
        return SkillView.model_validate(skill)

    def _files_for_skill(self, skill_id: str) -> list[SkillPackageFile]:
        return list(
            self.db.scalars(
                select(SkillPackageFile)
                .where(
                    SkillPackageFile.workspace_id == self.workspace_id,
                    SkillPackageFile.skill_id == skill_id,
                )
                .order_by(SkillPackageFile.relative_path)
            ).all()
        )

    def _recompute_package_state(self, skill: SkillRecord) -> None:
        files = [item for item in self._files_for_skill(skill.id) if not item.is_directory]
        payload = [
            {"path": item.relative_path, "sha256": item.blob_sha256, "size": item.size_bytes}
            for item in sorted(files, key=lambda row: row.relative_path)
        ]
        content_hash = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        has_scripts = any(
            item.relative_path == "scripts"
            or item.relative_path.startswith("scripts/")
            for item in self._files_for_skill(skill.id)
        )
        skill.content_hash = content_hash
        skill.has_scripts = has_scripts
        skill.package_format = "skill_md_v1"
        skill.kind = "agent_skill_package"
        # Keep authorization fingerprint tied to package contents.
        skill.manifest_hash = content_hash
        if has_scripts and "sandbox.execute" not in (skill.required_permissions or []):
            skill.required_permissions = list(
                dict.fromkeys([*(skill.required_permissions or []), "sandbox.execute"])
            )
        skill_md = next((item for item in files if item.relative_path == "SKILL.md"), None)
        if skill_md is not None:
            try:
                text = self.blobs.read_bytes(skill_md.blob_sha256, limit_bytes=MAX_SKILL_FILE_BYTES).decode(
                    "utf-8"
                )
            except UnicodeDecodeError:
                text = ""
            meta, body = parse_skill_md_frontmatter(text)
            if meta.get("name"):
                skill.name = str(meta["name"])[:160]
            if body:
                skill.instructions_markdown = body[:20_000]
            elif text:
                skill.instructions_markdown = text[:20_000]
            description = str(meta.get("description") or "")
            # Merge instead of replace so provenance keys written at install
            # time (e.g. "github", "market_id") survive package recomputes.
            manifest = dict(skill.manifest_json or {})
            manifest.update(
                {
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "name": skill.name,
                    "description": description,
                    "has_scripts": has_scripts,
                    "content_hash": content_hash,
                }
            )
            skill.manifest_json = manifest
        skill.validation_report = {
            **dict(skill.validation_report or {}),
            "package_files": len(files),
            "has_scripts": has_scripts,
            "content_hash": content_hash,
        }

    def _invalidate_authorization(self, skill: SkillRecord) -> None:
        skill.authorization_generation = int(skill.authorization_generation or 0) + 1
        skill.enabled = False
        skill.status = "authorization_required"

    def _require_not_official(self, skill: SkillRecord) -> None:
        if is_official_skill_record(skill):
            raise AppError(
                403,
                "official_skill_protected",
                "Official LearnGraph skills are managed by the system and cannot be edited",
            )

    def write_official_package_files(
        self, skill: SkillRecord, files: dict[str, bytes]
    ) -> None:
        """(Re)write the versioned source tree of an official skill package.

        Idempotent and reconciles against the shipped source: files no longer
        present are removed so ``content_hash`` tracks the current tree exactly.
        The caller recomputes ``origin_hash`` after this call.
        """

        wanted = set(files)
        existing = {row.relative_path for row in self._files_for_skill(skill.id)}
        for rel, data in files.items():
            self._write_file_bytes(skill, rel, data, invalidate=False)
        for rel in sorted(existing - wanted):
            for row in [
                item
                for item in self._files_for_skill(skill.id)
                if item.relative_path == rel or item.relative_path.startswith(rel + "/")
            ]:
                self.db.delete(row)
        self.db.flush()
        self._recompute_package_state(skill)

    def create_package(self, payload: SkillPackageCreateRequest) -> SkillRecord:
        assert_skill_identity_not_reserved(payload.skill_key, payload.source)
        if self.db.scalar(
            self.skills.query().where(SkillRecord.skill_key == payload.skill_key)
        ):
            raise AppError(409, "skill_key_exists", "Skill key already exists")
        name = payload.name.strip()
        description = (payload.description or name).strip() or name
        # Keep frontmatter single-line so the lightweight parser can read name/description.
        # Body uses the full Agent Skill layout (when-to-use / instructions / steps / examples).
        safe_description = " ".join(description.split())[:500]
        skill_md = (
            f"---\n"
            f"name: {payload.skill_key}\n"
            f"description: {safe_description}\n"
            f"---\n\n"
            f"# {name}\n\n"
            f"## When to use\n"
            f"- The user wants help that matches: {safe_description}\n"
            f"- The user explicitly invokes `/{payload.skill_key}` or asks for this capability\n"
            f"- Prefer this skill over generic answers when the request is in scope\n\n"
            f"## Instructions\n"
            f"1. Confirm the user's goal in one short sentence.\n"
            f"2. Gather only the missing inputs you need.\n"
            f"3. Follow the steps below; do not invent tools or run host code outside the sandbox.\n"
            f"4. Summarize outcomes and remaining risks.\n\n"
            f"## Steps\n"
            f"1. ...\n"
            f"2. ...\n"
            f"3. ...\n\n"
            f"## Examples\n"
            f"- **User:** \"...\"\n"
            f"  **Agent:** ...\n\n"
            f"## Notes\n"
            f"- Keep responses evidence-based; do not claim side effects you did not perform.\n"
            f"- Scripts under `scripts/` run only inside the Docker sandbox when authorized.\n"
        )
        skill = self.skills.add(
            SkillRecord(
                workspace_id=self.workspace_id,
                skill_key=payload.skill_key,
                name=name,
                source=payload.source.strip(),
                version=payload.version.strip(),
                generated_by="user_import",
                kind="agent_skill_package",
                package_format="skill_md_v1",
                origin_type="user_created",
                origin_ref="",
                origin_hash="",
                has_scripts=False,
                locale_source="",
                content_hash="",
                manifest_json={
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "name": name,
                    "description": description,
                },
                manifest_hash="",
                instructions_markdown="",
                required_tools=[],
                required_permissions=[],
                allowed_components=[],
                validation_report={},
                status="authorization_required",
                enabled=False,
            )
        )
        self.db.flush()
        self._write_file_bytes(skill, "SKILL.md", skill_md.encode("utf-8"), invalidate=False)
        if payload.with_sample_script:
            sample = (
                "#!/usr/bin/env python3\n"
                '"""Sample script — runs only inside LearnGraph Docker sandbox."""\n'
                "print('hello from skill sandbox')\n"
            )
            self._write_file_bytes(
                skill, "scripts/hello.py", sample.encode("utf-8"), invalidate=False
            )
        self._recompute_package_state(skill)
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.package.create",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "skill_key": skill.skill_key,
                "content_hash": skill.content_hash,
                "has_scripts": skill.has_scripts,
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def list_files(self, skill_id: str) -> SkillFileTreeView:
        skill = self.require_skill(skill_id)
        if skill.kind != "agent_skill_package" and not skill.package_format.startswith("skill_md"):
            # Still allow listing; empty for declarative
            return SkillFileTreeView(
                skill_id=skill.id,
                content_hash=skill.content_hash or skill.manifest_hash,
                has_scripts=bool(skill.has_scripts),
                files=[],
            )
        entries = [
            SkillFileEntryView(
                relative_path=item.relative_path,
                size_bytes=item.size_bytes,
                mime_type=item.mime_type,
                is_directory=bool(item.is_directory),
                blob_sha256=item.blob_sha256 or "",
                updated_at=item.updated_at,
            )
            for item in self._files_for_skill(skill.id)
        ]
        return SkillFileTreeView(
            skill_id=skill.id,
            content_hash=skill.content_hash or skill.manifest_hash,
            has_scripts=bool(skill.has_scripts),
            files=entries,
        )

    def read_file(self, skill_id: str, relative_path: str) -> SkillFileContentView:
        skill = self.require_skill(skill_id)
        path = normalize_skill_relative_path(relative_path)
        row = self.db.scalar(
            select(SkillPackageFile).where(
                SkillPackageFile.workspace_id == self.workspace_id,
                SkillPackageFile.skill_id == skill.id,
                SkillPackageFile.relative_path == path,
            )
        )
        if row is None or row.is_directory:
            raise AppError(404, "skill_file_not_found", "Skill file was not found")
        data = self.blobs.read_bytes(row.blob_sha256, limit_bytes=MAX_SKILL_FILE_BYTES)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(
                415,
                "skill_file_not_text",
                "Only UTF-8 text skill files can be edited in the lightweight editor",
            ) from exc
        return SkillFileContentView(
            relative_path=path,
            content=text,
            size_bytes=len(data),
            mime_type=row.mime_type,
            blob_sha256=row.blob_sha256,
            content_hash=skill.content_hash or skill.manifest_hash,
        )

    def write_file(
        self, skill_id: str, relative_path: str, payload: SkillFileWriteRequest
    ) -> tuple[SkillRecord, SkillFileContentView, bool]:
        skill = self.require_skill(skill_id)
        self._require_not_official(skill)
        if skill.kind not in ("agent_skill_package",) and skill.package_format != "skill_md_v1":
            # Promote declarative-only record only if empty package path used intentionally
            if skill.package_format == "declarative_json" and not skill.content_hash:
                raise AppError(
                    400,
                    "skill_not_package",
                    "Declarative skills cannot store a file tree; create an agent_skill_package",
                )
        if (
            payload.expected_content_hash
            and skill.content_hash
            and payload.expected_content_hash != skill.content_hash
        ):
            raise AppError(
                409,
                "skill_content_conflict",
                "Skill package changed; reload before saving",
            )
        data = payload.content.encode("utf-8")
        if len(data) > MAX_SKILL_FILE_BYTES:
            raise AppError(400, "skill_file_too_large", "Skill file exceeds 2 MB limit")
        path = normalize_skill_relative_path(relative_path)
        previous_hash = skill.content_hash or skill.manifest_hash
        self._write_file_bytes(skill, path, data, invalidate=True)
        self._recompute_package_state(skill)
        reauth = previous_hash != skill.content_hash
        if reauth:
            self._invalidate_authorization(skill)
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.package.write_file",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "path": path,
                "content_hash": skill.content_hash,
                "reauthorization_required": reauth,
                "size_bytes": len(data),
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        view = self.read_file(skill.id, path)
        return skill, view, reauth

    def delete_file(self, skill_id: str, relative_path: str) -> SkillRecord:
        skill = self.require_skill(skill_id)
        self._require_not_official(skill)
        path = normalize_skill_relative_path(relative_path)
        if path == "SKILL.md":
            raise AppError(400, "skill_md_required", "SKILL.md cannot be deleted from a package")
        rows = list(
            self.db.scalars(
                select(SkillPackageFile).where(
                    SkillPackageFile.workspace_id == self.workspace_id,
                    SkillPackageFile.skill_id == skill.id,
                )
            ).all()
        )
        targets = [
            row
            for row in rows
            if row.relative_path == path or row.relative_path.startswith(path + "/")
        ]
        if not targets:
            raise AppError(404, "skill_file_not_found", "Skill file was not found")
        for row in targets:
            self.db.delete(row)
        self.db.flush()
        previous = skill.content_hash
        self._recompute_package_state(skill)
        if previous != skill.content_hash:
            self._invalidate_authorization(skill)
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.package.delete_file",
            resource_type="skill",
            resource_id=skill.id,
            details={"path": path, "content_hash": skill.content_hash},
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def mkdir(self, skill_id: str, payload: SkillMkdirRequest) -> SkillFileTreeView:
        skill = self.require_skill(skill_id)
        self._require_not_official(skill)
        path = normalize_skill_relative_path(payload.relative_path)
        existing = self.db.scalar(
            select(SkillPackageFile).where(
                SkillPackageFile.workspace_id == self.workspace_id,
                SkillPackageFile.skill_id == skill.id,
                SkillPackageFile.relative_path == path,
            )
        )
        if existing is not None:
            raise AppError(409, "skill_path_exists", "Skill path already exists")
        self.db.add(
            SkillPackageFile(
                workspace_id=self.workspace_id,
                skill_id=skill.id,
                relative_path=path,
                blob_sha256="",
                size_bytes=0,
                mime_type="inode/directory",
                is_directory=True,
            )
        )
        self.db.flush()
        self._recompute_package_state(skill)
        self.db.commit()
        return self.list_files(skill.id)

    def validate(self, skill_id: str) -> SkillValidateResponse:
        skill = self.require_skill(skill_id)
        issues: list[str] = []
        frontmatter: dict[str, Any] = {}
        files = self._files_for_skill(skill.id)
        skill_md = next(
            (item for item in files if item.relative_path == "SKILL.md" and not item.is_directory),
            None,
        )
        if skill_md is None:
            issues.append("Missing SKILL.md")
        else:
            data = self.blobs.read_bytes(skill_md.blob_sha256, limit_bytes=MAX_SKILL_FILE_BYTES)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                issues.append("SKILL.md must be UTF-8 text")
                text = ""
            frontmatter, _ = parse_skill_md_frontmatter(text)
            if not frontmatter.get("name"):
                issues.append("SKILL.md frontmatter must include name")
            if not frontmatter.get("description"):
                issues.append("SKILL.md frontmatter must include description")
        total = sum(item.size_bytes for item in files if not item.is_directory)
        if total > MAX_SKILL_PACKAGE_BYTES:
            issues.append("Package exceeds 20 MB total size limit")
        if len([item for item in files if not item.is_directory]) > MAX_SKILL_FILES:
            issues.append("Package exceeds file count limit")
        return SkillValidateResponse(
            skill_id=skill.id,
            ok=not issues,
            content_hash=skill.content_hash or skill.manifest_hash,
            has_scripts=bool(skill.has_scripts),
            issues=issues,
            frontmatter=frontmatter,
        )

    def security_scan(self, skill_id: str) -> dict[str, Any]:
        """Re-run the layer-2 static scan over the stored package files."""

        from app.services.skill_security_scan import attach_scan_report

        skill = self.require_skill(skill_id)
        texts: list[tuple[str, str]] = []
        if self._files_for_skill(skill.id):
            for item in self._files_for_skill(skill.id):
                if item.is_directory:
                    continue
                try:
                    data = self.blobs.read_bytes(
                        item.blob_sha256, limit_bytes=MAX_SKILL_FILE_BYTES
                    )
                    texts.append((item.relative_path, data.decode("utf-8")))
                except Exception:  # noqa: BLE001 — binary/unreadable files are skipped
                    continue
        elif skill.instructions_markdown:
            texts.append(("SKILL.md", skill.instructions_markdown))
        report = attach_scan_report(skill, texts)
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.security_scan",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "risk_level": report.get("risk_level"),
                "finding_count": report.get("finding_count"),
                "scanned_files": report.get("scanned_files"),
                "content_hash": skill.content_hash,
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return {"skill_id": skill.id, **report, "content_hash": skill.content_hash or ""}

    def _write_file_bytes(
        self,
        skill: SkillRecord,
        relative_path: str,
        data: bytes,
        *,
        invalidate: bool,
    ) -> SkillPackageFile:
        path = normalize_skill_relative_path(relative_path)
        files = [item for item in self._files_for_skill(skill.id) if not item.is_directory]
        existing = next((item for item in files if item.relative_path == path), None)
        other_total = sum(item.size_bytes for item in files if item.relative_path != path)
        if other_total + len(data) > MAX_SKILL_PACKAGE_BYTES:
            raise AppError(400, "skill_package_too_large", "Skill package exceeds 20 MB limit")
        if existing is None and len(files) >= MAX_SKILL_FILES:
            raise AppError(400, "skill_too_many_files", "Skill package exceeds file count limit")
        # Ensure parent directory markers exist (optional UX)
        parent = str(PurePosixPath(path).parent)
        if parent and parent != ".":
            self._ensure_dir_marker(skill, parent)
        blob = self.blobs.put_bytes(data, mime_type=guess_mime(path))
        if existing is None:
            existing = SkillPackageFile(
                workspace_id=self.workspace_id,
                skill_id=skill.id,
                relative_path=path,
                blob_sha256=blob.sha256,
                size_bytes=len(data),
                mime_type=guess_mime(path),
                is_directory=False,
            )
            self.db.add(existing)
        else:
            existing.blob_sha256 = blob.sha256
            existing.size_bytes = len(data)
            existing.mime_type = guess_mime(path)
            existing.is_directory = False
            existing.updated_at = utc_now()
        self.db.flush()
        if invalidate:
            pass  # caller recomputes
        return existing

    def _ensure_dir_marker(self, skill: SkillRecord, path: str) -> None:
        path = normalize_skill_relative_path(path)
        existing = self.db.scalar(
            select(SkillPackageFile).where(
                SkillPackageFile.workspace_id == self.workspace_id,
                SkillPackageFile.skill_id == skill.id,
                SkillPackageFile.relative_path == path,
            )
        )
        if existing is not None:
            return
        parent = str(PurePosixPath(path).parent)
        if parent and parent != ".":
            self._ensure_dir_marker(skill, parent)
        self.db.add(
            SkillPackageFile(
                workspace_id=self.workspace_id,
                skill_id=skill.id,
                relative_path=path,
                blob_sha256="",
                size_bytes=0,
                mime_type="inode/directory",
                is_directory=True,
            )
        )
        self.db.flush()
