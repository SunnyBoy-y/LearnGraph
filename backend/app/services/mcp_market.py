"""Live, read-only MCP marketplace adapters. Never connects to listed endpoints."""

from __future__ import annotations

import urllib.parse
import hashlib
import json
from typing import Any

from app.core.errors import AppError
from app.domain.schemas.extensions import (
    McpRegistryDetailResponse, McpRegistrySearchItem, McpRegistrySearchResponse,
)
from app.services.skill_catalog_sources import ExternalCatalogService


def translation_source(detail: McpRegistryDetailResponse) -> dict[str, str]:
    texts = {"description": detail.item.description}
    for group, rows in (("configuration", detail.configuration), ("tools", detail.tools)):
        for index, row in enumerate(rows):
            texts[f"{group}:{index}:{row.get('name') or ''}"] = str(row.get("description") or "")
    return texts


def translation_source_hash(detail: McpRegistryDetailResponse) -> str:
    return hashlib.sha256(json.dumps(translation_source(detail), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def public_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    return value if parts.scheme in {"http", "https"} and parts.hostname and not parts.username else ""


class McpMarketService(ExternalCatalogService):
    def browse(
        self, *, catalog: str = "official", query: str = "",
        cursor: str | None = None, limit: int = 12,
    ) -> McpRegistrySearchResponse:
        self._check_catalog(catalog)
        limit = max(1, min(limit, 20))
        if catalog == "official":
            result = self.browse_mcp_registry(query=query, cursor=cursor, limit=limit)
            for item in result.items:
                self._normalize_official(item)
                item.catalog_url = self.settings.mcp_registry_url.rstrip("/") + "/?q=" + urllib.parse.quote(item.name, safe="")
            return result
        base = self._smithery_base()
        try:
            page = int(cursor or "1")
            if not 1 <= page <= 10_000:
                raise ValueError
        except ValueError as exc:
            raise AppError(400, "catalog_invalid_cursor", "无效的市场分页游标") from exc
        params = {"page": page, "pageSize": limit}
        if query.strip():
            params["q"] = query.strip()
        payload = self._get_json(base + "/servers?" + urllib.parse.urlencode(params))
        if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
            raise AppError(502, "catalog_invalid_response", "Smithery 返回了无效目录")
        pagination = payload.get("pagination") or {}
        items = [self._smithery_item(raw) for raw in payload["servers"] if isinstance(raw, dict) and raw.get("qualifiedName")]
        return McpRegistrySearchResponse(
            registry_url=base, query=query, items=items,
            next_cursor=str(page + 1) if page < int(pagination.get("totalPages") or page) else None,
        )

    def detail(self, name: str, *, catalog="official", version="latest") -> McpRegistryDetailResponse:
        result = self._detail(name, catalog=catalog, version=version)
        result.translation_source_hash = translation_source_hash(result)
        return result

    def _detail(self, name: str, *, catalog="official", version="latest") -> McpRegistryDetailResponse:
        self._check_catalog(catalog)
        if not name.strip() or any(part in {".", "..", ""} for part in name.split("/")):
            raise AppError(400, "catalog_invalid_name", "无效的 MCP 服务器标识")
        if version in {".", ".."}:
            raise AppError(400, "catalog_invalid_version", "无效的 MCP 版本")
        encoded = urllib.parse.quote(name, safe="")
        if catalog == "smithery":
            base = self._smithery_base()
            payload = self._get_json(f"{base}/servers/{encoded}")
            if not isinstance(payload, dict) or payload.get("qualifiedName") != name:
                raise AppError(502, "catalog_invalid_response", "Smithery 返回了不匹配的详情")
            item = self._smithery_item(payload, detail=True)
            connections = [c for c in payload.get("connections") or [] if isinstance(c, dict)]
            configuration = []
            for connection in connections:
                schema = connection.get("configSchema") or {}
                for key, field in (schema.get("properties") or {}).items():
                    configuration.append({"name": key, "description": field.get("description", ""),
                                          "isRequired": key in (schema.get("required") or []), "location": "Smithery 配置"})
            return McpRegistryDetailResponse(
                item=item, registry_url=base, remotes=connections,
                tools=[t for t in payload.get("tools") or [] if isinstance(t, dict)],
                configuration=configuration, raw=payload,
            )
        if not self.settings.mcp_registry_enabled:
            raise AppError(403, "catalog_disabled", "MCP Registry integration is disabled")
        base = self.settings.mcp_registry_url.rstrip("/")
        payload = self._get_json(f"{base}/v0.1/servers/{encoded}/versions/{urllib.parse.quote(version, safe='')}")
        item = self._registry_item(payload)
        if item is None or item.name != name:
            raise AppError(502, "catalog_invalid_response", "MCP Registry 返回了不匹配的详情")
        self._normalize_official(item)
        item.catalog_url = base + "/?q=" + encoded
        server = payload.get("server") or payload
        metadata = (payload.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
        remotes = [r for r in server.get("remotes") or [] if isinstance(r, dict)]
        packages = [p for p in server.get("packages") or [] if isinstance(p, dict)]
        configuration = []
        for remote in remotes:
            configuration.extend({**h, "location": "请求头"} for h in remote.get("headers") or [] if isinstance(h, dict))
        for package in packages:
            configuration.extend({**e, "location": "环境变量"} for e in package.get("environmentVariables") or [] if isinstance(e, dict))
        return McpRegistryDetailResponse(
            item=item, registry_url=base, remotes=remotes, package_details=packages,
            configuration=configuration, published_at=metadata.get("publishedAt") or "",
            updated_at=metadata.get("updatedAt") or "", raw=payload,
        )

    def _smithery_base(self) -> str:
        if not self.settings.mcp_smithery_enabled:
            raise AppError(403, "catalog_disabled", "Smithery 市场未启用")
        return self.settings.mcp_smithery_url.rstrip("/")

    @staticmethod
    def _check_catalog(catalog: str) -> None:
        if catalog not in {"official", "smithery"}:
            raise AppError(404, "catalog_unknown", "未知的 MCP 市场")

    @staticmethod
    def _normalize_official(item: McpRegistrySearchItem) -> None:
        item.website_url = public_url(item.website_url)
        item.repository_url = public_url(item.repository_url)
        if item.endpoint_url and (not public_url(item.endpoint_url) or "{" in item.endpoint_url):
            item.supported = False
            item.unsupported_reason = "远程地址需要手动配置，请检查 URL 和其中的占位符"

    @staticmethod
    def _smithery_item(raw: dict, *, detail=False) -> McpRegistrySearchItem:
        name = str(raw.get("qualifiedName") or "")
        endpoint = None
        required = []
        for connection in raw.get("connections") or []:
            if not isinstance(connection, dict):
                continue
            if connection.get("type") in {"http", "streamable-http", "streamable_http"}:
                candidate = public_url(connection.get("deploymentUrl") or connection.get("url"))
                if candidate and endpoint is None:
                    endpoint = candidate
                    required = list((connection.get("configSchema") or {}).get("required") or [])
        repository = raw.get("repository") or {}
        repository_url = public_url(repository.get("url") if isinstance(repository, dict) else repository)
        supported = bool(endpoint) and not required
        return McpRegistrySearchItem(
            name=name, title=str(raw.get("displayName") or name), description=str(raw.get("description") or ""),
            version=str(raw.get("version") or ""), status="inactive" if raw.get("inactive") else "",
            repository_url=repository_url, website_url=public_url(raw.get("homepage")),
            endpoint_url=endpoint, transport="streamable_http" if endpoint else None,
            env_hints=required, supported=supported, catalog="smithery",
            catalog_url="https://smithery.ai/servers/" + urllib.parse.quote(name, safe="/"),
            unsupported_reason=("需先在 Smithery 完成配置：" + "、".join(required)) if required else
                ("查看详情以确认连接方式" if not detail else "此条目未提供可直接注册的 HTTP 连接") if not endpoint else "",
        )
