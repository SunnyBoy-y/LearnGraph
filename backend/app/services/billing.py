from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    BudgetAlert,
    BudgetPolicy,
    ExchangeRateVersion,
    PriceVersion,
    ProviderConfig,
    UsageEvent,
    utc_now,
)
from app.services.pricing_catalog import PRICING_CATALOG
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    BudgetAlertRepository,
    BudgetPolicyRepository,
    ExchangeRateVersionRepository,
    PriceVersionRepository,
    UsageRepository,
)


DEFAULT_USD_CNY_RATE = Decimal("6.7704")


@dataclass(frozen=True)
class BillingQuote:
    provider_id: str
    model_id: str
    feature: str
    remote_capability: bool
    price_version_id: str | None
    exchange_rate_version_id: str | None
    input_usd_per_million: float
    cached_input_usd_per_million: float
    price_multiplier: float
    output_usd_per_million: float
    fixed_usd_per_call: float
    pricing_currency: str
    input_cny_per_million: float
    cached_input_cny_per_million: float
    output_cny_per_million: float
    fixed_cny_per_call: float
    usd_cny_rate: float
    projected_cost_usd: float
    projected_cost_cny: float
    quoted_at: str

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, value: dict[str, Any]) -> BillingQuote:
        return cls(
            provider_id=str(value.get("provider_id") or "unknown"),
            model_id=str(value.get("model_id") or "unknown"),
            feature=str(value.get("feature") or "unknown"),
            remote_capability=bool(value.get("remote_capability", True)),
            price_version_id=value.get("price_version_id"),
            exchange_rate_version_id=value.get("exchange_rate_version_id"),
            input_usd_per_million=float(value.get("input_usd_per_million") or 0),
            cached_input_usd_per_million=float(value.get("cached_input_usd_per_million") or value.get("input_usd_per_million") or 0),
            price_multiplier=float(value.get("price_multiplier") or 1),
            output_usd_per_million=float(value.get("output_usd_per_million") or 0),
            fixed_usd_per_call=float(value.get("fixed_usd_per_call") or 0),
            pricing_currency=str(value.get("pricing_currency") or "USD").upper(),
            input_cny_per_million=float(value.get("input_cny_per_million") or 0),
            cached_input_cny_per_million=float(value.get("cached_input_cny_per_million") or 0),
            output_cny_per_million=float(value.get("output_cny_per_million") or 0),
            fixed_cny_per_call=float(value.get("fixed_cny_per_call") or 0),
            usd_cny_rate=float(value.get("usd_cny_rate") or DEFAULT_USD_CNY_RATE),
            projected_cost_usd=float(value.get("projected_cost_usd") or 0),
            projected_cost_cny=float(value.get("projected_cost_cny") or 0),
            quoted_at=str(value.get("quoted_at") or utc_now().isoformat()),
        )


class BillingService:
    """Versioned pricing, immutable usage snapshots, and pre-call budgets."""

    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str = "system:billing",
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.prices = PriceVersionRepository(db, workspace_id)
        self.rates = ExchangeRateVersionRepository(db, workspace_id)
        self.policies = BudgetPolicyRepository(db, workspace_id)
        self.alerts = BudgetAlertRepository(db, workspace_id)
        self.usage = UsageRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def list_prices(self) -> list[PriceVersion]:
        return list(
            self.db.scalars(
                self.prices.query().order_by(
                    PriceVersion.effective_at.desc(),
                    PriceVersion.version.desc(),
                )
            ).all()
        )

    def create_price(
        self,
        *,
        provider_id: str,
        model_id: str,
        feature: str,
        input_usd_per_million: float,
        cached_input_usd_per_million: float | None = None,
        cache_write_usd_per_million: float | None = None,
        output_usd_per_million: float,
        fixed_usd_per_call: float,
        effective_at: datetime | None,
        source: str,
        conditions: dict[str, Any] | None = None,
        currency: str = "USD",
        input_cny_per_million: float | None = None,
        cached_input_cny_per_million: float | None = None,
        cache_write_cny_per_million: float | None = None,
        output_cny_per_million: float | None = None,
        fixed_cny_per_call: float | None = None,
    ) -> PriceVersion:
        scope = self._normalized_scope(provider_id, model_id, feature)
        pricing_currency = currency.strip().upper()
        if pricing_currency not in {"USD", "CNY"}:
            raise AppError(422, "invalid_pricing_currency", "Pricing currency must be USD or CNY")
        stored_conditions = dict(conditions or {})
        stored_conditions["pricing_currency"] = pricing_currency
        if pricing_currency == "CNY":
            native_values = {
                "input_cny_per_million": input_cny_per_million,
                "cached_input_cny_per_million": cached_input_cny_per_million,
                "cache_write_cny_per_million": cache_write_cny_per_million,
                "output_cny_per_million": output_cny_per_million,
                "fixed_cny_per_call": fixed_cny_per_call,
            }
            if native_values["input_cny_per_million"] is None or native_values[
                "output_cny_per_million"
            ] is None:
                raise AppError(
                    422,
                    "cny_price_required",
                    "CNY pricing requires input and output CNY rates",
                )
            if any(
                value is not None and float(value) < 0
                for value in native_values.values()
            ):
                raise AppError(422, "invalid_price", "CNY price rates cannot be negative")
            stored_conditions.update(native_values)
            # Keep the existing USD columns populated for legacy views and
            # external exports. Billing uses the native CNY values above, so a
            # later exchange-rate update never rewrites a provider's CNY list
            # price.
            input_usd_per_million = float(input_cny_per_million) / float(
                DEFAULT_USD_CNY_RATE
            )
            cached_input_usd_per_million = (
                float(cached_input_cny_per_million) / float(DEFAULT_USD_CNY_RATE)
                if cached_input_cny_per_million is not None
                else None
            )
            cache_write_usd_per_million = (
                float(cache_write_cny_per_million) / float(DEFAULT_USD_CNY_RATE)
                if cache_write_cny_per_million is not None
                else None
            )
            output_usd_per_million = float(output_cny_per_million) / float(
                DEFAULT_USD_CNY_RATE
            )
            fixed_usd_per_call = float(fixed_cny_per_call or 0) / float(
                DEFAULT_USD_CNY_RATE
            )
        version = (
            self.db.scalar(
                select(func.max(PriceVersion.version)).where(
                    PriceVersion.workspace_id == self.workspace_id,
                    PriceVersion.provider_id == scope[0],
                    PriceVersion.model_id == scope[1],
                    PriceVersion.feature == scope[2],
                )
            )
            or 0
        ) + 1
        price = self.prices.add(
            PriceVersion(
                workspace_id=self.workspace_id,
                provider_id=scope[0],
                model_id=scope[1],
                feature=scope[2],
                version=version,
                input_usd_per_million=input_usd_per_million,
                cached_input_usd_per_million=cached_input_usd_per_million,
                cache_write_usd_per_million=cache_write_usd_per_million,
                output_usd_per_million=output_usd_per_million,
                fixed_usd_per_call=fixed_usd_per_call,
                effective_at=self._as_utc(effective_at or utc_now()),
                source=source,
                conditions=stored_conditions,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="usage.price_version.created",
            resource_type="price_version",
            resource_id=price.id,
            details={"scope": list(scope), "version": version},
        )
        self.db.commit()
        self.db.refresh(price)
        return price

    def retire_price(self, price_id: str, *, retired_at: datetime | None = None) -> PriceVersion:
        price = self.prices.require(price_id, "price version")
        if price.retired_at is None:
            price.retired_at = self._as_utc(retired_at or utc_now())
            self.audit.record(
                actor_id=self.actor_id,
                action="usage.price_version.retired",
                resource_type="price_version",
                resource_id=price.id,
            )
            self.db.commit()
            self.db.refresh(price)
        return price

    def list_exchange_rates(self) -> list[ExchangeRateVersion]:
        return list(
            self.db.scalars(
                self.rates.query().order_by(
                    ExchangeRateVersion.effective_at.desc(),
                    ExchangeRateVersion.version.desc(),
                )
            ).all()
        )

    def create_exchange_rate(
        self,
        *,
        rate: float,
        effective_at: datetime | None,
        source: str,
        base_currency: str = "USD",
        quote_currency: str = "CNY",
    ) -> ExchangeRateVersion:
        base = base_currency.strip().upper()
        quote = quote_currency.strip().upper()
        version = (
            self.db.scalar(
                select(func.max(ExchangeRateVersion.version)).where(
                    ExchangeRateVersion.workspace_id == self.workspace_id,
                    ExchangeRateVersion.base_currency == base,
                    ExchangeRateVersion.quote_currency == quote,
                )
            )
            or 0
        ) + 1
        item = self.rates.add(
            ExchangeRateVersion(
                workspace_id=self.workspace_id,
                base_currency=base,
                quote_currency=quote,
                version=version,
                rate=rate,
                effective_at=self._as_utc(effective_at or utc_now()),
                source=source,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="usage.exchange_rate.created",
            resource_type="exchange_rate_version",
            resource_id=item.id,
            details={"pair": f"{base}/{quote}", "version": version},
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def retire_exchange_rate(
        self,
        rate_id: str,
        *,
        retired_at: datetime | None = None,
    ) -> ExchangeRateVersion:
        item = self.rates.require(rate_id, "exchange rate version")
        if item.retired_at is None:
            item.retired_at = self._as_utc(retired_at or utc_now())
            self.audit.record(
                actor_id=self.actor_id,
                action="usage.exchange_rate.retired",
                resource_type="exchange_rate_version",
                resource_id=item.id,
            )
            self.db.commit()
            self.db.refresh(item)
        return item

    def list_budget_policies(self) -> list[BudgetPolicy]:
        return list(
            self.db.scalars(
                self.policies.query().order_by(BudgetPolicy.created_at, BudgetPolicy.id)
            ).all()
        )

    def create_budget_policy(
        self,
        *,
        name: str,
        provider_id: str,
        model_id: str,
        feature: str,
        period: str,
        soft_limit_cny: float | None,
        hard_limit_cny: float | None,
        enabled: bool,
    ) -> BudgetPolicy:
        self._validate_limits(soft_limit_cny, hard_limit_cny)
        scope = self._normalized_scope(provider_id, model_id, feature)
        existing = self.db.scalar(
            self.policies.query().where(
                BudgetPolicy.provider_id == scope[0],
                BudgetPolicy.model_id == scope[1],
                BudgetPolicy.feature == scope[2],
                BudgetPolicy.period == period,
            )
        )
        if existing is not None:
            raise AppError(
                409,
                "budget_policy_scope_exists",
                "A budget policy already exists for this scope and period",
            )
        policy = self.policies.add(
            BudgetPolicy(
                workspace_id=self.workspace_id,
                name=name,
                provider_id=scope[0],
                model_id=scope[1],
                feature=scope[2],
                period=period,
                soft_limit_cny=soft_limit_cny,
                hard_limit_cny=hard_limit_cny,
                enabled=enabled,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="usage.budget_policy.created",
            resource_type="budget_policy",
            resource_id=policy.id,
            details={"scope": list(scope), "period": period},
        )
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def update_budget_policy(
        self,
        policy_id: str,
        *,
        name: str,
        soft_limit_cny: float | None,
        hard_limit_cny: float | None,
        enabled: bool,
    ) -> BudgetPolicy:
        self._validate_limits(soft_limit_cny, hard_limit_cny)
        policy = self.policies.require(policy_id, "budget policy")
        policy.name = name
        policy.soft_limit_cny = soft_limit_cny
        policy.hard_limit_cny = hard_limit_cny
        policy.enabled = enabled
        self.audit.record(
            actor_id=self.actor_id,
            action="usage.budget_policy.updated",
            resource_type="budget_policy",
            resource_id=policy.id,
        )
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def delete_budget_policy(self, policy_id: str) -> None:
        policy = self.policies.require(policy_id, "budget policy")
        self.audit.record(
            actor_id=self.actor_id,
            action="usage.budget_policy.deleted",
            resource_type="budget_policy",
            resource_id=policy.id,
        )
        self.db.delete(policy)
        self.db.commit()

    def list_alerts(self) -> list[BudgetAlert]:
        return list(
            self.db.scalars(
                self.alerts.query().order_by(BudgetAlert.created_at.desc())
            ).all()
        )

    def acknowledge_alert(self, alert_id: str) -> BudgetAlert:
        alert = self.alerts.require(alert_id, "budget alert")
        if alert.status != "acknowledged":
            alert.status = "acknowledged"
            alert.acknowledged_at = utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="usage.budget_alert.acknowledged",
                resource_type="budget_alert",
                resource_id=alert.id,
            )
            self.db.commit()
            self.db.refresh(alert)
        return alert

    def budget_statuses(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = self._as_utc(now or utc_now())
        result: list[dict[str, Any]] = []
        for policy in self.list_budget_policies():
            start, end = self._period_bounds(policy.period, current)
            spent = self._spent_for_policy(policy, start, end)
            result.append(
                {
                    "policy_id": policy.id,
                    "name": policy.name,
                    "provider_id": policy.provider_id,
                    "model_id": policy.model_id,
                    "feature": policy.feature,
                    "period": policy.period,
                    "period_start": start,
                    "period_end": end,
                    "spent_cny": spent,
                    "soft_limit_cny": policy.soft_limit_cny,
                    "hard_limit_cny": policy.hard_limit_cny,
                    "soft_exceeded": bool(
                        policy.soft_limit_cny is not None
                        and spent >= policy.soft_limit_cny
                    ),
                    "hard_exceeded": bool(
                        policy.hard_limit_cny is not None
                        and spent >= policy.hard_limit_cny
                    ),
                    "enabled": policy.enabled,
                }
            )
        return result

    def preflight_model_call(
        self,
        *,
        provider_id: str,
        model_id: str,
        feature: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        remote_capability: bool,
        now: datetime | None = None,
    ) -> BillingQuote:
        current = self._as_utc(now or utc_now())
        if not remote_capability:
            return BillingQuote(
                provider_id=provider_id,
                model_id=model_id,
                feature=feature,
                remote_capability=False,
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
                usd_cny_rate=0,
                projected_cost_usd=0,
                projected_cost_cny=0,
                quoted_at=current.isoformat(),
            )
        policies = self._matching_policies(provider_id, model_id, feature)
        self._block_if_already_exhausted(
            policies,
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            now=current,
        )
        price = self._active_price(provider_id, model_id, feature, current, max(0, estimated_input_tokens))
        if price is None:
            price = self._seed_default_catalog_price(
                provider_id=provider_id,
                model_id=model_id,
                feature=feature,
                now=current,
                input_tokens=max(0, estimated_input_tokens),
            )
        multiplier = self._price_multiplier(price, current) if price else 1.0
        rate = self._active_exchange_rate(current)
        if price is None and policies:
            self.db.commit()
            raise AppError(
                409,
                "usage_price_required",
                "A matching price version is required before a budgeted remote call",
                {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "feature": feature,
                },
            )
        pricing_currency, native_cny_rates = self._native_cny_rates(price)
        if price is not None and pricing_currency == "CNY":
            projected_cny = self._priced_cost_usd(
                input_tokens=max(0, estimated_input_tokens),
                output_tokens=max(0, estimated_output_tokens),
                input_rate=native_cny_rates["input"],
                output_rate=native_cny_rates["output"],
                fixed=native_cny_rates["fixed"],
            ) * multiplier
            projected_usd = projected_cny / rate.rate if rate.rate else 0.0
        else:
            projected_usd = self._priced_cost_usd(
                input_tokens=max(0, estimated_input_tokens),
                output_tokens=max(0, estimated_output_tokens),
                input_rate=price.input_usd_per_million if price else 0,
                output_rate=price.output_usd_per_million if price else 0,
                fixed=price.fixed_usd_per_call if price else 0,
            ) * multiplier
            projected_cny = projected_usd * rate.rate
        self._evaluate_policies(
            policies,
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            projected_cost_cny=projected_cny,
            now=current,
        )
        return BillingQuote(
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            remote_capability=True,
            price_version_id=price.id if price else None,
            exchange_rate_version_id=rate.id,
            input_usd_per_million=price.input_usd_per_million if price else 0,
            cached_input_usd_per_million=(price.cached_input_usd_per_million if price and price.cached_input_usd_per_million is not None else price.input_usd_per_million if price else 0),
            price_multiplier=multiplier,
            output_usd_per_million=price.output_usd_per_million if price else 0,
            fixed_usd_per_call=price.fixed_usd_per_call if price else 0,
            pricing_currency=pricing_currency,
            input_cny_per_million=native_cny_rates["input"],
            cached_input_cny_per_million=native_cny_rates["cached_input"],
            output_cny_per_million=native_cny_rates["output"],
            fixed_cny_per_call=native_cny_rates["fixed"],
            usd_cny_rate=rate.rate,
            projected_cost_usd=projected_usd,
            projected_cost_cny=projected_cny,
            quoted_at=current.isoformat(),
        )

    def preflight_research_call(
        self,
        *,
        provider_id: str,
        estimated_cost_cny: float,
        now: datetime | None = None,
    ) -> BillingQuote:
        current = self._as_utc(now or utc_now())
        model_id = "deep-research"
        feature = "deep_research"
        policies = self._matching_policies(provider_id, model_id, feature)
        self._block_if_already_exhausted(
            policies,
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            now=current,
        )
        price = self._active_price(provider_id, model_id, feature, current, 0)
        rate = self._active_exchange_rate(current)
        projected_cny = max(0.0, estimated_cost_cny)
        projected_usd = projected_cny / rate.rate if rate.rate else 0.0
        self._evaluate_policies(
            policies,
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            projected_cost_cny=projected_cny,
            now=current,
        )
        return BillingQuote(
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            remote_capability=True,
            price_version_id=price.id if price else None,
            exchange_rate_version_id=rate.id,
            input_usd_per_million=price.input_usd_per_million if price else 0,
            cached_input_usd_per_million=(price.cached_input_usd_per_million if price and price.cached_input_usd_per_million is not None else price.input_usd_per_million if price else 0),
            price_multiplier=self._price_multiplier(price, current) if price else 1,
            output_usd_per_million=price.output_usd_per_million if price else 0,
            fixed_usd_per_call=price.fixed_usd_per_call if price else 0,
            pricing_currency="USD",
            input_cny_per_million=0,
            cached_input_cny_per_million=0,
            output_cny_per_million=0,
            fixed_cny_per_call=0,
            usd_cny_rate=rate.rate,
            projected_cost_usd=projected_usd,
            projected_cost_cny=projected_cny,
            quoted_at=current.isoformat(),
        )

    def record_usage(
        self,
        quote: BillingQuote,
        *,
        input_tokens: int,
        output_tokens: int,
        attempt: int,
        cached_input_tokens: int = 0,
        reasoning_tokens: int = 0,
        latency_ms: int = 0,
        provider_reported_cost_cny: float | None = None,
        usage_reported: bool = True,
    ) -> UsageEvent:
        normalized_input = max(0, int(input_tokens))
        normalized_output = max(0, int(output_tokens))
        normalized_cached = min(normalized_input, max(0, int(cached_input_tokens)))
        normalized_reasoning = min(normalized_output, max(0, int(reasoning_tokens)))
        if not quote.remote_capability:
            cost_usd = 0.0
            cost_cny = 0.0
            status = "non_billable"
        elif provider_reported_cost_cny is not None:
            cost_cny = max(0.0, float(provider_reported_cost_cny))
            cost_usd = cost_cny / quote.usd_cny_rate if quote.usd_cny_rate else 0.0
            status = "provider_reported"
        elif quote.price_version_id is not None and not usage_reported:
            # The provider omitted token usage. Preserve a clearly labelled,
            # conservative pre-call projection so an unknown bill does not
            # become a false zero or silently bypass the hard budget.
            cost_usd = quote.projected_cost_usd
            cost_cny = quote.projected_cost_cny
            status = "estimated_usage_missing"
        elif quote.price_version_id is not None:
            if quote.pricing_currency == "CNY":
                cost_cny = self._priced_cost_usd(
                    input_tokens=normalized_input - normalized_cached,
                    output_tokens=normalized_output,
                    input_rate=quote.input_cny_per_million,
                    output_rate=quote.output_cny_per_million,
                    fixed=quote.fixed_cny_per_call,
                ) + (
                    normalized_cached
                    * quote.cached_input_cny_per_million
                    / 1_000_000
                )
                cost_cny *= quote.price_multiplier
                cost_usd = cost_cny / quote.usd_cny_rate if quote.usd_cny_rate else 0.0
            else:
                cost_usd = self._priced_cost_usd(
                    input_tokens=normalized_input - normalized_cached,
                    output_tokens=normalized_output,
                    input_rate=quote.input_usd_per_million,
                    output_rate=quote.output_usd_per_million,
                    fixed=quote.fixed_usd_per_call,
                ) + normalized_cached * quote.cached_input_usd_per_million / 1_000_000
                cost_usd *= quote.price_multiplier
                cost_cny = cost_usd * quote.usd_cny_rate
            status = "priced"
        else:
            cost_usd = 0.0
            cost_cny = 0.0
            status = "unpriced"
        return self.usage.add(
            UsageEvent(
                workspace_id=self.workspace_id,
                provider_id=quote.provider_id,
                model_id=quote.model_id,
                feature=quote.feature,
                input_tokens=normalized_input,
                cached_input_tokens=normalized_cached,
                output_tokens=normalized_output,
                reasoning_tokens=normalized_reasoning,
                total_tokens=normalized_input + normalized_output,
                attempt=max(1, int(attempt)),
                cost_usd=cost_usd,
                cost_cny=cost_cny,
                cost_status=status,
                price_version_id=quote.price_version_id,
                exchange_rate_version_id=quote.exchange_rate_version_id,
                input_usd_per_million=quote.input_usd_per_million,
                cached_input_usd_per_million=quote.cached_input_usd_per_million,
                price_multiplier=quote.price_multiplier,
                output_usd_per_million=quote.output_usd_per_million,
                fixed_usd_per_call=quote.fixed_usd_per_call,
                usd_cny_rate=quote.usd_cny_rate,
                latency_ms=max(0, int(latency_ms)),
                created_at=self._as_utc(datetime.fromisoformat(quote.quoted_at)),
            )
        )

    def _active_price(
        self,
        provider_id: str,
        model_id: str,
        feature: str,
        now: datetime,
        input_tokens: int = 0,
    ) -> PriceVersion | None:
        candidates = list(
            self.db.scalars(
                self.prices.query().where(
                    PriceVersion.provider_id.in_([provider_id, "*"]),
                    PriceVersion.model_id.in_([model_id, "*"]),
                    PriceVersion.feature.in_([feature, "*"]),
                    PriceVersion.effective_at <= now,
                )
            ).all()
        )
        candidates = [
            item
            for item in candidates
            if item.retired_at is None or self._as_utc(item.retired_at) > now
        ]
        candidates = [item for item in candidates if self._conditions_match(item.conditions or {}, input_tokens)]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                int(item.provider_id == provider_id)
                + int(item.model_id == model_id)
                + int(item.feature == feature),
                self._as_utc(item.effective_at),
                item.version,
            ),
        )

    @staticmethod
    def _provider_catalog_key(provider: ProviderConfig) -> str | None:
        declared = (provider.capabilities or {}).get("pricing_provider_key")
        if isinstance(declared, str) and declared.strip():
            return declared.strip().casefold()
        if provider.provider_type == "openai_responses":
            return "openai"
        if provider.provider_type == "deepseek_chat":
            return "deepseek"
        try:
            hostname = (urlparse(provider.base_url or "").hostname or "").casefold()
        except ValueError:
            hostname = ""
        if hostname == "api.openai.com":
            return "openai"
        if hostname == "api.deepseek.com":
            return "deepseek"
        if hostname.endswith("dashscope.aliyuncs.com"):
            return "qwen"
        if hostname.endswith("moonshot.cn") or hostname.endswith("kimi.com"):
            return "kimi"
        if hostname.endswith("z.ai") or hostname.endswith("bigmodel.cn"):
            return "zai"
        if hostname.endswith("minimax.io"):
            return "minimax"
        return None

    def provider_ids_for_catalog_key(self, provider_key: str) -> list[str]:
        """Return configured provider instance IDs matching a catalog vendor."""

        expected = provider_key.strip().casefold()
        if not expected:
            return []
        providers = self.db.scalars(
            select(ProviderConfig).where(
                ProviderConfig.workspace_id == self.workspace_id,
            )
        ).all()
        return [
            provider.id
            for provider in providers
            if self._provider_catalog_key(provider) == expected
        ]

    def _seed_default_catalog_price(
        self,
        *,
        provider_id: str,
        model_id: str,
        feature: str,
        now: datetime,
        input_tokens: int,
    ) -> PriceVersion | None:
        """Materialize a known public list price for the real provider instance.

        Price catalog rows are vendor templates; usage events are scoped to a
        workspace ProviderConfig UUID.  Persisting the selected template under
        that UUID removes the former manual-import prerequisite without making
        a global provider-key row silently match unrelated configurations.
        """

        provider = self.db.scalar(
            select(ProviderConfig).where(
                ProviderConfig.workspace_id == self.workspace_id,
                ProviderConfig.id == provider_id,
            )
        )
        if provider is None:
            return None
        provider_key = self._provider_catalog_key(provider)
        if provider_key is None:
            return None
        matches = [
            item
            for item in PRICING_CATALOG
            if item.get("provider_key") == provider_key
            and item.get("model_id") == model_id
            and self._conditions_match(
                dict(item.get("conditions") or {}),
                input_tokens,
            )
        ]
        if not matches:
            return None
        # The catalog can contain context tiers. The last matching entry is
        # deterministic and catalog ordering deliberately keeps a more
        # specific later row after a generic one.
        item = matches[-1]
        scope = self._normalized_scope(provider_id, model_id, feature)
        version = (
            self.db.scalar(
                select(func.max(PriceVersion.version)).where(
                    PriceVersion.workspace_id == self.workspace_id,
                    PriceVersion.provider_id == scope[0],
                    PriceVersion.model_id == scope[1],
                    PriceVersion.feature == scope[2],
                )
            )
            or 0
        ) + 1
        conditions = dict(item.get("conditions") or {})
        currency = str(item.get("currency") or "USD").upper()
        conditions.update(
            {
                "pricing_currency": currency,
                "catalog_id": item.get("catalog_id"),
                "catalog_provider_key": provider_key,
                "catalog_as_of": item.get("as_of"),
                "catalog_source_url": item.get("source_url"),
            }
        )
        if currency == "CNY":
            conditions.update(
                {
                    "input_cny_per_million": item.get("native_input_per_million"),
                    "cached_input_cny_per_million": item.get(
                        "native_cached_input_per_million"
                    ),
                    "cache_write_cny_per_million": item.get(
                        "native_cache_write_per_million"
                    ),
                    "output_cny_per_million": item.get("native_output_per_million"),
                    "fixed_cny_per_call": 0,
                }
            )
        price = self.prices.add(
            PriceVersion(
                workspace_id=self.workspace_id,
                provider_id=scope[0],
                model_id=scope[1],
                feature=scope[2],
                version=version,
                input_usd_per_million=float(item.get("input_usd_per_million") or 0),
                cached_input_usd_per_million=(
                    float(item["cached_input_usd_per_million"])
                    if item.get("cached_input_usd_per_million") is not None
                    else None
                ),
                cache_write_usd_per_million=(
                    float(item["cache_write_usd_per_million"])
                    if item.get("cache_write_usd_per_million") is not None
                    else None
                ),
                output_usd_per_million=float(item.get("output_usd_per_million") or 0),
                fixed_usd_per_call=0,
                effective_at=now,
                source=f"official_catalog:{item.get('source_url') or 'unknown'}",
                conditions=conditions,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="usage.price_version.catalog_default_loaded",
            resource_type="price_version",
            resource_id=price.id,
            details={
                "scope": list(scope),
                "catalog_id": item.get("catalog_id"),
                "currency": currency,
            },
        )
        self.db.flush()
        return price

    @staticmethod
    def _native_cny_rates(price: PriceVersion | None) -> tuple[str, dict[str, float]]:
        if price is None:
            return "USD", {
                "input": 0.0,
                "cached_input": 0.0,
                "output": 0.0,
                "fixed": 0.0,
            }
        conditions = dict(price.conditions or {})
        currency = str(conditions.get("pricing_currency") or "USD").upper()
        if currency != "CNY":
            return "USD", {
                "input": 0.0,
                "cached_input": 0.0,
                "output": 0.0,
                "fixed": 0.0,
            }

        def number(key: str, fallback: float = 0.0) -> float:
            value = conditions.get(key, fallback)
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return fallback

        input_rate = number("input_cny_per_million")
        return "CNY", {
            "input": input_rate,
            "cached_input": number("cached_input_cny_per_million", input_rate),
            "output": number("output_cny_per_million"),
            "fixed": number("fixed_cny_per_call"),
        }

    @staticmethod
    def _conditions_match(conditions: dict[str, Any], input_tokens: int) -> bool:
        minimum = conditions.get("min_input_tokens")
        maximum = conditions.get("max_input_tokens")
        return not ((minimum is not None and input_tokens < int(minimum)) or (maximum is not None and input_tokens > int(maximum)))

    @staticmethod
    def _price_multiplier(price: PriceVersion, now: datetime) -> float:
        conditions = price.conditions or {}
        windows = conditions.get("peak_windows") or []
        if not windows:
            return 1.0
        zone_name = str(conditions.get("time_zone") or "UTC")
        # Asia/Shanghai has no DST in the supported billing period.  Keeping
        # this common tariff zone explicit also works on Windows hosts where
        # the optional IANA tzdata package is not installed.
        if zone_name == "Asia/Shanghai":
            local = now.astimezone(timezone(timedelta(hours=8)))
        else:
            from zoneinfo import ZoneInfo
            local = now.astimezone(ZoneInfo(zone_name))
        minute = local.hour * 60 + local.minute
        for window in windows:
            start, end = str(window).split("-", 1)
            sh, sm = (int(part) for part in start.split(":"))
            eh, em = (int(part) for part in end.split(":"))
            if sh * 60 + sm <= minute < eh * 60 + em:
                return float(conditions.get("peak_multiplier") or 1)
        return 1.0

    def _active_exchange_rate(self, now: datetime) -> ExchangeRateVersion:
        candidates = list(
            self.db.scalars(
                self.rates.query().where(
                    ExchangeRateVersion.base_currency == "USD",
                    ExchangeRateVersion.quote_currency == "CNY",
                    ExchangeRateVersion.effective_at <= now,
                )
            ).all()
        )
        candidates = [
            item
            for item in candidates
            if item.retired_at is None or self._as_utc(item.retired_at) > now
        ]
        if candidates:
            return max(
                candidates,
                key=lambda item: (self._as_utc(item.effective_at), item.version),
            )
        version = (
            self.db.scalar(
                select(func.max(ExchangeRateVersion.version)).where(
                    ExchangeRateVersion.workspace_id == self.workspace_id,
                    ExchangeRateVersion.base_currency == "USD",
                    ExchangeRateVersion.quote_currency == "CNY",
                )
            )
            or 0
        ) + 1
        fallback = self.rates.add(
            ExchangeRateVersion(
                workspace_id=self.workspace_id,
                base_currency="USD",
                quote_currency="CNY",
                version=version,
                rate=float(DEFAULT_USD_CNY_RATE),
                effective_at=now,
                source="wise_reference_2026-07-14",
            )
        )
        self.db.flush()
        return fallback

    def _matching_policies(
        self,
        provider_id: str,
        model_id: str,
        feature: str,
    ) -> list[BudgetPolicy]:
        policies = list(
            self.db.scalars(
                self.policies.query().where(BudgetPolicy.enabled.is_(True))
            ).all()
        )
        return [
            policy
            for policy in policies
            if self._scope_matches(policy.provider_id, provider_id)
            and self._scope_matches(policy.model_id, model_id)
            and self._scope_matches(policy.feature, feature)
        ]

    def _block_if_already_exhausted(
        self,
        policies: Iterable[BudgetPolicy],
        *,
        provider_id: str,
        model_id: str,
        feature: str,
        now: datetime,
    ) -> None:
        for policy in policies:
            if policy.hard_limit_cny is None:
                continue
            start, end = self._period_bounds(policy.period, now)
            spent = self._spent_for_policy(policy, start, end)
            if spent >= policy.hard_limit_cny:
                self._persist_alert(
                    policy,
                    level="hard",
                    provider_id=provider_id,
                    model_id=model_id,
                    feature=feature,
                    start=start,
                    end=end,
                    spent=spent,
                    projected=0,
                    limit=policy.hard_limit_cny,
                )
                self.db.commit()
                raise self._hard_limit_error(policy, spent, 0)

    def _evaluate_policies(
        self,
        policies: Iterable[BudgetPolicy],
        *,
        provider_id: str,
        model_id: str,
        feature: str,
        projected_cost_cny: float,
        now: datetime,
    ) -> None:
        hard_error: AppError | None = None
        changed = False
        for policy in policies:
            start, end = self._period_bounds(policy.period, now)
            spent = self._spent_for_policy(policy, start, end)
            projected_total = spent + projected_cost_cny
            if (
                policy.soft_limit_cny is not None
                and projected_total >= policy.soft_limit_cny
            ):
                self._persist_alert(
                    policy,
                    level="soft",
                    provider_id=provider_id,
                    model_id=model_id,
                    feature=feature,
                    start=start,
                    end=end,
                    spent=spent,
                    projected=projected_cost_cny,
                    limit=policy.soft_limit_cny,
                )
                changed = True
            if (
                policy.hard_limit_cny is not None
                and projected_total > policy.hard_limit_cny
            ):
                self._persist_alert(
                    policy,
                    level="hard",
                    provider_id=provider_id,
                    model_id=model_id,
                    feature=feature,
                    start=start,
                    end=end,
                    spent=spent,
                    projected=projected_cost_cny,
                    limit=policy.hard_limit_cny,
                )
                changed = True
                hard_error = self._hard_limit_error(
                    policy,
                    spent,
                    projected_cost_cny,
                )
        if changed:
            self.db.commit()
        if hard_error is not None:
            raise hard_error

    def _persist_alert(
        self,
        policy: BudgetPolicy,
        *,
        level: str,
        provider_id: str,
        model_id: str,
        feature: str,
        start: datetime,
        end: datetime,
        spent: float,
        projected: float,
        limit: float,
    ) -> BudgetAlert:
        alert = self.db.scalar(
            self.alerts.query().where(
                BudgetAlert.policy_id == policy.id,
                BudgetAlert.level == level,
                BudgetAlert.period_start == start,
            )
        )
        if alert is None:
            alert = self.alerts.add(
                BudgetAlert(
                    workspace_id=self.workspace_id,
                    policy_id=policy.id,
                    level=level,
                    status="open",
                    provider_id=provider_id,
                    model_id=model_id,
                    feature=feature,
                    period_start=start,
                    period_end=end,
                    spent_cny=spent,
                    projected_cost_cny=projected,
                    limit_cny=limit,
                )
            )
            self.db.flush()
        else:
            alert.status = "open"
            alert.acknowledged_at = None
            alert.spent_cny = spent
            alert.projected_cost_cny = projected
            alert.limit_cny = limit
        self.audit.record(
            actor_id=self.actor_id,
            action=f"usage.budget.{level}",
            resource_type="budget_policy",
            resource_id=policy.id,
            outcome="blocked" if level == "hard" else "warning",
            details={
                "spent_cny": spent,
                "projected_cost_cny": projected,
                "limit_cny": limit,
                "provider_id": provider_id,
                "model_id": model_id,
                "feature": feature,
            },
        )
        return alert

    def _spent_for_policy(
        self,
        policy: BudgetPolicy,
        start: datetime,
        end: datetime,
    ) -> float:
        conditions = [
            UsageEvent.workspace_id == self.workspace_id,
            UsageEvent.created_at >= start,
            UsageEvent.created_at < end,
        ]
        if policy.provider_id != "*":
            conditions.append(UsageEvent.provider_id == policy.provider_id)
        if policy.model_id != "*":
            conditions.append(UsageEvent.model_id == policy.model_id)
        if policy.feature != "*":
            conditions.append(UsageEvent.feature == policy.feature)
        return float(
            self.db.scalar(
                select(func.coalesce(func.sum(UsageEvent.cost_cny), 0.0)).where(
                    *conditions
                )
            )
            or 0.0
        )

    @staticmethod
    def _priced_cost_usd(
        *,
        input_tokens: int,
        output_tokens: int,
        input_rate: float,
        output_rate: float,
        fixed: float,
    ) -> float:
        value = (
            Decimal(max(0, input_tokens))
            * Decimal(str(max(0.0, input_rate)))
            / Decimal(1_000_000)
            + Decimal(max(0, output_tokens))
            * Decimal(str(max(0.0, output_rate)))
            / Decimal(1_000_000)
            + Decimal(str(max(0.0, fixed)))
        )
        return float(value)

    @staticmethod
    def _period_bounds(period: str, now: datetime) -> tuple[datetime, datetime]:
        if period == "calendar_day_utc":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start, start + timedelta(days=1)
        if period == "calendar_month_utc":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
            return start, end
        raise AppError(422, "invalid_budget_period", "Unsupported budget period")

    @staticmethod
    def _normalized_scope(
        provider_id: str,
        model_id: str,
        feature: str,
    ) -> tuple[str, str, str]:
        return tuple(
            value.strip() if value.strip() else "*"
            for value in (provider_id, model_id, feature)
        )  # type: ignore[return-value]

    @staticmethod
    def _scope_matches(configured: str, actual: str) -> bool:
        return configured == "*" or configured == actual

    @staticmethod
    def _validate_limits(soft: float | None, hard: float | None) -> None:
        if soft is None and hard is None:
            raise AppError(
                422,
                "budget_limit_required",
                "At least one soft or hard budget limit is required",
            )
        if soft is not None and hard is not None and soft > hard:
            raise AppError(
                422,
                "invalid_budget_limits",
                "Soft budget limit cannot exceed the hard limit",
            )

    @staticmethod
    def _hard_limit_error(
        policy: BudgetPolicy,
        spent: float,
        projected: float,
    ) -> AppError:
        return AppError(
            402,
            "budget_hard_limit_exceeded",
            "The remote provider call was blocked before execution by a workspace budget",
            {
                "policy_id": policy.id,
                "spent_cny": spent,
                "projected_cost_cny": projected,
                "hard_limit_cny": policy.hard_limit_cny,
            },
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
