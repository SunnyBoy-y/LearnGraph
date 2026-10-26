from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import AppSettings, SystemAdminContext
from app.core.errors import AppError

router = APIRouter(prefix="/updates", tags=["updates"])


def _update_lease(settings):
    from app.core.database_routing import OperationLease, read_json, state_root
    root = state_root(settings)
    try:
        lease = OperationLease(root)
    except BlockingIOError as exc:
        raise AppError(409, "database_update_interlock", "主业务库迁移正在执行，不能同时更新服务") from exc
    job = read_json(root / "job.json")
    active = read_json(root / "active.json")
    if active and active.get("phase") != "postgresql_active" or job and job.get("state") not in {"COMPLETED", "CANCELLED"}:
        lease.close()
        raise AppError(409, "database_update_interlock", "主业务库迁移尚未结束，不能同时更新服务")
    pending = read_json(root / "site-restore.json")
    if pending and pending.get("state") not in {"COMPLETED", "ROLLED_BACK"}:
        lease.close()
        raise AppError(409, "database_update_interlock", "请先完成全站恢复")
    return lease


class UpdateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9-]{16,64}$")
    version: str | None = Field(default=None, pattern=r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$")
    rollback_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.version) == bool(self.rollback_id):
            raise ValueError("Choose one version or rollback_id")
        return self


class UpdatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_install: bool = Field(strict=True)


async def call_agent(settings, method: Literal["GET", "POST"], path: str, body=None):
    if not settings.update_agent_url or not settings.update_agent_token_file:
        raise AppError(503, "updater_disabled", "尚未安装独立更新器，请按部署文档完成一次性配置")
    try:
        token = settings.update_agent_token_file.read_text(encoding="ascii").strip()
        if len(token) < 32:
            raise OSError("Invalid token")
        async with httpx.AsyncClient(timeout=40, trust_env=False) as client:
            response = await client.request(method, settings.update_agent_url.rstrip("/") + path,
                                            headers={"Authorization": f"Bearer {token}"}, json=body)
        data = response.json()
    except (OSError, UnicodeError, ValueError, httpx.HTTPError) as exc:
        raise AppError(503, "updater_unavailable", "无法连接独立更新器，服务重启期间会自动重连") from exc
    if response.status_code >= 400:
        message = data.get("error") if isinstance(data, dict) else None
        raise AppError(response.status_code, "updater_error", message if isinstance(message, str) else "更新器请求失败")
    return data


@router.get("/status")
async def status(context: SystemAdminContext, settings: AppSettings):
    if not settings.update_agent_url:
        return {"enabled": False, "current": {"version": settings.app_version},
                "message": "尚未安装独立更新器，请参阅 README 的应用内版本更新说明"}
    return await call_agent(settings, "GET", "/v1/status")


@router.post("/check")
async def check(context: SystemAdminContext, settings: AppSettings):
    return await call_agent(settings, "POST", "/v1/check", {})


@router.post("/jobs", status_code=202)
async def create_job(payload: UpdateJobRequest, context: SystemAdminContext, settings: AppSettings):
    lease = _update_lease(settings)
    try:
        return await call_agent(settings, "POST", "/v1/jobs", payload.model_dump(exclude_none=True))
    finally:
        lease.close()


@router.post("/policy")
async def policy(payload: UpdatePolicy, context: SystemAdminContext, settings: AppSettings):
    lease = _update_lease(settings) if payload.auto_install else None
    try:
        return await call_agent(settings, "POST", "/v1/policy", payload.model_dump())
    finally:
        if lease:
            lease.close()
