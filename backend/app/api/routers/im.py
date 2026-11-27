from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.im_models import IMBinding, IMBindingCode, IMChannelConfig, IMChannelState
from app.services.im_channels import (
    encode_feishu_secret_payload,
    encode_wechat_secret_payload,
    feishu_config_for_user,
    feishu_connection_for_user,
    issue_code,
    wechat_config_for_user,
    wechat_connection_for_user,
)
from app.services.im_feishu import feishu_qr_manager
from app.services.im_feishu_manager import feishu_listener_manager
from app.services.im_wechat import wechat_qr_manager
from app.services.im_wechat_manager import wechat_listener_manager
from app.services.provider_secrets import encrypt_provider_secret

router = APIRouter(prefix="/im", tags=["IM"])


class FeishuConfigRequest(BaseModel):
    app_id: str = Field(..., min_length=1, max_length=64)
    app_secret: str | None = Field(default=None, max_length=256)


class FeishuQrStartRequest(BaseModel):
    domain: str = Field(default="feishu", max_length=16)


def _is_personal_workspace(context: CurrentWorkspace) -> bool:
    return (
        context.workspace.workspace_kind == "personal"
        and context.workspace.owner_user_id == context.principal.user_id
    )


def _require_personal_workspace(context: CurrentWorkspace) -> None:
    if not _is_personal_workspace(context):
        raise AppError(403, "im_personal_workspace_required", "IM bindings require your personal workspace")


def _upsert_feishu_config(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    *,
    app_id: str,
    app_secret: str,
    domain: str = "feishu",
    auto_bind_open_id: str | None = None,
) -> None:
    conflict = db.scalar(
        select(IMChannelConfig).where(
            IMChannelConfig.provider == "feishu",
            IMChannelConfig.app_id == app_id,
            IMChannelConfig.user_id != context.principal.user_id,
        )
    )
    if conflict is not None:
        raise AppError(409, "feishu_app_id_in_use", "This Feishu App ID is already configured by another user")

    existing = db.scalar(
        select(IMChannelConfig).where(
            IMChannelConfig.provider == "feishu",
            IMChannelConfig.user_id == context.principal.user_id,
        )
    )
    if existing is None:
        if not app_secret:
            raise AppError(422, "feishu_secret_required", "Feishu App Secret is required")
        encrypted = encrypt_provider_secret(settings, encode_feishu_secret_payload(app_secret, domain))
        db.add(
            IMChannelConfig(
                provider="feishu",
                workspace_id=context.workspace_id,
                user_id=context.principal.user_id,
                app_id=app_id,
                secret_ciphertext=encrypted.ciphertext,
                secret_algorithm=encrypted.algorithm,
                secret_key_provider=encrypted.key_provider,
                secret_key_version=encrypted.key_version,
                enabled=True,
            )
        )
    else:
        if existing.app_id != app_id or existing.workspace_id != context.workspace_id:
            for model in (IMBinding, IMBindingCode):
                db.execute(
                    delete(model).where(
                        model.connection.like("feishu:%"),
                        model.workspace_id == context.workspace_id,
                        model.user_id == context.principal.user_id,
                    )
                )
        existing.workspace_id = context.workspace_id
        existing.app_id = app_id
        existing.enabled = True
        if app_secret:
            encrypted = encrypt_provider_secret(settings, encode_feishu_secret_payload(app_secret, domain))
            existing.secret_ciphertext = encrypted.ciphertext
            existing.secret_algorithm = encrypted.algorithm
            existing.secret_key_provider = encrypted.key_provider
            existing.secret_key_version = encrypted.key_version

    if auto_bind_open_id:
        connection = f"feishu:{app_id}"
        existing_sender = db.scalar(
            select(IMBinding).where(
                IMBinding.connection == connection,
                IMBinding.sender == auto_bind_open_id,
            )
        )
        existing_user = db.scalar(
            select(IMBinding).where(
                IMBinding.connection == connection,
                IMBinding.user_id == context.principal.user_id,
            )
        )
        if existing_sender is None and existing_user is None:
            db.add(
                IMBinding(
                    connection=connection,
                    sender=auto_bind_open_id,
                    workspace_id=context.workspace_id,
                    user_id=context.principal.user_id,
                )
            )

    db.commit()
    feishu_listener_manager.wake()


def _upsert_wechat_config(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    *,
    bot_id: str,
    bot_token: str,
    ilink_user_id: str,
    base_url: str,
) -> None:
    if not bot_id or len(bot_id) > 64 or any(ch.isspace() for ch in bot_id):
        raise AppError(422, "invalid_wechat_bot_id", "Invalid WeChat bot identifier")
    if not bot_token or not ilink_user_id or len(ilink_user_id) > 192:
        raise AppError(422, "invalid_wechat_credentials", "Incomplete WeChat bot credentials")

    conflict = db.scalar(
        select(IMChannelConfig).where(
            IMChannelConfig.provider == "wechat",
            IMChannelConfig.app_id == bot_id,
            IMChannelConfig.user_id != context.principal.user_id,
        )
    )
    if conflict is not None:
        raise AppError(409, "wechat_bot_in_use", "This WeChat bot is already bound to another user")

    encrypted = encrypt_provider_secret(
        settings,
        encode_wechat_secret_payload(
            bot_token=bot_token,
            base_url=base_url,
            ilink_user_id=ilink_user_id,
        ),
    )

    existing = db.scalar(
        select(IMChannelConfig).where(
            IMChannelConfig.provider == "wechat",
            IMChannelConfig.user_id == context.principal.user_id,
        )
    )
    if existing is None:
        cfg = IMChannelConfig(
            provider="wechat",
            workspace_id=context.workspace_id,
            user_id=context.principal.user_id,
            app_id=bot_id,
            secret_ciphertext=encrypted.ciphertext,
            secret_algorithm=encrypted.algorithm,
            secret_key_provider=encrypted.key_provider,
            secret_key_version=encrypted.key_version,
            enabled=True,
        )
        db.add(cfg)
        db.flush()
    else:
        cfg = existing
        if existing.app_id != bot_id or existing.workspace_id != context.workspace_id:
            for model in (IMBinding, IMBindingCode):
                db.execute(
                    delete(model).where(
                        model.connection.like("wechat:%"),
                        model.workspace_id == context.workspace_id,
                        model.user_id == context.principal.user_id,
                    )
                )
        existing.workspace_id = context.workspace_id
        existing.app_id = bot_id
        existing.enabled = True
        existing.secret_ciphertext = encrypted.ciphertext
        existing.secret_algorithm = encrypted.algorithm
        existing.secret_key_provider = encrypted.key_provider
        existing.secret_key_version = encrypted.key_version
        db.flush()

    state = db.get(IMChannelState, cfg.id)
    if state is None:
        state = IMChannelState(config_id=cfg.id)
        db.add(state)
    state.sync_buf = ""
    state.session_expired = False
    state.last_error = None

    connection = f"wechat:{bot_id}"
    binding = db.scalar(
        select(IMBinding).where(
            IMBinding.connection == connection,
            IMBinding.user_id == context.principal.user_id,
        )
    )
    if binding is None:
        db.execute(
            delete(IMBinding).where(
                IMBinding.connection.like("wechat:%"),
                IMBinding.user_id == context.principal.user_id,
            )
        )
        db.add(
            IMBinding(
                connection=connection,
                sender=ilink_user_id,
                workspace_id=context.workspace_id,
                user_id=context.principal.user_id,
            )
        )
    elif binding.sender != ilink_user_id:
        binding.sender = ilink_user_id
        binding.workspace_id = context.workspace_id

    db.commit()
    wechat_listener_manager.wake()


def _status_payload(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    private = _is_personal_workspace(context)
    connection, user_cfg = (
        feishu_connection_for_user(
            db,
            user_id=context.principal.user_id,
            workspace_id=context.workspace_id,
        )
        if settings.durable_queue_enabled
        else (None, None)
    )
    binding = (
        db.scalar(
            select(IMBinding).where(
                IMBinding.connection == connection,
                IMBinding.workspace_id == context.workspace_id,
                IMBinding.user_id == context.principal.user_id,
            )
        )
        if connection
        else None
    )
    return {
        "configured": bool(connection),
        "personal_workspace": private,
        "bound": binding is not None,
        "session_id": binding.session_id if binding else None,
        "config_source": "user" if user_cfg is not None else ("env" if connection else None),
        "app_id": user_cfg.app_id if user_cfg is not None else (connection.removeprefix("feishu:") if connection else None),
    }


def _wechat_status_payload(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    private = _is_personal_workspace(context)
    connection, user_cfg = (
        wechat_connection_for_user(
            db,
            user_id=context.principal.user_id,
            workspace_id=context.workspace_id,
        )
        if settings.durable_queue_enabled
        else (None, None)
    )
    binding = (
        db.scalar(
            select(IMBinding).where(
                IMBinding.connection == connection,
                IMBinding.workspace_id == context.workspace_id,
                IMBinding.user_id == context.principal.user_id,
            )
        )
        if connection
        else None
    )
    state = db.get(IMChannelState, user_cfg.id) if user_cfg is not None else None
    return {
        "configured": bool(connection),
        "personal_workspace": private,
        "bound": binding is not None,
        "session_id": binding.session_id if binding else None,
        "bot_id": user_cfg.app_id if user_cfg is not None else None,
        "session_expired": bool(state.session_expired) if state is not None else False,
        "last_error": state.last_error if state is not None else None,
    }


@router.get("/feishu")
def status(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    return _status_payload(db, context, settings)


@router.put("/feishu/config")
def save_config(body: FeishuConfigRequest, db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    _require_personal_workspace(context)
    if not settings.durable_queue_enabled:
        raise AppError(503, "durable_queue_disabled", "Durable queue must be enabled for IM channels")
    app_id = body.app_id.strip()
    if not app_id or len(app_id) > 56 or any(ch.isspace() for ch in app_id):
        raise AppError(422, "invalid_feishu_app_id", "Feishu App ID must be 1-56 non-whitespace characters")
    app_secret = body.app_secret.strip() if body.app_secret is not None else ""
    _upsert_feishu_config(db, context, settings, app_id=app_id, app_secret=app_secret)
    return _status_payload(db, context, settings)


@router.delete("/feishu/config")
def delete_config(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    _require_personal_workspace(context)
    cfg = feishu_config_for_user(db, user_id=context.principal.user_id, workspace_id=context.workspace_id)
    if cfg is not None:
        db.delete(cfg)
    for model in (IMBinding, IMBindingCode):
        db.execute(
            delete(model).where(
                model.connection.like("feishu:%"),
                model.workspace_id == context.workspace_id,
                model.user_id == context.principal.user_id,
            )
        )
    db.commit()
    feishu_listener_manager.wake()
    return _status_payload(db, context, settings)


@router.post("/feishu/qr", status_code=201)
def start_feishu_qr(
    context: CurrentWorkspace,
    settings: AppSettings,
    body: FeishuQrStartRequest | None = None,
) -> dict:
    _require_personal_workspace(context)
    if not settings.durable_queue_enabled:
        raise AppError(503, "durable_queue_disabled", "Durable queue must be enabled for IM channels")
    domain = body.domain if body is not None else "feishu"
    return feishu_qr_manager.start(
        user_id=context.principal.user_id,
        workspace_id=context.workspace_id,
        domain=domain,
    )


@router.get("/feishu/qr/{qr_session_id}")
def poll_feishu_qr(qr_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    _require_personal_workspace(context)
    if not settings.durable_queue_enabled:
        raise AppError(503, "durable_queue_disabled", "Durable queue must be enabled for IM channels")
    result = feishu_qr_manager.poll(
        qr_session_id,
        user_id=context.principal.user_id,
        workspace_id=context.workspace_id,
    )
    if result.get("status") != "confirmed":
        return result

    _upsert_feishu_config(
        db,
        context,
        settings,
        app_id=result["app_id"],
        app_secret=result["app_secret"],
        domain=result.get("domain", "feishu"),
        auto_bind_open_id=result.get("open_id"),
    )
    feishu_qr_manager.consume(
        qr_session_id,
        user_id=context.principal.user_id,
        workspace_id=context.workspace_id,
    )
    return {
        "status": "confirmed",
        "channel_status": _status_payload(db, context, settings),
    }


@router.delete("/feishu/qr/{qr_session_id}")
def cancel_feishu_qr(qr_session_id: str, context: CurrentWorkspace) -> dict:
    _require_personal_workspace(context)
    feishu_qr_manager.cancel(
        qr_session_id,
        user_id=context.principal.user_id,
        workspace_id=context.workspace_id,
    )
    return {"cancelled": True}


@router.post("/feishu/binding-code", status_code=201)
def create_code(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    _require_personal_workspace(context)
    connection, _ = (
        feishu_connection_for_user(
            db,
            user_id=context.principal.user_id,
            workspace_id=context.workspace_id,
        )
        if settings.durable_queue_enabled
        else (None, None)
    )
    if connection is None:
        raise AppError(503, "feishu_not_configured", "Feishu bot is not configured")
    return {"code": issue_code(db, context, connection), "expires_in": 600}


@router.delete("/feishu/binding")
def unbind(db: DB, context: CurrentWorkspace) -> dict:
    for model in (IMBinding, IMBindingCode):
        db.execute(
            delete(model).where(
                model.connection.like("feishu:%"),
                model.workspace_id == context.workspace_id,
                model.user_id == context.principal.user_id,
            )
        )
    db.commit()
    return {"bound": False}


@router.get("/wechat")
def wechat_status(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    return _wechat_status_payload(db, context, settings)


@router.post("/wechat/qr", status_code=201)
def start_wechat_qr(context: CurrentWorkspace, settings: AppSettings) -> dict:
    _require_personal_workspace(context)
    if not settings.durable_queue_enabled:
        raise AppError(503, "durable_queue_disabled", "Durable queue must be enabled for IM channels")
    return wechat_qr_manager.start(
        user_id=context.principal.user_id,
        workspace_id=context.workspace_id,
    )


@router.get("/wechat/qr/{qr_session_id}")
def poll_wechat_qr(qr_session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    _require_personal_workspace(context)
    if not settings.durable_queue_enabled:
        raise AppError(503, "durable_queue_disabled", "Durable queue must be enabled for IM channels")
    state = wechat_qr_manager.get_status(
        qr_session_id,
        user_id=context.principal.user_id,
        workspace_id=context.workspace_id,
    )
    if state.get("status") != "confirmed":
        return state

    result = state.get("result") or {}
    _upsert_wechat_config(
        db,
        context,
        settings,
        bot_id=str(result.get("bot_id") or "").strip(),
        bot_token=str(result.get("bot_token") or "").strip(),
        ilink_user_id=str(result.get("ilink_user_id") or "").strip(),
        base_url=str(result.get("base_url") or "").strip(),
    )
    wechat_qr_manager.consume(
        qr_session_id,
        user_id=context.principal.user_id,
        workspace_id=context.workspace_id,
    )
    return {
        "status": "confirmed",
        "channel_status": _wechat_status_payload(db, context, settings),
    }


@router.delete("/wechat/qr/{qr_session_id}")
def cancel_wechat_qr(qr_session_id: str, context: CurrentWorkspace) -> dict:
    _require_personal_workspace(context)
    wechat_qr_manager.cancel(
        qr_session_id,
        user_id=context.principal.user_id,
        workspace_id=context.workspace_id,
    )
    return {"cancelled": True}


@router.delete("/wechat/config")
def delete_wechat_config(db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    _require_personal_workspace(context)
    cfg = wechat_config_for_user(db, user_id=context.principal.user_id, workspace_id=context.workspace_id)
    if cfg is not None:
        db.execute(delete(IMChannelState).where(IMChannelState.config_id == cfg.id))
        db.delete(cfg)
    for model in (IMBinding, IMBindingCode):
        db.execute(
            delete(model).where(
                model.connection.like("wechat:%"),
                model.workspace_id == context.workspace_id,
                model.user_id == context.principal.user_id,
            )
        )
    db.commit()
    wechat_listener_manager.wake()
    return _wechat_status_payload(db, context, settings)
