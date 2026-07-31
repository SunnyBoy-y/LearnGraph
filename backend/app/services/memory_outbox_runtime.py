from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.memory_outbox import MemoryOutboxWorker
from app.services.memory_provider_projector import MemoryProviderProjector

logger = logging.getLogger(__name__)


def run_memory_outbox_once(*, limit: int = 25) -> dict[str, int]:
    settings = get_settings()
    with SessionLocal() as db:
        provider_projector = MemoryProviderProjector(db, settings)

        def acknowledge_sync_projection(_item) -> None:
            # Structured/FTS projections are applied in the canonical command
            # transaction. This durable signal records that fact for replay parity.
            return None

        def unsupported_projection(item) -> None:
            raise RuntimeError(
                f"durable handler for {item.projection_kind} is not configured"
            )

        worker = MemoryOutboxWorker(
            db,
            {
                "markdown": provider_projector.handle,
                "mem0": provider_projector.handle,
                "embedding": unsupported_projection,
                "profile": unsupported_projection,
                "index": acknowledge_sync_projection,
                "episode": acknowledge_sync_projection,
            },
            worker_id="embedded-memory-outbox",
            lease_seconds=settings.memory_outbox_lease_seconds,
            max_attempts=settings.memory_outbox_max_attempts,
        )
        report = worker.run_once(limit=limit)
        return {
            "claimed": report.claimed,
            "succeeded": report.succeeded,
            "failed": report.failed,
            "dead_letter": report.dead_letter,
        }


async def memory_outbox_scheduler(stop, interval_seconds: int | None = None) -> None:
    import asyncio

    settings = get_settings()
    interval = max(
        1,
        interval_seconds
        if interval_seconds is not None
        else settings.memory_outbox_interval_seconds,
    )
    idle_cycles = 0
    while not stop.is_set():
        try:
            report = await asyncio.to_thread(run_memory_outbox_once)
            idle_cycles = idle_cycles + 1 if report["claimed"] == 0 else 0
        except Exception:
            logger.exception("Periodic memory outbox wake-up failed")
            idle_cycles += 1
        wait_seconds = min(60, interval * (2 ** min(idle_cycles, 4)))
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait_seconds)
        except TimeoutError:
            continue
