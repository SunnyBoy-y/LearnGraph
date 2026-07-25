"""Agent Skill file packages (SKILL.md trees) — D-077 / D-081."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
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


def parse_skill_md_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Minimal YAML frontmatter: key: value lines between --- fences."""

    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, Any] = {}
    body_start = 1
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            body_start = index + 1
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            meta[key] = value
    else:
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


SYSTEM_CANVAS_SKILL_KEY = "canvas-emit-trusted-component"
SYSTEM_CANVAS_SKILL_NAME = "Canvas 可信组件发布"
SYSTEM_CANVAS_SKILL_VERSION = "1.0.0"
SYSTEM_GOAL_ROUTE_SKILL_KEY = "goal-learning-route"
SYSTEM_GOAL_ROUTE_SKILL_NAME = "目标学习路线编排"
SYSTEM_GOAL_ROUTE_SKILL_VERSION = "1.0.0"


def system_canvas_skill_md() -> str:
    """Return the shipped SKILL.md body for canvas trusted-component emit.

    Content is loaded from the repo path when available so product and code stay
    aligned; a compact fallback keeps seed/tests working without the file tree.
    """

    candidates = [
        Path(__file__).resolve().parents[1] / "skills" / "canvas_emit_trusted_component" / "SKILL.md",
        Path(__file__).resolve().parents[2] / "app" / "skills" / "canvas_emit_trusted_component" / "SKILL.md",
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return (
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


def system_goal_route_skill_md() -> str:
    candidates = [
        Path(__file__).resolve().parents[1] / "skills" / "goal_learning_route" / "SKILL.md",
        Path(__file__).resolve().parents[2] / "app" / "skills" / "goal_learning_route" / "SKILL.md",
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return (
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


def ensure_system_canvas_skill_package(
    db: Session,
    workspace_id: str,
    *,
    actor_id: str = "system-policy",
    settings: Settings | None = None,
) -> SkillRecord | None:
    """Install/refresh the system canvas emit skill and auto-enable it (instruction-only).

    The package never registers scripts. Agent turns inject the SKILL.md body via
    D-077 ``agent_skill_package_instructions`` so models learn valid props.
    """

    from app.domain.extension_models import ExtensionPermissionGrant
    from app.repositories.extensions import ExtensionPermissionGrantRepository

    resolved_settings = settings or get_settings()
    service = SkillPackageService(db, workspace_id, actor_id, resolved_settings)
    skill_md = system_canvas_skill_md()
    existing = db.scalar(
        select(SkillRecord).where(
            SkillRecord.workspace_id == workspace_id,
            SkillRecord.skill_key == SYSTEM_CANVAS_SKILL_KEY,
        )
    )
    body_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()
    if existing is None:
        skill = service.skills.add(
            SkillRecord(
                workspace_id=workspace_id,
                skill_key=SYSTEM_CANVAS_SKILL_KEY,
                name=SYSTEM_CANVAS_SKILL_NAME,
                source="learngraph_system",
                version=SYSTEM_CANVAS_SKILL_VERSION,
                generated_by="system",
                kind="agent_skill_package",
                package_format="skill_md_v1",
                origin_type="system",
                origin_ref="backend/app/skills/canvas_emit_trusted_component",
                origin_hash=body_hash,
                has_scripts=False,
                locale_source="zh-CN",
                content_hash="",
                manifest_json={
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "name": SYSTEM_CANVAS_SKILL_NAME,
                    "description": (
                        "Teach the Agent how to call canvas_emit_trusted_component "
                        "with valid channel-A props so UI cards render."
                    ),
                },
                manifest_hash="",
                instructions_markdown="",
                required_tools=[],
                required_permissions=[],
                allowed_components=list(
                    {
                        "weather_card",
                        "metric_card",
                        "option_group",
                        "single_choice",
                        "multiple_choice",
                        "fill_blank",
                        "short_answer_table",
                        "image_frame",
                    }
                ),
                validation_report={"system_skill": True},
                status="authorization_required",
                enabled=False,
            )
        )
        db.flush()
        service._write_file_bytes(skill, "SKILL.md", skill_md.encode("utf-8"), invalidate=False)
        service._recompute_package_state(skill)
        skill.origin_hash = body_hash
    else:
        skill = existing
        # Refresh only when the shipped SKILL.md body changes.
        if skill.origin_hash != body_hash or not (skill.instructions_markdown or "").strip():
            service._write_file_bytes(skill, "SKILL.md", skill_md.encode("utf-8"), invalidate=False)
            service._recompute_package_state(skill)
            skill.origin_hash = body_hash

    # Instruction-only system skill: durable always grant so Agent prompt injection works.
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
                reason="system_canvas_skill_auto_enable",
            )
        )
    skill.status = "enabled"
    skill.enabled = True
    skill.generated_by = "system"
    skill.origin_type = "system"
    report = dict(skill.validation_report or {})
    report["system_skill"] = True
    report["auto_enabled"] = True
    skill.validation_report = report
    db.flush()
    return skill


def ensure_system_goal_route_skill_package(
    db: Session,
    workspace_id: str,
    *,
    actor_id: str = "system-policy",
    settings: Settings | None = None,
) -> SkillRecord | None:
    """Install and authorize the contextual Goal + Agent instruction skill."""

    from app.domain.extension_models import ExtensionPermissionGrant
    from app.repositories.extensions import ExtensionPermissionGrantRepository

    resolved_settings = settings or get_settings()
    service = SkillPackageService(db, workspace_id, actor_id, resolved_settings)
    skill_md = system_goal_route_skill_md()
    existing = db.scalar(
        select(SkillRecord).where(
            SkillRecord.workspace_id == workspace_id,
            SkillRecord.skill_key == SYSTEM_GOAL_ROUTE_SKILL_KEY,
        )
    )
    body_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()
    if existing is None:
        skill = service.skills.add(
            SkillRecord(
                workspace_id=workspace_id,
                skill_key=SYSTEM_GOAL_ROUTE_SKILL_KEY,
                name=SYSTEM_GOAL_ROUTE_SKILL_NAME,
                source="learngraph_system",
                version=SYSTEM_GOAL_ROUTE_SKILL_VERSION,
                generated_by="system",
                kind="agent_skill_package",
                package_format="skill_md_v1",
                origin_type="system",
                origin_ref="backend/app/skills/goal_learning_route",
                origin_hash=body_hash,
                has_scripts=False,
                locale_source="zh-CN",
                content_hash="",
                manifest_json={
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "name": SYSTEM_GOAL_ROUTE_SKILL_NAME,
                    "description": (
                        "Guide Goal + Agent turns to clarify consequential gaps "
                        "before selecting authorized graph, file, search, or roadmap tools."
                    ),
                },
                manifest_hash="",
                instructions_markdown="",
                required_tools=[],
                required_permissions=[],
                allowed_components=[],
                validation_report={
                    "system_skill": True,
                    "contextual_activation": "goal_mode+agent_mode",
                },
                status="authorization_required",
                enabled=False,
            )
        )
        db.flush()
        service._write_file_bytes(
            skill,
            "SKILL.md",
            skill_md.encode("utf-8"),
            invalidate=False,
        )
        service._recompute_package_state(skill)
        skill.origin_hash = body_hash
    else:
        skill = existing
        if skill.origin_hash != body_hash or not (skill.instructions_markdown or "").strip():
            service._write_file_bytes(
                skill,
                "SKILL.md",
                skill_md.encode("utf-8"),
                invalidate=False,
            )
            service._recompute_package_state(skill)
            skill.origin_hash = body_hash

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
                reason="system_goal_route_skill_auto_enable",
            )
        )
    skill.status = "enabled"
    skill.enabled = True
    skill.generated_by = "system"
    skill.origin_type = "system"
    report = dict(skill.validation_report or {})
    report["system_skill"] = True
    report["auto_enabled"] = True
    report["contextual_activation"] = "goal_mode+agent_mode"
    skill.validation_report = report
    db.flush()
    return skill


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
            skill.manifest_json = {
                "schema_version": "1.0",
                "kind": "agent_skill_package",
                "name": skill.name,
                "description": description,
                "has_scripts": has_scripts,
                "content_hash": content_hash,
            }
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

    def create_package(self, payload: SkillPackageCreateRequest) -> SkillRecord:
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
