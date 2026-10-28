"""Refresh shared models.dev prices daily without modifying workspace overrides."""

from __future__ import annotations

import asyncio
import logging

from app.core.database import SessionLocal
from app.core.process_lock import acquire_advisory_lock, release_advisory_lock
from app.providers.models_dev import refresh_snapshot, snapshot_status

logger = logging.getLogger(__name__)
POLL_SECONDS = 60
RETRY_SECONDS = 15 * 60
LOCK_NAME = "models-dev-price-refresh"


def refresh_if_due() -> None:
    if not snapshot_status()["stale"]:
        return
    with SessionLocal() as db:
        token = acquire_advisory_lock(db, LOCK_NAME, ttl_seconds=120)
    if token is None:
        return
    try:
        if snapshot_status()["stale"]:
            status = refresh_snapshot()
            logger.info("models.dev refreshed: %s models", status["model_count"])
    finally:
        with SessionLocal() as db:
            release_advisory_lock(db, LOCK_NAME, token)


async def model_pricing_sync_worker(stop: asyncio.Event) -> None:
    # Catch up on startup; an unavailable source retains the last good cache.
    while not stop.is_set():
        delay = POLL_SECONDS
        try:
            await asyncio.to_thread(refresh_if_due)
        except Exception:
            logger.exception("models.dev refresh failed; retaining the last successful prices")
            delay = RETRY_SECONDS
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
