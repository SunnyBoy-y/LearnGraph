"""External skill/MCP catalog aggregation (search-only, federated index).

LearnGraph keeps its own metadata index; external catalogs are used for
discovery and enrichment only.  Install flows still resolve to pinned GitHub
content or manual import so third-party payloads stay reviewable:

- ClawHub   — official documented API at ``clawhub.ai/api/v1`` (anonymous reads).
- skills.sh — documented API, but authenticated via Vercel OIDC; disabled by
  default and treated as best-effort enrichment.
- MCP Registry — frozen ``v0.1`` API at ``registry.modelcontextprotocol.io``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.schemas.extensions import (
    ExternalCatalogSourceView,
    ExternalSkillSearchItem,
    ExternalSkillSearchResponse,
    McpRegistrySearchItem,
    McpRegistrySearchResponse,
)

MAX_CATALOG_RESPONSE_BYTES = 512 * 1024
MAX_CATALOG_RESULTS = 20


class ExternalCatalogService:
    """Read-only proxy over the configured external skill/MCP catalogs."""

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

    # -- catalog inventory --------------------------------------------------

    def catalog_sources(self) -> list[ExternalCatalogSourceView]:
        return [
            ExternalCatalogSourceView(
                id="clawhub",
                label="ClawHub",
                kind="skill",
                enabled=bool(self.settings.clawhub_enabled),
                base_url=self.settings.clawhub_api_url,
                auth_required=False,
                notes="OpenClaw 官方注册中心；公开读取，带安全扫描与信任元数据。",
            ),
            ExternalCatalogSourceView(
                id="skills_sh",
                label="skills.sh",
                kind="skill",
                enabled=bool(self.settings.skills_sh_enabled),
                base_url=self.settings.skills_sh_api_url,
                auth_required=True,
                notes="Vercel Agent Skills 目录；API 需 Vercel OIDC 凭据，默认关闭。",
            ),
            ExternalCatalogSourceView(
                id="mcp_registry",
                label="MCP Registry",
                kind="mcp",
                enabled=bool(self.settings.mcp_registry_enabled),
                base_url=self.settings.mcp_registry_url,
                auth_required=False,
                notes="官方 MCP 服务器注册中心（v0.1 冻结 API，匿名读取）。",
            ),
        ]

    # -- skill catalogs -----------------------------------------------------

    def search_skills(
        self, catalog: str, query: str, *, limit: int = 10
    ) -> ExternalSkillSearchResponse:
        catalog = (catalog or "").strip().lower()
        query = (query or "").strip()
        limit = max(1, min(int(limit or 10), MAX_CATALOG_RESULTS))
        if len(query) < 2:
            raise AppError(400, "catalog_query_too_short", "Search query needs at least 2 characters")
        if catalog == "clawhub":
            return self._search_clawhub(query, limit)
        if catalog == "skills_sh":
            return self._search_skills_sh(query, limit)
        raise AppError(404, "catalog_unknown", f"Unknown skill catalog: {catalog}")

    def _search_clawhub(self, query: str, limit: int) -> ExternalSkillSearchResponse:
        if not self.settings.clawhub_enabled:
            raise AppError(403, "catalog_disabled", "ClawHub integration is disabled")
        base = self.settings.clawhub_api_url.rstrip("/")
        url = f"{base}/search?" + urllib.parse.urlencode({"q": query, "limit": limit})
        payload = self._get_json(url)
        raw_items = payload.get("results") if isinstance(payload, dict) else None
        if raw_items is None and isinstance(payload, dict):
            raw_items = payload.get("items")
        if raw_items is None and isinstance(payload, list):
            raw_items = payload
        items: list[ExternalSkillSearchItem] = []
        for raw in list(raw_items or [])[:limit]:
            if not isinstance(raw, dict):
                continue
            slug = str(raw.get("slug") or raw.get("id") or "").strip()
            if not slug:
                continue
            items.append(
                ExternalSkillSearchItem(
                    catalog="clawhub",
                    external_id=slug,
                    name=str(raw.get("displayName") or raw.get("name") or slug)[:200],
                    description=str(raw.get("description") or raw.get("summary") or "")[:2000],
                    version=str(raw.get("version") or "")[:80],
                    owner=str(raw.get("ownerHandle") or raw.get("owner") or "")[:120],
                    homepage_url=f"https://clawhub.ai/skills/{urllib.parse.quote(slug)}",
                    install_hint=(
                        "外部目录结果：请先在来源页核对内容与扫描状态，"
                        "再通过 GitHub 导入或手动导入安装。"
                    ),
                    trust={
                        key: raw[key]
                        for key in ("score", "downloads", "stars", "trust", "security")
                        if key in raw
                    },
                )
            )
        return ExternalSkillSearchResponse(catalog="clawhub", query=query, items=items)

    def _search_skills_sh(self, query: str, limit: int) -> ExternalSkillSearchResponse:
        if not self.settings.skills_sh_enabled:
            raise AppError(403, "catalog_disabled", "skills.sh integration is disabled")
        base = self.settings.skills_sh_api_url.rstrip("/")
        url = f"{base}/skills/search?" + urllib.parse.urlencode({"q": query, "limit": limit})
        payload = self._get_json(url)
        raw_items = payload.get("skills") if isinstance(payload, dict) else None
        if raw_items is None and isinstance(payload, dict):
            raw_items = payload.get("results") or payload.get("items")
        if raw_items is None and isinstance(payload, list):
            raw_items = payload
        items: list[ExternalSkillSearchItem] = []
        for raw in list(raw_items or [])[:limit]:
            if not isinstance(raw, dict):
                continue
            slug = str(raw.get("slug") or raw.get("id") or raw.get("name") or "").strip()
            source = str(raw.get("source") or "").strip()
            if not slug:
                continue
            items.append(
                ExternalSkillSearchItem(
                    catalog="skills_sh",
                    external_id=f"{source}/{slug}" if source else slug,
                    name=str(raw.get("name") or slug)[:200],
                    description=str(raw.get("description") or "")[:2000],
                    version="",
                    owner=source[:120],
                    homepage_url=str(raw.get("url") or "https://skills.sh/")[:500],
                    install_hint=(
                        "skills.sh 条目通常对应 GitHub 仓库；"
                        "建议以固定 commit 从 GitHub 导入。"
                    ),
                    trust={
                        key: raw[key]
                        for key in ("installs", "sourceType", "isDuplicate")
                        if key in raw
                    },
                )
            )
        return ExternalSkillSearchResponse(catalog="skills_sh", query=query, items=items)

    # -- MCP registry -------------------------------------------------------

    def search_mcp_registry(self, query: str, *, limit: int = 10) -> McpRegistrySearchResponse:
        query = (query or "").strip()
        if len(query) < 2:
            raise AppError(400, "catalog_query_too_short", "Search query needs at least 2 characters")
        return self._query_mcp_registry(query=query, cursor=None, limit=limit)

    def browse_mcp_registry(
        self,
        *,
        query: str = "",
        cursor: str | None = None,
        limit: int = 12,
    ) -> McpRegistrySearchResponse:
        """Browse the official registry as a marketplace (cursor pagination)."""

        query = (query or "").strip()
        if query and len(query) < 2:
            query = ""
        return self._query_mcp_registry(query=query, cursor=cursor, limit=limit)

    def _query_mcp_registry(
        self,
        *,
        query: str,
        cursor: str | None,
        limit: int,
    ) -> McpRegistrySearchResponse:
        if not self.settings.mcp_registry_enabled:
            raise AppError(403, "catalog_disabled", "MCP Registry integration is disabled")
        limit = max(1, min(int(limit or 10), MAX_CATALOG_RESULTS))
        base = self.settings.mcp_registry_url.rstrip("/")
        params: dict[str, Any] = {"limit": limit, "version": "latest"}
        if query:
            params["search"] = query
        if cursor:
            params["cursor"] = str(cursor)[:300]
        url = f"{base}/v0.1/servers?" + urllib.parse.urlencode(params)
        payload = self._get_json(url)
        raw_items = payload.get("servers") if isinstance(payload, dict) else None
        items: list[McpRegistrySearchItem] = []
        for raw in list(raw_items or [])[:limit]:
            item = self._registry_item(raw)
            if item is not None:
                items.append(item)
        next_cursor: str | None = None
        if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict):
            raw_cursor = payload["metadata"].get("nextCursor") or payload["metadata"].get("next_cursor")
            if isinstance(raw_cursor, str) and raw_cursor.strip():
                next_cursor = raw_cursor.strip()[:300]
        return McpRegistrySearchResponse(
            registry_url=self.settings.mcp_registry_url,
            query=query,
            items=items,
            next_cursor=next_cursor,
        )

    @staticmethod
    def _registry_item(raw: Any) -> McpRegistrySearchItem | None:
        if not isinstance(raw, dict):
            return None
        server = raw.get("server") if isinstance(raw.get("server"), dict) else raw
        name = str(server.get("name") or "").strip()
        if not name:
            return None
        endpoint_url: str | None = None
        transport: str | None = None
        for remote in server.get("remotes") or []:
            if not isinstance(remote, dict):
                continue
            remote_type = str(remote.get("type") or "").strip()
            remote_url = str(remote.get("url") or "").strip()
            if not remote_url:
                continue
            if remote_type in {"streamable-http", "streamable_http", "http"}:
                endpoint_url = remote_url
                transport = "streamable_http"
                break
            if endpoint_url is None:
                endpoint_url = remote_url
                transport = remote_type or None
        packages: list[str] = []
        env_hints: list[str] = []
        for package in server.get("packages") or []:
            if not isinstance(package, dict):
                continue
            registry_type = str(package.get("registryType") or package.get("registry_type") or "")
            identifier = str(package.get("identifier") or "")
            if identifier:
                packages.append(f"{registry_type}:{identifier}" if registry_type else identifier)
            for env in package.get("environmentVariables") or []:
                if not isinstance(env, dict):
                    continue
                env_name = str(env.get("name") or "").strip()
                if env_name and env.get("isRequired") and env_name not in env_hints:
                    env_hints.append(env_name)
        repository = server.get("repository")
        repository_url = ""
        if isinstance(repository, dict):
            repository_url = str(repository.get("url") or "")
        elif isinstance(repository, str):
            repository_url = repository
        meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
        official = meta.get("io.modelcontextprotocol.registry/official")
        status = ""
        if isinstance(official, dict):
            status = str(official.get("status") or "")
        if not status:
            status = str(meta.get("status") or server.get("status") or "")
        supported = transport == "streamable_http" and bool(endpoint_url)
        unsupported_reason = ""
        if not supported:
            if endpoint_url and transport not in {None, "streamable_http"}:
                unsupported_reason = f"仅提供 {transport} 远程；当前支持 Streamable HTTP"
            elif packages:
                unsupported_reason = "仅提供本地安装包（npm/pypi 等）；当前仅支持 Streamable HTTP 远程"
            else:
                unsupported_reason = "注册表条目未提供可用的远程端点"
        return McpRegistrySearchItem(
            name=name[:200],
            title=str(server.get("title") or "")[:200],
            description=str(server.get("description") or "")[:2000],
            version=str(server.get("version") or "")[:80],
            status=status[:40],
            repository_url=repository_url[:500],
            website_url=str(server.get("websiteUrl") or "")[:500],
            endpoint_url=endpoint_url,
            transport=transport,
            packages=packages[:8],
            env_hints=env_hints[:8],
            supported=supported,
            unsupported_reason=unsupported_reason,
        )

    # -- HTTP ---------------------------------------------------------------

    def _get_json(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "LearnGraph-ExtensionCatalog/1.0",
                "Accept": "application/json",
            },
            method="GET",
        )
        timeout = float(self.settings.external_catalog_timeout_seconds or 12.0)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                data = response.read(MAX_CATALOG_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise AppError(
                502,
                "catalog_unavailable",
                f"External catalog returned HTTP {exc.code}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 — network failures must not 500
            raise AppError(502, "catalog_unavailable", str(exc)[:500]) from exc
        if len(data) > MAX_CATALOG_RESPONSE_BYTES:
            raise AppError(502, "catalog_response_too_large", "External catalog response too large")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(502, "catalog_invalid_response", "External catalog returned invalid JSON") from exc
