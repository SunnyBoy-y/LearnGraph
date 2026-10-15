from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import ProviderConfig, ProviderSecret, WorkspaceSecretReference
from app.domain.provider_balance_models import ProviderBalanceLease, ProviderBalanceManualState, ProviderBalanceState
from app.providers.remote.balance import detect_balance_vendor, official_no_balance_notice, supports_gateway_billing

logger = logging.getLogger(__name__)
MAX_REQUESTS = 3
DEADLINE_SECONDS = 20.0
LEASE_SECONDS = 35.0
REFRESH_SECONDS = 3600.0
MANUAL_COOLDOWN_SECONDS = 30.0
MAX_RESPONSE_BYTES = 1_048_576


class BalanceFailure(RuntimeError):
    def __init__(self, state: str, message: str):
        self.state = state
        super().__init__(message)


def binding_for(db: Session, provider: ProviderConfig) -> str:
    secret = db.get(ProviderSecret, provider.id)
    caps = provider.capabilities or {}
    label = caps.get("balance_access_key_reference")
    reference = db.scalar(select(WorkspaceSecretReference).where(
        WorkspaceSecretReference.workspace_id == provider.workspace_id,
        WorkspaceSecretReference.label == label,
    )) if isinstance(label, str) and label else None
    data = {
        "provider": provider.id, "workspace": provider.workspace_id,
        "type": provider.provider_type, "url": provider.base_url,
        "fingerprint": provider.secret_fingerprint,
        "secret_version": secret.secret_version if secret else None,
        "revoked": str(secret.revoked_at) if secret else None,
        "headers": caps.get("extra_headers"), "reference": label,
        "reference_version": reference.version if reference else None,
        "query_config": (caps.get("balance_query") or {}).get("config"),
        "brand": caps.get("brand_id"), "family": caps.get("model_family"),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def enqueue_initial(service, provider: ProviderConfig) -> None:
    # Called before create() commits: provider, secret and pending state are atomic.
    service.db.flush()
    if service.db.get(ProviderBalanceState, provider.id) is None:
        service.db.add(ProviderBalanceState(
            provider_id=provider.id, workspace_id=provider.workspace_id,
            actor_id=service.actor_id, binding=binding_for(service.db, provider),
            state="pending", consumed=False, generation=0, request_count=0,
        ))


def invalidate(service, provider_id: str) -> None:
    provider = service.providers.require(provider_id, 'provider')
    caps = dict(provider.capabilities or {})
    bucket = caps.get('balance_query')
    if isinstance(bucket, dict) and 'last_result' in bucket:
        caps['balance_query'] = {key: value for key, value in bucket.items() if key != 'last_result'}
        provider.capabilities = caps
    service.db.execute(update(ProviderBalanceManualState).where(
        ProviderBalanceManualState.provider_id == provider_id,
    ).values(state='invalidated', run_token=None, deadline_at=None))
    service.db.execute(update(ProviderBalanceState).where(
        ProviderBalanceState.provider_id == provider_id,
        ProviderBalanceState.workspace_id == service.workspace_id,
    ).values(state="invalidated", method_id=None, consumed=True, run_token=None,
             next_query_at=None, last_result=None, last_error="连接或凭据已更改，请手动重新探测"))


def _stamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat() if value is not None else None


def status_for(service, provider_id: str) -> dict:
    provider = service.providers.require(provider_id, "provider")
    row = service.db.get(ProviderBalanceState, provider.id)
    if row and row.state != "invalidated" and row.binding != binding_for(service.db, provider):
        invalidate(service, provider_id)
        service.db.commit()
        service.db.refresh(row)
    if row and row.state == "running" and (row.deadline_at or 0) < time.time():
        token = row.run_token
        service.db.execute(update(ProviderBalanceState).where(
            ProviderBalanceState.provider_id == provider.id,
            ProviderBalanceState.state == "running", ProviderBalanceState.run_token == token,
        ).values(state="interrupted", consumed=True, run_token=None, next_query_at=None,
                 last_error="上次查询中断，已停止自动尝试"))
        service.db.commit()
        service.db.refresh(row)
    return {
        "provider_id": provider.id, "state": row.state if row else "not_configured",
        "method_id": row.method_id if row else None,
        "last_result": row.last_result if row else None,
        "last_attempt_at": _stamp(row.last_attempt_at) if row else None,
        "next_query_at": _stamp(row.next_query_at) if row else None,
        "last_error": row.last_error if row else "尚未验证余额方法",
        "auto_refresh_enabled": bool(row and row.state == "ready" and provider.enabled),
        "request_count": row.request_count if row else 0,
    }


def request_discovery(service, provider_id: str) -> dict:
    provider = service.providers.require(provider_id, "provider")
    current = status_for(service, provider.id)
    row = service.db.get(ProviderBalanceState, provider.id)
    if row and row.state in {"pending", "running"}:
        return current
    if row and row.last_attempt_at and time.time() - row.last_attempt_at < MANUAL_COOLDOWN_SECONDS:
        raise AppError(429, "balance_cooldown", "请稍后再试，余额操作至少间隔30秒")
    if row is None:
        enqueue_initial(service, provider)
    else:
        result = service.db.execute(update(ProviderBalanceState).where(
            ProviderBalanceState.provider_id == provider.id,
            ProviderBalanceState.generation == row.generation,
            ProviderBalanceState.state == row.state,
        ).values(state="pending", method_id=None, binding=binding_for(service.db, provider),
                 generation=row.generation + 1, consumed=False, request_count=0,
                 run_token=None, next_query_at=None, last_error=None))
        if not result.rowcount:
            service.db.rollback()
            return status_for(service, provider.id)
    service.db.commit()
    return status_for(service, provider.id)


def _method(service, provider: ProviderConfig) -> str:
    from app.services.management import _is_dashscope_host
    from app.providers.remote.deepseek import is_official_deepseek_api_base_url
    if provider.provider_type == "codex_chatgpt":
        return "codex:v1"
    if _is_dashscope_host(provider.base_url):
        if not (provider.capabilities or {}).get("balance_access_key_reference"):
            raise BalanceFailure("auth_error", "需配置阿里云账务AccessKey，API Key不能查询账户余额")
        return "dashscope:v1"
    if is_official_deepseek_api_base_url(provider.base_url):
        return "deepseek:v1"
    vendor = detect_balance_vendor(provider.base_url)
    if vendor:
        return f"{vendor}:v1"
    notice = official_no_balance_notice(provider.base_url)
    if notice:
        raise BalanceFailure("unsupported", notice)
    if provider.provider_type in service._GATEWAY_BILLING_PROVIDER_TYPES and supports_gateway_billing(provider.base_url):
        return "gateway:v1"
    raise BalanceFailure("unsupported", "没有可安全验证的余额查询方法，已停止自动尝试")


def _acquire(db: Session, token: str, now: float) -> bool:
    acquired = db.execute(update(ProviderBalanceLease).where(
        ProviderBalanceLease.name == "global", ProviderBalanceLease.expires_at <= now,
    ).values(token=token, expires_at=now + LEASE_SECONDS))
    if acquired.rowcount:
        db.commit()
        return True
    if db.get(ProviderBalanceLease, "global") is not None:
        db.rollback()
        return False
    db.add(ProviderBalanceLease(name="global", token=token, expires_at=now + LEASE_SECONDS))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


class BudgetTransport(httpx.BaseTransport):
    """Every actual send consumes durable budget; no retry/redirect/large body."""
    def __init__(self, bind, provider_id: str, token: str, deadline: float, inner=None, *, state_model=ProviderBalanceState, max_requests=MAX_REQUESTS):
        self.bind, self.provider_id, self.token = bind, provider_id, token
        self.deadline = deadline
        self.state_model, self.max_requests = state_model, max_requests
        self.inner = inner or httpx.HTTPTransport(retries=0)

    def close(self):
        # Several vendor calls share this transport; caller owns its lifetime.
        pass

    def finish(self):
        self.inner.close()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        remaining = self.deadline - time.time()
        if remaining <= 0:
            raise BalanceFailure("transient_error", "本次余额查询超过时间预算，已停止")
        with Session(self.bind) as db:
            model = self.state_model
            state = db.get(model, self.provider_id)
            provider = db.get(ProviderConfig, self.provider_id)
            if state is None or provider is None or state.binding != binding_for(db, provider):
                raise BalanceFailure("invalidated", "连接或凭据已变更，旧任务已停止")
            lease = db.get(ProviderBalanceLease, "global")
            if not lease or lease.token != self.token or lease.expires_at <= time.time():
                raise BalanceFailure("interrupted", "查询租约失效，已停止")
            changed = db.execute(update(model).where(
                model.provider_id == self.provider_id,
                model.state == "running",
                model.run_token == self.token,
                model.request_count < self.max_requests,
            ).values(request_count=model.request_count + 1))
            db.commit()
            if not changed.rowcount:
                raise BalanceFailure("interrupted", "查询已中断或请求预算耗尽")
        timeout = min(5.0, remaining / 4)
        request.extensions["timeout"] = dict(connect=timeout, read=timeout, write=timeout, pool=timeout)
        try:
            response = self.inner.handle_request(request)
            if 300 <= response.status_code < 400:
                response.close()
                raise BalanceFailure("unsupported", "余额端点要求重定向，已停止以保护凭据")
            if response.status_code in {401, 403}:
                response.close()
                raise BalanceFailure("auth_error", "余额查询认证失败，请检查账务凭据或权限")
            if response.status_code in {404, 405}:
                response.close()
                raise BalanceFailure("unsupported", "未找到兼容的余额端点，已停止自动尝试")
            if response.status_code >= 400:
                response.close()
                raise BalanceFailure("transient_error", "余额端点暂时不可用，已停止自动尝试")
            chunks, size = [], 0
            try:
                chunks_source = [response.content] if response.is_stream_consumed else response.iter_raw()
                for chunk in chunks_source:
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES or time.time() > self.deadline:
                        raise BalanceFailure("transient_error", "余额响应超过大小或时间预算")
                    chunks.append(chunk)
                content = b"".join(chunks)
            finally:
                response.close()
            return httpx.Response(response.status_code, headers=response.headers, content=content, request=request)
        except httpx.HTTPError as exc:
            raise BalanceFailure("transient_error", "余额查询网络失败，已停止自动尝试") from exc


def run_balance(service, provider_id: str, *, automatic=False, transport=None) -> dict | None:
    provider = service.providers.require(provider_id, "provider")
    view = status_for(service, provider_id)
    row = service.db.get(ProviderBalanceState, provider_id)
    if not row or row.state not in {"pending", "ready"}:
        if automatic:
            return None
        raise AppError(409, "balance_not_verified", "余额方法尚未验证或已暂停，请点击重新探测", {"state": view["state"]})
    if not automatic and row.state == "pending":
        raise AppError(409, "balance_pending", "余额方法正在等待后台验证")
    now = time.time()
    manual = service.db.get(ProviderBalanceManualState, provider_id)
    if manual and manual.last_attempt_at and now - manual.last_attempt_at < MANUAL_COOLDOWN_SECONDS:
        if automatic:
            return None
        if row.last_result:
            return row.last_result
        raise AppError(429, "balance_cooldown", "余额操作至少间隔30秒")
    if row.state == "ready":
        if automatic and (not provider.enabled or (row.next_query_at or float("inf")) > now):
            return None
        if not automatic and row.last_attempt_at and now - row.last_attempt_at < MANUAL_COOLDOWN_SECONDS:
            if row.last_result:
                return row.last_result
            raise AppError(429, "balance_cooldown", "请稍后再试")
    token, generation, original_binding = str(uuid4()), row.generation, row.binding
    previous_method, phase = row.method_id, row.state
    previous_attempt = row.last_attempt_at
    bind = service.db.get_bind()
    service.db.commit()
    if not _acquire(service.db, token, now):
        if automatic:
            return None
        raise AppError(409, "balance_busy", "已有余额任务执行中，请稍后查看缓存")
    budget = None
    try:
        conditions = [ProviderBalanceState.provider_id == provider_id,
                      ProviderBalanceState.generation == generation,
                      ProviderBalanceState.state == phase, ProviderBalanceState.binding == original_binding,
                      ProviderBalanceState.last_attempt_at == previous_attempt]
        if phase == "pending":
            conditions.append(ProviderBalanceState.consumed.is_(False))
        claimed = service.db.execute(update(ProviderBalanceState).where(*conditions).values(
            state="running", consumed=True, run_token=token, request_count=0,
            deadline_at=now + LEASE_SECONDS, last_attempt_at=now, next_query_at=None,
        ))
        service.db.commit()
        if not claimed.rowcount:
            return None
        failure, result, method = None, None, previous_method
        try:
            service.db.expire_all()
            provider = service.providers.require(provider_id, "provider")
            if binding_for(service.db, provider) != original_binding:
                raise BalanceFailure("invalidated", "连接配置已改变，请重新探测")
            method = _method(service, provider)
            if previous_method and previous_method != method:
                raise BalanceFailure("invalidated", "已保存的余额方法不再适用")
            service.db.commit()
            budget = BudgetTransport(bind, provider_id, token, now + DEADLINE_SECONDS, transport)
            result = jsonable_encoder(service._balance_unmanaged(provider_id, transport=budget))
        except BalanceFailure as exc:
            failure = exc
        except AppError as exc:
            state = "auth_error" if "secret" in exc.code or "credential" in exc.code else "transient_error"
            failure = BalanceFailure(state, "余额方法未能验证，已停止自动尝试；请检查凭据或手动重试")
        except Exception:
            failure = BalanceFailure("transient_error", "余额响应无法验证，已停止自动尝试")
        service.db.rollback()
        service.db.expire_all()
        provider = service.db.get(ProviderConfig, provider_id)
        if provider is None:
            return None
        if binding_for(service.db, provider) != original_binding:
            failure = BalanceFailure("invalidated", "连接或凭据已变更，旧查询结果已丢弃")
            result = None
        values = dict(run_token=None, deadline_at=None)
        if failure:
            values.update(state=failure.state if phase == "pending" or failure.state in {"invalidated", "auth_error"} else "paused",
                          last_error=str(failure), next_query_at=None)
            if failure.state == "invalidated":
                values.update(last_result=None, method_id=None)
        else:
            values.update(state="ready", method_id=method, last_result=result,
                          last_error=None, next_query_at=time.time() + REFRESH_SECONDS)
        saved = service.db.execute(update(ProviderBalanceState).where(
            ProviderBalanceState.provider_id == provider_id, ProviderBalanceState.run_token == token,
            ProviderBalanceState.generation == generation, ProviderBalanceState.binding == original_binding,
        ).values(**values))
        service.db.commit()
        if not saved.rowcount:
            return None
        if failure and not automatic:
            raise AppError(409, "balance_paused", str(failure))
        return result
    finally:
        if budget:
            budget.finish()
        service.db.rollback()
        service.db.execute(update(ProviderBalanceLease).where(
            ProviderBalanceLease.name == "global", ProviderBalanceLease.token == token,
        ).values(expires_at=0))
        service.db.commit()


def _tick() -> None:
    from app.core.database import SessionLocal
    from app.core.config import get_settings
    from app.services.management import ProviderService
    with SessionLocal() as db:
        now = time.time()
        db.execute(update(ProviderBalanceState).where(
            ProviderBalanceState.state == "running", ProviderBalanceState.deadline_at < now,
        ).values(state="interrupted", run_token=None, next_query_at=None,
                 last_error="上次查询中断，已停止自动尝试"))
        db.commit()
        row = db.scalar(select(ProviderBalanceState).join(ProviderConfig, ProviderConfig.id == ProviderBalanceState.provider_id).where(or_(
            (ProviderBalanceState.state == "pending") & ProviderBalanceState.consumed.is_(False),
            (ProviderBalanceState.state == "ready") & (ProviderBalanceState.next_query_at <= now) & ProviderConfig.enabled.is_(True),
        )).order_by(ProviderBalanceState.last_attempt_at.asc()).limit(1))
        if row:
            service = ProviderService(db, row.workspace_id, row.actor_id, get_settings())
            run_balance(service, row.provider_id, automatic=True)


async def balance_worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(_tick)
        except Exception:
            # Never log HTTP bodies/headers/credentials. The row/lease fences retries.
            logger.warning("Provider balance worker could not complete a scheduling tick")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5.0)
        except TimeoutError:
            pass
