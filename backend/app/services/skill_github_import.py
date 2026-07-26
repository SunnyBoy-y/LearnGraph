"""GitHub-pinned Agent Skill import (marketplace phase 2 install loop).

Flow: parse reference → resolve ref to a commit SHA (GitHub commits API) →
list the tree (git trees API, one request) → fetch candidate files from
``raw.githubusercontent.com`` at the pinned SHA.  Installed packages record
their full provenance (owner/repo/path/ref/commit) in ``manifest_json`` so
update checks can compare the upstream commit later.

Only text files are imported (same suffix policy as the local probe); binary
assets are counted and reported as skipped, never silently pretended present.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.extension_models import SkillPackageFile, SkillRecord
from app.domain.schemas.extensions import (
    SkillGitHubCandidate,
    SkillGitHubInstallRequest,
    SkillGitHubPreviewRequest,
    SkillGitHubPreviewResponse,
    SkillNpxImportRequest,
    SkillNpxImportResponse,
    SkillNpxSkippedItem,
    SkillUpdateCheckResponse,
    SkillView,
)
from app.repositories.audit import AuditRepository
from app.repositories.extensions import SkillRepository
from app.services.session_workspace import BlobStore
from app.services.skill_package import (
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_FILES,
    MAX_SKILL_PACKAGE_BYTES,
    SkillPackageService,
    assert_skill_identity_not_reserved,
    guess_mime,
    normalize_skill_relative_path,
    parse_skill_md_frontmatter,
)
from app.services.skill_security_scan import attach_scan_report, scan_skill_files

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")
OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
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
    ".css",
    ".html",
    ".csv",
}
MAX_TREE_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PREVIEW_CANDIDATES = 20
MAX_PREVIEW_DETAIL_FETCHES = 10


@dataclass(frozen=True)
class GitHubRef:
    owner: str
    repo: str
    path: str  # "" == repo root
    ref: str | None  # branch / tag / sha; None == default branch


def parse_github_reference(text: str) -> GitHubRef:
    """Parse GitHub URL or shorthand forms into (owner, repo, path, ref).

    Accepted: ``https://github.com/o/r``, ``…/o/r/tree/<ref>/<path>``,
    ``…/o/r/blob/<ref>/<path>``, ``o/r``, ``o/r/path``, ``o/r[/path]@ref``.
    """

    raw = (text or "").strip()
    if not raw:
        raise AppError(400, "invalid_github_reference", "GitHub reference is required")
    ref: str | None = None
    if raw.startswith(("http://", "https://")):
        parts = urllib.parse.urlsplit(raw)
        if parts.hostname not in {"github.com", "www.github.com"}:
            raise AppError(400, "invalid_github_reference", "Only github.com URLs are supported")
        segments = [seg for seg in parts.path.split("/") if seg]
        if len(segments) < 2:
            raise AppError(400, "invalid_github_reference", "URL must include owner/repo")
        owner, repo = segments[0], segments[1].removesuffix(".git")
        path = ""
        if len(segments) >= 4 and segments[2] in {"tree", "blob"}:
            ref = segments[3]
            path = "/".join(segments[4:])
            if segments[2] == "blob" and path:
                # A blob URL points at a file; import its parent directory.
                path = "/".join(path.split("/")[:-1])
        elif len(segments) > 2:
            raise AppError(
                400,
                "invalid_github_reference",
                "Use a repo URL or a /tree/<ref>/<path> URL",
            )
    else:
        body = raw
        if "@" in body:
            body, _, ref_part = body.rpartition("@")
            ref = ref_part.strip() or None
        segments = [seg for seg in body.replace("\\", "/").split("/") if seg]
        if len(segments) < 2:
            raise AppError(
                400,
                "invalid_github_reference",
                "Use owner/repo, owner/repo/path, or a github.com URL",
            )
        owner, repo = segments[0], segments[1].removesuffix(".git")
        path = "/".join(segments[2:])
    if not OWNER_REPO_RE.match(owner) or not OWNER_REPO_RE.match(repo):
        raise AppError(400, "invalid_github_reference", "Invalid owner or repo name")
    path = path.strip("/")
    if path:
        path = normalize_skill_relative_path(path)
    return GitHubRef(owner=owner, repo=repo, path=path, ref=ref)


# `npx skills add` command support (skills.sh CLI compatible). The command is
# parsed only — LearnGraph never launches npx or any host process for it.
NPX_WRAPPER_TOKENS = {"npx", "bunx", "dlx", "exec"}
NPX_RUNNER_TOKENS = {"pnpm", "yarn", "bun"}
NPX_ADD_VERBS = {"add", "install", "i"}
NPX_VALUE_FLAGS = {"-a", "--agent"}


@dataclass(frozen=True)
class NpxSkillsCommand:
    reference: str
    skills: list[str]
    all_skills: bool


def _normalize_skills_source(source: str) -> tuple[str, list[str]]:
    """Map a skills.sh URL onto (github reference, skill filters)."""

    if source.startswith(("http://", "https://")):
        parts = urllib.parse.urlsplit(source)
        host = (parts.hostname or "").lower()
        if host in {"skills.sh", "www.skills.sh"}:
            segments = [seg for seg in parts.path.split("/") if seg]
            if len(segments) < 2:
                raise AppError(
                    400,
                    "invalid_npx_command",
                    "skills.sh URL must include owner/repo (e.g. skills.sh/anthropics/skills/frontend-design)",
                )
            reference = f"{segments[0]}/{segments[1]}"
            return reference, ([segments[-1]] if len(segments) >= 3 else [])
    return source, []


def parse_npx_skills_command(text: str) -> NpxSkillsCommand:
    """Parse ``npx skills add <source> [--skill name]… [--all]`` or a bare source."""

    raw = (text or "").strip()
    if not raw:
        raise AppError(400, "invalid_npx_command", "Command is required")
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise AppError(400, "invalid_npx_command", f"Cannot parse command: {exc}") from exc

    index = 0
    while index < len(tokens):
        lowered = tokens[index].lower()
        if lowered in NPX_WRAPPER_TOKENS or lowered in NPX_RUNNER_TOKENS or lowered in {"-y", "--yes"}:
            index += 1
            continue
        break
    if index < len(tokens) and tokens[index].lower() == "skills":
        index += 1
        if index >= len(tokens) or tokens[index].lower() not in NPX_ADD_VERBS:
            verb = tokens[index] if index < len(tokens) else "(缺失)"
            raise AppError(
                400,
                "npx_subcommand_unsupported",
                f"仅支持 `skills add`；不支持子命令 `{verb}`",
            )
        index += 1

    source: str | None = None
    skills: list[str] = []
    all_skills = False
    while index < len(tokens):
        token = tokens[index]
        if token in {"--skill", "-s"}:
            index += 1
            if index >= len(tokens):
                raise AppError(400, "invalid_npx_command", "--skill needs a value")
            skills.append(tokens[index])
        elif token.startswith("--skill="):
            skills.append(token.split("=", 1)[1])
        elif token == "--all":
            all_skills = True
        elif token in {"--list", "-l"}:
            raise AppError(
                400,
                "npx_list_unsupported",
                "`--list` 仅用于查看；请改用预览接口或去掉该参数",
            )
        elif token in NPX_VALUE_FLAGS or token.startswith("--agent="):
            if token in NPX_VALUE_FLAGS:
                index += 1  # swallow the agent name; LearnGraph is the agent
        elif token.startswith("-"):
            pass  # -g / -y / --global / --copy … are host-CLI concerns; ignore
        elif source is None:
            source = token
        else:
            raise AppError(
                400,
                "invalid_npx_command",
                "一次只支持一个来源；如需多个 Skill 请使用 --skill 重复指定",
            )
        index += 1
    if not source:
        raise AppError(400, "invalid_npx_command", "命令缺少 Skill 来源（仓库或 URL）")

    reference, url_skills = _normalize_skills_source(source)
    merged = list(dict.fromkeys(s.strip() for s in [*skills, *url_skills] if s.strip()))
    return NpxSkillsCommand(reference=reference, skills=merged, all_skills=all_skills)


MAX_NPX_INSTALLS = 10


class SkillGitHubImportService:
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

    # -- public API ---------------------------------------------------------

    def preview(self, payload: SkillGitHubPreviewRequest) -> SkillGitHubPreviewResponse:
        ref = parse_github_reference(payload.reference)
        commit = self._resolve_commit(ref)
        tree, truncated = self._fetch_tree(ref.owner, ref.repo, commit)
        candidates = self._find_candidates(tree, ref.path)
        views: list[SkillGitHubCandidate] = []
        for index, candidate_path in enumerate(candidates[:MAX_PREVIEW_CANDIDATES]):
            stats = self._candidate_stats(tree, candidate_path)
            meta: dict[str, Any] = {}
            scan_risk = ""
            scan_finding_count = 0
            if index < MAX_PREVIEW_DETAIL_FETCHES:
                try:
                    text = self._fetch_raw(
                        ref.owner, ref.repo, commit, self._join(candidate_path, "SKILL.md")
                    ).decode("utf-8", errors="replace")
                    meta, _ = parse_skill_md_frontmatter(text)
                    quick_scan = scan_skill_files([("SKILL.md", text)])
                    scan_risk = str(quick_scan.get("risk_level") or "")
                    scan_finding_count = int(quick_scan.get("finding_count") or 0)
                except AppError:
                    meta = {}
            views.append(
                SkillGitHubCandidate(
                    path=candidate_path,
                    name=str(meta.get("name") or candidate_path.rsplit("/", 1)[-1] or ref.repo)[:200],
                    description=str(meta.get("description") or "")[:2000],
                    license=str(meta.get("license") or "")[:200],
                    allowed_tools=str(meta.get("allowed-tools") or "")[:500],
                    file_count=stats["file_count"],
                    total_size_bytes=stats["total_size_bytes"],
                    has_scripts=stats["has_scripts"],
                    skipped_file_count=stats["skipped_file_count"],
                    required_permissions=(
                        ["sandbox.execute"] if stats["has_scripts"] else []
                    ),
                    scan_risk=scan_risk,
                    scan_finding_count=scan_finding_count,
                )
            )
        return SkillGitHubPreviewResponse(
            owner=ref.owner,
            repo=ref.repo,
            ref=ref.ref or "HEAD",
            commit=commit,
            tree_truncated=truncated,
            candidates=views,
        )

    def install(self, payload: SkillGitHubInstallRequest) -> SkillRecord:
        ref = parse_github_reference(payload.reference)
        if payload.path is not None:
            path = payload.path.strip("/")
            ref = GitHubRef(ref.owner, ref.repo, normalize_skill_relative_path(path) if path else "", ref.ref)
        commit = (payload.commit or "").strip().lower()
        if commit:
            if not COMMIT_RE.match(commit):
                raise AppError(400, "invalid_github_reference", "commit must be a hex SHA")
        else:
            commit = self._resolve_commit(ref)
        tree, truncated = self._fetch_tree(ref.owner, ref.repo, commit)
        candidates = self._find_candidates(tree, ref.path)
        if not candidates:
            raise AppError(404, "skill_md_required", "No SKILL.md found under the requested path")
        if len(candidates) > 1 and ref.path not in candidates:
            raise AppError(
                409,
                "github_path_ambiguous",
                "Multiple skills found; pick one path from preview",
                {"candidates": candidates[:MAX_PREVIEW_CANDIDATES]},
            )
        skill_dir = ref.path if ref.path in candidates else candidates[0]

        files, skipped = self._collect_files(tree, skill_dir)
        contents: list[tuple[str, bytes]] = []
        total = 0
        for rel_path, size in files:
            data = self._fetch_raw(ref.owner, ref.repo, commit, self._join(skill_dir, rel_path))
            if len(data) > MAX_SKILL_FILE_BYTES:
                skipped.append(rel_path)
                continue
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(rel_path)
                continue
            total += len(data)
            if total > MAX_SKILL_PACKAGE_BYTES:
                raise AppError(400, "skill_package_too_large", "Package exceeds 20 MB limit")
            contents.append((rel_path, data))
        if not any(p == "SKILL.md" for p, _ in contents):
            raise AppError(400, "skill_md_required", "SKILL.md could not be fetched as UTF-8 text")

        skill_md_text = next(d for p, d in contents if p == "SKILL.md").decode("utf-8")
        meta, body = parse_skill_md_frontmatter(skill_md_text)
        default_key = str(meta.get("name") or skill_dir.rsplit("/", 1)[-1] or ref.repo).lower()
        skill_key = payload.skill_key or re.sub(r"[^a-z0-9._-]+", "-", default_key).strip("-")[:80]
        if not KEY_RE.match(skill_key):
            skill_key = f"gh-{hashlib.sha256(f'{ref.owner}/{ref.repo}/{skill_dir}'.encode()).hexdigest()[:12]}"
        assert_skill_identity_not_reserved(skill_key)
        if self.db.scalar(self.skills.query().where(SkillRecord.skill_key == skill_key)):
            raise AppError(409, "skill_key_exists", "Skill key already exists in this workspace")

        origin_hash = hashlib.sha256(
            json.dumps(
                [{"path": p, "sha256": hashlib.sha256(d).hexdigest()} for p, d in contents],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        provenance = {
            "owner": ref.owner,
            "repo": ref.repo,
            "path": skill_dir,
            "ref": ref.ref or "HEAD",
            "commit": commit,
        }
        skill = self.skills.add(
            SkillRecord(
                workspace_id=self.workspace_id,
                skill_key=skill_key,
                name=str(meta.get("name") or skill_key)[:160],
                source=f"github:{ref.owner}/{ref.repo}/{skill_dir}"[:255],
                version=commit[:12],
                generated_by="github_import",
                kind="agent_skill_package",
                package_format="skill_md_v1",
                origin_type="github_import",
                origin_ref=f"{ref.owner}/{ref.repo}/{skill_dir}@{commit}"[:500],
                origin_hash=origin_hash,
                has_scripts=False,
                locale_source="",
                content_hash="",
                manifest_json={
                    "schema_version": "1.0",
                    "kind": "agent_skill_package",
                    "name": str(meta.get("name") or skill_key),
                    "description": str(meta.get("description") or "")[:4000],
                    "license": str(meta.get("license") or ""),
                    "github": provenance,
                },
                manifest_hash="",
                instructions_markdown=(body or skill_md_text)[:20_000],
                required_tools=[],
                required_permissions=[],
                allowed_components=[],
                validation_report={
                    "origin": "github_import",
                    "github": provenance,
                    "tree_truncated": truncated,
                    "skipped_files": skipped[:50],
                    "skipped_file_count": len(skipped),
                },
                status="authorization_required",
                enabled=False,
            )
        )
        self.db.flush()
        self._write_package_files(skill, contents)
        SkillPackageService(
            self.db, self.workspace_id, self.actor_id, self.settings
        )._recompute_package_state(skill)
        attach_scan_report(
            skill, [(p, d.decode("utf-8", errors="replace")) for p, d in contents]
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.github.import",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "repo": f"{ref.owner}/{ref.repo}",
                "path": skill_dir,
                "commit": commit,
                "file_count": len(contents),
                "skipped_file_count": len(skipped),
                "content_hash": skill.content_hash,
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def install_from_command(
        self,
        payload: SkillNpxImportRequest,
        *,
        extra_skills: list[str] | None = None,
    ) -> SkillNpxImportResponse:
        """Install the skills referenced by an ``npx skills add`` command.

        Equivalent outcome to running the CLI, but content is fetched through
        the commit-pinned GitHub importer — no npx, no host shell.
        """

        command = parse_npx_skills_command(payload.command)
        if extra_skills:
            merged = list(
                dict.fromkeys(
                    [*command.skills, *(s.strip() for s in extra_skills if s.strip())]
                )
            )
            command = NpxSkillsCommand(
                reference=command.reference, skills=merged, all_skills=command.all_skills
            )
        ref = parse_github_reference(command.reference)
        commit = self._resolve_commit(ref)
        tree, _truncated = self._fetch_tree(ref.owner, ref.repo, commit)
        candidates = self._find_candidates(tree, ref.path)
        if not candidates:
            raise AppError(404, "skill_md_required", "No SKILL.md found under the requested source")

        skipped: list[SkillNpxSkippedItem] = []
        targets: list[str] = []
        if command.skills:
            names_by_path: dict[str, str] = {}
            for want in command.skills:
                slug = want.strip().lower()
                match = next(
                    (
                        c
                        for c in candidates
                        if (c.rsplit("/", 1)[-1] or ref.repo).lower() == slug
                    ),
                    None,
                )
                if match is None:
                    # Fall back to SKILL.md frontmatter names (bounded fetches).
                    for candidate in candidates[:MAX_PREVIEW_CANDIDATES]:
                        if candidate not in names_by_path:
                            try:
                                text = self._fetch_raw(
                                    ref.owner, ref.repo, commit, self._join(candidate, "SKILL.md")
                                ).decode("utf-8", errors="replace")
                                meta, _ = parse_skill_md_frontmatter(text)
                                names_by_path[candidate] = str(meta.get("name") or "").strip().lower()
                            except AppError:
                                names_by_path[candidate] = ""
                        if names_by_path[candidate] == slug:
                            match = candidate
                            break
                if match is None:
                    skipped.append(SkillNpxSkippedItem(target=want, reason="skill_not_found_in_repo"))
                elif match not in targets:
                    targets.append(match)
        elif command.all_skills or len(candidates) == 1:
            targets = candidates[:MAX_NPX_INSTALLS]
            for extra in candidates[MAX_NPX_INSTALLS:]:
                skipped.append(SkillNpxSkippedItem(target=extra, reason="install_capped"))
        else:
            raise AppError(
                409,
                "github_path_ambiguous",
                "该来源包含多个 Skill；请追加 --skill <name> 或 --all",
                {"candidates": candidates[:MAX_PREVIEW_CANDIDATES]},
            )

        installed: list[SkillView] = []
        for path in targets:
            try:
                skill = self.install(
                    SkillGitHubInstallRequest(
                        reference=command.reference,
                        path=path,
                        commit=commit,
                        skill_key=payload.skill_key if len(targets) == 1 else None,
                    )
                )
                installed.append(self.skill_view(skill))
            except AppError as exc:
                skipped.append(
                    SkillNpxSkippedItem(target=path or "(root)", reason=f"{exc.code}: {exc.message}")
                )
        return SkillNpxImportResponse(
            reference=command.reference,
            owner=ref.owner,
            repo=ref.repo,
            commit=commit,
            requested_skills=command.skills,
            installed=installed,
            skipped=skipped,
        )

    def check_update(self, skill_id: str) -> SkillUpdateCheckResponse:
        skill = self.skills.require(skill_id, "Skill")
        provenance = self._provenance(skill)
        if provenance is None:
            return SkillUpdateCheckResponse(
                skill_id=skill.id,
                supported=False,
                message="此 Skill 不是 GitHub 固定导入，无法检查上游更新",
            )
        ref = GitHubRef(
            owner=provenance["owner"],
            repo=provenance["repo"],
            path=provenance.get("path") or "",
            ref=None if provenance.get("ref") in {None, "", "HEAD"} else str(provenance["ref"]),
        )
        latest = self._resolve_commit(ref)
        current = str(provenance.get("commit") or "")
        return SkillUpdateCheckResponse(
            skill_id=skill.id,
            supported=True,
            current_commit=current,
            latest_commit=latest,
            update_available=bool(current) and latest != current,
            checked_ref=ref.ref or "HEAD",
        )

    def upgrade(self, skill_id: str) -> SkillRecord:
        skill = self.skills.require(skill_id, "Skill")
        provenance = self._provenance(skill)
        if provenance is None:
            raise AppError(
                409,
                "skill_upgrade_unsupported",
                "Only GitHub-pinned skills can be upgraded from upstream",
            )
        ref = GitHubRef(
            owner=provenance["owner"],
            repo=provenance["repo"],
            path=provenance.get("path") or "",
            ref=None if provenance.get("ref") in {None, "", "HEAD"} else str(provenance["ref"]),
        )
        latest = self._resolve_commit(ref)
        if latest == str(provenance.get("commit") or ""):
            return skill
        tree, truncated = self._fetch_tree(ref.owner, ref.repo, latest)
        files, skipped = self._collect_files(tree, ref.path)
        contents: list[tuple[str, bytes]] = []
        total = 0
        for rel_path, _size in files:
            data = self._fetch_raw(ref.owner, ref.repo, latest, self._join(ref.path, rel_path))
            if len(data) > MAX_SKILL_FILE_BYTES:
                skipped.append(rel_path)
                continue
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(rel_path)
                continue
            total += len(data)
            if total > MAX_SKILL_PACKAGE_BYTES:
                raise AppError(400, "skill_package_too_large", "Package exceeds 20 MB limit")
            contents.append((rel_path, data))
        if not any(p == "SKILL.md" for p, _ in contents):
            raise AppError(400, "skill_md_required", "Upstream no longer provides a readable SKILL.md")

        previous_commit = str(provenance.get("commit") or "")
        rows = list(
            self.db.scalars(
                select(SkillPackageFile).where(
                    SkillPackageFile.workspace_id == self.workspace_id,
                    SkillPackageFile.skill_id == skill.id,
                )
            ).all()
        )
        for row in rows:
            self.db.delete(row)
        self.db.flush()
        self._write_package_files(skill, contents)
        manifest = dict(skill.manifest_json or {})
        manifest["github"] = {**provenance, "commit": latest}
        skill.manifest_json = manifest
        skill.version = latest[:12]
        skill.origin_ref = f"{ref.owner}/{ref.repo}/{ref.path}@{latest}"[:500]
        skill.origin_hash = hashlib.sha256(
            json.dumps(
                [{"path": p, "sha256": hashlib.sha256(d).hexdigest()} for p, d in contents],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        pkg = SkillPackageService(self.db, self.workspace_id, self.actor_id, self.settings)
        pkg._recompute_package_state(skill)
        pkg._invalidate_authorization(skill)
        attach_scan_report(
            skill, [(p, d.decode("utf-8", errors="replace")) for p, d in contents]
        )
        report = dict(skill.validation_report or {})
        report["github"] = manifest["github"]
        report["previous_commit"] = previous_commit
        report["skipped_files"] = skipped[:50]
        report["skipped_file_count"] = len(skipped)
        report["tree_truncated"] = truncated
        skill.validation_report = report
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.github.upgrade",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "repo": f"{ref.owner}/{ref.repo}",
                "path": ref.path,
                "from_commit": previous_commit,
                "to_commit": latest,
                "content_hash": skill.content_hash,
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def skill_view(self, skill: SkillRecord) -> SkillView:
        return SkillView.model_validate(skill)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _provenance(skill: SkillRecord) -> dict[str, Any] | None:
        for container in (skill.manifest_json, skill.validation_report):
            if not isinstance(container, dict):
                continue
            github = container.get("github")
            if (
                isinstance(github, dict)
                and github.get("owner")
                and github.get("repo")
                and github.get("commit")
            ):
                return dict(github)
        return None

    @staticmethod
    def _join(base: str, rel: str) -> str:
        return f"{base}/{rel}" if base else rel

    def _resolve_commit(self, ref: GitHubRef) -> str:
        target = urllib.parse.quote(ref.ref or "HEAD", safe="")
        url = f"https://api.github.com/repos/{ref.owner}/{ref.repo}/commits/{target}"
        payload = self._get_json(url)
        sha = str(payload.get("sha") or "") if isinstance(payload, dict) else ""
        if not COMMIT_RE.match(sha.lower()):
            raise AppError(502, "github_resolve_failed", "Could not resolve commit SHA")
        return sha.lower()

    def _fetch_tree(
        self, owner: str, repo: str, commit: str
    ) -> tuple[list[dict[str, Any]], bool]:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{commit}?recursive=1"
        payload = self._get_json(url, limit=MAX_TREE_RESPONSE_BYTES)
        if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
            raise AppError(502, "github_tree_failed", "Could not list repository tree")
        return list(payload["tree"]), bool(payload.get("truncated"))

    @staticmethod
    def _find_candidates(tree: list[dict[str, Any]], path: str) -> list[str]:
        """Directories (relative to repo root) containing a SKILL.md, under path."""

        prefix = f"{path}/" if path else ""
        found: list[str] = []
        for item in tree:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            item_path = str(item.get("path") or "")
            if not item_path.endswith("/SKILL.md") and item_path != "SKILL.md":
                continue
            if path and not (item_path == f"{path}/SKILL.md" or item_path.startswith(prefix)):
                continue
            directory = item_path.rsplit("/", 1)[0] if "/" in item_path else ""
            if path == directory or not path or directory.startswith(prefix) or directory == "":
                found.append(directory)
        # Stable order, exact-path match first.
        found = sorted(set(found), key=lambda d: (d != path, d))
        return found

    def _candidate_stats(self, tree: list[dict[str, Any]], directory: str) -> dict[str, Any]:
        prefix = f"{directory}/" if directory else ""
        file_count = 0
        total = 0
        has_scripts = False
        skipped = 0
        for item in tree:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            item_path = str(item.get("path") or "")
            if directory and not item_path.startswith(prefix):
                continue
            rel = item_path[len(prefix):] if prefix else item_path
            if not rel or "/" in rel and any(seg.startswith(".") for seg in rel.split("/")):
                continue
            if rel.startswith("."):
                continue
            if self._importable(rel):
                file_count += 1
                total += int(item.get("size") or 0)
                if rel.startswith("scripts/"):
                    has_scripts = True
            else:
                skipped += 1
        return {
            "file_count": file_count,
            "total_size_bytes": total,
            "has_scripts": has_scripts,
            "skipped_file_count": skipped,
        }

    @staticmethod
    def _importable(rel_path: str) -> bool:
        lower = rel_path.lower()
        if lower == "skill.md" or lower.endswith("/skill.md"):
            return True
        suffix = "." + lower.rsplit(".", 1)[-1] if "." in lower.rsplit("/", 1)[-1] else ""
        return suffix in TEXT_SUFFIXES

    def _collect_files(
        self, tree: list[dict[str, Any]], directory: str
    ) -> tuple[list[tuple[str, int]], list[str]]:
        prefix = f"{directory}/" if directory else ""
        files: list[tuple[str, int]] = []
        skipped: list[str] = []
        for item in tree:
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            item_path = str(item.get("path") or "")
            if directory and not item_path.startswith(prefix):
                continue
            rel = item_path[len(prefix):] if prefix else item_path
            if not rel or any(seg.startswith(".") for seg in rel.split("/")):
                continue
            try:
                rel = normalize_skill_relative_path(rel)
            except AppError:
                skipped.append(rel)
                continue
            if rel.casefold() == "skill.md":
                rel = "SKILL.md"
            size = int(item.get("size") or 0)
            if not self._importable(rel) or size > MAX_SKILL_FILE_BYTES:
                skipped.append(rel)
                continue
            files.append((rel, size))
            if len(files) >= MAX_SKILL_FILES:
                break
        return files, skipped

    def _write_package_files(
        self, skill: SkillRecord, contents: list[tuple[str, bytes]]
    ) -> None:
        for rel_path, data in contents:
            blob = self.blobs.put_bytes(data, mime_type=guess_mime(rel_path))
            self.db.add(
                SkillPackageFile(
                    workspace_id=self.workspace_id,
                    skill_id=skill.id,
                    relative_path=rel_path,
                    blob_sha256=blob.sha256,
                    size_bytes=len(data),
                    mime_type=guess_mime(rel_path),
                    is_directory=False,
                )
            )
        self.db.flush()

    # -- HTTP ---------------------------------------------------------------

    def _headers(self, url: str, *, accept: str) -> dict[str, str]:
        headers = {"User-Agent": "LearnGraph-SkillGitHub/1.0", "Accept": accept}
        token = (getattr(self.settings, "skill_market_github_token", None) or "").strip()
        host = urllib.parse.urlsplit(url).hostname or ""
        if token and (host == "github.com" or host.endswith((".github.com", ".githubusercontent.com"))):
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get_bytes(self, url: str, *, accept: str, limit: int) -> bytes:
        request = urllib.request.Request(
            url, headers=self._headers(url, accept=accept), method="GET"
        )
        timeout = float(getattr(self.settings, "external_catalog_timeout_seconds", 12.0) or 12.0)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                data = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            code = "github_not_found" if exc.code == 404 else "github_unavailable"
            raise AppError(502, code, f"GitHub returned HTTP {exc.code} for {url.split('?')[0]}") from exc
        except Exception as exc:  # noqa: BLE001 — network failures must not 500
            raise AppError(502, "github_unavailable", str(exc)[:500]) from exc
        if len(data) > limit:
            raise AppError(502, "github_response_too_large", "GitHub response exceeds size limit")
        return data

    def _get_json(self, url: str, *, limit: int = 1024 * 1024) -> Any:
        data = self._get_bytes(url, accept="application/vnd.github+json", limit=limit)
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(502, "github_invalid_response", "GitHub returned invalid JSON") from exc

    def _fetch_raw(self, owner: str, repo: str, commit: str, path: str) -> bytes:
        quoted = "/".join(urllib.parse.quote(seg) for seg in path.split("/"))
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{quoted}"
        return self._get_bytes(url, accept="text/plain, */*", limit=MAX_SKILL_FILE_BYTES + 1)
