from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.money import amount_text, currency_code, decimal_amount, exact_money, sum_amounts
from app.domain.models import BudgetAlert, BudgetPolicy, PriceVersion, ProviderConfig, UsageEvent, Workspace, utc_now
from app.providers.models_dev import price_entry_for_model
from app.services.pricing_catalog import PRICING_CATALOG
from app.repositories.audit import AuditRepository
from app.repositories.domain import BudgetAlertRepository, BudgetPolicyRepository, PriceVersionRepository, UsageRepository

MANUAL_PRICE_SOURCE = "workspace_manual"


@dataclass(slots=True)
class BillingQuote:
    provider_id: str
    model_id: str
    feature: str
    remote_capability: bool
    currency: str | None
    price_version_id: str | None = None
    input_per_million: str = "0"
    cached_input_per_million: str = "0"
    cache_write_per_million: str = "0"
    output_per_million: str = "0"
    fixed_per_call: str = "0"
    price_multiplier: str = "1"
    projected_amount: str | None = None
    quoted_at: str = field(default_factory=lambda: utc_now().isoformat())
    quote_id: str = field(default_factory=lambda: str(uuid4()))
    reservation_id: str | None = None
    workspace_id: str | None = None
    rates_by_unit: dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    schema_version: int = 2
    durable_reservation: bool = False
    approval_fingerprint: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, value: dict[str, Any]) -> BillingQuote:
        if value.get("schema_version") != 2:
            raise AppError(409, "billing_quote_expired", "Historical approval must be quoted again in its native currency")
        fields = cls.__dataclass_fields__
        return cls(**{key: item for key, item in value.items() if key in fields})


class BillingService:
    """One native-currency billing pipeline for every existing provider call."""

    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db, self.workspace_id, self.actor_id = db, workspace_id, actor_id
        self.prices = PriceVersionRepository(db, workspace_id)
        self.policies = BudgetPolicyRepository(db, workspace_id)
        self.alerts = BudgetAlertRepository(db, workspace_id)
        self.usage = UsageRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def create_price(self, *, provider_id: str, model_id: str, feature: str = "*",
                     currency: str, input_per_million: Any, output_per_million: Any,
                     cached_input_per_million: Any = None, cache_write_per_million: Any = None,
                     fixed_per_call: Any = "0", rates_by_unit: dict[str, Any] | None = None,
                     effective_at: datetime | None = None, source: str = MANUAL_PRICE_SOURCE,
                     conditions: dict[str, Any] | None = None, commit: bool = True) -> PriceVersion:
        currency = currency_code(currency)
        scope = self._normalized_scope(provider_id, model_id, feature)
        if provider_id != "*":
            self._require_provider(provider_id)
        self._lock_budget()
        version = (self.db.scalar(select(func.max(PriceVersion.version)).where(
            PriceVersion.workspace_id == self.workspace_id, PriceVersion.provider_id == scope[0],
            PriceVersion.model_id == scope[1], PriceVersion.feature == scope[2], PriceVersion.currency == currency,
        )) or 0) + 1
        normalized_units = {unit: amount_text(value) for unit, value in (rates_by_unit or {}).items()}
        price = self.prices.add(PriceVersion(
            workspace_id=self.workspace_id, provider_id=scope[0], model_id=scope[1], feature=scope[2],
            currency=currency, version=version, input_per_million=amount_text(input_per_million),
            cached_input_per_million=amount_text(cached_input_per_million) if cached_input_per_million is not None else None,
            cache_write_per_million=amount_text(cache_write_per_million) if cache_write_per_million is not None else None,
            output_per_million=amount_text(output_per_million), fixed_per_call=amount_text(fixed_per_call),
            rates_by_unit=normalized_units, effective_at=self._as_utc(effective_at or utc_now()),
            source=source, conditions={**(conditions or {}), "native_schema": 2},
        ))
        self.audit.record(actor_id=self.actor_id, action="usage.price_version.created",
                          resource_type="price_version", resource_id=price.id,
                          details={"scope": list(scope), "currency": currency, "source": source})
        self.db.flush()
        if commit:
            self.db.commit()
            self.db.refresh(price)
        return price

    def list_manual_prices(self, currency: str | None = None) -> list[dict[str, Any]]:
        statement = self.prices.query().where(PriceVersion.source == MANUAL_PRICE_SOURCE, PriceVersion.retired_at.is_(None))
        if currency:
            statement = statement.where(PriceVersion.currency == currency_code(currency))
        return [self._manual_price_view(price) for price in self.db.scalars(statement.order_by(PriceVersion.effective_at.desc())).all()]

    def upsert_manual_price(self, *, model_id: str, provider_id: str, currency: str,
                            input_per_million: Any, output_per_million: Any,
                            cached_input_per_million: Any = None, cache_write_per_million: Any = None,
                            fixed_per_call: Any = "0", feature: str = "*",
                            conditions: dict[str, Any] | None = None,
                            rates_by_unit: dict[str, Any] | None = None) -> dict[str, Any]:
        if provider_id == "*":
            raise AppError(422, "price_provider_required", "A manual price must select a workspace provider")
        self._require_provider(provider_id)
        currency = currency_code(currency)
        # A binding includes its native currency and tariff tier. It never
        # retires another provider's price or another currency's price.
        self._lock_budget()
        binding = dict(conditions or {})
        for old in self.db.scalars(self.prices.query().where(
            PriceVersion.model_id == model_id, PriceVersion.provider_id == provider_id,
            PriceVersion.feature == feature, PriceVersion.currency == currency,
            PriceVersion.source == MANUAL_PRICE_SOURCE, PriceVersion.retired_at.is_(None),
        )).all():
            old_binding = {key: value for key, value in (old.conditions or {}).items() if key != "native_schema"}
            if old_binding == binding:
                old.retired_at = utc_now()
        price = self.create_price(provider_id=provider_id, model_id=model_id, feature=feature, currency=currency,
            input_per_million=input_per_million, output_per_million=output_per_million,
            cached_input_per_million=cached_input_per_million, cache_write_per_million=cache_write_per_million,
            fixed_per_call=fixed_per_call, conditions=binding, rates_by_unit=rates_by_unit)
        return self._manual_price_view(price)

    def remove_manual_price(self, price_id: str) -> int:
        price = self.prices.require(price_id, "manual price")
        if price.source != MANUAL_PRICE_SOURCE:
            raise AppError(422, "not_manual_price", "Only a manual price may be removed")
        removed = int(price.retired_at is None)
        price.retired_at = price.retired_at or utc_now()
        self.audit.record(actor_id=self.actor_id, action="usage.manual_price.removed", resource_type="price_version", resource_id=price.id)
        self.db.commit()
        return removed

    @staticmethod
    def _manual_price_view(price: PriceVersion) -> dict[str, Any]:
        return {key: getattr(price, key) for key in ("id", "provider_id", "model_id", "feature", "currency",
            "input_per_million", "cached_input_per_million", "cache_write_per_million", "output_per_million",
            "fixed_per_call", "rates_by_unit", "conditions", "effective_at", "version")}

    def list_budget_policies(self, currency: str | None = None) -> list[BudgetPolicy]:
        statement = self.policies.query()
        if currency:
            statement = statement.where(BudgetPolicy.currency == currency_code(currency))
        return list(self.db.scalars(statement.order_by(BudgetPolicy.created_at, BudgetPolicy.id)).all())

    def create_budget_policy(self, *, name: str, provider_id: str = "*", model_id: str = "*",
                             feature: str = "*", period: str = "calendar_month_utc",
                             soft_limit: Any = None, hard_limit: Any = None,
                             currency: str, enabled: bool = True) -> BudgetPolicy:
        self._validate_limits(soft_limit, hard_limit)
        self._period_bounds(period, utc_now())
        currency = currency_code(currency)
        scope = self._normalized_scope(provider_id, model_id, feature)
        if scope[0] != "*":
            self._require_provider(scope[0])
        self._lock_budget()
        existing = self.db.scalar(self.policies.query().where(
            BudgetPolicy.provider_id == scope[0], BudgetPolicy.model_id == scope[1],
            BudgetPolicy.feature == scope[2], BudgetPolicy.period == period, BudgetPolicy.currency == currency))
        if existing is not None:
            raise AppError(409, "budget_policy_scope_exists", "A budget already exists for this currency, scope and period")
        policy = self.policies.add(BudgetPolicy(workspace_id=self.workspace_id, name=name, provider_id=scope[0],
            model_id=scope[1], feature=scope[2], period=period, currency=currency,
            soft_limit=amount_text(soft_limit) if soft_limit is not None else None,
            hard_limit=amount_text(hard_limit) if hard_limit is not None else None, enabled=enabled))
        self.audit.record(actor_id=self.actor_id, action="usage.budget_policy.created", resource_type="budget_policy",
                          resource_id=policy.id, details={"scope": list(scope), "currency": currency})
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def update_budget_policy(self, policy_id: str, *, name: str, soft_limit: Any = None,
                             hard_limit: Any = None, enabled: bool = True, currency: str | None = None) -> BudgetPolicy:
        self._validate_limits(soft_limit, hard_limit)
        self._lock_budget()
        policy = self.policies.require(policy_id, "budget policy")
        if currency and currency_code(currency) != policy.currency:
            raise AppError(409, "budget_currency_immutable", "Create a policy in the other currency instead of relabelling its history")
        policy.name, policy.enabled = name, enabled
        policy.soft_limit = amount_text(soft_limit) if soft_limit is not None else None
        policy.hard_limit = amount_text(hard_limit) if hard_limit is not None else None
        self.audit.record(actor_id=self.actor_id, action="usage.budget_policy.updated", resource_type="budget_policy", resource_id=policy.id)
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def delete_budget_policy(self, policy_id: str) -> None:
        # Retain alert history and references; disable the existing policy.
        policy = self.policies.require(policy_id, "budget policy")
        policy.enabled = False
        self.audit.record(actor_id=self.actor_id, action="usage.budget_policy.disabled", resource_type="budget_policy", resource_id=policy.id)
        self.db.commit()

    def list_alerts(self, currency: str | None = None) -> list[BudgetAlert]:
        statement = self.alerts.query()
        if currency:
            statement = statement.where(BudgetAlert.currency == currency_code(currency))
        return list(self.db.scalars(statement.order_by(BudgetAlert.created_at.desc())).all())

    def acknowledge_alert(self, alert_id: str) -> BudgetAlert:
        alert = self.alerts.require(alert_id, "budget alert")
        alert.status, alert.acknowledged_at = "acknowledged", utc_now()
        self.audit.record(actor_id=self.actor_id, action="usage.budget_alert.acknowledged", resource_type="budget_alert", resource_id=alert.id)
        self.db.commit()
        self.db.refresh(alert)
        return alert

    @exact_money
    def budget_statuses(self, *, currency: str | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
        current = self._as_utc(now or utc_now())
        result = []
        for policy in self.list_budget_policies(currency):
            start, end = self._period_bounds(policy.period, current)
            spent, reserved, unknown = self._totals_for_policy(policy, start, end)
            total = spent + reserved
            hard = decimal_amount(policy.hard_limit) if policy.hard_limit is not None else None
            soft = decimal_amount(policy.soft_limit) if policy.soft_limit is not None else None
            result.append(dict(policy_id=policy.id, name=policy.name, provider_id=policy.provider_id,
                model_id=policy.model_id, feature=policy.feature, period=policy.period, currency=policy.currency,
                period_start=start, period_end=end, spent_amount=amount_text(spent), reserved_amount=amount_text(reserved),
                soft_limit=policy.soft_limit, hard_limit=policy.hard_limit,
                remaining_amount=amount_text(max(Decimal(0), hard-total)) if hard is not None and not unknown else None,
                unknown_events=unknown, status="amount_unknown" if unknown else "ready",
                soft_exceeded=soft is not None and total >= soft, hard_exceeded=hard is not None and total >= hard,
                enabled=policy.enabled, usage_ratio=float(min(Decimal(1), total/hard)) if hard else None))
        return result

    def preflight_model_call(self, *, provider_id: str, model_id: str, feature: str,
                             estimated_input_tokens: int, estimated_output_tokens: int,
                             remote_capability: bool, now: datetime | None = None,
                             estimated_units: dict[str, Any] | None = None, reserve: bool = True,
                             durable_reservation: bool = False) -> BillingQuote:
        current = self._as_utc(now or utc_now())
        if not remote_capability:
            return BillingQuote(provider_id, model_id, feature, False, "USD", projected_amount="0", workspace_id=self.workspace_id)
        configured = self.db.get(ProviderConfig, provider_id)
        if configured is not None and configured.workspace_id != self.workspace_id:
            raise AppError(404, "provider_not_found", "Provider is not in the current workspace")
        self._lock_budget()
        # Some existing internal provider ports have no persisted configuration.
        # They can produce usage, but no vendor/currency is inferred from their
        # name. Unknown pricing is still blocked by any matching hard budget.
        price = self._active_price(provider_id, model_id, feature, current, max(0, estimated_input_tokens)) if configured else None
        if price is None and configured:
            price = self._seed_default_catalog_price(provider_id=provider_id, model_id=model_id, feature=feature,
                                                      now=current, input_tokens=max(0, estimated_input_tokens))
        declared = (configured.capabilities or {}).get("billing_currency") if configured else None
        native_currency = price.currency if price else (currency_code(declared) if declared else None)
        quote = BillingQuote(provider_id, model_id, feature, True, native_currency,
                             quoted_at=current.isoformat(), workspace_id=self.workspace_id, durable_reservation=durable_reservation)
        if price:
            quote.price_version_id, quote.source = price.id, price.source
            quote.input_per_million, quote.output_per_million, quote.fixed_per_call = price.input_per_million, price.output_per_million, price.fixed_per_call
            quote.cached_input_per_million = price.cached_input_per_million if price.cached_input_per_million is not None else price.input_per_million
            quote.cache_write_per_million = price.cache_write_per_million if price.cache_write_per_million is not None else price.input_per_million
            quote.price_multiplier = amount_text(self._price_multiplier(price, current))
            quote.rates_by_unit = dict(price.rates_by_unit or {})
            quote.projected_amount = self._cost(quote, max(0, estimated_input_tokens), max(0, estimated_output_tokens), 0, 0, estimated_units)
        if reserve:
            self._reserve(quote)
        else:
            self.db.commit()
        return quote


    def execute_search(self, provider: Any, query: str, max_results: int, *,
                       allowed_domains: set[str] | None = None,
                       maximum_amount: tuple[str, str] | None = None) -> tuple[list[Any], UsageEvent]:
        """Meter the existing search ports through the same native ledger."""
        quote = self.preflight_model_call(provider_id=provider.provider_id,
            model_id=getattr(provider, "model_id", "web-search"), feature="web_search",
            estimated_input_tokens=0, estimated_output_tokens=0,
            estimated_units={"requests": 1, "results": max_results},
            remote_capability=provider.remote_capability)
        if maximum_amount and quote.remote_capability:
            currency, limit = maximum_amount
            if quote.currency is None or quote.projected_amount is None:
                self.release_reservation(quote, reason="search_price_unknown")
                raise AppError(409, "usage_price_required", "Search budget requires a known native price")
            if quote.currency != currency:
                self.release_reservation(quote, reason="search_currency_mismatch")
                raise AppError(422, "search_currency_mismatch", "Choose the search supplier's native currency", {"currency": quote.currency})
            if decimal_amount(quote.projected_amount) > decimal_amount(limit):
                self.release_reservation(quote, reason="search_budget_blocked")
                raise AppError(402, "search_budget_exceeded", "Search was blocked before execution")
        results = None
        try:
            results = provider.search(query, max_results, allowed_domains=allowed_domains)
        finally:
            usage = dict(getattr(provider, "last_usage", {}) or {}) if results is not None else {}
            event = self.record_usage(quote, input_tokens=0, output_tokens=0, attempt=1,
                usage_reported=False,
                usage_units={"requests": 1, "results": len(results)} if results is not None else {},
                provider_reported_cost_amount=usage.get("cost_amount"),
                provider_reported_currency=usage.get("currency"))
        return results, event

    def _lock_budget(self) -> None:
        # SQLite UPDATE acquires its single-writer lock before reading totals.
        # Other engines serialize on the owning workspace row, across workers.
        self.db.execute(update(Workspace).where(Workspace.id == self.workspace_id).values(id=Workspace.id))
        self.db.flush()

    @exact_money
    def _reserve(self, quote: BillingQuote) -> None:
        if quote.workspace_id is not None and quote.workspace_id != self.workspace_id:
            raise AppError(403, "billing_scope_mismatch", "Quote belongs to another workspace")
        now = self._as_utc(datetime.fromisoformat(quote.quoted_at))
        policies = [policy for policy in self.list_budget_policies() if policy.enabled
            and self._scope_matches(policy.provider_id, quote.provider_id)
            and self._scope_matches(policy.model_id, quote.model_id) and self._scope_matches(policy.feature, quote.feature)
            and (quote.currency is None or policy.currency == quote.currency)]
        error = None
        for policy in policies:
            start, end = self._period_bounds(policy.period, now)
            spent, reserved, unknown = self._totals_for_policy(policy, start, end)
            projected = decimal_amount(quote.projected_amount) if quote.projected_amount is not None else None
            if (projected is None or unknown) and policy.hard_limit is not None:
                error = AppError(409, "usage_price_required", "The budget cannot authorize a call with an unknown native amount",
                    {"currency": policy.currency, "policy_id": policy.id, "unknown_events": unknown})
                continue
            for level, value in (("soft", policy.soft_limit), ("hard", policy.hard_limit)):
                if value is None or projected is None:
                    continue
                limit = decimal_amount(value)
                exceeded = spent + reserved + projected >= limit if level == "soft" else spent + reserved + projected > limit
                if exceeded:
                    self._persist_alert(policy, level, quote, start, end, spent, reserved, projected, limit)
                    if level == "hard":
                        error = AppError(402, "budget_hard_limit_exceeded", "The call was blocked before execution",
                            {"policy_id": policy.id, "currency": policy.currency, "spent_amount": amount_text(spent),
                             "reserved_amount": amount_text(reserved), "projected_amount": quote.projected_amount, "hard_limit": policy.hard_limit})
        if error:
            self.db.commit()
            raise error
        event = self.usage.add(UsageEvent(workspace_id=self.workspace_id, provider_id=quote.provider_id,
            model_id=quote.model_id, feature=quote.feature, currency=quote.currency, record_stage="reserved",
            reserved_amount=quote.projected_amount, cost_status="reserved", amount_source=quote.source,
            idempotency_key=quote.quote_id + ":1", price_version_id=quote.price_version_id,
            reservation_expires_at=now + timedelta(hours=24), reservation_owner=self.actor_id, created_at=now))
        self.db.flush()
        quote.reservation_id = event.id
        event.pricing_snapshot = quote.snapshot()
        self.db.commit()
        if not quote.durable_reservation:
            self.db.info.setdefault("native_pending_quotes", {})[quote.quote_id] = (quote, self.actor_id)

    def release_reservation(self, quote: BillingQuote, *, reason: str = "not_executed") -> None:
        if not quote.reservation_id:
            return
        event = self.usage.require(quote.reservation_id, "usage reservation")
        if event.record_stage == "reserved":
            event.record_stage, event.cost_status = "released", reason
            event.reserved_amount = None
            self.db.commit()
        self.db.info.get("native_pending_quotes", {}).pop(quote.quote_id, None)

    @staticmethod
    def reconcile_session(db: Session) -> None:
        """Persist an interrupted HTTP attempt as unknown, never as a free call."""
        pending = list(getattr(db, "info", {}).get("native_pending_quotes", {}).values())
        if not pending:
            return
        db.rollback()
        for quote, actor_id in pending:
            billing = BillingService(db, quote.workspace_id, actor_id)
            event = billing.usage.require(quote.reservation_id, "usage reservation")
            if event.record_stage == "reserved":
                billing.record_usage(quote, input_tokens=0, output_tokens=0, attempt=1, usage_reported=False)
            db.info.get("native_pending_quotes", {}).pop(quote.quote_id, None)

    @staticmethod
    def reconcile_expired(db: Session, *, now: datetime | None = None) -> int:
        """Recover expired attempts after process loss, retaining unknown occupancy."""
        current = now or utc_now()
        events = list(db.scalars(select(UsageEvent).where(UsageEvent.record_stage == "reserved",
            UsageEvent.reservation_expires_at <= current)).all())
        for event in events:
            event.record_stage, event.cost_amount, event.cost_status = "settled", None, "interrupted_usage_missing"
            event.amount_source, event.reservation_expires_at = "unknown", None
        if events:
            db.commit()
        return len(events)

    def record_usage(self, quote: BillingQuote, *, input_tokens: int, output_tokens: int, attempt: int,
                     cached_input_tokens: int = 0, cache_creation_input_tokens: int = 0, reasoning_tokens: int = 0,
                     latency_ms: int = 0, provider_reported_cost_amount: Any = None,
                     provider_reported_currency: str | None = None, usage_reported: bool = True,
                     usage_units: dict[str, Any] | None = None) -> UsageEvent:
        if quote.workspace_id is not None and quote.workspace_id != self.workspace_id:
            raise AppError(403, "billing_scope_mismatch", "Quote belongs to another workspace")
        key = quote.quote_id + ":" + str(max(1, int(attempt)))
        self._lock_budget()
        event = self.db.scalar(self.usage.query().where(UsageEvent.idempotency_key == key))
        tokens_required = any(decimal_amount(rate) for rate in (quote.input_per_million, quote.cached_input_per_million,
            quote.cache_write_per_million, quote.output_per_million))
        zero_tariff = not decimal_amount(quote.price_multiplier) or not any(decimal_amount(rate) for rate in (
            quote.input_per_million, quote.cached_input_per_million, quote.cache_write_per_million,
            quote.output_per_million, quote.fixed_per_call, *quote.rates_by_unit.values()))
        actual_usage_available = usage_reported or zero_tariff or (bool(usage_units) and not tokens_required)
        actual_report_available = provider_reported_cost_amount is not None or (actual_usage_available and quote.price_version_id is not None)
        if event and event.record_stage == "settled" and (event.cost_amount is not None or not actual_report_available):
            self.db.commit()
            return event
        if event is None:
            event = self.usage.require(quote.reservation_id, "usage reservation") if quote.reservation_id else None
            if event is None or event.record_stage != "reserved":
                event = self.usage.add(UsageEvent(workspace_id=self.workspace_id, provider_id=quote.provider_id,
                    model_id=quote.model_id, feature=quote.feature, idempotency_key=key, created_at=utc_now()))
            event.idempotency_key = key
        inp, out = max(0, int(input_tokens)), max(0, int(output_tokens))
        cached = min(inp, max(0, int(cached_input_tokens)))
        created = min(inp-cached, max(0, int(cache_creation_input_tokens)))
        currency = quote.currency
        if not quote.remote_capability:
            amount, status, source = "0", "non_billable", "non_billable"
        elif provider_reported_cost_amount is not None:
            if not provider_reported_currency:
                raise AppError(422, "reported_currency_required", "Provider-reported money requires its native currency")
            currency = currency_code(provider_reported_currency)
            amount, status, source = amount_text(provider_reported_cost_amount), "provider_reported", "provider_reported"
        elif quote.price_version_id and actual_usage_available:
            amount = self._cost(quote, inp, out, cached, created, usage_units)
            status, source = ("priced", quote.source) if amount is not None else ("unpriced_units", "unknown")
        else:
            # Missing actual usage is unknown, never a fabricated zero.
            amount, status, source = None, "usage_missing" if quote.price_version_id else "unpriced", "unknown"
        event.currency, event.cost_amount, event.cost_status, event.amount_source = currency, amount, status, source
        event.record_stage, event.attempt = "settled", max(1, int(attempt))
        # Retain the conservative authorization until unknown spend is resolved.
        event.reserved_amount = quote.projected_amount if amount is None else None
        event.input_tokens, event.output_tokens = inp, out
        event.cached_input_tokens, event.cache_creation_input_tokens = cached, created
        event.reasoning_tokens, event.total_tokens = min(out, max(0, int(reasoning_tokens))), inp+out
        event.latency_ms, event.price_version_id = max(0, int(latency_ms)), quote.price_version_id
        event.pricing_snapshot, event.usage_units = quote.snapshot(), dict(usage_units or {})
        event.reservation_expires_at = None
        self.db.flush()
        self.db.commit()
        self.db.info.get("native_pending_quotes", {}).pop(quote.quote_id, None)
        return event

    @staticmethod
    def _cost(quote: BillingQuote, inp: int, out: int, cached: int, created: int,
              units: dict[str, Any] | None = None) -> str | None:
        with localcontext() as context:
            context.prec = 256
            if not decimal_amount(quote.price_multiplier):
                return "0"
            value = (Decimal(inp-cached-created)*decimal_amount(quote.input_per_million)
                + Decimal(cached)*decimal_amount(quote.cached_input_per_million)
                + Decimal(created)*decimal_amount(quote.cache_write_per_million)
                + Decimal(out)*decimal_amount(quote.output_per_million)) / Decimal(1000000)
            value += decimal_amount(quote.fixed_per_call)
            for unit, rate in quote.rates_by_unit.items():
                if not decimal_amount(rate):
                    continue
                if not units or unit not in units:
                    return None
                value += decimal_amount(units[unit])*decimal_amount(rate)
            try:
                return amount_text(value*decimal_amount(quote.price_multiplier))
            except ValueError:
                # Preserve unsupported legacy rate evidence as unknown rather
                # than rounding a charge or crashing after a provider call.
                return None

    def _totals_for_policy(self, policy: BudgetPolicy, start: datetime, end: datetime) -> tuple[Decimal, Decimal, int]:
        statement = self.usage.query().where(UsageEvent.currency == policy.currency,
            or_(UsageEvent.record_stage == "reserved", (UsageEvent.created_at >= start) & (UsageEvent.created_at < end)),
            UsageEvent.record_stage != "released")
        for name in ("provider_id", "model_id", "feature"):
            value = getattr(policy, name)
            if value != "*":
                statement = statement.where(getattr(UsageEvent, name) == value)
        events = self.db.scalars(statement).all()
        spent = decimal_amount(sum_amounts(event.cost_amount for event in events if event.record_stage == "settled"))
        reserved = decimal_amount(sum_amounts(event.reserved_amount for event in events))
        unknown = sum((event.record_stage == "settled" and event.cost_amount is None)
            or (event.record_stage == "reserved" and event.reserved_amount is None) for event in events)
        # Unknown legacy currency can affect either budget. Require resolution;
        # never silently drop historical spend out of enforcement.
        unknown_statement = self.usage.query().where(UsageEvent.currency.is_(None),
            or_(UsageEvent.record_stage == "reserved", (UsageEvent.created_at >= start) & (UsageEvent.created_at < end)),
            UsageEvent.record_stage != "released", UsageEvent.cost_status != "non_billable")
        for name in ("provider_id", "model_id", "feature"):
            if getattr(policy, name) != "*":
                unknown_statement = unknown_statement.where(getattr(UsageEvent, name) == getattr(policy, name))
        unknown += len(self.db.scalars(unknown_statement).all())
        return spent, reserved, unknown

    def _persist_alert(self, policy: BudgetPolicy, level: str, quote: BillingQuote, start: datetime,
                       end: datetime, spent: Decimal, reserved: Decimal, projected: Decimal, limit: Decimal) -> BudgetAlert:
        alert = self.db.scalar(self.alerts.query().where(BudgetAlert.policy_id == policy.id,
            BudgetAlert.level == level, BudgetAlert.period_start == start))
        notify = alert is None or alert.status == "acknowledged"
        if alert is None:
            alert = self.alerts.add(BudgetAlert(workspace_id=self.workspace_id, policy_id=policy.id, level=level,
                provider_id=quote.provider_id, model_id=quote.model_id, feature=quote.feature,
                period_start=start, period_end=end, limit_cny=0))
        alert.status, alert.acknowledged_at, alert.currency = "open", None, policy.currency
        alert.spent_amount, alert.reserved_amount = amount_text(spent), amount_text(reserved)
        alert.projected_amount, alert.limit_amount = amount_text(projected), amount_text(limit)
        self.db.flush()
        if notify:
            from app.services.alert_email import notify_budget_alert
            notify_budget_alert(self.db, self.workspace_id, policy_name=policy.name, level=level,
                scope=f"{quote.provider_id} / {quote.model_id} / {quote.feature}", currency=policy.currency,
                spent_amount=alert.spent_amount, projected_amount=alert.projected_amount, limit_amount=alert.limit_amount)
        self.audit.record(actor_id=self.actor_id, action=f"usage.budget.{level}", resource_type="budget_policy",
            resource_id=policy.id, outcome="blocked" if level == "hard" else "warning",
            details={"currency": policy.currency, "spent_amount": alert.spent_amount, "reserved_amount": alert.reserved_amount,
                     "projected_amount": alert.projected_amount, "limit_amount": alert.limit_amount})
        return alert

    def _require_provider(self, provider_id: str) -> ProviderConfig:
        provider = self.db.scalar(select(ProviderConfig).where(ProviderConfig.workspace_id == self.workspace_id, ProviderConfig.id == provider_id))
        if provider is None:
            raise AppError(404, "provider_not_found", "Provider is not in the current workspace")
        return provider

    def _active_price(self, provider_id: str, model_id: str, feature: str, now: datetime, input_tokens: int = 0) -> PriceVersion | None:
        provider = self._require_provider(provider_id)
        candidates = [price for price in self.db.scalars(self.prices.query().where(
            PriceVersion.provider_id == provider_id, PriceVersion.model_id.in_([model_id, "*"]),
            PriceVersion.feature.in_([feature, "*"]), PriceVersion.effective_at <= now)).all()
            if (price.retired_at is None or self._as_utc(price.retired_at) > now)
            and self._conditions_match(price.conditions or {}, input_tokens, provider) and not (price.conditions or {}).get("native_unpriced")]
        if not candidates:
            return None
        declared_currency = (provider.capabilities or {}).get("billing_currency")
        if declared_currency:
            candidates = [price for price in candidates if price.currency == currency_code(declared_currency)]
        if not candidates:
            return None
        def rank(price: PriceVersion) -> tuple[int, ...]:
            binding = price.conditions or {}
            specificity = sum(binding.get(key) is not None for key in ("region", "endpoint", "billing_tier", "min_input_tokens", "max_input_tokens"))
            return (int(price.source == MANUAL_PRICE_SOURCE), int(price.model_id == model_id),
                int(price.feature == feature), specificity, int(binding.get("min_input_tokens") or 0))
        best_rank = max(rank(price) for price in candidates)
        finalists = [price for price in candidates if rank(price) == best_rank]
        if len({price.currency for price in finalists}) > 1:
            raise AppError(409, "pricing_currency_ambiguous", "Select the provider's billing currency before calling a model with tariffs in both currencies")
        return max(finalists, key=lambda price: (self._as_utc(price.effective_at), price.version))

    @staticmethod
    def _provider_catalog_key(provider: ProviderConfig) -> str | None:
        declared = (provider.capabilities or {}).get("pricing_provider_key")
        if isinstance(declared, str) and declared.strip():
            return declared.strip().casefold()
        kinds = {"openai_responses": "openai", "deepseek_chat": "deepseek", "qwen": "qwen"}
        if provider.provider_type in kinds:
            return kinds[provider.provider_type]
        try:
            host = (urlparse(provider.base_url or "").hostname or "").casefold()
        except ValueError:
            return None
        for suffix, vendor in (("api.openai.com", "openai"), ("api.deepseek.com", "deepseek"),
            ("dashscope.aliyuncs.com", "qwen"), ("moonshot.cn", "kimi"), ("kimi.com", "kimi"),
            ("z.ai", "zai"), ("bigmodel.cn", "zai"), ("minimax.io", "minimax"),
            ("generativelanguage.googleapis.com", "google"), ("api.anthropic.com", "anthropic")):
            if host == suffix or host.endswith("." + suffix):
                return vendor
        return None

    def provider_ids_for_catalog_key(self, provider_key: str) -> list[str]:
        return [provider.id for provider in self.db.scalars(select(ProviderConfig).where(ProviderConfig.workspace_id == self.workspace_id)).all()
            if self._provider_catalog_key(provider) == provider_key.strip().casefold()]

    def _seed_default_catalog_price(self, *, provider_id: str, model_id: str, feature: str,
                                   now: datetime, input_tokens: int) -> PriceVersion | None:
        provider = self._require_provider(provider_id)
        vendor = self._provider_catalog_key(provider)
        if vendor is None:
            return None
        declared_currency = (provider.capabilities or {}).get("billing_currency")
        builtin = [item for item in PRICING_CATALOG if item["provider_key"] == vendor and item["model_id"] == model_id
                   and self._conditions_match(item.get("conditions") or {}, input_tokens, provider)
                   and (not declared_currency or item["currency"] == currency_code(declared_currency))]
        native_currencies = [currency_code(declared_currency)] if declared_currency else ["USD", "CNY"]
        network_choices = [entry for currency in native_currencies
            if (entry := price_entry_for_model(model_id, input_tokens, provider_key=vendor, currency=currency))
            and self._conditions_match(entry.get("conditions") or {}, input_tokens, provider)]
        choices = network_choices or builtin
        if len({entry["currency"] for entry in choices}) > 1:
            raise AppError(409, "pricing_currency_ambiguous", "Select the provider's native billing currency before execution")
        # Read models.dev prices in their published currency. Official tariffs
        # are a native fallback, never a reason to reinterpret a USD record.
        item = choices[-1] if choices else None
        if item is None:
            return None
        return self.create_price(provider_id=provider_id, model_id=model_id, feature=feature, currency=item["currency"],
            input_per_million=item["native_input_per_million"], output_per_million=item["native_output_per_million"],
            cached_input_per_million=item.get("native_cached_input_per_million"),
            cache_write_per_million=item.get("native_cache_write_per_million"), fixed_per_call=item.get("native_fixed_per_call", "0"),
            rates_by_unit=item.get("rates_by_unit"), effective_at=now, commit=False,
            source=f"{item.get('source') or 'official_catalog'}:{item.get('source_url') or 'unknown'}",
            conditions={**(item.get("conditions") or {}), "catalog_id": item["catalog_id"], "catalog_provider_key": vendor,
                "catalog_as_of": item.get("as_of"), "catalog_source_url": item.get("source_url")})

    @staticmethod
    def _conditions_match(conditions: dict[str, Any], input_tokens: int, provider: ProviderConfig | None = None) -> bool:
        if ((conditions.get("min_input_tokens") is not None and input_tokens < int(conditions["min_input_tokens"]))
            or (conditions.get("max_input_tokens") is not None and input_tokens > int(conditions["max_input_tokens"]))):
            return False
        if provider:
            capabilities = provider.capabilities or {}
            for key in ("region", "endpoint", "billing_tier"):
                expected = conditions.get(key)
                if expected is not None:
                    actual = provider.base_url if key == "endpoint" else capabilities.get(key)
                    if str(expected).rstrip("/") != str(actual or "").rstrip("/"):
                        return False
        return True

    @staticmethod
    def _price_multiplier(price: PriceVersion, now: datetime) -> Any:
        conditions = price.conditions or {}
        windows = conditions.get("peak_windows") or []
        if windows:
            from zoneinfo import ZoneInfo
            local = now.astimezone(ZoneInfo(str(conditions.get("time_zone") or "UTC")))
            minute = local.hour*60 + local.minute
            for window in windows:
                start, end = str(window).split("-", 1)
                sh, sm = map(int, start.split(":"))
                eh, em = map(int, end.split(":"))
                if sh*60+sm <= minute < eh*60+em:
                    return conditions.get("peak_multiplier", "1")
        return "1"

    @staticmethod
    def _period_bounds(period: str, now: datetime) -> tuple[datetime, datetime]:
        now = BillingService._as_utc(now)
        if period == "calendar_day_utc":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start, start + timedelta(days=1)
        if period == "calendar_month_utc":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start, start.replace(year=start.year+1, month=1) if start.month == 12 else start.replace(month=start.month+1)
        raise AppError(422, "invalid_budget_period", "Unsupported budget period")

    @staticmethod
    def _normalized_scope(provider_id: str, model_id: str, feature: str) -> tuple[str, str, str]:
        return tuple(value.strip() or "*" for value in (provider_id, model_id, feature))

    @staticmethod
    def _scope_matches(configured: str, actual: str) -> bool:
        return configured == "*" or configured == actual

    @staticmethod
    def _validate_limits(soft: Any, hard: Any) -> None:
        if soft is None and hard is None:
            raise AppError(422, "budget_limit_required", "At least one budget limit is required")
        if soft is not None:
            decimal_amount(soft)
        if hard is not None:
            decimal_amount(hard)
        if soft is not None and hard is not None and decimal_amount(soft) > decimal_amount(hard):
            raise AppError(422, "invalid_budget_limits", "Soft limit cannot exceed hard limit")

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
