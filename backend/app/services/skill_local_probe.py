"""Same-host local skill discovery (D-079). Remote API deployments stay unavailable."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.extension_models import SkillLocalProbePolicy, SkillPackageFile, SkillRecord
from app.domain.models import new_id, utc_now
from app.domain.schemas.extensions import (
    SkillLocalImportRequest,
    SkillLocalProbeItem,
    SkillLocalProbePolicyUpdate,
    SkillLocalProbePolicyView,
    SkillLocalProbeScanResponse,
    SkillView,
)
from app.repositories.audit import AuditRepository
from app.repositories.extensions import SkillRepository
from app.services.session_workspace import BlobStore
from app.services.skill_package import (
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_FILES,
    MAX_SKILL_PACKAGE_BYTES,
    assert_skill_identity_not_reserved,
    guess_mime,
    normalize_skill_relative_path,
    parse_skill_md_frontmatter,
)

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
MAX_SCAN_DEPTH = 4
MAX_SCAN_DIRS = 200
MAX_SCAN_FILES = 2000
TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".json",
    ".yaml",
    ".yml",
    ".sh",
}


def _home() -> Path:
    """Resolve the interactive user's home without hardcoding usernames or drive letters.

    Order: USERPROFILE (Windows) → HOME → Path.home(). Never embeds a fixed path
    like C:\\Users\\13600; the API process must run as the same host user for same-host probe.
    """

    for key in ("USERPROFILE", "HOME"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            try:
                path = Path(raw).expanduser()
                # Prefer absolute existing homes; still accept non-existent so candidates render.
                if path.is_absolute():
                    return path
            except (OSError, RuntimeError, ValueError):
                continue
    try:
        return Path.home().expanduser()
    except (OSError, RuntimeError):
        # Last resort: empty relative path so UI shows unavailable-looking roots rather than crash.
        return Path(".")


def candidate_skill_roots() -> list[dict[str, Any]]:
    home = _home()
    # All paths are derived from the process user environment — never hardcode a username.
    candidates = [
        {"label": "Claude Code skills", "path": str(home / ".claude" / "skills")},
        {"label": "Claude Code agents skills", "path": str(home / ".config" / "claude" / "skills")},
        {"label": "Codex skills", "path": str(home / ".codex" / "skills")},
        {"label": "OpenClaw skills", "path": str(home / ".openclaw" / "skills")},
        {"label": "Hermes skills", "path": str(home / ".hermes" / "skills")},
        {"label": "LiveAgent skills", "path": str(home / ".liveagent" / "skills")},
        {"label": "User skills", "path": str(home / "skills")},
        {"label": "Agent skills", "path": str(home / ".agents" / "skills")},
    ]
    results: list[dict[str, Any]] = []
    for item in candidates:
        path = Path(item["path"])
        results.append(
            {
                **item,
                "exists": path.is_dir(),
                "readable": os.access(path, os.R_OK) if path.exists() else False,
            }
        )
    return results


def same_host_probe_available(settings: Settings) -> tuple[bool, str | None]:
    """Return whether this API process may scan the interactive user's local disk.

    Remote multi-host deployments must not pretend to see the browser user's machine.
    Opt-out: LEARNGRAPH_SKILL_LOCAL_PROBE=0 / false / remote.
    """

    flag = str(getattr(settings, "skill_local_probe_mode", "") or os.environ.get("LEARNGRAPH_SKILL_LOCAL_PROBE", "")).strip().lower()
    if flag in {"0", "false", "off", "remote", "disabled", "unavailable"}:
        return False, "本机探测已关闭或当前为远程 API 部署（仅前后端同机可用）"
    if flag in {"1", "true", "on", "local", "same_host"}:
        return True, None
    # Default: allow only when process appears local (not obviously containerized remote).
    if Path("/.dockerenv").exists() and not flag:
        # Dockerized API may still be same-machine Docker Desktop; do not hard-block,
        # but require explicit allowed roots from the user after enable.
        return True, None
    return True, None


class SkillLocalProbeService:
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

    def _policy_row(self) -> SkillLocalProbePolicy | None:
        return self.db.scalar(
            select(SkillLocalProbePolicy).where(
                SkillLocalProbePolicy.workspace_id == self.workspace_id
            )
        )

    def get_policy(self) -> SkillLocalProbePolicyView:
        available, reason = same_host_probe_available(self.settings)
        row = self._policy_row()
        return SkillLocalProbePolicyView(
            enabled=bool(row.enabled) if row else False,
            allowed_roots=list(row.allowed_roots_json or []) if row else [],
            same_host_available=available,
            unavailable_reason=None if available else reason,
            last_scanned_at=row.last_scanned_at if row else None,
            last_scan_summary=dict(row.last_scan_summary or {}) if row else {},
            candidate_roots=candidate_skill_roots() if available else [],
        )

    def update_policy(self, payload: SkillLocalProbePolicyUpdate) -> SkillLocalProbePolicyView:
        available, reason = same_host_probe_available(self.settings)
        if not available:
            raise AppError(503, "local_probe_unavailable", reason or "Local probe unavailable")
        roots: list[str] = []
        for raw in payload.allowed_roots:
            path = Path(raw).expanduser().resolve()
            if not path.is_dir():
                raise AppError(400, "local_probe_root_missing", f"Root is not a directory: {path}")
            if not os.access(path, os.R_OK):
                raise AppError(400, "local_probe_root_unreadable", f"Root is not readable: {path}")
            roots.append(str(path))
        row = self._policy_row()
        if row is None:
            row = SkillLocalProbePolicy(
                id=new_id(),
                workspace_id=self.workspace_id,
                enabled=payload.enabled,
                allowed_roots_json=roots,
            )
            self.db.add(row)
        else:
            row.enabled = payload.enabled
            row.allowed_roots_json = roots
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.local_probe.policy_update",
            resource_type="workspace",
            resource_id=self.workspace_id,
            details={"enabled": payload.enabled, "roots": len(roots)},
        )
        self.db.commit()
        return self.get_policy()

    def scan(self) -> SkillLocalProbeScanResponse:
        available, reason = same_host_probe_available(self.settings)
        if not available:
            return SkillLocalProbeScanResponse(
                available=False,
                unavailable_reason=reason or "仅前后端同机部署可用",
            )
        row = self._policy_row()
        if row is None or not row.enabled:
            raise AppError(
                400,
                "local_probe_disabled",
                "Local skill probe is disabled; enable it in Extensions Hub first",
            )
        roots = [Path(p) for p in (row.allowed_roots_json or [])]
        if not roots:
            # Fall back to existing candidate roots that exist
            roots = [Path(item["path"]) for item in candidate_skill_roots() if item.get("exists")]
        items: list[SkillLocalProbeItem] = []
        scanned: list[str] = []
        dirs_seen = 0
        files_seen = 0
        for root in roots:
            root = root.expanduser().resolve()
            if not root.is_dir() or not os.access(root, os.R_OK):
                continue
            scanned.append(str(root))
            for dirpath, dirnames, filenames in os.walk(root):
                dirs_seen += 1
                if dirs_seen > MAX_SCAN_DIRS:
                    dirnames[:] = []
                    break
                rel_depth = len(Path(dirpath).relative_to(root).parts)
                if rel_depth > MAX_SCAN_DEPTH:
                    dirnames[:] = []
                    continue
                # skip hidden
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                skill_md = None
                for name in filenames:
                    files_seen += 1
                    if files_seen > MAX_SCAN_FILES:
                        break
                    if name.lower() in {"skill.md"}:
                        skill_md = Path(dirpath) / name
                        break
                if skill_md is None:
                    continue
                try:
                    text = skill_md.read_text(encoding="utf-8")
                except OSError:
                    continue
                meta, _ = parse_skill_md_frontmatter(text)
                rel_dir = str(Path(dirpath).relative_to(root)).replace("\\", "/")
                if rel_dir == ".":
                    rel_dir = skill_md.parent.name
                skill_key = re.sub(
                    r"[^a-z0-9._-]+",
                    "-",
                    str(meta.get("name") or Path(dirpath).name).lower(),
                ).strip("-")[:80]
                if not KEY_RE.match(skill_key):
                    skill_key = f"local-{hash(dirpath) & 0xFFFFFFFF:x}"
                has_scripts = (Path(dirpath) / "scripts").is_dir()
                items.append(
                    SkillLocalProbeItem(
                        root_label=str(root),
                        root_path=str(root),
                        skill_key=skill_key,
                        name=str(meta.get("name") or Path(dirpath).name)[:160],
                        description=str(meta.get("description") or "")[:2000],
                        relative_dir=rel_dir,
                        has_scripts=has_scripts,
                        skill_md_present=True,
                    )
                )
        row.last_scanned_at = utc_now()
        row.last_scan_summary = {
            "scanned_roots": scanned,
            "item_count": len(items),
            "dirs_seen": dirs_seen,
            "files_seen": files_seen,
        }
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.local_probe.scan",
            resource_type="workspace",
            resource_id=self.workspace_id,
            details={"item_count": len(items), "roots": len(scanned)},
        )
        self.db.commit()
        return SkillLocalProbeScanResponse(
            available=True,
            scanned_roots=scanned,
            items=items,
        )

    def import_local(self, payload: SkillLocalImportRequest) -> SkillRecord:
        available, reason = same_host_probe_available(self.settings)
        if not available:
            raise AppError(503, "local_probe_unavailable", reason or "Local probe unavailable")
        row = self._policy_row()
        if row is None or not row.enabled:
            raise AppError(400, "local_probe_disabled", "Local skill probe is disabled")
        root = Path(payload.root_path).expanduser().resolve()
        allowed = {str(Path(p).expanduser().resolve()) for p in (row.allowed_roots_json or [])}
        if allowed and str(root) not in allowed:
            # also allow candidate roots that exist when user enabled without custom list
            candidates = {str(Path(item["path"]).expanduser().resolve()) for item in candidate_skill_roots()}
            if str(root) not in candidates:
                raise AppError(403, "local_probe_root_not_allowed", "Root is not in the allowed probe list")
        rel = payload.relative_dir.replace("\\", "/").strip("/")
        if ".." in Path(rel).parts:
            raise AppError(400, "invalid_skill_path", "relative_dir cannot escape root")
        skill_dir = (root / rel).resolve()
        if root not in skill_dir.parents and skill_dir != root:
            raise AppError(400, "invalid_skill_path", "Skill directory escapes allowed root")
        if not skill_dir.is_dir():
            raise AppError(404, "local_skill_not_found", "Local skill directory was not found")
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            skill_md = skill_dir / "skill.md"
        if not skill_md.is_file():
            raise AppError(400, "skill_md_required", "Local skill is missing SKILL.md")

        skill_key = payload.skill_key or re.sub(
            r"[^a-z0-9._-]+", "-", skill_dir.name.lower()
        ).strip("-")[:80]
        if not KEY_RE.match(skill_key):
            raise AppError(400, "invalid_skill_key", "skill_key is invalid")
        assert_skill_identity_not_reserved(skill_key)
        if self.db.scalar(self.skills.query().where(SkillRecord.skill_key == skill_key)):
            raise AppError(409, "skill_key_exists", "Skill key already exists")

        files: list[tuple[str, bytes]] = []
        total = 0
        for dirpath, dirnames, filenames in os.walk(skill_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                path = Path(dirpath) / name
                rel_path = path.relative_to(skill_dir).as_posix()
                if path.suffix.casefold() not in TEXT_SUFFIXES and path.name.lower() not in {
                    "skill.md",
                    "readme.md",
                }:
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if len(data) > MAX_SKILL_FILE_BYTES:
                    continue
                total += len(data)
                if total > MAX_SKILL_PACKAGE_BYTES or len(files) >= MAX_SKILL_FILES:
                    break
                files.append((rel_path if rel_path != "skill.md" else "SKILL.md", data))
            if total > MAX_SKILL_PACKAGE_BYTES or len(files) >= MAX_SKILL_FILES:
                break
        if not any(p == "SKILL.md" for p, _ in files):
            raise AppError(400, "skill_md_required", "Could not read SKILL.md as text")

        text = next(d for p, d in files if p == "SKILL.md").decode("utf-8", errors="replace")
        meta, body = parse_skill_md_frontmatter(text)
        skill = self.skills.add(
            SkillRecord(
                workspace_id=self.workspace_id,
                skill_key=skill_key,
                name=str(meta.get("name") or skill_dir.name)[:160],
                source=f"local:{root.name}/{rel}",
                version="local",
                generated_by="local_import",
                kind="agent_skill_package",
                package_format="skill_md_v1",
                origin_type="local_probe",
                origin_ref=str(skill_dir),
                origin_hash="",
                has_scripts=False,
                locale_source="",
                content_hash="",
                manifest_json={
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "origin": "local_probe",
                },
                manifest_hash="",
                instructions_markdown=(body or text)[:20_000],
                required_tools=[],
                required_permissions=[],
                allowed_components=[],
                validation_report={},
                status="authorization_required",
                enabled=False,
            )
        )
        self.db.flush()
        for rel_path, data in files:
            path = normalize_skill_relative_path(rel_path)
            blob = self.blobs.put_bytes(data, mime_type=guess_mime(path))
            self.db.add(
                SkillPackageFile(
                    workspace_id=self.workspace_id,
                    skill_id=skill.id,
                    relative_path=path,
                    blob_sha256=blob.sha256,
                    size_bytes=len(data),
                    mime_type=guess_mime(path),
                    is_directory=False,
                )
            )
        self.db.flush()
        from app.services.skill_package import SkillPackageService
        from app.services.skill_security_scan import attach_scan_report

        SkillPackageService(
            self.db, self.workspace_id, self.actor_id, self.settings
        )._recompute_package_state(skill)
        attach_scan_report(
            skill,
            [(p, d.decode("utf-8", errors="replace")) for p, d in files],
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.local_probe.import",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "skill_key": skill.skill_key,
                "relative_dir": rel,
                "file_count": len(files),
                "content_hash": skill.content_hash,
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def skill_view(self, skill: SkillRecord) -> SkillView:
        return SkillView.model_validate(skill)
