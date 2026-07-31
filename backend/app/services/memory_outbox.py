from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.domain.memory_event_models import MemoryProjectionOutbox, utc_now


class OutboxHandler(Protocol):
    def __call__(self, item: MemoryProjectionOutbox) -> None: ...


@dataclass(frozen=True, slots=True)
class OutboxRunReport:
    claimed: int
    succeeded: int
    failed: int
    dead_letter: int


class MemoryOutboxWorker:
    """DB-durable leased worker; crashes are recovered when leases expire."""

    def __init__(
        self,
        db: Session,
        handlers: dict[str, OutboxHandler],
        *,
        worker_id: str,
        lease_seconds: int = 120,
        max_attempts: int = 8,
    ) -> None:
        self.db = db
        self.handlers = handlers
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def run_once(self, *, limit: int = 25) -> OutboxRunReport:
        now = utc_now()
        candidates = self.db.scalars(
            select(MemoryProjectionOutbox)
            .where(
                MemoryProjectionOutbox.available_at <= now,
                or_(
                    MemoryProjectionOutbox.status == "queued",
                    (
                        MemoryProjectionOutbox.status == "leased"
                    )
                    & (MemoryProjectionOutbox.lease_until < now),
                    MemoryProjectionOutbox.status == "failed",
                ),
            )
            .order_by(MemoryProjectionOutbox.available_at, MemoryProjectionOutbox.created_at)
            .limit(limit)
        ).all()
        claimed: list[str] = []
        lease_until = now + timedelta(seconds=self.lease_seconds)
        for item in candidates:
            result = self.db.execute(
                update(MemoryProjectionOutbox)
                .where(
                    MemoryProjectionOutbox.id == item.id,
                    MemoryProjectionOutbox.status.in_(("queued", "failed", "leased")),
                )
                .values(
                    status="leased", lease_owner=self.worker_id, lease_until=lease_until
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                claimed.append(item.id)
        self.db.commit()

        succeeded = failed = dead_letter = 0
        for item_id in claimed:
            item = self.db.get(MemoryProjectionOutbox, item_id)
            if item is None or item.lease_owner != self.worker_id:
                continue
            handler = self.handlers.get(item.projection_kind)
            try:
                if handler is None:
                    raise RuntimeError(f"no handler for {item.projection_kind}")
                handler(item)
                item.status = "succeeded"
                item.last_error = ""
                succeeded += 1
            except Exception as exc:
                item.attempt_count += 1
                item.last_error = str(exc)[:2_000]
                if item.attempt_count >= self.max_attempts:
                    item.status = "dead_letter"
                    dead_letter += 1
                else:
                    item.status = "failed"
                    delay = min(3_600, 2 ** min(item.attempt_count, 10))
                    item.available_at = utc_now() + timedelta(seconds=delay)
                    failed += 1
            item.lease_owner = None
            item.lease_until = None
            self.db.commit()
        return OutboxRunReport(len(claimed), succeeded, failed, dead_letter)

    @staticmethod
    def retry(db: Session, outbox_id: str) -> None:
        item = db.get(MemoryProjectionOutbox, outbox_id)
        if item is None:
            return
        item.status = "queued"
        item.available_at = utc_now()
        item.lease_owner = None
        item.lease_until = None
        db.commit()
