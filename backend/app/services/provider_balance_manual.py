from __future__ import annotations

import time
from uuid import uuid4

from sqlalchemy import update

from app.core.errors import AppError
from app.domain.provider_balance_models import ProviderBalanceLease, ProviderBalanceManualState, ProviderBalanceState
from app.services.provider_balance_lifecycle import (
    BalanceFailure, BudgetTransport, DEADLINE_SECONDS, MANUAL_COOLDOWN_SECONDS,
    _acquire, binding_for,
)


def run_custom_balance(service, provider_id: str, payload, *, transport=None) -> dict:
    service.providers.require(provider_id, "provider")
    token, now = str(uuid4()), time.time()
    service.db.commit()
    if not _acquire(service.db, token, now):
        raise AppError(409, "balance_busy", "已有余额任务执行中，请稍后再试")
    budget = None
    try:
        # Refresh only after taking the global lease; stale clients cannot
        # resurrect an old claim after a previous request has already finished.
        service.db.expire_all()
        provider = service.providers.require(provider_id, "provider")
        row = service.db.get(ProviderBalanceManualState, provider_id)
        automatic = service.db.get(ProviderBalanceState, provider_id)
        last_attempt = max(row.last_attempt_at or 0 if row else 0,
                           automatic.last_attempt_at or 0 if automatic else 0)
        if now - last_attempt < MANUAL_COOLDOWN_SECONDS:
            raise AppError(429, "balance_cooldown", "余额操作至少间隔30秒，失败也不会自动重试")
        original_binding = binding_for(service.db, provider)
        if row is None:
            row = ProviderBalanceManualState(provider_id=provider_id, binding=original_binding)
            service.db.add(row)
        row.binding, row.state, row.run_token = original_binding, "running", token
        row.request_count, row.last_attempt_at = 0, now
        row.deadline_at = now + DEADLINE_SECONDS
        service.db.commit()
        budget = BudgetTransport(service.db.get_bind(), provider_id, token, now + DEADLINE_SECONDS,
                                 transport, state_model=ProviderBalanceManualState, max_requests=1)
        result = service._execute_balance_query_unmanaged(provider_id, payload, transport=budget)
        service.db.rollback()
        service.db.expire_all()
        provider = service.providers.require(provider_id, "provider")
        if binding_for(service.db, provider) != original_binding:
            raise AppError(409, "balance_invalidated", "连接或凭据已更改，旧查询结果已丢弃")
        changed = service.db.execute(update(ProviderBalanceManualState).where(
            ProviderBalanceManualState.provider_id == provider_id,
            ProviderBalanceManualState.run_token == token,
            ProviderBalanceManualState.state == "running",
            ProviderBalanceManualState.binding == original_binding,
        ).values(state="complete", deadline_at=time.time() + 300))
        service.db.commit()
        if not changed.rowcount:
            raise AppError(409, "balance_invalidated", "查询状态已变化，请重新手动查询")
        return {**result, "query_token": token}
    except BalanceFailure as exc:
        raise AppError(409, "balance_custom_stopped", str(exc)) from exc
    finally:
        try:
            if budget:
                budget.finish()
        finally:
            service.db.rollback()
            service.db.execute(update(ProviderBalanceManualState).where(
                ProviderBalanceManualState.provider_id == provider_id,
                ProviderBalanceManualState.run_token == token,
                ProviderBalanceManualState.state == "running",
            ).values(state="failed", run_token=None, deadline_at=None))
            service.db.execute(update(ProviderBalanceLease).where(
                ProviderBalanceLease.name == "global", ProviderBalanceLease.token == token,
            ).values(expires_at=0))
            service.db.commit()


def consume_custom_result(service, provider, token: str | None) -> None:
    if not token:
        raise AppError(409, "balance_result_unbound", "请先执行一次手动查询，再保存其结果")
    claimed = service.db.execute(update(ProviderBalanceManualState).where(
        ProviderBalanceManualState.provider_id == provider.id,
        ProviderBalanceManualState.binding == binding_for(service.db, provider),
        ProviderBalanceManualState.state == "complete",
        ProviderBalanceManualState.run_token == token,
        ProviderBalanceManualState.deadline_at > time.time(),
    ).values(state="saved", run_token=None, deadline_at=None))
    if not claimed.rowcount:
        service.db.rollback()
        raise AppError(409, "balance_result_stale", "查询结果已过期、已保存或连接已改变，请重新查询")
    # Caller commits this receipt together with the extracted display result.
