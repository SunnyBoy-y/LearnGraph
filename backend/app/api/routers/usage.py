from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.management import (
    AlertEmailConfigUpdateRequest,
    AlertEmailConfigView,
    AlertEmailTestResult,
    BudgetAlertView,
    BudgetPolicyCreateRequest,
    BudgetPolicyUpdateRequest,
    BudgetPolicyView,
    BudgetStatusView,
    ExchangeRateInfo,
    ExchangeRateSetRequest,
    ManualPriceUpsertRequest,
    ManualPriceView,
    ModelsDevSnapshotStatus,
    PriceCatalogItem,
    UsageEventView,
    UsageSummary,
)
from app.providers.models_dev import (
    pricing_entries,
    refresh_snapshot,
    snapshot_status,
)
from app.services import alert_email
from app.services.billing import BillingService
from app.services.management import UsageService
from app.services.pricing_catalog import PRICING_CATALOG
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


@router.get("/price-catalog", response_model=list[PriceCatalogItem])
def list_price_catalog(db: DB, context: CurrentWorkspace) -> list[PriceCatalogItem]:
    del db, context
    return [
        PriceCatalogItem.model_validate({**item, "source": "builtin"})
        for item in PRICING_CATALOG
    ] + [PriceCatalogItem.model_validate(item) for item in pricing_entries()]


@router.get("/models-dev", response_model=ModelsDevSnapshotStatus)
def models_dev_status(db: DB, context: CurrentWorkspace) -> ModelsDevSnapshotStatus:
    del db, context
    return ModelsDevSnapshotStatus.model_validate(snapshot_status())


@router.post("/models-dev/refresh", response_model=ModelsDevSnapshotStatus)
def models_dev_refresh(db: DB, context: CurrentWorkspace) -> ModelsDevSnapshotStatus:
    del db, context
    try:
        return ModelsDevSnapshotStatus.model_validate(refresh_snapshot())
    except Exception as exc:  # noqa: BLE001 - network and payload faults alike
        raise AppError(
            502,
            "models_dev_refresh_failed",
            f"Refreshing tariffs from models.dev failed: {exc}",
        ) from exc


@router.get("/manual-prices", response_model=list[ManualPriceView])
def list_manual_prices(db: DB, context: CurrentWorkspace) -> list[ManualPriceView]:
    return [
        ManualPriceView.model_validate(item)
        for item in billing_service(db, context).list_manual_prices()
    ]


@router.put("/manual-prices", response_model=ManualPriceView)
def upsert_manual_price(
    payload: ManualPriceUpsertRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ManualPriceView:
    return ManualPriceView.model_validate(
        billing_service(db, context).upsert_manual_price(**payload.model_dump())
    )


@router.delete("/manual-prices")
def remove_manual_price(
    db: DB,
    context: CurrentWorkspace,
    model_id: str = Query(min_length=1, max_length=160),
) -> dict[str, int]:
    return {
        "removed_count": billing_service(db, context).remove_manual_price(model_id)
    }


@router.get("/exchange-rate", response_model=ExchangeRateInfo)
def current_exchange_rate(db: DB, context: CurrentWorkspace) -> ExchangeRateInfo:
    return ExchangeRateInfo.model_validate(
        billing_service(db, context).current_exchange_rate(),
        from_attributes=True,
    )


@router.put("/exchange-rate", response_model=ExchangeRateInfo)
def set_exchange_rate(
    payload: ExchangeRateSetRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ExchangeRateInfo:
    return ExchangeRateInfo.model_validate(
        billing_service(db, context).set_exchange_rate(payload.rate),
        from_attributes=True,
    )


@router.post("/exchange-rate/refresh", response_model=ExchangeRateInfo)
def refresh_exchange_rate(db: DB, context: CurrentWorkspace) -> ExchangeRateInfo:
    return ExchangeRateInfo.model_validate(
        billing_service(db, context).refresh_exchange_rate_from_network(),
        from_attributes=True,
    )


@router.get("/alert-email", response_model=AlertEmailConfigView)
def get_alert_email_config(db: DB, context: CurrentWorkspace) -> AlertEmailConfigView:
    return AlertEmailConfigView.model_validate(
        alert_email.load_config(db, context.workspace_id).view()
    )


@router.put("/alert-email", response_model=AlertEmailConfigView)
def update_alert_email_config(
    payload: AlertEmailConfigUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> AlertEmailConfigView:
    return AlertEmailConfigView.model_validate(
        alert_email.save_config(
            db,
            context.workspace_id,
            context.principal.user_id,
            **payload.model_dump(),
        ).view()
    )


@router.post("/alert-email/test", response_model=AlertEmailTestResult)
def send_test_alert_email(db: DB, context: CurrentWorkspace) -> AlertEmailTestResult:
    config = alert_email.load_config(db, context.workspace_id)
    try:
        alert_email.send_mail(
            config,
            "[LearnGraph] 预算告警测试邮件",
            "这是一封来自 LearnGraph 用量预算模块的测试邮件；收到即表示 SMTP 配置可用。",
        )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - report SMTP faults verbatim
        return AlertEmailTestResult(ok=False, detail=str(exc))
    return AlertEmailTestResult(ok=True, detail="测试邮件已发送")


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
