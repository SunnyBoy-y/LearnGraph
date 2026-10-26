"""Read-only memory admission audit. Never prints memory text or credentials.

Run from backend: python -m scripts.audit_memory_contamination
Old extraction records without verbatim proof are review candidates, not proven
false facts. This command does not delete or quarantine them.
"""
from __future__ import annotations

from collections import Counter
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import MemoryRecord, MemoryRevision
from app.services.memory_safety import record_source_reason, record_preference_verified
from app.services.memory_usage import classify_memory


def audit(db: Session) -> dict:
    reasons: Counter = Counter()
    sources: Counter = Counter()
    checked = 0
    blocked = 0
    roles: Counter = Counter()
    unverified_preferences = 0
    for record, revision in db.execute(
        select(MemoryRecord, MemoryRevision).outerjoin(MemoryRevision, (
            (MemoryRevision.workspace_id == MemoryRecord.workspace_id)
            & (MemoryRevision.memory_id == MemoryRecord.id)
            & (MemoryRevision.revision == MemoryRecord.revision)
        )).where(MemoryRecord.state == "active", MemoryRecord.deleted_at.is_(None),
                 MemoryRecord.content_destroyed_at.is_(None))
    ):
        checked += 1
        sources[record.source] += 1
        if revision and revision.content:
            role = str(classify_memory(revision.content, record.record_kind)["role"])
            roles[role] += 1
            if role == "preference" and not record_preference_verified(db, record, revision.content):
                unverified_preferences += 1
        reason = ("missing_current_revision" if revision is None or not revision.content
                  else record_source_reason(db, record, revision.content, strict_legacy=True))
        if reason:
            reasons[reason] += 1
            if revision is None or not revision.content or record_source_reason(db, record, revision.content):
                blocked += 1
    return {
        "read_only": True, "active_records_checked": checked,
        "sources": dict(sorted(sources.items())),
        "review_candidates": sum(reasons.values()),
        "would_block_on_recall": blocked,
        "memory_roles": dict(sorted(roles.items())),
        "preferences_without_user_preference_evidence": unverified_preferences,
        "reasons": dict(sorted(reasons.items())),
        "note": "Review candidates are not all proven contamination; legacy extraction may lack verifiable source quotes.",
    }


def main() -> None:
    from app.core.database import engine

    with engine.connect() as connection:
        dialect = connection.dialect.name
        if dialect not in {"sqlite", "postgresql"}:
            raise RuntimeError("Read-only audit supports SQLite and PostgreSQL only")
        try:
            connection.exec_driver_sql("PRAGMA query_only=ON" if dialect == "sqlite" else "SET TRANSACTION READ ONLY")
            with Session(bind=connection, autoflush=False) as db:
                print(json.dumps(audit(db), ensure_ascii=False, indent=2))
        finally:
            connection.rollback()
            if dialect == "sqlite":
                connection.exec_driver_sql("PRAGMA query_only=OFF")
                connection.rollback()


if __name__ == "__main__":
    main()
