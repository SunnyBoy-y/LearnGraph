"""Public ClawHub browsing and version-pinned workspace package imports."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.extension_models import SkillRecord
from app.domain.models import utc_now
from app.domain.schemas.extensions import (
    SkillArchiveImportRequest, SkillGitHubInstallRequest, SkillMarketCardView,
    SkillMarketListResponse, SkillMarketInstallRequest,
    SkillMarketDetailResponse, SkillMarketFileView,
)
from app.services.skill_package import MAX_SKILL_PACKAGE_BYTES, MAX_SKILL_FILE_BYTES, parse_skill_md_frontmatter

_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_COOLDOWNS: dict[str, float] = {}
_LOCK = threading.Lock()
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")


class ClawHubSkillMarket:
    def __init__(self, db: Session, workspace_id: str, actor_id: str, settings: Settings) -> None:
        self.db, self.workspace_id, self.actor_id, self.settings = db, workspace_id, actor_id, settings
        self.base = settings.clawhub_api_url.rstrip("/")

    def _get(self, path: str, params: dict | None = None, *, binary: bool = False, refresh: bool = False, plain: bool = False):
        if not self.settings.clawhub_enabled:
            raise AppError(403, "catalog_disabled", "ClawHub 市场未启用")
        url = f"{self.base}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        if not binary and not refresh:
            with _LOCK:
                cached = _CACHE.get(url)
                if cached and time.monotonic() - cached[0] < 60:
                    return cached[1]
        with _LOCK:
            remaining = math.ceil(_COOLDOWNS.get(self.base, 0) - time.monotonic())
        if remaining > 0:
            raise AppError(429, "clawhub_rate_limited", f"ClawHub 请求过于频繁，请 {remaining} 秒后重试",
                           {"retry_after": remaining})
        request = urllib.request.Request(url, headers={
            "User-Agent": "LearnGraph-ClawHub/1.0",
            "Accept": "text/plain, text/markdown, application/json" if plain else "application/zip, application/json" if binary else "application/json",
        })
        maximum = MAX_SKILL_FILE_BYTES if plain else MAX_SKILL_PACKAGE_BYTES if binary else 1024 * 1024
        try:
            with urllib.request.urlopen(request, timeout=self.settings.external_catalog_timeout_seconds) as response:
                data = response.read(maximum + 1)
        except urllib.error.HTTPError as exc:
            message = exc.read(500).decode("utf-8", errors="replace")
            retry = exc.headers.get("Retry-After")
            if exc.code == 429:
                seconds = int(retry) if retry and retry.isdigit() else 60
                with _LOCK:
                    _COOLDOWNS[self.base] = time.monotonic() + seconds
                raise AppError(429, "clawhub_rate_limited", f"ClawHub 请求过于频繁，请 {seconds} 秒后重试",
                               {"retry_after": seconds}) from exc
            raise AppError(exc.code if exc.code in {403, 404, 410} else 502, "clawhub_unavailable",
                           f"ClawHub HTTP {exc.code}: {message}" + (f"（{retry} 秒后重试）" if retry else "")) from exc
        except (OSError, TimeoutError) as exc:
            raise AppError(502, "clawhub_unavailable", f"无法连接 ClawHub：{str(exc)[:200]}") from exc
        if len(data) > maximum:
            raise AppError(502, "clawhub_response_too_large", "ClawHub 响应超过大小限制")
        if binary:
            return data
        try:
            result = data.decode("utf-8") if plain else json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise AppError(502, "clawhub_invalid_response", "ClawHub 返回了无效 JSON") from exc
        if not plain and not isinstance(result, dict):
            raise AppError(502, "clawhub_invalid_response", "ClawHub 返回了无效目录")
        with _LOCK:
            _CACHE[url] = (time.monotonic(), result)
            _CACHE.move_to_end(url)
            while len(_CACHE) > 128:
                _CACHE.popitem(last=False)
        return result

    @staticmethod
    def _owner(raw: dict) -> str:
        owner = raw.get("owner")
        return str(raw.get("ownerHandle") or (owner.get("handle") if isinstance(owner, dict) else owner) or "")

    @staticmethod
    def _identity(market_id: str) -> tuple[str, str]:
        if not market_id.startswith("clawhub:"):
            raise AppError(400, "clawhub_invalid_skill", "无效的 ClawHub Skill 标识")
        parts = market_id.removeprefix("clawhub:").split("/")
        if len(parts) > 2 or not all(_IDENTITY.fullmatch(part) for part in parts):
            raise AppError(400, "clawhub_invalid_skill", "无效的 ClawHub Skill 标识")
        return parts[-1], parts[0] if len(parts) == 2 else ""

    def detail(self, market_id: str, *, version: str | None = None) -> SkillMarketDetailResponse:
        slug, owner = self._identity(market_id)
        params = {"owner": owner} if owner else {}
        metadata = self._get(f"skills/{slug}", params)
        actual_owner = self._owner(metadata)
        if owner and actual_owner != owner:
            raise AppError(409, "clawhub_owner_mismatch", "ClawHub 返回的发布者与所选条目不一致")
        skill = metadata.get("skill") or {}
        latest = metadata.get("latestVersion") or {}
        resolved = version or latest.get("version") or ""
        if resolved in {".", ".."}:
            raise AppError(400, "clawhub_invalid_version", "无效的 ClawHub Skill 版本")
        owner = actual_owner or owner
        moderation = metadata.get("moderation") or {}
        result = SkillMarketDetailResponse(
            market_id=market_id, name=str(skill.get("displayName") or skill.get("name") or slug),
            owner=owner, version=resolved, description=str(skill.get("summary") or ""),
            homepage_url=f"https://clawhub.ai/{owner}/skills/{slug}" if owner else f"https://clawhub.ai/skills/{slug}",
            changelog=str(latest.get("changelog") or "") if resolved == latest.get("version") else "",
            installable=bool(resolved) and not (moderation.get("isMalwareBlocked") or moderation.get("isSuspicious")),
        )
        if not resolved:
            result.skill_md = str(skill.get("description") or "")
            _, result.instructions_markdown = parse_skill_md_frontmatter(result.skill_md)
            result.files_error = "此 Skill 暂无公开版本，无法读取版本文件列表。"
            if not result.skill_md:
                result.instructions_error = "来源未提供完整说明。"
            return result
        encoded = urllib.parse.quote(str(resolved), safe="")
        try:
            payload = self._get(f"skills/{slug}/versions/{encoded}", params)
            version_data = payload.get("version")
            if not isinstance(version_data, dict) or not isinstance(version_data.get("files"), list):
                raise AppError(502, "clawhub_invalid_response", "ClawHub 未返回有效的版本文件列表")
            if version_data.get("version") != resolved:
                raise AppError(502, "clawhub_version_mismatch", "ClawHub 返回的文件版本与所选版本不一致")
            result.files = [SkillMarketFileView(
                path=str(item["path"]), size_bytes=item.get("size") if isinstance(item.get("size"), int) else None,
                sha256=str(item.get("sha256") or ""), content_type=str(item.get("contentType") or ""),
            ) for item in version_data["files"] if isinstance(item, dict) and item.get("path")]
            result.changelog = str(version_data.get("changelog") or "")
        except AppError as exc:
            result.files_error = exc.message
        try:
            text = self._get(f"skills/{slug}/file", {**params, "path": "SKILL.md", "version": resolved}, plain=True)
            if text.lstrip().startswith("{"):
                try:
                    handoff = json.loads(text)
                except ValueError:
                    handoff = None
                if isinstance(handoff, dict) and handoff.get("sourceRef") == "public-github":
                    text = self._read_github_skill_md(handoff)
            skill_file = next((item for item in result.files if item.path == "SKILL.md"), None)
            if skill_file and skill_file.sha256 and hashlib.sha256(text.encode("utf-8")).hexdigest() != skill_file.sha256:
                raise AppError(502, "clawhub_file_mismatch", "SKILL.md 与版本文件校验值不一致，请重试")
            result.skill_md = text
            frontmatter, result.instructions_markdown = parse_skill_md_frontmatter(text.lstrip("\ufeff"))
            if frontmatter.get("description"):
                result.description = str(frontmatter["description"])
        except AppError as exc:
            result.instructions_error = exc.message
        return result

    def _read_github_skill_md(self, handoff: dict) -> str:
        from app.services.skill_github_import import SkillGitHubImportService, parse_github_reference
        from app.services.skill_package import normalize_skill_relative_path

        commit = str(handoff.get("commit") or "")
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise AppError(502, "clawhub_invalid_response", "ClawHub 返回了无效的 GitHub 文件来源")
        ref = parse_github_reference(str(handoff.get("repo") or ""))
        directory = str(handoff.get("path") or "").strip("/")
        path = normalize_skill_relative_path(f"{directory}/SKILL.md" if directory else "SKILL.md")
        data = SkillGitHubImportService(self.db, self.workspace_id, self.actor_id, self.settings)._fetch_raw(ref.owner, ref.repo, commit, path)
        if len(data) > MAX_SKILL_FILE_BYTES:
            raise AppError(502, "clawhub_response_too_large", "SKILL.md 超过大小限制")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(502, "clawhub_invalid_response", "SKILL.md 不是有效 UTF-8 文本") from exc

    def list_cards(
        self, *, refresh: bool = False, query: str = "", page: int = 1,
        page_size: int = 12, cursor: str | None = None,
    ) -> SkillMarketListResponse:
        page = max(1, page)
        page_size = min(48, max(1, page_size))
        query = query.strip()
        params = {"limit": 48 if query else page_size, "nonSuspiciousOnly": "true"}
        params.update({"q": query} if query else {"sort": "downloads"})
        if cursor and not query:
            params["cursor"] = cursor
        payload = self._get("search" if query else "skills", params, refresh=refresh)
        items = payload.get("results" if query else "items")
        if not isinstance(items, list):
            raise AppError(502, "clawhub_invalid_response", "ClawHub 返回了无效目录")
        total = len(items)
        if query:
            items = items[(page - 1) * page_size:page * page_size]
        cards = []
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                continue
            slug, owner = str(raw.get("slug") or ""), self._owner(raw)
            if not _IDENTITY.fullmatch(slug) or (owner and not _IDENTITY.fullmatch(owner)):
                continue
            identity = f"{owner}/{slug}" if owner else slug
            latest = raw.get("latestVersion") or {}
            version = str(raw.get("version") or latest.get("version") or (raw.get("tags") or {}).get("latest") or "")
            homepage = f"https://clawhub.ai/{owner}/skills/{slug}" if owner else f"https://clawhub.ai/skills/{slug}"
            stats = raw.get("stats") or {}
            # Unified search can return version=null while explicitly declaring
            # installability; resolve that version through details on install.
            installable = bool(version) or bool(query and (raw.get("trust") or {}).get("installability") == "installable")
            cards.append(SkillMarketCardView(
                market_id=f"clawhub:{identity}", slug=slug,
                name=str(raw.get("displayName") or slug)[:200], source=f"clawhub/{owner}" if owner else "clawhub",
                description=str(raw.get("summary") or raw.get("description") or "")[:4000],
                install_url=homepage, homepage_url=homepage,
                installs=int(stats.get("downloads") or raw.get("downloads") or 0), source_type="clawhub",
                origin_hash="", fetch_status="available" if installable else "unavailable",
                fetched_at=utc_now(), rank=index, version=version, installable=installable,
            ))
        return SkillMarketListResponse(
            source="clawhub", refreshed_at=utc_now(), cards=cards, page=page, page_size=page_size,
            total=total, total_pages=(total + page_size - 1) // page_size if query else 0,
            query=query, next_cursor=payload.get("nextCursor") if not query else None,
        )

    def install(self, payload: SkillMarketInstallRequest) -> SkillRecord:
        identity = payload.market_id.removeprefix("clawhub:")
        slug, owner = self._identity(payload.market_id)
        params = {"owner": owner} if owner else {}
        detail = self._get(f"skills/{slug}", params, refresh=True)
        if owner and self._owner(detail) != owner:
            raise AppError(409, "clawhub_owner_mismatch", "ClawHub 返回的发布者与所选条目不一致")
        moderation = detail.get("moderation") or {}
        if moderation.get("isMalwareBlocked") or moderation.get("isSuspicious"):
            raise AppError(409, "clawhub_skill_blocked", "ClawHub 已标记此 Skill，无法安装")
        version = payload.version or (detail.get("latestVersion") or {}).get("version")
        if not version:
            raise AppError(409, "clawhub_version_missing", "此 Skill 没有可安装的公开版本")
        params.update({"slug": slug, "version": version})
        data = self._get("download", params, binary=True)
        key = payload.skill_key or re.sub(r"[^a-z0-9._-]+", "-", slug.lower()).strip("-")[:80]
        if len(key) < 2:
            key = "clh-" + hashlib.sha256(identity.encode()).hexdigest()[:12]
        origin_ref = f"clawhub:{identity}@{version}"
        if data.lstrip().startswith(b"{"):
            # Newer registry versions hand GitHub-backed packages off at an exact commit.
            try:
                handoff = json.loads(data)
                repo, commit = handoff["repo"], handoff["commit"]
                if handoff.get("sourceRef") != "public-github" or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                    raise ValueError("invalid handoff")
                from app.services.skill_github_import import SkillGitHubImportService

                service = SkillGitHubImportService(self.db, self.workspace_id, self.actor_id, self.settings)
                skill = service.install(SkillGitHubInstallRequest(
                    reference=repo, commit=commit, path=handoff.get("path") or "", skill_key=key,
                ))
            except (ValueError, KeyError, TypeError) as exc:
                raise AppError(502, "clawhub_invalid_download", "ClawHub 返回了无效的下载来源") from exc
            skill.source = f"clawhub:{identity}"
            skill.version = version
            skill.manifest_json = {**skill.manifest_json, "clawhub_origin": origin_ref}
            self.db.commit()
            self.db.refresh(skill)
            return skill
        from app.services.skill_archive_import import SkillArchiveImportService

        return SkillArchiveImportService(self.db, self.workspace_id, self.actor_id, self.settings).import_archive(
            SkillArchiveImportRequest(filename=f"{slug}-{version}.zip", skill_key=key,
                                      archive_base64=base64.b64encode(data).decode()),
            origin_type="clawhub_market", origin_ref=origin_ref,
            source=f"clawhub:{identity}", version=version,
        )
