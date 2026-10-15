from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.errors import AppError
from app.domain.models import BudgetPolicy, ProviderConfig, UsageEvent
from app.services.billing import BillingService
from app.services.management import UsageService


def setup_native(register_user, currency="USD"):
    token, workspace, *_ = register_user("native")
    provider_id = str(uuid4())
    with SessionLocal() as db:
        db.add(ProviderConfig(id=provider_id, workspace_id=workspace, display_name="native-test",
            provider_type="openai_compatible", base_url="https://unknown.example", capabilities={"billing_currency": currency},
            remote_capability=True, enabled=True))
        db.commit()
        billing = BillingService(db, workspace, "test")
        billing.create_price(provider_id=provider_id, model_id="exact", currency=currency,
            input_per_million="0.1", output_per_million="0.2", cached_input_per_million="0", cache_write_per_million="0.3")
    return token, workspace, provider_id


def quote(billing, provider, input_tokens=1000000):
    return billing.preflight_model_call(provider_id=provider, model_id="exact", feature="chat",
        estimated_input_tokens=input_tokens, estimated_output_tokens=0, remote_capability=True)


def test_native_exact_cache_and_archive_do_not_reset_spend(client, register_user, auth_headers):
    token, workspace, provider = setup_native(register_user, "CNY")
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        decision = quote(billing, provider, 3)
        event = billing.record_usage(decision, input_tokens=3, output_tokens=1, cached_input_tokens=1,
            cache_creation_input_tokens=1, attempt=7)
        assert event.currency == "CNY" and event.cost_amount == "0.0000006"
        assert billing.record_usage(decision, input_tokens=999, output_tokens=999, attempt=7).id == event.id
        assert not db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == workspace, UsageEvent.record_stage == "reserved")).all()
        before = UsageService(db, workspace).summary(currency="CNY")
    response = client.delete("/api/v1/usage/events?currency=CNY", headers=auth_headers(token, workspace))
    assert response.status_code == 200 and response.json()["archived_count"] == 1
    result = client.get("/api/v1/usage/summary?currency=CNY", headers=auth_headers(token, workspace)).json()
    assert result["cost_amount"] == before.cost_amount and result["attempts"] == 1
    assert "cost_usd" not in result and "cost_cny" not in result
    assert client.get("/api/v1/usage/summary?currency=USD", headers=auth_headers(token, workspace)).json()["cost_amount"] == "0"


def test_budgets_are_independent_and_atomic(client, register_user):
    _, workspace, provider = setup_native(register_user)
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        usd = billing.create_budget_policy(name="USD", currency="USD", hard_limit="0.1")
        billing.create_budget_policy(name="CNY", currency="CNY", hard_limit="0")
        assert len(billing.list_budget_policies()) == 2
        with pytest.raises(AppError) as failure:
            billing.update_budget_policy(usd.id, name="relabel", hard_limit="1", currency="CNY")
        assert failure.value.code == "budget_currency_immutable"
        db.rollback()
    def reserve():
        with SessionLocal() as db:
            billing = BillingService(db, workspace, "test")
            try:
                # Simulate an asynchronous attempt that stays in-flight when
                # the request session closes.
                return billing.preflight_model_call(provider_id=provider, model_id="exact", feature="chat",
                    estimated_input_tokens=600000, estimated_output_tokens=0, remote_capability=True, durable_reservation=True)
            except AppError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: reserve(), range(2)))
    assert sum(not isinstance(item, str) for item in outcomes) == 1, outcomes
    assert "budget_hard_limit_exceeded" in outcomes
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        status = billing.budget_statuses(currency="USD")[0]
        assert status["spent_amount"] == "0" and status["reserved_amount"] == "0.06"
        assert status["remaining_amount"] == "0.04"
        billing.release_reservation(next(item for item in outcomes if not isinstance(item, str)))
        assert billing.budget_statuses(currency="USD")[0]["reserved_amount"] == "0"


def test_unknown_is_not_zero_and_provider_report_currency_is_retained(client, register_user):
    _, workspace, provider = setup_native(register_user)
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        decision = quote(billing, provider)
        event = billing.record_usage(decision, input_tokens=0, output_tokens=0, attempt=1, usage_reported=False)
        assert event.cost_amount is None and event.reserved_amount == "0.1"
        billing.create_budget_policy(name="cap", currency="USD", hard_limit="10")
        with pytest.raises(AppError) as failure:
            quote(billing, provider)
        assert failure.value.code == "usage_price_required"
        decision = billing.preflight_research_call(provider_id=provider, currency="CNY", estimated_cost_amount="1")
        event = billing.record_usage(decision, input_tokens=0, output_tokens=0, attempt=1,
            provider_reported_cost_amount="0.01234567890123456789", provider_reported_currency="USD")
        assert event.currency == "USD" and event.cost_amount == "0.01234567890123456789"


def test_price_binding_and_cross_workspace_ids(client, register_user, auth_headers):
    token, workspace, provider = setup_native(register_user)
    token_b, workspace_b, provider_b = setup_native(register_user)
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        first = billing.upsert_manual_price(provider_id=provider, model_id="same", currency="USD", input_per_million="1", output_per_million="2")
        second = billing.upsert_manual_price(provider_id=provider, model_id="same", currency="CNY", input_per_million="3", output_per_million="4")
        billing.remove_manual_price(first["id"])
        assert [item["id"] for item in billing.list_manual_prices("CNY")] == [second["id"]]
    response = client.delete("/api/v1/usage/manual-prices", params={"price_id": second["id"]}, headers=auth_headers(token_b, workspace_b))
    assert response.status_code == 404
    response = client.put("/api/v1/usage/manual-prices", headers=auth_headers(token, workspace), json={
        "provider_id": provider_b, "model_id": "same", "currency": "USD", "input_per_million": "1", "output_per_million": "2"})
    assert response.status_code == 404
    assert client.post("/api/v1/usage/models-dev/refresh", headers=auth_headers(token, workspace)).status_code == 403
    permissions = client.post(f"/api/v1/workspaces/{workspace}/select", headers=auth_headers(token)).json()["workspace"]["effective_permissions"]
    assert "workspace.manage" in permissions


def test_interrupted_calls_and_expired_leases_remain_unknown(client, register_user):
    from datetime import timedelta
    from app.domain.models import utc_now
    _, workspace, provider = setup_native(register_user)
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        first = quote(billing, provider)
        first_id = first.reservation_id
    with SessionLocal() as db:
        event = db.get(UsageEvent, first_id)
        assert event.record_stage == "settled" and event.cost_amount is None and event.reserved_amount == "0.1"
        billing = BillingService(db, workspace, "test")
        second = billing.preflight_model_call(provider_id=provider, model_id="exact", feature="chat", remote_capability=True,
            estimated_input_tokens=1000000, estimated_output_tokens=0, durable_reservation=True)
        db.get(UsageEvent, second.reservation_id).reservation_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
        assert BillingService.reconcile_expired(db) == 1
        assert BillingService.reconcile_expired(db) == 0
        summary = UsageService(db, workspace).summary(currency="USD")
        assert summary.cost_amount == "0" and summary.unpriced_events == 2 and summary.reserved_amount == "0.2"
        billing.record_usage(second, input_tokens=100000, output_tokens=0, attempt=1)
        summary = UsageService(db, workspace).summary(currency="USD")
        assert summary.cost_amount == "0.01" and summary.unpriced_events == 1 and summary.attempts == 2


def test_price_snapshot_and_non_token_units_are_exact(client, register_user):
    _, workspace, provider = setup_native(register_user)
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        binding = billing.upsert_manual_price(provider_id=provider, model_id="seconds", currency="USD",
            input_per_million="0", output_per_million="0", fixed_per_call="0.1", rates_by_unit={"seconds": "0.02"})
        decision = billing.preflight_model_call(provider_id=provider, model_id="seconds", feature="audio_transcription",
            estimated_input_tokens=0, estimated_output_tokens=0, estimated_units={"seconds": "3"}, remote_capability=True)
        assert decision.projected_amount == "0.16"
        billing.upsert_manual_price(provider_id=provider, model_id="seconds", currency="USD", input_per_million="99", output_per_million="99")
        event = billing.record_usage(decision, input_tokens=0, output_tokens=0, attempt=1, usage_units={"seconds": "2.5"})
        assert event.cost_amount == "0.15" and event.price_version_id == binding["id"]
        assert event.pricing_snapshot["rates_by_unit"] == {"seconds": "0.02"}


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1e99"])
def test_money_validation(client, register_user, auth_headers, value):
    token, workspace, _ = setup_native(register_user)
    response = client.post("/api/v1/usage/budget-policies", headers=auth_headers(token, workspace), json={"name": "bad", "currency": "USD", "hard_limit": value})
    assert response.status_code == 422


def test_tiny_native_rate_survives_per_million_charge(client, register_user, auth_headers):
    token, workspace, provider = setup_native(register_user, "CNY")
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        billing.upsert_manual_price(provider_id=provider, model_id="tiny", currency="CNY",
            input_per_million="0.000000000000000000000001", output_per_million="0")
        decision = billing.preflight_model_call(provider_id=provider, model_id="tiny", feature="chat",
            estimated_input_tokens=1, estimated_output_tokens=0, remote_capability=True)
        assert decision.projected_amount == "0.000000000000000000000000000001"
        event = billing.record_usage(decision, input_tokens=1, output_tokens=0, attempt=1)
        assert event.cost_amount == decision.projected_amount
    response = client.get("/api/v1/usage/summary?currency=CNY", headers=auth_headers(token, workspace))
    assert response.status_code == 200 and response.json()["cost_amount"] == event.cost_amount


def test_declared_cny_unknown_price_does_not_consume_usd_budget(client, register_user):
    _, workspace, provider = setup_native(register_user, "CNY")
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        billing.create_budget_policy(name="USD only", currency="USD", hard_limit="0")
        decision = billing.preflight_model_call(provider_id=provider, model_id="unpriced", feature="chat",
            estimated_input_tokens=1, estimated_output_tokens=1, remote_capability=True)
        event = billing.record_usage(decision, input_tokens=1, output_tokens=1, attempt=1, usage_reported=False)
        assert event.currency == "CNY" and event.cost_amount is None
        assert billing.budget_statuses(currency="USD")[0]["unknown_events"] == 0


def test_known_free_remote_call_fits_zero_native_budget(client, register_user):
    _, workspace, provider = setup_native(register_user)
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        billing.create_budget_policy(name="free only", currency="USD", hard_limit="0")
        billing.upsert_manual_price(provider_id=provider, model_id="free", currency="USD", input_per_million="0", output_per_million="0", rates_by_unit={"seconds": "0"})
        decision = billing.preflight_model_call(provider_id=provider, model_id="free", feature="chat",
            estimated_input_tokens=100, estimated_output_tokens=100, remote_capability=True)
        assert billing.record_usage(decision, input_tokens=0, output_tokens=0, attempt=1, usage_reported=False).cost_amount == "0"
        with pytest.raises(AppError) as unknown:
            billing.preflight_model_call(provider_id=provider, model_id="unknown", feature="chat",
                estimated_input_tokens=1, estimated_output_tokens=1, remote_capability=True)
        assert unknown.value.code == "usage_price_required"


def test_manual_price_overrides_existing_auto_price_and_matches_region(client, register_user):
    _, workspace, provider = setup_native(register_user, "CNY")
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        billing.create_price(provider_id=provider, model_id="override", feature="chat", currency="CNY",
            input_per_million="99", output_per_million="99", source="models_dev")
        billing.upsert_manual_price(provider_id=provider, model_id="override", currency="CNY", input_per_million="1", output_per_million="0")
        regional = billing.upsert_manual_price(provider_id=provider, model_id="override", currency="CNY",
            input_per_million="2", output_per_million="0", conditions={"region": "mainland"})
        config = db.get(ProviderConfig, provider)
        config.capabilities = {**config.capabilities, "region": "mainland"}
        db.commit()
        decision = billing.preflight_model_call(provider_id=provider, model_id="override", feature="chat",
            estimated_input_tokens=1000000, estimated_output_tokens=0, remote_capability=True)
        assert decision.projected_amount == "2" and decision.price_version_id == regional["id"]
        billing.record_usage(decision, input_tokens=1000000, output_tokens=0, attempt=1)


@pytest.mark.parametrize("currencies", [("USD",), ("USD", "CNY")])
def test_models_dev_native_currency_is_not_overwritten_by_regional_fallback(client, register_user, monkeypatch, currencies):
    from app.services import billing as module
    _, workspace, provider = setup_native(register_user)
    def tariff(currency, source):
        return dict(currency=currency, provider_key="native-vendor", model_id="catalog-model", catalog_id=source+currency,
            native_input_per_million="0.25", native_output_per_million="0", conditions={}, source=source, source_url="https://models.dev")
    monkeypatch.setattr(module, "PRICING_CATALOG", [tariff("CNY", "official_catalog")])
    monkeypatch.setattr(module, "price_entry_for_model", lambda *args, currency=None, **kwargs: tariff(currency, "models_dev") if currency in currencies else None)
    with SessionLocal() as db:
        config = db.get(ProviderConfig, provider)
        config.capabilities = {"pricing_provider_key": "native-vendor"}
        db.commit()
        billing = BillingService(db, workspace, "test")
        kwargs = dict(provider_id=provider, model_id="catalog-model", feature="chat",
            estimated_input_tokens=1000000, estimated_output_tokens=0, remote_capability=True)
        if len(currencies) == 2:
            with pytest.raises(AppError) as ambiguous:
                billing.preflight_model_call(**kwargs)
            assert ambiguous.value.code == "pricing_currency_ambiguous"
            db.rollback()
            config.capabilities = {**config.capabilities, "billing_currency": "CNY"}
            db.commit()
            decision = billing.preflight_model_call(**kwargs)
            assert decision.currency == "CNY"
        else:
            decision = billing.preflight_model_call(**kwargs)
            assert decision.currency == "USD"
        assert decision.projected_amount == "0.25" and decision.source.startswith("models_dev")
        billing.record_usage(decision, input_tokens=1000000, output_tokens=0, attempt=1)


def test_native_search_requests_share_the_same_budget_and_ledger(client, register_user):
    _, workspace, provider = setup_native(register_user, "CNY")
    class Search:
        provider_id = provider
        remote_capability = True
        calls = 0
        def search(self, query, max_results, **kwargs):
            self.calls += 1
            return []
    port = Search()
    with SessionLocal() as db:
        billing = BillingService(db, workspace, "test")
        billing.create_budget_policy(name="USD only", currency="USD", hard_limit="0")
        billing.upsert_manual_price(provider_id=provider, model_id="web-search", currency="CNY",
            input_per_million="0", output_per_million="0", rates_by_unit={"requests": "0.03"})
        _, event = billing.execute_search(port, "query", 5, maximum_amount=("CNY", "0.04"))
        assert port.calls == 1 and event.currency == "CNY" and event.cost_amount == "0.03"
        with pytest.raises(AppError, match="blocked before execution"):
            billing.execute_search(port, "query", 5, maximum_amount=("CNY", "0.02"))
        assert port.calls == 1
        summary = UsageService(db, workspace).summary(currency="CNY")
        assert summary.attempts == 1 and summary.cost_amount == "0.03" and summary.reserved_amount == "0"


def test_chat_api_uses_native_cny_price_with_zero_usd_limit(client, register_user, auth_headers, monkeypatch):
    from app.api.routers import chat as router
    from app.providers.ports.model import ProviderStreamEvent
    from app.services.chat import ChatService
    token, workspace, provider_id = setup_native(register_user, "CNY")
    class Model:
        available, remote_capability, thinking_mode, search_route = True, True, "off", "disabled"
        actual_reasoning_effort = None
        model_id = "exact"
        last_usage = {"input_tokens": 3, "output_tokens": 1, "cached_input_tokens": 1, "cache_creation_input_tokens": 1}
        def __init__(self):
            self.provider_id = provider_id
        def stream_chat(self, messages, **kwargs):
            yield ProviderStreamEvent(type="text_delta", content="Native answer")
            yield ProviderStreamEvent(type="completed", finish_reason="stop")
        def stream_answer(self, prompt):
            yield "Native answer"
        def generate_json(self, prompt, schema_name, schema):
            return {}
    with SessionLocal() as db:
        BillingService(db, workspace, "test").create_budget_policy(name="USD only", currency="USD", hard_limit="0")
    monkeypatch.setattr(router, "service", lambda db, context, settings, **kwargs: ChatService(
        db, context.workspace_id, context.principal.user_id, Model(), settings=settings))
    headers = auth_headers(token, workspace)
    session = client.post("/api/v1/sessions", headers=headers, json={"title": "native"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{session}/messages/stream", headers=headers, json={"content": "native"})
    assert response.status_code == 200 and '"message.completed"' in response.text, response.text[-1500:]
    with SessionLocal() as db:
        events = db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == workspace, UsageEvent.feature == "chat")).all()
        assert len(events) == 1 and events[0].currency == "CNY" and events[0].cost_amount == "0.0000006"
