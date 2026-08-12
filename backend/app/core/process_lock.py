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
from sqlalchemy.exc import IntegrityError, OperationalError
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
    try:
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
        expires = row.expires_at
        if expires is not None and expires.tzinfo is None:
            # SQLite drops the UTC tzinfo on storage; interpret the stored
            # wall-clock value as UTC before comparing with the aware `now`.
            expires = expires.replace(tzinfo=timezone.utc)
        if expires is None or expires < now:
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
    except OperationalError:
        # SQLite write-lock contention (another process claiming/releasing
        # the lease, or shutdown overlap): skip this round instead of crashing
        # the scheduler task; the next interval retries.
        db.rollback()
        return None


def release_advisory_lock(db: Session, name: str, token: str) -> None:
    """Release the lease only if we still own it (token match).

    A release that hits write-lock contention is dropped silently: the lease
    expires on its own (``ttl_seconds``) so the next claim is not blocked.
    """
    try:
        row = db.scalar(select(AdvisoryLock).where(AdvisoryLock.name == name))
        if row is not None and row.token == token:
            db.delete(row)
            db.commit()
    except OperationalError:
        db.rollback()
