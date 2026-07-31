from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.domain.memory_event_models import MemoryProjectionOutbox, utc_now


class OutboxHandler(Protocol):
    def __call__(self, item: MemoryProjectionOutbox) -> None: ...


@dataclass(frozen=True, slots=True)
class ClaimedOutboxItem:
    id: str
    lease_generation: int


@dataclass(frozen=True, slots=True)
class OutboxRunReport:
    claimed: int
    succeeded: int
    failed: int
    dead_letter: int
    ownership_lost: int = 0


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
        strict_leases: bool = False,
    ) -> None:
        self.db = db
        self.handlers = handlers
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.strict_leases = strict_leases

    def run_once(self, *, limit: int = 25) -> OutboxRunReport:
        now = utc_now()
        candidates = self.db.scalars(
            select(MemoryProjectionOutbox.id)
            .where(
                MemoryProjectionOutbox.available_at <= now,
                or_(
                    MemoryProjectionOutbox.status == "queued",
                    MemoryProjectionOutbox.status == "failed",
                    and_(
                        MemoryProjectionOutbox.status == "leased",
                        MemoryProjectionOutbox.lease_until < now,
                    ),
                ),
            )
            .order_by(MemoryProjectionOutbox.available_at, MemoryProjectionOutbox.created_at)
            .limit(limit)
        ).all()
        claimed = self._claim(candidates, now)
        self.db.commit()

        succeeded = failed = dead_letter = ownership_lost = 0
        for claim in claimed:
            item = self.db.get(MemoryProjectionOutbox, claim.id)
            if item is None or not self._is_claim_owner(item, claim):
                ownership_lost += 1
                continue
            handler = self.handlers.get(item.projection_kind)
            try:
                if handler is None:
                    raise RuntimeError(f"no handler for {item.projection_kind}")
                handler(item)
                if self._mark_succeeded(claim):
                    succeeded += 1
                else:
                    ownership_lost += 1
            except Exception as exc:
                outcome = self._mark_failed(claim, str(exc))
                if outcome == "failed":
                    failed += 1
                elif outcome == "dead_letter":
                    dead_letter += 1
                else:
                    ownership_lost += 1
            self.db.commit()
        return OutboxRunReport(
            len(claimed), succeeded, failed, dead_letter, ownership_lost
        )

    def _claim(self, candidate_ids: list[str], now) -> list[ClaimedOutboxItem]:
        claimed: list[ClaimedOutboxItem] = []
        lease_until = now + timedelta(seconds=self.lease_seconds)
        for item_id in candidate_ids:
            eligibility = self._claim_eligibility(now)
            values = {
                "status": "leased",
                "lease_owner": self.worker_id,
                "lease_until": lease_until,
            }
            if self.strict_leases:
                values["lease_generation"] = MemoryProjectionOutbox.lease_generation + 1
            result = self.db.execute(
                update(MemoryProjectionOutbox)
                .where(MemoryProjectionOutbox.id == item_id, eligibility)
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                continue
            self.db.expire_all()
            item = self.db.get(MemoryProjectionOutbox, item_id)
            if item is not None:
                claimed.append(ClaimedOutboxItem(item.id, item.lease_generation))
        return claimed

    def _claim_eligibility(self, now):
        if not self.strict_leases:
            return MemoryProjectionOutbox.status.in_(("queued", "failed", "leased"))
        return and_(
            MemoryProjectionOutbox.available_at <= now,
            or_(
                MemoryProjectionOutbox.status == "queued",
                MemoryProjectionOutbox.status == "failed",
                and_(
                    MemoryProjectionOutbox.status == "leased",
                    MemoryProjectionOutbox.lease_until < now,
                ),
            ),
        )

    def _is_claim_owner(
        self, item: MemoryProjectionOutbox, claim: ClaimedOutboxItem
    ) -> bool:
        if item.status != "leased" or item.lease_owner != self.worker_id:
            return False
        return not self.strict_leases or item.lease_generation == claim.lease_generation

    def _claim_condition(self, claim: ClaimedOutboxItem):
        conditions = [
            MemoryProjectionOutbox.id == claim.id,
            MemoryProjectionOutbox.status == "leased",
            MemoryProjectionOutbox.lease_owner == self.worker_id,
        ]
        if self.strict_leases:
            conditions.append(
                MemoryProjectionOutbox.lease_generation == claim.lease_generation
            )
        return and_(*conditions)

    def _mark_succeeded(self, claim: ClaimedOutboxItem) -> bool:
        result = self.db.execute(
            update(MemoryProjectionOutbox)
            .where(self._claim_condition(claim))
            .values(
                status="succeeded",
                last_error="",
                lease_owner=None,
                lease_until=None,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def _mark_failed(self, claim: ClaimedOutboxItem, error: str) -> str | None:
        item = self.db.get(MemoryProjectionOutbox, claim.id)
        if item is None or not self._is_claim_owner(item, claim):
            return None
        attempts = item.attempt_count + 1
        values: dict[str, object] = {
            "attempt_count": attempts,
            "last_error": error[:2_000],
            "lease_owner": None,
            "lease_until": None,
        }
        if attempts >= self.max_attempts:
            values["status"] = "dead_letter"
            outcome = "dead_letter"
        else:
            values["status"] = "failed"
            delay = min(3_600, 2 ** min(attempts, 10))
            values["available_at"] = utc_now() + timedelta(seconds=delay)
            outcome = "failed"
        result = self.db.execute(
            update(MemoryProjectionOutbox)
            .where(self._claim_condition(claim))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return outcome if result.rowcount == 1 else None

    @staticmethod
    def retry(db: Session, outbox_id: str) -> bool:
        now = utc_now()
        result = db.execute(
            update(MemoryProjectionOutbox)
            .where(
                MemoryProjectionOutbox.id == outbox_id,
                or_(
                    MemoryProjectionOutbox.status.in_(("failed", "dead_letter", "succeeded")),
                    and_(
                        MemoryProjectionOutbox.status == "leased",
                        MemoryProjectionOutbox.lease_until < now,
                    ),
                ),
            )
            .values(
                status="queued",
                available_at=now,
                lease_owner=None,
                lease_until=None,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return result.rowcount == 1
