from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.errors import AppError
from app.domain.models import ProviderConfig, UsageEvent
from app.domain.schemas.research import ResearchApprovalRequest, ResearchRequest
from app.providers.remote.research import DeepResearchProviderTimeout
from app.services.research import ResearchService


class NativeResearchProvider:
    remote_capability = True
    def __init__(self, provider_id, currency):
        self.provider_id, self.currency, self.created = provider_id, currency, 0
    def capabilities(self):
        return {"billing_currency": self.currency}
    def estimate(self, *, question, budget_amount, currency):
        assert currency == self.currency
        return budget_amount
    def create_task(self, *, question, budget_amount, currency, source_scope, allowed_domains):
        assert currency == self.currency and budget_amount == "1.25"
        self.created += 1
        return "native-task"
    def get_task(self, task_id):
        return {"status": "completed", "currency": self.currency, "actual_cost_amount": "0.01234567890123456789",
            "evidence_pack": {"sources": []}}


@pytest.mark.parametrize("currency", ["USD", "CNY"])
def test_research_approval_quote_and_actual_amount_are_native(client, register_user, currency, monkeypatch):
    _, workspace, *_ = register_user("native-research")
    provider_id = str(uuid4())
    with SessionLocal() as db:
        db.add(ProviderConfig(id=provider_id, workspace_id=workspace, display_name="research", provider_type="deep_research",
            capabilities={"billing_currency": currency}, enabled=True, remote_capability=True))
        db.commit()
        provider = NativeResearchProvider(provider_id, currency)
        service = ResearchService(db, workspace, "owner", None, provider)
        monkeypatch.setattr(service, "_enqueue_poll", lambda job: None)
        monkeypatch.setattr(service, "_provider_for_job", lambda job: provider)
        job = service.create_research(ResearchRequest(question="native billing question", currency=currency, budget_amount="1.25"))
        assert not db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == workspace)).all()
        with pytest.raises(AppError) as stale:
            service.approve_research(job.id, ResearchApprovalRequest(approved=True))
        assert stale.value.code == "billing_quote_expired" and provider.created == 0
        db.rollback()
        quote_id = job.billing_snapshot["quote_id"]
        service.approve_research(job.id, ResearchApprovalRequest(approved=True, quote_id=quote_id))
        assert provider.created == 1 and job.status == "queued"
        with pytest.raises(AppError):
            service.approve_research(job.id, ResearchApprovalRequest(approved=True, quote_id=quote_id))
        db.rollback()
        service.refresh_research(job.id)
        service.refresh_research(job.id)
        events = db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == workspace)).all()
        assert len(events) == 1 and events[0].currency == currency
        assert events[0].cost_amount == "0.01234567890123456789"
        assert job.actual_cost_amount == events[0].cost_amount
        assert job.evidence_pack["cost_summary"] == {"currency": currency, "cost_amount": events[0].cost_amount}


def test_research_changed_quote_and_creation_timeout_do_not_look_free(client, register_user, monkeypatch):
    _, workspace, *_ = register_user("native-research")
    provider_id = str(uuid4())
    with SessionLocal() as db:
        config = ProviderConfig(id=provider_id, workspace_id=workspace, display_name="research", provider_type="deep_research",
            capabilities={"billing_currency": "USD"}, enabled=True, remote_capability=True)
        db.add(config); db.commit()
        provider = NativeResearchProvider(provider_id, "USD")
        service = ResearchService(db, workspace, "owner", None, provider)
        monkeypatch.setattr(service, "_enqueue_poll", lambda job: None)
        monkeypatch.setattr(service, "_provider_for_job", lambda job: provider)
        request = ResearchRequest(question="native billing question", currency="USD", budget_amount="1.25")
        job = service.create_research(request)
        config.capabilities = {"billing_currency": "CNY"}; db.commit()
        with pytest.raises(AppError) as stale:
            service.approve_research(job.id, ResearchApprovalRequest(approved=True, quote_id=job.billing_snapshot["quote_id"]))
        assert stale.value.code == "billing_quote_expired" and provider.created == 0
        db.rollback(); config.capabilities = {"billing_currency": "USD"}; db.commit()
        def timed_out(**kwargs):
            raise DeepResearchProviderTimeout("unknown acceptance")
        monkeypatch.setattr(provider, "create_task", timed_out)
        service.billing.create_budget_policy(name="USD cap", currency="USD", hard_limit="2")
        with pytest.raises(AppError) as timeout:
            service.approve_research(job.id, ResearchApprovalRequest(approved=True, quote_id=job.billing_snapshot["quote_id"]))
        assert timeout.value.status_code == 504
        event = db.scalar(select(UsageEvent).where(UsageEvent.workspace_id == workspace))
        assert event.cost_amount is None and event.record_stage == "settled" and event.reserved_amount == "1.25"
        assert service.billing.budget_statuses(currency="USD")[0]["unknown_events"] == 1
