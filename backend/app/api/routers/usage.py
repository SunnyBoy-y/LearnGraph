from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.management import (
    BudgetAlertView,
    BudgetPolicyCreateRequest,
    BudgetPolicyUpdateRequest,
    BudgetPolicyView,
    BudgetStatusView,
    ExchangeRateCreateRequest,
    ExchangeRateVersionView,
    PriceVersionCreateRequest,
    PriceCatalogApplyRequest,
    PriceCatalogItem,
    PriceVersionView,
    UsageEventView,
    UsageSummary,
    VersionRetireRequest,
)
from app.services.billing import BillingService
from app.services.management import UsageService
from app.services.pricing_catalog import PRICING_CATALOG, get_catalog_entry
from app.core.errors import AppError


router = APIRouter(prefix="/usage", tags=["usage"])


def billing_service(db: DB, context: CurrentWorkspace) -> BillingService:
    return BillingService(
        db,
        context.workspace_id,
        context.principal.user_id,
    )


@router.get("/summary", response_model=UsageSummary)
def usage_summary(
    db: DB,
    context: CurrentWorkspace,
    provider_id: str | None = None,
    model_id: str | None = None,
    feature: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> UsageSummary:
    return UsageService(db, context.workspace_id).summary(
        provider_id=provider_id,
        model_id=model_id,
        feature=feature,
        start_at=start_at,
        end_at=end_at,
    )


@router.get("/events", response_model=list[UsageEventView])
def usage_events(
    db: DB,
    context: CurrentWorkspace,
    provider_id: str | None = None,
    model_id: str | None = None,
    feature: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> list[UsageEventView]:
    return [
        UsageEventView.model_validate(item)
        for item in UsageService(db, context.workspace_id).events(
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            start_at=start_at,
            end_at=end_at,
        )
    ]


@router.delete("/events")
def clear_usage_events(
    db: DB,
    context: CurrentWorkspace,
) -> dict[str, int]:
    return UsageService(db, context.workspace_id).clear_events(
        actor_id=context.principal.user_id,
    )


@router.get("/prices", response_model=list[PriceVersionView])
def list_prices(db: DB, context: CurrentWorkspace) -> list[PriceVersionView]:
    return [
        PriceVersionView.model_validate(item)
        for item in billing_service(db, context).list_prices()
    ]


@router.get("/price-catalog", response_model=list[PriceCatalogItem])
def list_price_catalog(db: DB, context: CurrentWorkspace) -> list[PriceCatalogItem]:
    del db, context
    return [PriceCatalogItem.model_validate(item) for item in PRICING_CATALOG]


@router.post("/price-catalog/apply", response_model=PriceVersionView, status_code=status.HTTP_201_CREATED)
def apply_price_catalog(payload: PriceCatalogApplyRequest, db: DB, context: CurrentWorkspace) -> PriceVersionView:
    item = get_catalog_entry(payload.catalog_id)
    if item is None:
        raise AppError(404, "price_catalog_item_not_found", "Price catalog item not found")
    service = billing_service(db, context)
    provider_id = payload.provider_id
    if provider_id is None:
        matching_provider_ids = service.provider_ids_for_catalog_key(
            str(item["provider_key"])
        )
        if len(matching_provider_ids) != 1:
            raise AppError(
                409,
                "provider_instance_required",
                "Select exactly one configured Provider instance before importing this catalog price",
                {
                    "provider_key": item["provider_key"],
                    "matching_provider_ids": matching_provider_ids,
                },
            )
        provider_id = matching_provider_ids[0]

    def selected(name: str) -> float | None:
        override = getattr(payload, name)
        return override if override is not None else item[name]
    currency = str(item["currency"])
    price = service.create_price(
        provider_id=provider_id,
        model_id=item["model_id"],
        feature=payload.feature,
        input_usd_per_million=selected("input_usd_per_million") or 0,
        cached_input_usd_per_million=selected("cached_input_usd_per_million"),
        cache_write_usd_per_million=selected("cache_write_usd_per_million"),
        output_usd_per_million=selected("output_usd_per_million") or 0,
        fixed_usd_per_call=0,
        effective_at=None,
        source=f"official_catalog:{item['source_url']}",
        conditions=item["conditions"],
        currency=currency,
        input_cny_per_million=(
            float(item["native_input_per_million"])
            if currency == "CNY"
            else None
        ),
        cached_input_cny_per_million=(
            float(item["native_cached_input_per_million"])
            if currency == "CNY" and item["native_cached_input_per_million"] is not None
            else None
        ),
        cache_write_cny_per_million=(
            float(item["native_cache_write_per_million"])
            if currency == "CNY" and item["native_cache_write_per_million"] is not None
            else None
        ),
        output_cny_per_million=(
            float(item["native_output_per_million"])
            if currency == "CNY"
            else None
        ),
    )
    return PriceVersionView.model_validate(price)


@router.post(
    "/prices",
    response_model=PriceVersionView,
    status_code=status.HTTP_201_CREATED,
)
def create_price(
    payload: PriceVersionCreateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> PriceVersionView:
    return PriceVersionView.model_validate(
        billing_service(db, context).create_price(**payload.model_dump())
    )


@router.post("/prices/{price_id}/retire", response_model=PriceVersionView)
def retire_price(
    price_id: str,
    payload: VersionRetireRequest,
    db: DB,
    context: CurrentWorkspace,
) -> PriceVersionView:
    return PriceVersionView.model_validate(
        billing_service(db, context).retire_price(
            price_id,
            retired_at=payload.retired_at,
        )
    )


@router.get("/exchange-rates", response_model=list[ExchangeRateVersionView])
def list_exchange_rates(
    db: DB,
    context: CurrentWorkspace,
) -> list[ExchangeRateVersionView]:
    return [
        ExchangeRateVersionView.model_validate(item)
        for item in billing_service(db, context).list_exchange_rates()
    ]


@router.post(
    "/exchange-rates",
    response_model=ExchangeRateVersionView,
    status_code=status.HTTP_201_CREATED,
)
def create_exchange_rate(
    payload: ExchangeRateCreateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ExchangeRateVersionView:
    return ExchangeRateVersionView.model_validate(
        billing_service(db, context).create_exchange_rate(**payload.model_dump())
    )


@router.post(
    "/exchange-rates/{rate_id}/retire",
    response_model=ExchangeRateVersionView,
)
def retire_exchange_rate(
    rate_id: str,
    payload: VersionRetireRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ExchangeRateVersionView:
    return ExchangeRateVersionView.model_validate(
        billing_service(db, context).retire_exchange_rate(
            rate_id,
            retired_at=payload.retired_at,
        )
    )


@router.get("/budget-policies", response_model=list[BudgetPolicyView])
def list_budget_policies(
    db: DB,
    context: CurrentWorkspace,
) -> list[BudgetPolicyView]:
    return [
        BudgetPolicyView.model_validate(item)
        for item in billing_service(db, context).list_budget_policies()
    ]


@router.post(
    "/budget-policies",
    response_model=BudgetPolicyView,
    status_code=status.HTTP_201_CREATED,
)
def create_budget_policy(
    payload: BudgetPolicyCreateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> BudgetPolicyView:
    return BudgetPolicyView.model_validate(
        billing_service(db, context).create_budget_policy(**payload.model_dump())
    )


@router.put("/budget-policies/{policy_id}", response_model=BudgetPolicyView)
def update_budget_policy(
    policy_id: str,
    payload: BudgetPolicyUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> BudgetPolicyView:
    return BudgetPolicyView.model_validate(
        billing_service(db, context).update_budget_policy(
            policy_id,
            **payload.model_dump(),
        )
    )


@router.delete(
    "/budget-policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_budget_policy(
    policy_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> Response:
    billing_service(db, context).delete_budget_policy(policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/budget-status", response_model=list[BudgetStatusView])
def budget_status(
    db: DB,
    context: CurrentWorkspace,
) -> list[BudgetStatusView]:
    return [
        BudgetStatusView.model_validate(item)
        for item in billing_service(db, context).budget_statuses()
    ]


@router.get("/budget-alerts", response_model=list[BudgetAlertView])
def list_budget_alerts(
    db: DB,
    context: CurrentWorkspace,
) -> list[BudgetAlertView]:
    return [
        BudgetAlertView.model_validate(item)
        for item in billing_service(db, context).list_alerts()
    ]


@router.post(
    "/budget-alerts/{alert_id}/acknowledge",
    response_model=BudgetAlertView,
)
def acknowledge_budget_alert(
    alert_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> BudgetAlertView:
    return BudgetAlertView.model_validate(
        billing_service(db, context).acknowledge_alert(alert_id)
    )
