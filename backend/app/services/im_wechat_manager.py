"""Asyncio long-polling supervisor for per-user WeChat ClawBot (iLink) connections."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.im_models import IMChannelConfig, IMChannelState
from app.services.im_wechat import (
    CHANNEL_VERSION,
    SESSION_EXPIRED_ERRCODE,
    build_headers,
    normalize_base_url,
    parse_message_rich,
)

logger = logging.getLogger(__name__)


@dataclass
class _ManagedPoller:
    config_id: str
    app_id: str
    updated_at: datetime | None
    task: asyncio.Task[None]


class WeChatListenerManager:
    def __init__(self) -> None:
        self._tasks: dict[str, _ManagedPoller] = {}
        self._wake_event: asyncio.Event | None = None

    def wake(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()

    def _load_active_configs(self) -> list[IMChannelConfig]:
        with SessionLocal() as db:
            configs = list(
                db.scalars(
                    select(IMChannelConfig).where(
                        IMChannelConfig.provider == "wechat",
                        IMChannelConfig.enabled.is_(True),
                    )
                )
            )
            states = {
                state.config_id: state
                for state in db.scalars(select(IMChannelState))
            }
            return [
                cfg
                for cfg in configs
                if not (states.get(cfg.id) and states[cfg.id].session_expired)
            ]

    def _load_config_credentials(self, config_id: str) -> tuple[str, str, str, str] | None:
        from app.services.im_channels import decrypt_wechat_credentials

        settings = get_settings()
        with SessionLocal() as db:
            cfg = db.get(IMChannelConfig, config_id)
            if not cfg or cfg.provider != "wechat" or not cfg.enabled:
                return None
            state = db.get(IMChannelState, config_id)
            if state and state.session_expired:
                return None
            bot_token, base_url, _ilink_user_id = decrypt_wechat_credentials(settings, cfg)
            sync_buf = state.sync_buf if state else ""
            return cfg.app_id, bot_token, normalize_base_url(base_url), sync_buf

    def _mark_session_expired(self, config_id: str) -> None:
        with SessionLocal() as db:
            state = db.get(IMChannelState, config_id)
            if state is None:
                state = IMChannelState(config_id=config_id)
                db.add(state)
            state.session_expired = True
            state.last_error = "微信登录态已失效，请重新扫码登录。"
            db.commit()

    def _record_poll_result(
        self,
        config_id: str,
        connection: str,
        new_sync_buf: str | None,
        messages: list[dict],
    ) -> None:
        from app.services.im_channels import intake

        latest_context_token = ""
        parsed_messages: list[dict] = []
        for raw_msg in messages:
            parsed = parse_message_rich(raw_msg)
            if parsed:
                parsed_messages.append(parsed)
                if parsed.get("context_token"):
                    latest_context_token = parsed["context_token"]

        with SessionLocal() as db:
            state = db.get(IMChannelState, config_id)
            if state is None:
                state = IMChannelState(config_id=config_id)
                db.add(state)
            if new_sync_buf is not None:
                state.sync_buf = new_sync_buf
            if latest_context_token:
                state.context_token = latest_context_token
            state.session_expired = False
            state.last_error = None
            db.commit()

        for parsed in parsed_messages:
            with SessionLocal() as db:
                intake(
                    db,
                    connection,
                    parsed["sender"],
                    parsed["message_id"],
                    parsed["text"],
                    context_token=parsed.get("context_token") or None,
                    media=parsed.get("media") or None,
                )

    async def _poll_config(self, config_id: str, stop_event: asyncio.Event) -> None:
        loaded = await asyncio.to_thread(self._load_config_credentials, config_id)
        if not loaded:
            return
        app_id, bot_token, base_url, sync_buf = loaded
        connection = f"wechat:{app_id}"
        failures = 0

        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0), follow_redirects=False) as client:
            while not stop_event.is_set():
                try:
                    resp = await client.post(
                        f"{base_url}/ilink/bot/getupdates",
                        headers=build_headers(bot_token),
                        json={
                            "get_updates_buf": sync_buf,
                            "base_info": {"channel_version": CHANNEL_VERSION},
                        },
                    )
                except asyncio.CancelledError:
                    raise
                except httpx.TimeoutException:
                    # Normal long-poll timeout with no new messages.
                    failures = 0
                    continue
                except httpx.TransportError as exc:
                    failures += 1
                    delay = min(60.0, 5.0 * (2 ** min(failures - 1, 4)))
                    logger.warning("WeChat poller %s transport error (%s); retrying in %.0fs", app_id, exc, delay)
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 429 or resp.status_code >= 500:
                    failures += 1
                    delay = min(60.0, 5.0 * (2 ** min(failures - 1, 4)))
                    logger.warning("WeChat poller %s HTTP %s; retrying in %.0fs", app_id, resp.status_code, delay)
                    await asyncio.sleep(delay)
                    continue

                try:
                    data = resp.json() if resp.content else {}
                except ValueError:
                    failures += 1
                    delay = min(60.0, 5.0 * (2 ** min(failures - 1, 4)))
                    await asyncio.sleep(delay)
                    continue

                if not isinstance(data, dict):
                    failures += 1
                    await asyncio.sleep(min(60.0, 5.0 * (2 ** min(failures - 1, 4))))
                    continue

                errcode = data.get("errcode", 0)
                ret = data.get("ret", 0)
                if errcode == SESSION_EXPIRED_ERRCODE or ret == SESSION_EXPIRED_ERRCODE:
                    logger.warning("WeChat session expired for %s; pausing poller until re-login", app_id)
                    await asyncio.to_thread(self._mark_session_expired, config_id)
                    return

                if ret not in (0, None) or errcode not in (0, None):
                    failures += 1
                    delay = min(60.0, 5.0 * (2 ** min(failures - 1, 4)))
                    logger.warning(
                        "WeChat getupdates returned ret=%s errcode=%s for %s; retrying in %.0fs",
                        ret,
                        errcode,
                        app_id,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                failures = 0
                new_buf = data.get("get_updates_buf")
                if isinstance(new_buf, str) and new_buf:
                    sync_buf = new_buf
                raw_msgs = data.get("msgs")
                msgs = raw_msgs if isinstance(raw_msgs, list) else []
                if msgs or (isinstance(new_buf, str) and new_buf):
                    await asyncio.to_thread(
                        self._record_poll_result,
                        config_id,
                        connection,
                        sync_buf if isinstance(new_buf, str) and new_buf else None,
                        msgs,
                    )

    async def _stop_all(self) -> None:
        tasks = [managed.task for managed in self._tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def reconcile_once(self, stop_event: asyncio.Event) -> None:
        configs = await asyncio.to_thread(self._load_active_configs)
        desired = {cfg.id: cfg for cfg in configs}

        for config_id in list(self._tasks):
            managed = self._tasks[config_id]
            cfg = desired.get(config_id)
            if cfg is None or cfg.app_id != managed.app_id or cfg.updated_at != managed.updated_at:
                managed.task.cancel()
                try:
                    await managed.task
                except (asyncio.CancelledError, Exception):
                    pass
                del self._tasks[config_id]
                continue
            if managed.task.done():
                try:
                    managed.task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("WeChat poller task for %s crashed", managed.app_id)
                del self._tasks[config_id]

        for config_id, cfg in desired.items():
            if config_id in self._tasks:
                continue
            task = asyncio.create_task(
                self._poll_config(config_id, stop_event),
                name=f"wechat-poller-{cfg.app_id[:16]}",
            )
            self._tasks[config_id] = _ManagedPoller(
                config_id=config_id,
                app_id=cfg.app_id,
                updated_at=cfg.updated_at,
                task=task,
            )

    async def supervise(self, stop_event: asyncio.Event) -> None:
        self._wake_event = asyncio.Event()
        try:
            while not stop_event.is_set():
                try:
                    await self.reconcile_once(stop_event)
                except Exception:
                    logger.exception("WeChat listener reconciliation failed")
                self._wake_event.clear()
                stop_task = asyncio.create_task(stop_event.wait())
                wake_task = asyncio.create_task(self._wake_event.wait())
                try:
                    _done, pending = await asyncio.wait(
                        {stop_task, wake_task},
                        timeout=15.0,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                except asyncio.CancelledError:
                    stop_task.cancel()
                    wake_task.cancel()
                    raise
        finally:
            self._wake_event = None
            await self._stop_all()


wechat_listener_manager = WeChatListenerManager()
