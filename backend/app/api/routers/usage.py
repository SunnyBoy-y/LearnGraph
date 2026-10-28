from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentWorkspace, DB, SystemAdminContext
from app.domain.schemas.management import (
    AlertEmailConfigUpdateRequest,
    AlertEmailConfigView,
    AlertEmailTestResult,
    BudgetAlertView,
    BudgetPolicyCreateRequest,
    BudgetPolicyUpdateRequest,
    BudgetPolicyView,
    BudgetStatusView,
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
    currency: Literal["USD", "CNY"] = "USD",
    provider_id: str | None = None,
    model_id: str | None = None,
    feature: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> UsageSummary:
    return UsageService(db, context.workspace_id).summary(
        currency=currency,
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
    currency: Literal["USD", "CNY"] = "USD",
    provider_id: str | None = None,
    model_id: str | None = None,
    feature: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    archived: Literal["exclude", "include", "only"] = "exclude",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    unknown_currency: bool = False,
) -> list[UsageEventView]:
    return [
        UsageEventView.model_validate(item)
        for item in UsageService(db, context.workspace_id).events(
            currency=currency, archived=archived, offset=offset, limit=limit, unknown_currency=unknown_currency,
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
    currency: Literal["USD", "CNY"],
    provider_id: str | None = None, model_id: str | None = None, feature: str | None = None,
    start_at: datetime | None = None, end_at: datetime | None = None,
) -> dict[str, int]:
    context.require_permission("workspace.manage")
    return UsageService(db, context.workspace_id).clear_events(
        actor_id=context.principal.user_id, currency=currency, provider_id=provider_id,
        model_id=model_id, feature=feature, start_at=start_at, end_at=end_at,
    )


@router.get("/price-catalog", response_model=list[PriceCatalogItem])
def list_price_catalog(db: DB, context: CurrentWorkspace, currency: Literal["USD", "CNY"] = "USD") -> list[PriceCatalogItem]:
    del db, context
    return [PriceCatalogItem.model_validate(item) for item in pricing_entries() if item["currency"] == currency]


@router.get("/models-dev", response_model=ModelsDevSnapshotStatus)
def models_dev_status(db: DB, context: CurrentWorkspace) -> ModelsDevSnapshotStatus:
    del db, context
    return ModelsDevSnapshotStatus.model_validate(snapshot_status())


@router.post("/models-dev/refresh", response_model=ModelsDevSnapshotStatus)
def models_dev_refresh(db: DB, context: CurrentWorkspace, admin: SystemAdminContext) -> ModelsDevSnapshotStatus:
    context.require_permission("workspace.write")
    del db, admin
    try:
        return ModelsDevSnapshotStatus.model_validate(refresh_snapshot())
    except Exception as exc:  # noqa: BLE001 - network and payload faults alike
        raise AppError(
            502,
            "models_dev_refresh_failed",
            f"Refreshing tariffs from models.dev failed: {exc}",
        ) from exc


@router.get("/manual-prices", response_model=list[ManualPriceView])
def list_manual_prices(db: DB, context: CurrentWorkspace, currency: Literal["USD", "CNY"] = "USD") -> list[ManualPriceView]:
    return [
        ManualPriceView.model_validate(item)
        for item in billing_service(db, context).list_manual_prices(currency)
    ]


@router.put("/manual-prices", response_model=ManualPriceView)
def upsert_manual_price(
    payload: ManualPriceUpsertRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ManualPriceView:
    context.require_permission("workspace.manage")
    return ManualPriceView.model_validate(
        billing_service(db, context).upsert_manual_price(**payload.model_dump())
    )


@router.delete("/manual-prices")
def remove_manual_price(
    db: DB,
    context: CurrentWorkspace,
    price_id: str = Query(min_length=1, max_length=36),
) -> dict[str, int]:
    context.require_permission("workspace.manage")
    return {
        "removed_count": billing_service(db, context).remove_manual_price(price_id)
    }


@router.get("/alert-email", response_model=AlertEmailConfigView)
def get_alert_email_config(db: DB, context: CurrentWorkspace) -> AlertEmailConfigView:
    context.require_permission("workspace.manage")
    return AlertEmailConfigView.model_validate(
        alert_email.load_config(db, context.workspace_id).view()
    )


@router.put("/alert-email", response_model=AlertEmailConfigView)
def update_alert_email_config(
    payload: AlertEmailConfigUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> AlertEmailConfigView:
    context.require_permission("workspace.manage")
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
    context.require_permission("workspace.manage")
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
    currency: Literal["USD", "CNY"] = "USD",
) -> list[BudgetPolicyView]:
    return [
        BudgetPolicyView.model_validate(item)
        for item in billing_service(db, context).list_budget_policies(currency)
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
    context.require_permission("workspace.manage")
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
    context.require_permission("workspace.manage")
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
    context.require_permission("workspace.manage")
    billing_service(db, context).delete_budget_policy(policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/budget-status", response_model=list[BudgetStatusView])
def budget_status(
    db: DB,
    context: CurrentWorkspace,
    currency: Literal["USD", "CNY"] = "USD",
) -> list[BudgetStatusView]:
    return [
        BudgetStatusView.model_validate(item)
        for item in billing_service(db, context).budget_statuses(currency=currency)
    ]


@router.get("/budget-alerts", response_model=list[BudgetAlertView])
def list_budget_alerts(
    db: DB,
    context: CurrentWorkspace,
    currency: Literal["USD", "CNY"] = "USD",
) -> list[BudgetAlertView]:
    return [
        BudgetAlertView.model_validate(item)
        for item in billing_service(db, context).list_alerts(currency)
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
    context.require_permission("workspace.manage")
    return BudgetAlertView.model_validate(
        billing_service(db, context).acknowledge_alert(alert_id)
    )
