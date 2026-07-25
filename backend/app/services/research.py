from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.core.tasks import task_queue
from app.domain.models import ResearchJob, ResearchJobEvent, utc_now
from app.domain.schemas.research import (
    ResearchApprovalRequest,
    ResearchJobView,
    ResearchPlanView,
    ResearchRequest,
    SearchRequest,
    SearchResponse,
)
from app.providers.factory import deep_research_provider_for_workspace, search_provider_for_workspace
from app.providers.ports.research import DeepResearchProviderPort
from app.providers.ports.search import SearchProviderPort
from app.providers.remote.research import (
    DeepResearchProviderError,
    DeepResearchProviderTimeout,
)
from app.providers.remote.search import (
    SearchProviderError,
    SearchProviderTimeout,
)
from app.repositories.audit import AuditRepository
from app.repositories.domain import ResearchEventRepository, ResearchRepository
from app.services.billing import BillingQuote, BillingService, DEFAULT_USD_CNY_RATE


TERMINAL_RESEARCH_STATUSES = {
    "completed",
    "completed_local_demo",
    "completed_source_collection",
    "failed",
    "cancelled",
    "needs_review",
    "over_budget",
    "rejected",
}
ACTIVE_RESEARCH_STATUSES = {"awaiting_approval", "queued", "running", "cancel_requested"}


class ResearchService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        search_provider: SearchProviderPort,
        deep_research_provider: DeepResearchProviderPort | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.search_provider = search_provider
        self.deep_research_provider = deep_research_provider
        self.settings = settings or get_settings()
        self.research = ResearchRepository(db, workspace_id)
        self.events = ResearchEventRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.billing = BillingService(db, workspace_id, actor_id)

    def search(self, payload: SearchRequest) -> SearchResponse:
        if getattr(self.search_provider, "available", True) is False:
            raise AppError(
                503,
                "search_provider_unavailable",
                getattr(self.search_provider, "reason", "No usable SearchProvider is configured"),
                {"provider_id": self.search_provider.provider_id},
            )
        allowed_domains = {item.strip().casefold() for item in payload.allowed_domains if item.strip()}
        try:
            results = self.search_provider.search(
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

    def plan_research(self, payload: ResearchRequest) -> ResearchPlanView:
        provider = self.deep_research_provider
        if provider is None:
            if getattr(self.search_provider, "available", True) is False:
                raise AppError(
                    503,
                    "research_provider_unavailable",
                    "Neither DeepResearchProvider nor a real SearchProvider is configured",
                )
            return ResearchPlanView(
                provider_id="local_search_composer",
                provider_capabilities={"background": False, "citations": False, "remote": False},
                question=payload.question,
                budget_cny=payload.budget_cny,
                estimated_cost_cny=0,
                requires_approval=False,
            )
        # A fully exhausted hard policy blocks before even consulting a remote
        # research provider for capabilities or an estimate.
        self.billing.preflight_research_call(
            provider_id=provider.provider_id,
            estimated_cost_cny=0,
        )
        try:
            capabilities = provider.capabilities()
            estimate = provider.estimate(question=payload.question, budget_cny=payload.budget_cny)
        except DeepResearchProviderTimeout as exc:
            raise AppError(504, "research_provider_timeout", "Research provider estimate timed out") from exc
        except DeepResearchProviderError as exc:
            raise AppError(502, "research_provider_failed", "Research provider estimate failed", {"provider_id": provider.provider_id}) from exc
        return ResearchPlanView(
            provider_id=provider.provider_id,
            provider_capabilities=capabilities,
            question=payload.question,
            budget_cny=payload.budget_cny,
            estimated_cost_cny=max(0.0, min(float(estimate), payload.budget_cny)),
            requires_approval=True,
        )

    def create_research(self, payload: ResearchRequest) -> ResearchJob:
        if self.deep_research_provider is None:
            if getattr(self.search_provider, "available", True) is False:
                raise AppError(
                    503,
                    "research_provider_unavailable",
                    "Neither DeepResearchProvider nor a real SearchProvider is configured",
                )
            return self._create_search_composed_research(payload)

        plan = self.plan_research(payload)
        job = self.research.add(
            ResearchJob(
                workspace_id=self.workspace_id,
                question=payload.question,
                status="awaiting_approval",
                provider_id=plan.provider_id,
                budget_cny=payload.budget_cny,
                estimated_cost_cny=plan.estimated_cost_cny,
                approval_status="pending",
                source_scope=list(dict.fromkeys(payload.source_scope)),
                allowed_domains=list(dict.fromkeys(payload.allowed_domains)),
                evidence_pack={
                    "publishable": False,
                    "remote_research_performed": False,
                    "plan": {
                        "provider_id": plan.provider_id,
                        "estimated_cost_cny": plan.estimated_cost_cny,
                        "source_scope": list(dict.fromkeys(payload.source_scope)),
                        "allowed_domains": list(dict.fromkeys(payload.allowed_domains)),
                    },
                },
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="research.plan_created",
            resource_type="research_job",
            resource_id=job.id,
            details={"provider_id": plan.provider_id, "budget_cny": payload.budget_cny},
        )
        self._record_event(
            job,
            "research.plan_created",
            {"provider_id": plan.provider_id, "budget_cny": payload.budget_cny},
        )
        self.db.commit()
        self.db.refresh(job)
        if payload.approved:
            return self._start_remote(job)
        return job

    def approve_research(self, job_id: str, payload: ResearchApprovalRequest) -> ResearchJob:
        job = self.research.require(job_id, "research job")
        if job.status != "awaiting_approval":
            raise AppError(409, "research_not_awaiting_approval", "Research job is not awaiting approval")
        if not payload.approved:
            job.status = "rejected"
            job.approval_status = "rejected"
            self.audit.record(
                actor_id=self.actor_id,
                action="research.approval_rejected",
                resource_type="research_job",
                resource_id=job.id,
            )
            self._record_event(job, "research.approval_rejected", {})
            self.db.commit()
            self.db.refresh(job)
            return job
        return self._start_remote(job)

    def get_research(self, job_id: str) -> ResearchJob:
        return self.research.require(job_id, "research job")

    def list_events(self, job_id: str) -> list[ResearchJobEvent]:
        self.research.require(job_id, "research job")
        return list(
            self.db.scalars(
                self.events.query()
                .where(ResearchJobEvent.research_job_id == job_id)
                .order_by(ResearchJobEvent.sequence)
            ).all()
        )

    def cancel_research(self, job_id: str) -> ResearchJob:
        job = self.research.require(job_id, "research job")
        if job.status in TERMINAL_RESEARCH_STATUSES:
            return job
        if job.status == "awaiting_approval":
            job.status = "cancelled"
            job.approval_status = "cancelled"
        else:
            provider = self._provider_for_job(job)
            if not job.provider_task_id:
                job.status = "cancelled"
            else:
                try:
                    provider.cancel_task(job.provider_task_id)
                except DeepResearchProviderTimeout as exc:
                    raise AppError(504, "research_provider_timeout", "Research provider cancellation timed out") from exc
                except DeepResearchProviderError as exc:
                    raise AppError(502, "research_provider_failed", "Research provider cancellation failed") from exc
                job.status = "cancel_requested"
        self.audit.record(
            actor_id=self.actor_id,
            action="research.cancel_requested",
            resource_type="research_job",
            resource_id=job.id,
        )
        self._record_event(job, "research.cancel_requested", {"status": job.status})
        self.db.commit()
        self.db.refresh(job)
        if job.status == "cancel_requested":
            self._enqueue_poll(job)
        return job

    def refresh_research(self, job_id: str) -> ResearchJob:
        job = self.research.require(job_id, "research job")
        if job.status in TERMINAL_RESEARCH_STATUSES or job.status == "awaiting_approval":
            return job
        provider = self._provider_for_job(job)
        if not job.provider_task_id:
            job.status = "failed"
            job.error_message = "Research job has no provider task id"
            self.db.commit()
            return job
        try:
            result = provider.get_task(job.provider_task_id)
        except DeepResearchProviderTimeout as exc:
            job.status = "running"
            job.error_message = "Last research status poll timed out"
            self.db.commit()
            return job
        except DeepResearchProviderError as exc:
            job.status = "failed"
            job.error_message = "Research provider status poll failed"
            self._record_terminal_usage(job)
            self.audit.record(
                actor_id=self.actor_id,
                action="research.failed",
                resource_type="research_job",
                resource_id=job.id,
                outcome="failed",
            )
            self.db.commit()
            return job
        self._apply_provider_state(job, result)
        self.db.commit()
        self.db.refresh(job)
        return job

    def list_research(self) -> list[ResearchJob]:
        return list(
            self.db.scalars(
                self.research.query().order_by(ResearchJob.created_at.desc()).limit(100)
            ).all()
        )

    def _start_remote(self, job: ResearchJob) -> ResearchJob:
        if job.budget_cny <= 0:
            raise AppError(
                409,
                "research_budget_required",
                "A positive budget is required before a billable deep-research task can start",
            )
        provider = self._provider_for_job(job)
        quote = self.billing.preflight_research_call(
            provider_id=provider.provider_id,
            estimated_cost_cny=job.estimated_cost_cny,
        )
        job.billing_snapshot = quote.snapshot()
        # Persist the exact price/FX decision before a billable external task
        # exists, so a crash cannot detach the task from its billing snapshot.
        self.db.commit()
        try:
            task_id = provider.create_task(
                question=job.question,
                budget_cny=job.budget_cny,
                source_scope=list(job.source_scope or []),
                allowed_domains=list(job.allowed_domains or []),
            )
        except DeepResearchProviderTimeout as exc:
            raise AppError(504, "research_provider_timeout", "Research provider task creation timed out") from exc
        except DeepResearchProviderError as exc:
            raise AppError(502, "research_provider_failed", "Research provider task creation failed", {"provider_id": job.provider_id}) from exc
        job.provider_task_id = task_id
        job.status = "queued"
        job.approval_status = "approved"
        self.audit.record(
            actor_id=self.actor_id,
            action="research.queued",
            resource_type="research_job",
            resource_id=job.id,
            details={"provider_id": job.provider_id, "provider_task_id": task_id},
        )
        self._record_event(job, "research.queued", {"provider_task_id": task_id})
        self.db.commit()
        self.db.refresh(job)
        self._enqueue_poll(job)
        return job

    def _create_search_composed_research(self, payload: ResearchRequest) -> ResearchJob:
        try:
            results = self.search_provider.search(
                payload.question,
                5,
                allowed_domains={item.strip().casefold() for item in payload.allowed_domains if item.strip()} or None,
            )
        except SearchProviderTimeout as exc:
            raise AppError(504, "search_provider_timeout", "Search provider timed out") from exc
        except SearchProviderError as exc:
            raise AppError(502, "search_provider_failed", "Search provider failed") from exc
        remote_search = self.search_provider.remote_capability
        job = self.research.add(
            ResearchJob(
                workspace_id=self.workspace_id,
                question=payload.question,
                status="completed_source_collection" if remote_search else "completed_local_demo",
                provider_id=self.search_provider.provider_id,
                budget_cny=payload.budget_cny,
                approval_status="not_required",
                source_scope=list(dict.fromkeys(payload.source_scope)),
                allowed_domains=list(dict.fromkeys(payload.allowed_domains)),
                evidence_pack={
                    "claims": [],
                    "sources": [item.model_dump(mode="json") for item in results],
                    "candidate_concepts": [],
                    "candidate_relations": [],
                    "learning_resources": [],
                    "coverage_gaps": [
                        "DeepResearchProvider 未配置；该结果仅为可审核的来源收集，不能直接发布图谱或路线。"
                    ],
                    "publishable": False,
                    "remote_research_performed": False,
                    "remote_search_performed": remote_search,
                },
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="research.source_collection" if remote_search else "research.local_demo",
            resource_type="research_job",
            resource_id=job.id,
            details={"budget_cny": payload.budget_cny, "billable_call": False},
        )
        self._record_event(
            job,
            "research.completed_source_collection" if remote_search else "research.completed_local_demo",
            {"provider_id": self.search_provider.provider_id, "source_count": len(results)},
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def _provider_for_job(self, job: ResearchJob) -> DeepResearchProviderPort:
        provider = self.deep_research_provider
        if provider is None or provider.provider_id != job.provider_id:
            raise AppError(
                409,
                "research_provider_unavailable",
                "The provider selected for this research job is no longer available",
            )
        return provider

    def _apply_provider_state(self, job: ResearchJob, result: dict[str, Any]) -> None:
        previous_status = job.status
        raw_status = str(result.get("status") or "").strip().casefold()
        status_map = {
            "pending": "queued",
            "queued": "queued",
            "running": "running",
            "in_progress": "running",
            "cancel_requested": "cancel_requested",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "failed": "failed",
            "error": "failed",
            "timeout": "failed",
            "completed": "completed",
            "succeeded": "completed",
        }
        mapped = status_map.get(raw_status)
        if mapped is None:
            job.status = "needs_review"
            job.error_message = "Research provider returned an unknown task state"
            job.evidence_pack = {
                **dict(job.evidence_pack or {}),
                "provider_raw_status": raw_status,
                "publishable": False,
            }
        else:
            job.status = mapped
            if mapped == "completed":
                pack = self._normalize_evidence_pack(job, result)
                job.evidence_pack = pack
                if pack.get("normalization_error"):
                    job.status = "needs_review"
                    job.error_message = "Research report could not be normalized into an evidence pack"
            elif mapped in {"failed", "cancelled"}:
                job.error_message = str(result.get("error") or "")[:2_000] or None
                job.evidence_pack = {
                    **dict(job.evidence_pack or {}),
                    "publishable": False,
                    "partial_sources": self._json_list(result.get("sources")),
                }
        actual = self._number(result.get("actual_cost_cny", result.get("cost_cny", 0)))
        job.actual_cost_cny = max(job.actual_cost_cny, actual)
        if job.actual_cost_cny > job.budget_cny:
            job.status = "over_budget"
            job.error_message = "Provider-reported cost exceeded the approved budget"
            job.evidence_pack = {**dict(job.evidence_pack or {}), "publishable": False, "partial": True}
        if job.status in TERMINAL_RESEARCH_STATUSES:
            self._record_terminal_usage(job)
            self.audit.record(
                actor_id=self.actor_id,
                action=f"research.{job.status}",
                resource_type="research_job",
                resource_id=job.id,
                outcome="success" if job.status == "completed" else "failed",
            )
        if job.status != previous_status:
            self._record_event(
                job,
                f"research.{job.status}",
                {"provider_task_id": job.provider_task_id, "actual_cost_cny": job.actual_cost_cny},
            )

    def _normalize_evidence_pack(self, job: ResearchJob, result: dict[str, Any]) -> dict[str, Any]:
        raw_pack = result.get("evidence_pack")
        if not isinstance(raw_pack, dict):
            return {
                "publishable": False,
                "remote_research_performed": True,
                "normalization_error": "Provider completed without an evidence_pack object",
                "provider_raw_artifact_ref": result.get("artifact_ref"),
            }
        sources = self._json_list(raw_pack.get("sources"))
        claims = self._json_list(raw_pack.get("claims"))
        return {
            "claims": claims,
            "sources": sources,
            "candidate_concepts": self._json_list(raw_pack.get("candidate_concepts")),
            "candidate_relations": self._json_list(raw_pack.get("candidate_relations")),
            "learning_resources": self._json_list(raw_pack.get("learning_resources")),
            "coverage_gaps": self._json_list(raw_pack.get("coverage_gaps")),
            "conflicts": self._json_list(raw_pack.get("conflicts")),
            "provider_task_id": job.provider_task_id,
            "provider_raw_artifact_ref": raw_pack.get("provider_raw_artifact_ref") or result.get("artifact_ref"),
            "model_or_agent_version": raw_pack.get("model_or_agent_version") or result.get("model_or_agent_version"),
            "completed_at": result.get("completed_at"),
            "cost_summary": {"cny": self._number(result.get("actual_cost_cny", result.get("cost_cny", 0)))},
            "publishable": False,
            "remote_research_performed": True,
        }

    def _record_terminal_usage(self, job: ResearchJob) -> None:
        pack = dict(job.evidence_pack or {})
        if pack.get("usage_recorded"):
            return
        snapshot = dict(job.billing_snapshot or {})
        quote = (
            BillingQuote.from_snapshot(snapshot)
            if snapshot
            else BillingQuote(
                provider_id=job.provider_id,
                model_id="deep-research",
                feature="deep_research",
                remote_capability=True,
                price_version_id=None,
                exchange_rate_version_id=None,
                input_usd_per_million=0,
                cached_input_usd_per_million=0,
                price_multiplier=1,
                output_usd_per_million=0,
                fixed_usd_per_call=0,
                pricing_currency="USD",
                input_cny_per_million=0,
                cached_input_cny_per_million=0,
                output_cny_per_million=0,
                fixed_cny_per_call=0,
                usd_cny_rate=float(DEFAULT_USD_CNY_RATE),
                projected_cost_usd=0,
                projected_cost_cny=0,
                quoted_at=utc_now().isoformat(),
            )
        )
        self.billing.record_usage(
            quote,
            input_tokens=0,
            output_tokens=0,
            attempt=1,
            provider_reported_cost_cny=job.actual_cost_cny,
        )
        job.evidence_pack = {**pack, "usage_recorded": True}

    def _record_event(self, job: ResearchJob, event_type: str, payload: dict[str, Any]) -> ResearchJobEvent:
        sequence = (
            self.db.scalar(
                select(func.max(ResearchJobEvent.sequence)).where(
                    ResearchJobEvent.workspace_id == self.workspace_id,
                    ResearchJobEvent.research_job_id == job.id,
                )
            )
            or 0
        ) + 1
        return self.events.add(
            ResearchJobEvent(
                workspace_id=self.workspace_id,
                research_job_id=job.id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
            )
        )

    def _enqueue_poll(self, job: ResearchJob) -> None:
        task_queue.submit(
            _poll_research_job,
            job.id,
            self.workspace_id,
            self.actor_id,
            self.settings.research_poll_seconds,
            self.settings.research_max_polls,
        )

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    @staticmethod
    def _number(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if number >= 0 else 0.0


def _poll_research_job(
    job_id: str,
    workspace_id: str,
    actor_id: str,
    poll_seconds: float,
    max_polls: int,
) -> None:
    """Poll a remote task with fresh sessions so request sessions never leak to workers."""

    for _ in range(max(1, max_polls)):
        with SessionLocal() as db:
            settings = get_settings()
            service = ResearchService(
                db,
                workspace_id,
                actor_id,
                search_provider_for_workspace(db, workspace_id, settings),
                deep_research_provider_for_workspace(db, workspace_id, settings),
                settings,
            )
            try:
                job = service.refresh_research(job_id)
            except AppError:
                return
            if job.status in TERMINAL_RESEARCH_STATUSES:
                return
        time.sleep(max(0.01, min(poll_seconds, 5.0)))
