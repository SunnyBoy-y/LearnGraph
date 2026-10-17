from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.schemas.search import SearchRequest, SearchResponse
from app.providers.factory import research_policy_domains
from app.providers.ports.search import SearchProviderPort
from app.providers.remote.search import SearchProviderError, SearchProviderTimeout
from app.repositories.audit import AuditRepository
from app.services.billing import BillingService


class SearchService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str,
                 search_provider: SearchProviderPort) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.search_provider = search_provider
        self.audit = AuditRepository(db, workspace_id)
        self.billing = BillingService(db, workspace_id, actor_id)

    def _effective_allowed_domains(self, requested: list[str]) -> list[str]:
        from app.providers.factory import access_allow_all

        workspace_domains = research_policy_domains(self.db, self.workspace_id)
        requested_domains = {
            item.strip().casefold().rstrip(".") for item in requested if item.strip()
        }
        if access_allow_all(self.db, self.workspace_id):
            # No-interception mode: search is not restricted to
            # a workspace source list (provider and network safety still apply).
            return sorted(requested_domains)
        if workspace_domains:
            if requested_domains:
                outside = sorted(requested_domains - workspace_domains)
                if outside:
                    raise AppError(
                        422,
                        "search_domain_not_allowed",
                        "请求包含不在工作区搜索白名单中的域名",
                        {"domains": outside},
                    )
                effective = requested_domains
            else:
                effective = set(workspace_domains)
        else:
            effective = requested_domains
        return sorted(effective)

    def search(self, payload: SearchRequest) -> SearchResponse:
        if getattr(self.search_provider, "available", True) is False:
            raise AppError(
                503,
                "search_provider_unavailable",
                getattr(self.search_provider, "reason", "No usable SearchProvider is configured"),
                {"provider_id": self.search_provider.provider_id},
            )
        allowed_domains = set(self._effective_allowed_domains(payload.allowed_domains))
        try:
            results, _ = self.billing.execute_search(self.search_provider,
                payload.query,
                payload.max_results,
                allowed_domains=allowed_domains or None,
            )
        except SearchProviderTimeout as exc:
            raise AppError(504, "search_provider_timeout", "Search provider timed out") from exc
        except SearchProviderError as exc:
            raise AppError(502, "search_provider_failed", "Search provider failed", {"provider_id": self.search_provider.provider_id}) from exc
        remote = self.search_provider.remote_capability
        self.audit.record(
            actor_id=self.actor_id,
            action="search.remote" if remote else "search.local_demo",
            resource_type="search_query",
            resource_id="not-persisted",
            details={
                "provider_id": self.search_provider.provider_id,
                "remote_capability": remote,
                "result_count": len(results),
                "allowed_domain_count": len(allowed_domains),
            },
        )
        self.db.commit()
        return SearchResponse(
            provider_id=self.search_provider.provider_id,
            remote_capability=remote,
            query=payload.query,
            results=results,
            notice=(
                "结果来自已配置的 SearchProvider；仅返回当前请求允许域名内的 HTTP(S) 来源。"
                if remote
                else "仅检索内置本地演示索引；没有执行互联网搜索。"
            ),
        )

