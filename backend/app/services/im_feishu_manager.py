from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import importlib.util
import logging
import os
import subprocess
import sys
import time

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.im_models import IMChannelConfig

logger = logging.getLogger(__name__)


@dataclass
class _ManagedListener:
    config_id: str
    app_id: str
    updated_at: datetime | None
    process: subprocess.Popen[bytes]
    started_at: float


class FeishuListenerManager:
    def __init__(self) -> None:
        self._children: dict[str, _ManagedListener] = {}
        self._retry_at: dict[str, tuple[float, int]] = {}
        self._wake_event: asyncio.Event | None = None
        self._sdk_missing_logged = False

    def wake(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()

    def _has_sdk(self) -> bool:
        if importlib.util.find_spec("lark_oapi") is not None:
            return True
        if not self._sdk_missing_logged:
            self._sdk_missing_logged = True
            logger.info("lark_oapi is not installed; per-user Feishu listeners are disabled.")
        return False

    def _stop_child(self, child: _ManagedListener) -> None:
        proc = child.process
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        except OSError:
            pass

    def _stop_all(self) -> None:
        for child in list(self._children.values()):
            self._stop_child(child)
        self._children.clear()

    def reconcile_once(self) -> None:
        if not self._has_sdk():
            return
        now = time.monotonic()
        with SessionLocal() as db:
            configs = list(
                db.scalars(
                    select(IMChannelConfig).where(
                        IMChannelConfig.provider == "feishu",
                        IMChannelConfig.enabled.is_(True),
                    )
                )
            )
        desired = {cfg.id: cfg for cfg in configs}

        for config_id in list(self._children):
            child = self._children[config_id]
            cfg = desired.get(config_id)
            if cfg is None or cfg.app_id != child.app_id or cfg.updated_at != child.updated_at:
                self._stop_child(child)
                del self._children[config_id]
                self._retry_at.pop(config_id, None)
                continue
            exit_code = child.process.poll()
            if exit_code is not None:
                uptime = now - child.started_at
                failures = 1 if uptime > 30 else self._retry_at.get(config_id, (0.0, 0))[1] + 1
                delay = min(60, 5 * (2 ** min(failures - 1, 4)))
                self._retry_at[config_id] = (now + delay, failures)
                logger.warning(
                    "Feishu listener for config %s (%s) exited with code %s; retrying in %ss",
                    config_id,
                    child.app_id,
                    exit_code,
                    delay,
                )
                del self._children[config_id]

        for config_id, cfg in desired.items():
            if config_id in self._children:
                continue
            retry_at, _failures = self._retry_at.get(config_id, (0.0, 0))
            if now < retry_at:
                continue
            try:
                child_env = os.environ.copy()
                # Separate OS processes cannot share app's in-process SQLite
                # write gate; give the listener a higher busy_timeout so
                # transient cross-process contention waits instead of fast-failing.
                try:
                    current_busy = int(child_env.get("LEARNGRAPH_SQLITE_BUSY_TIMEOUT_MS") or 0)
                except ValueError:
                    current_busy = 0
                if current_busy < 10_000:
                    child_env["LEARNGRAPH_SQLITE_BUSY_TIMEOUT_MS"] = "10000"
                proc = subprocess.Popen(
                    [sys.executable, "-m", "app.services.im_feishu", "--config-id", config_id],
                    env=child_env,
                )
                self._children[config_id] = _ManagedListener(
                    config_id=config_id,
                    app_id=cfg.app_id,
                    updated_at=cfg.updated_at,
                    process=proc,
                    started_at=now,
                )
            except Exception:
                logger.exception("Failed to start Feishu listener for config %s", config_id)
                self._retry_at[config_id] = (now + 15, 1)

    async def supervise(self, stop_event: asyncio.Event) -> None:
        self._wake_event = asyncio.Event()
        try:
            while not stop_event.is_set():
                try:
                    await asyncio.to_thread(self.reconcile_once)
                except Exception:
                    logger.exception("Feishu listener reconciliation failed")
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
            await asyncio.to_thread(self._stop_all)


feishu_listener_manager = FeishuListenerManager()
