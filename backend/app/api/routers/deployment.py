from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AppSettings

router = APIRouter(tags=["deployment"])


@router.get("/deployment/profile")
def deployment_profile(settings: AppSettings) -> dict:
    """公开的部署能力标志（无鉴权，登录页即可读取）。

    前端据此隐藏/显示能力入口（如注册开关）。
    不要在这里暴露密钥或内部细节。
    """
    return {
        "deployment_profile": settings.deployment_profile,
        "single_user": False,
        "registration_enabled": True,
        "demo_login_enabled": settings.demo_login_enabled,
        "sandbox_enabled": settings.sandbox_enabled,
    }
