from __future__ import annotations

import os

from fastapi import APIRouter
from sqlalchemy import select, text

from app.api.deps import DB
from app.domain.models import ProviderConfig
from app.domain.schemas.common import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/livez")
async def livez() -> dict[str, str | int]:
    """Process/event-loop liveness probe with no database dependencies.

    The development supervisor must not kill active Agent runs merely because
    SQLite readiness or the sync worker pool is temporarily busy.
    """

    return {
        "status": "ok",
        "service": "learngraph-backend",
        "pid": os.getpid(),
    }


@router.get("/health", response_model=HealthResponse)
def health(db: DB) -> HealthResponse:
    """健康检查。

    无需认证或工作区 Header。返回服务状态、数据库类型、API 版本，以及是否存在已启用的远程能力。
    `status=ok` 只表示应用和 SQLite 可连接，不代表所有外部 Provider 都可用。
    """
    db.execute(text("SELECT 1"))
    remote_enabled = db.scalar(
        select(ProviderConfig.id)
        .where(
            ProviderConfig.enabled.is_(True),
            ProviderConfig.remote_capability.is_(True),
        )
        .limit(1)
    )
    return HealthResponse(
        status="ok",
        service="learngraph-backend",
        version="0.1.0",
        database="sqlite",
        remote_capabilities_enabled=remote_enabled is not None,
    )
