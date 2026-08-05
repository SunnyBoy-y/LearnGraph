from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import SourceCitation, SourceLink, SourceRecord, WorkspaceSetting
from app.domain.schemas.sources import FetchSourceRequest
from app.domain.schemas.workflow import DeleteImpact, ImpactItem
from app.providers.ports.fetch import FetchProviderPort
from app.providers.remote.fetch import (
    FetchProviderError,
    FetchProviderTimeout,
    UnsafeFetchURL,
    require_public_http_url,
)
from app.providers.remote.search import normalize_domain
from app.repositories.audit import AuditRepository
from app.repositories.domain import ResearchRepository, SourceRecordRepository


class SourceService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        fetch_provider: FetchProviderPort | None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.fetch_provider = fetch_provider
        self.sources = SourceRecordRepository(db, workspace_id)
        self.research = ResearchRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def list(self) -> list[SourceRecord]:
        return list(self.db.scalars(self.sources.query().order_by(SourceRecord.created_at.desc()).limit(100)).all())

    def get(self, source_id: str) -> SourceRecord:
        return self.sources.require(source_id, "source")

    def delete_impact(self, source_id: str) -> DeleteImpact:
        source = self.get(source_id)
        citations = self.db.scalar(
            select(func.count()).select_from(SourceCitation).where(
                SourceCitation.workspace_id == self.workspace_id,
                SourceCitation.source_id == source.id,
            )
        ) or 0
        links = self.db.scalar(
            select(func.count()).select_from(SourceLink).where(
                SourceLink.workspace_id == self.workspace_id,
                SourceLink.source_id == source.id,
            )
        ) or 0
        title = source.title.strip() or source.final_url
        return DeleteImpact(
            resource_type="source",
            resource_id=source.id,
            title=title,
            confirmation_text=title,
            impacts=[
                ImpactItem(resource_type="source_citation", count=citations, action="delete"),
                ImpactItem(resource_type="source_link", count=links, action="delete"),
                ImpactItem(
                    resource_type="research_job",
                    count=1 if source.research_job_id else 0,
                    action="preserve_history",
                ),
            ],
        )

    def delete_confirmed(self, source_id: str, confirmation: str) -> DeleteImpact:
        source = self.get(source_id)
        impact = self.delete_impact(source_id)
        if confirmation != impact.confirmation_text:
            raise AppError(
                409,
                "confirmation_mismatch",
                "Confirmation text does not match the source title or URL",
            )
        citation_rows = list(
            self.db.scalars(
                select(SourceCitation).where(
                    SourceCitation.workspace_id == self.workspace_id,
                    SourceCitation.source_id == source.id,
                )
            ).all()
        )
        link_rows = list(
            self.db.scalars(
                select(SourceLink).where(
                    SourceLink.workspace_id == self.workspace_id,
                    SourceLink.source_id == source.id,
                )
            ).all()
        )
        for citation in citation_rows:
            self.db.delete(citation)
        for link in link_rows:
            self.db.delete(link)
        self.audit.record(
            actor_id=self.actor_id,
            action="source.delete",
            resource_type="source",
            resource_id=source.id,
            details={
                "impacts": [item.model_dump() for item in impact.impacts],
                "source_url": source.final_url,
                "content_hash": source.content_hash,
            },
        )
        self.db.delete(source)
        self.db.commit()
        return impact

    def fetch(self, payload: FetchSourceRequest) -> SourceRecord:
        if self.fetch_provider is None:
            raise AppError(
                409,
                "fetch_provider_unavailable",
                "No Crawl4AI fetch provider is configured for this workspace",
            )
        policy_setting = self.db.scalar(
            select(WorkspaceSetting).where(
                WorkspaceSetting.workspace_id == self.workspace_id,
                WorkspaceSetting.key == "web_fetch.policy",
            )
        )
        policy = (
            policy_setting.value
            if policy_setting is not None and isinstance(policy_setting.value, dict)
            else {}
        )
        policy_domains = {
            domain
            for value in policy.get("allowed_domains", [])
            if isinstance(value, str) and (domain := normalize_domain(value))
        }
        requested_domains = {
            domain
            for value in payload.authorized_domains
            if (domain := normalize_domain(value))
        }
        allowed_domains = policy_domains or requested_domains
        if policy.get("allow_without_confirmation") is True and not allowed_domains:
            try:
                allowed_domains = {require_public_http_url(payload.url, None)}
            except UnsafeFetchURL as exc:
                raise AppError(422, "fetch_url_blocked", "The source URL is not allowed by the fetch safety policy") from exc
        if not allowed_domains:
            raise AppError(422, "authorized_domain_required", "At least one valid authorized domain is required")
        try:
            authorized_domain = require_public_http_url(payload.url, allowed_domains)
        except UnsafeFetchURL as exc:
            raise AppError(422, "fetch_url_blocked", "The source URL is not allowed by the fetch safety policy") from exc
        if payload.research_job_id:
            self.research.require(payload.research_job_id, "research job")
        try:
            document = self.fetch_provider.fetch(payload.url)
        except FetchProviderTimeout as exc:
            raise AppError(504, "fetch_provider_timeout", "Fetch provider timed out") from exc
        except FetchProviderError as exc:
            raise AppError(502, "fetch_provider_failed", "Fetch provider failed", {"provider_id": self.fetch_provider.provider_id}) from exc
        try:
            require_public_http_url(document.final_url, allowed_domains)
        except UnsafeFetchURL as exc:
            raise AppError(502, "fetch_redirect_blocked", "Fetch provider returned an unsafe or unauthorized final URL") from exc
        content_hash = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        existing = self.db.scalar(
            select(SourceRecord).where(
                SourceRecord.workspace_id == self.workspace_id,
                SourceRecord.content_hash == content_hash,
            ).order_by(SourceRecord.created_at.desc())
        )
        if existing is not None:
            self.audit.record(
                actor_id=self.actor_id,
                action="source.reuse",
                resource_type="source",
                resource_id=existing.id,
                details={"requested_url": payload.url, "content_hash": content_hash},
            )
            self.db.commit()
            return existing
        source = self.sources.add(
            SourceRecord(
                workspace_id=self.workspace_id,
                provider_id=self.fetch_provider.provider_id,
                source_url=payload.url,
                final_url=document.final_url,
                title=document.title,
                content=document.content,
                content_hash=content_hash,
                content_type=document.content_type,
                authorized_domain=authorized_domain,
                cache_status="fresh",
                research_job_id=payload.research_job_id,
                metadata_json=document.metadata,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="source.fetch",
            resource_type="source",
            resource_id=source.id,
            details={
                "provider_id": self.fetch_provider.provider_id,
                "authorized_domain": authorized_domain,
                "content_hash": content_hash,
            },
        )
        self.db.commit()
        self.db.refresh(source)
        return source
