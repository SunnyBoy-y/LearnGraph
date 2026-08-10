"""Cross-process advisory locks for scheduler sweeps (B1-7).

SQLite has no built-in advisory locks; a single-writer DB plus multiple
uvicorn workers would otherwise run every scheduler sweep in every process
(repeated writes, repeated paid LLM calls). This module keeps a small
``advisory_locks`` table with an owner token and a TTL: only the process that
successfully claims (or renews) the lease runs the sweep; the others skip the
round. Leases expire so a crashed worker cannot permanently block a sweep.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models import AdvisoryLock


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def acquire_advisory_lock(
    db: Session,
    name: str,
    *,
    ttl_seconds: int = 300,
) -> str | None:
    """Try to claim the named lease. Returns an opaque token on success.

    A caller that returns None must skip the sweep round. The token must be
    passed back to ``release_advisory_lock`` so a stale owner cannot release
    the lease of the current owner.
    """
    token = uuid4().hex
    now = utc_now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    row = db.scalar(select(AdvisoryLock).where(AdvisoryLock.name == name))
    if row is None:
        db.add(
            AdvisoryLock(
                name=name,
                token=token,
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )
        )
        try:
            db.commit()
            return token
        except IntegrityError:
            # Another process won the insert race.
            db.rollback()
            return None
    if row.expires_at is None or row.expires_at < now:
        row.token = token
        row.expires_at = expires_at
        row.updated_at = now
        try:
            db.commit()
            return token
        except IntegrityError:  # pragma: no cover - defensive
            db.rollback()
            return None
    return None


def release_advisory_lock(db: Session, name: str, token: str) -> None:
    """Release the lease only if we still own it (token match)."""
    row = db.scalar(select(AdvisoryLock).where(AdvisoryLock.name == name))
    if row is not None and row.token == token:
        db.delete(row)
        db.commit()
