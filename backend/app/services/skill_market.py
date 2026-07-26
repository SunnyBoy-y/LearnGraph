"""skills.sh market seed + GitHub raw cache and workspace install (D-078)."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.extension_models import SkillMarketCacheEntry, SkillPackageFile, SkillRecord
from app.domain.models import new_id, utc_now
from app.domain.schemas.extensions import (
    SkillManualImportRequest,
    SkillMarketCardView,
    SkillMarketInstallRequest,
    SkillMarketListResponse,
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
    normalize_skill_relative_path,
    parse_skill_md_frontmatter,
    guess_mime,
)

# Curated popular seeds (skills.sh leaderboard-style). MVP without OIDC.
MARKET_SEEDS: list[dict[str, Any]] = [
    {
        "market_id": "vercel-labs/skills/find-skills",
        "slug": "find-skills",
        "name": "find-skills",
        "source": "vercel-labs/skills",
        "description": "Discover and install Agent Skills from the ecosystem.",
        "install_url": "vercel-labs/skills@find-skills",
        "homepage_url": "https://www.skills.sh/",
        "installs": 2_600_000,
        "github_owner": "vercel-labs",
        "github_repo": "skills",
        "github_path": "skills/find-skills",
        "rank": 1,
    },
    {
        "market_id": "anthropics/skills/frontend-design",
        "slug": "frontend-design",
        "name": "frontend-design",
        "source": "anthropics/skills",
        "description": "Frontend design guidance for production-quality UI.",
        "install_url": "anthropics/skills@frontend-design",
        "homepage_url": "https://www.skills.sh/",
        "installs": 687_000,
        "github_owner": "anthropics",
        "github_repo": "skills",
        "github_path": "skills/frontend-design",
        "rank": 2,
    },
    {
        "market_id": "anthropics/skills/skill-creator",
        "slug": "skill-creator",
        "name": "skill-creator",
        "source": "anthropics/skills",
        "description": "Guide for creating new Agent Skills packages.",
        "install_url": "anthropics/skills@skill-creator",
        "homepage_url": "https://www.skills.sh/",
        "installs": 400_000,
        "github_owner": "anthropics",
        "github_repo": "skills",
        "github_path": "skills/skill-creator",
        "rank": 3,
    },
    {
        "market_id": "vercel-labs/agent-skills/vercel-react-best-practices",
        "slug": "vercel-react-best-practices",
        "name": "vercel-react-best-practices",
        "source": "vercel-labs/agent-skills",
        "description": "React and Next.js best practices for agent coding.",
        "install_url": "vercel-labs/agent-skills@vercel-react-best-practices",
        "homepage_url": "https://www.skills.sh/",
        "installs": 567_000,
        "github_owner": "vercel-labs",
        "github_repo": "agent-skills",
        "github_path": "skills/vercel-react-best-practices",
        "rank": 4,
    },
    {
        "market_id": "vercel-labs/agent-browser/agent-browser",
        "slug": "agent-browser",
        "name": "agent-browser",
        "source": "vercel-labs/agent-browser",
        "description": "Browser automation skill for agents.",
        "install_url": "vercel-labs/agent-browser@agent-browser",
        "homepage_url": "https://www.skills.sh/",
        "installs": 564_000,
        "github_owner": "vercel-labs",
        "github_repo": "agent-browser",
        "github_path": "skills/agent-browser",
        "rank": 5,
    },
    {
        "market_id": "learngraph/builtin/review-due",
        "slug": "review-due",
        "name": "review-due",
        "source": "learngraph/builtin",
        "description": "LearnGraph declarative-style review instructions (package form).",
        "install_url": "learngraph/builtin@review-due",
        "homepage_url": "https://www.skills.sh/",
        "installs": 0,
        "github_owner": "",
        "github_repo": "",
        "github_path": "",
        "rank": 100,
        "inline_files": [
            {
                "path": "SKILL.md",
                "contents": (
                    "---\n"
                    "name: review-due\n"
                    "description: Help the learner review due mastery nodes with evidence-aware prompts.\n"
                    "---\n\n"
                    "# Review due nodes\n\n"
                    "When the user wants to review, list due nodes, explain why each is due, "
                    "and propose short retrieval practice. Do not invent mastery stars.\n"
                ),
            }
        ],
    },
]

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


class SkillMarketService:
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

    def ensure_seeds(self) -> None:
        for seed in MARKET_SEEDS:
            existing = self.db.scalar(
                select(SkillMarketCacheEntry).where(
                    SkillMarketCacheEntry.market_id == seed["market_id"]
                )
            )
            if existing is not None:
                continue
            files = list(seed.get("inline_files") or [])
            entry = SkillMarketCacheEntry(
                id=new_id(),
                market_id=seed["market_id"],
                slug=seed["slug"],
                name=seed["name"],
                source=seed["source"],
                description=seed["description"],
                install_url=seed["install_url"],
                homepage_url=seed["homepage_url"],
                installs=int(seed.get("installs") or 0),
                source_type="seed" if files else "seed_pending_github",
                origin_hash=hashlib.sha256(seed["market_id"].encode()).hexdigest(),
                files_json=files,
                fetched_at=utc_now() if files else None,
                fetch_status="ready" if files else "seeded",
                rank=int(seed.get("rank") or 999),
            )
            self.db.add(entry)
        self.db.commit()

    def refresh_from_github(self, *, limit: int = 8) -> dict[str, Any]:
        """Refresh market cache from GitHub. Always commits; returns per-seed status summary."""

        self.ensure_seeds()
        if not getattr(self.settings, "skill_market_refresh_enabled", True):
            return {
                "attempted": 0,
                "ready": 0,
                "failed": 0,
                "skipped": len(MARKET_SEEDS),
                "errors": [],
                "disabled": True,
            }
        summary: dict[str, Any] = {
            "attempted": 0,
            "ready": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
        }
        for seed in MARKET_SEEDS[:limit]:
            row = self.db.scalar(
                select(SkillMarketCacheEntry).where(
                    SkillMarketCacheEntry.market_id == seed["market_id"]
                )
            )
            if row is None:
                summary["skipped"] += 1
                continue
            if seed.get("inline_files") and not seed.get("github_owner"):
                # Builtin/inline seed — mark ready so install always works.
                if not row.files_json:
                    row.files_json = list(seed.get("inline_files") or [])
                    row.fetch_status = "ready"
                    row.fetch_error = None
                    row.fetched_at = utc_now()
                    row.source_type = "seed"
                    row.origin_hash = hashlib.sha256(
                        _canonical_bytes(row.files_json)
                    ).hexdigest()
                summary["ready"] += 1
                continue
            summary["attempted"] += 1
            try:
                files = self._fetch_github_skill_files(seed)
                row.files_json = files
                row.fetch_status = "ready"
                row.fetch_error = None
                row.fetched_at = utc_now()
                row.source_type = "github_raw"
                row.origin_hash = hashlib.sha256(_canonical_bytes(files)).hexdigest()
                # Prefer frontmatter name/description when present
                skill_md = next(
                    (f for f in files if str(f.get("path")) == "SKILL.md"),
                    None,
                )
                if skill_md and skill_md.get("contents"):
                    meta, _ = parse_skill_md_frontmatter(str(skill_md["contents"]))
                    if meta.get("name"):
                        row.name = str(meta["name"])[:200]
                    if meta.get("description"):
                        row.description = str(meta["description"])[:4000]
                summary["ready"] += 1
            except AppError as exc:
                row.fetch_status = "failed"
                row.fetch_error = exc.message
                row.fetched_at = utc_now()
                summary["failed"] += 1
                summary["errors"].append(
                    {"market_id": seed["market_id"], "error": exc.message}
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc)[:1000]
                row.fetch_status = "failed"
                row.fetch_error = message
                row.fetched_at = utc_now()
                summary["failed"] += 1
                summary["errors"].append(
                    {"market_id": seed["market_id"], "error": message}
                )
        # Aggregate the ClawHub catalog as discovery-only cards (settings-gated,
        # best-effort — a ClawHub outage must not fail the GitHub refresh).
        if getattr(self.settings, "clawhub_enabled", False):
            try:
                from app.services.skill_clawhub_sync import ClawHubMarketSync

                summary["clawhub"] = ClawHubMarketSync(self.db, self.settings).refresh()
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                summary["clawhub"] = {"enabled": True, "error": str(exc)[:200]}
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.market.refresh",
            resource_type="workspace",
            resource_id=self.workspace_id,
            details={
                "attempted": summary["attempted"],
                "ready": summary["ready"],
                "failed": summary["failed"],
                "clawhub": summary.get("clawhub"),
            },
        )
        self.db.commit()
        return summary

    def list_cards(
        self,
        *,
        refresh: bool = False,
        query: str = "",
        page: int = 1,
        page_size: int = 12,
    ) -> SkillMarketListResponse:
        self.ensure_seeds()
        if refresh:
            self.refresh_from_github(limit=len(MARKET_SEEDS))
        page = max(1, int(page or 1))
        page_size = min(48, max(1, int(page_size or 12)))
        q = (query or "").strip()
        statement = select(SkillMarketCacheEntry)
        if q:
            like = f"%{q}%"
            statement = statement.where(
                or_(
                    SkillMarketCacheEntry.name.ilike(like),
                    SkillMarketCacheEntry.slug.ilike(like),
                    SkillMarketCacheEntry.source.ilike(like),
                    SkillMarketCacheEntry.description.ilike(like),
                    SkillMarketCacheEntry.market_id.ilike(like),
                )
            )
        total = int(
            self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = list(
            self.db.scalars(
                statement.order_by(
                    SkillMarketCacheEntry.rank, SkillMarketCacheEntry.market_id
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        cards = [self._card(row) for row in rows]
        refreshed = self.db.scalar(
            select(func.max(SkillMarketCacheEntry.fetched_at))
        )
        total_pages = max(1, (total + page_size - 1) // page_size) if total else 0
        return SkillMarketListResponse(
            source="seed+github",
            refreshed_at=refreshed,
            cards=cards,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            query=q,
        )

    def _card(self, row: SkillMarketCacheEntry) -> SkillMarketCardView:
        files = list(row.files_json or [])
        has_scripts = any(
            str(item.get("path") or "").startswith("scripts/") for item in files if isinstance(item, dict)
        )
        return SkillMarketCardView(
            market_id=row.market_id,
            slug=row.slug,
            name=row.name,
            source=row.source,
            description=row.description,
            install_url=row.install_url,
            homepage_url=row.homepage_url,
            installs=row.installs,
            source_type=row.source_type,
            origin_hash=row.origin_hash,
            fetch_status=row.fetch_status,
            fetch_error=row.fetch_error,
            fetched_at=row.fetched_at,
            rank=row.rank,
            file_count=len(files),
            has_scripts=has_scripts,
            official=str(row.source or "").startswith("learngraph"),
        )

    def _http_get(self, url: str, timeout: float = 12.0) -> bytes:
        headers = {
            "User-Agent": "LearnGraph-SkillMarket/1.0",
            "Accept": "application/vnd.github+json, text/plain, */*",
        }
        token = (getattr(self.settings, "skill_market_github_token", None) or "").strip()
        host = urllib.parse.urlsplit(url).hostname or ""
        if token and (host == "github.com" or host.endswith((".github.com", ".githubusercontent.com"))):
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data = response.read(MAX_SKILL_FILE_BYTES + 1)
        if len(data) > MAX_SKILL_FILE_BYTES:
            raise AppError(400, "market_file_too_large", "Remote skill file exceeds size limit")
        return data

    def _fetch_github_skill_files(self, seed: dict[str, Any]) -> list[dict[str, str]]:
        owner = seed.get("github_owner") or ""
        repo = seed.get("github_repo") or ""
        path = (seed.get("github_path") or "").strip("/")
        slug = (seed.get("slug") or "").strip("/")
        if not owner or not repo:
            return list(seed.get("inline_files") or [])
        # Prefer SKILL.md at configured path; try common Agent Skill layouts.
        candidates: list[str] = []
        if path:
            candidates.append(f"{path}/SKILL.md")
            candidates.append(f"{path}/skill.md")
        if slug:
            candidates.append(f"skills/{slug}/SKILL.md")
            candidates.append(f"{slug}/SKILL.md")
        candidates.append("SKILL.md")
        # Deduplicate while preserving order
        seen: set[str] = set()
        ordered: list[str] = []
        for rel in candidates:
            if rel not in seen:
                seen.add(rel)
                ordered.append(rel)
        last_error: str | None = None
        for rel in ordered:
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{rel}"
            try:
                body = self._http_get(url)
                text = body.decode("utf-8")
                return [{"path": "SKILL.md", "contents": text}]
            except Exception as exc:  # noqa: BLE001 — record honest failure per candidate
                last_error = str(exc)
                continue
        raise AppError(
            502,
            "market_fetch_failed",
            last_error or "Failed to fetch skill from GitHub",
        )

    def install(self, payload: SkillMarketInstallRequest) -> SkillRecord:
        self.ensure_seeds()
        row = self.db.scalar(
            select(SkillMarketCacheEntry).where(
                SkillMarketCacheEntry.market_id == payload.market_id
            )
        )
        if row is None:
            raise AppError(404, "market_skill_not_found", "Market skill was not found in cache")
        files = list(row.files_json or [])
        if not files:
            # try one-shot fetch
            seed = next((s for s in MARKET_SEEDS if s["market_id"] == row.market_id), None)
            if seed is None:
                raise AppError(409, "market_skill_empty", "Market skill has no cached files yet")
            try:
                files = self._fetch_github_skill_files(seed)
                row.files_json = files
                row.fetch_status = "ready"
                row.fetched_at = utc_now()
                row.origin_hash = hashlib.sha256(_canonical_bytes(files)).hexdigest()
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AppError(502, "market_fetch_failed", str(exc)) from exc

        skill_key = payload.skill_key or re.sub(r"[^a-z0-9._-]+", "-", row.slug.lower()).strip("-")
        if not KEY_RE.match(skill_key):
            skill_key = f"market-{row.slug[:40]}".lower()
            skill_key = re.sub(r"[^a-z0-9._-]+", "-", skill_key).strip("-")[:80]
            if not KEY_RE.match(skill_key):
                skill_key = f"mkt-{hashlib.sha256(row.market_id.encode()).hexdigest()[:12]}"
        assert_skill_identity_not_reserved(skill_key)
        if self.db.scalar(self.skills.query().where(SkillRecord.skill_key == skill_key)):
            raise AppError(409, "skill_key_exists", "Skill key already exists in this workspace")

        if len(files) > MAX_SKILL_FILES:
            raise AppError(400, "skill_too_many_files", "Market package exceeds file count limit")

        skill = self.skills.add(
            SkillRecord(
                workspace_id=self.workspace_id,
                skill_key=skill_key,
                name=row.name or skill_key,
                source=f"skills.sh:{row.market_id}",
                version="market",
                generated_by="market_install",
                kind="agent_skill_package",
                package_format="skill_md_v1",
                origin_type="skills_sh_market",
                origin_ref=row.market_id,
                origin_hash=row.origin_hash or "",
                has_scripts=False,
                locale_source="",
                content_hash="",
                manifest_json={
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "market_id": row.market_id,
                    "source": row.source,
                },
                manifest_hash="",
                instructions_markdown="",
                required_tools=[],
                required_permissions=[],
                allowed_components=[],
                validation_report={"market_id": row.market_id},
                status="authorization_required",
                enabled=False,
            )
        )
        self.db.flush()
        total = 0
        for item in files:
            if not isinstance(item, dict):
                continue
            path = normalize_skill_relative_path(str(item.get("path") or ""))
            contents = str(item.get("contents") or "")
            data = contents.encode("utf-8")
            total += len(data)
            if total > MAX_SKILL_PACKAGE_BYTES:
                raise AppError(400, "skill_package_too_large", "Market package exceeds size limit")
            if len(data) > MAX_SKILL_FILE_BYTES:
                raise AppError(400, "skill_file_too_large", f"File {path} exceeds size limit")
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
        # Recompute via package service helper logic
        from app.services.skill_package import SkillPackageService
        from app.services.skill_security_scan import attach_scan_report

        pkg = SkillPackageService(self.db, self.workspace_id, self.actor_id, self.settings)
        pkg._recompute_package_state(skill)
        attach_scan_report(
            skill,
            [
                (str(item.get("path") or ""), str(item.get("contents") or ""))
                for item in files
                if isinstance(item, dict)
            ],
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.market.install",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "market_id": row.market_id,
                "content_hash": skill.content_hash,
                "fetch_status": row.fetch_status,
                "source": "skills.sh_seed_github",
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def import_manual(
        self,
        payload: SkillManualImportRequest,
        *,
        origin_type: str = "manual_import",
        origin_ref: str = "user_editor",
        report_extra: dict[str, Any] | None = None,
    ) -> SkillRecord:
        """Install a user-edited multi-file package (must include SKILL.md)."""

        assert_skill_identity_not_reserved(payload.skill_key, payload.source)
        if self.db.scalar(self.skills.query().where(SkillRecord.skill_key == payload.skill_key)):
            raise AppError(409, "skill_key_exists", "Skill key already exists in this workspace")
        if len(payload.files) > MAX_SKILL_FILES:
            raise AppError(400, "skill_too_many_files", "Package exceeds file count limit")

        normalized_files: list[tuple[str, bytes]] = []
        total = 0
        seen: set[str] = set()
        for item in payload.files:
            path = normalize_skill_relative_path(item.path)
            if path.casefold() == "skill.md":
                path = "SKILL.md"
            if path in seen:
                raise AppError(400, "duplicate_skill_path", f"Duplicate path: {path}")
            seen.add(path)
            data = item.contents.encode("utf-8")
            total += len(data)
            if len(data) > MAX_SKILL_FILE_BYTES:
                raise AppError(400, "skill_file_too_large", f"File {path} exceeds size limit")
            if total > MAX_SKILL_PACKAGE_BYTES:
                raise AppError(400, "skill_package_too_large", "Package exceeds size limit")
            normalized_files.append((path, data))
        if "SKILL.md" not in seen:
            raise AppError(400, "skill_md_required", "Manual import requires SKILL.md")

        skill_md = next(data for path, data in normalized_files if path == "SKILL.md")
        try:
            text = skill_md.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(415, "skill_file_not_text", "SKILL.md must be UTF-8 text") from exc
        meta, body = parse_skill_md_frontmatter(text)
        name = (payload.name or str(meta.get("name") or payload.skill_key)).strip()[:160]
        description = str(meta.get("description") or "")[:4000]

        skill = self.skills.add(
            SkillRecord(
                workspace_id=self.workspace_id,
                skill_key=payload.skill_key,
                name=name,
                source=payload.source.strip(),
                version=payload.version.strip(),
                generated_by=origin_type,
                kind="agent_skill_package",
                package_format="skill_md_v1",
                origin_type=origin_type,
                origin_ref=origin_ref[:500],
                origin_hash=hashlib.sha256(_canonical_bytes([
                    {"path": p, "sha256": hashlib.sha256(d).hexdigest()}
                    for p, d in normalized_files
                ])).hexdigest(),
                has_scripts=False,
                locale_source="",
                content_hash="",
                manifest_json={
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "name": name,
                    "description": description,
                    "origin": origin_type,
                },
                manifest_hash="",
                instructions_markdown=(body or text)[:20_000],
                required_tools=[],
                required_permissions=[],
                allowed_components=[],
                validation_report={
                    "origin": origin_type,
                    "file_count": len(normalized_files),
                    **(report_extra or {}),
                },
                status="authorization_required",
                enabled=False,
            )
        )
        self.db.flush()
        for path, data in normalized_files:
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
            [(p, d.decode("utf-8", errors="replace")) for p, d in normalized_files],
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.manual.import",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "skill_key": skill.skill_key,
                "origin_type": origin_type,
                "file_count": len(normalized_files),
                "content_hash": skill.content_hash,
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def skill_view(self, skill: SkillRecord) -> SkillView:
        return SkillView.model_validate(skill)
