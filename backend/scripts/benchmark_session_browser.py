"""Reproducible synthetic benchmark; never opens the application's database.
Run from backend: uv run python scripts/benchmark_session_browser.py
"""
from __future__ import annotations

import gc
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.core.security import Principal
from app.domain.models import ChatSession, Membership, ResourceACL, Workspace
from app.domain.schemas.chat import SessionView
from app.core.migrations.session_browser import apply_session_browser
from app.services.session_browser import SessionBrowser


def run(size):
    engine = create_engine('sqlite://')
    for model in (Workspace, ChatSession, ResourceACL, Membership):
        model.__table__.create(engine)
    with engine.begin() as connection:
        apply_session_browser(connection)
    with Session(engine) as db:
        workspace = Workspace(id='benchmark', tenant_id='benchmark-tenant', owner_user_id='benchmark-owner', name='Synthetic benchmark')
        db.add(workspace)
        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        db.execute(ChatSession.__table__.insert(), [dict(
            id=f'benchmark-{index:06}', workspace_id=workspace.id, title=f'Synthetic session {index}',
            created_at=now - timedelta(minutes=index), updated_at=now - timedelta(minutes=index),
            model_snapshot={'synthetic_payload': 'x' * 1024}, context_capsule={'synthetic_payload': 'y' * 1024},
        ) for index in range(size)])
        db.commit()
        browser = SessionBrowser(db, workspace, Principal(user_id='benchmark-owner', username='benchmark', tenant_id='benchmark-tenant', session_id='benchmark-auth'))
        def legacy():
            rows = db.scalars(select(ChatSession).where(ChatSession.workspace_id == workspace.id).order_by(ChatSession.updated_at.desc())).all()
            return json.dumps([SessionView.model_validate(row).model_dump(mode='json') for row in rows], separators=(',', ':')).encode()
        def paged():
            return browser.page(limit=20, ungrouped=True).model_dump_json().encode()
        results = {}
        for name, operation in [('legacy_full', legacy), ('paged_20', paged)]:
            timings, length = [], 0
            for _ in range(3):
                db.expunge_all(); gc.collect()
                started = perf_counter(); payload = operation(); timings.append((perf_counter() - started) * 1000); length = len(payload)
            results[name] = {'median_ms': round(median(timings), 2), 'response_bytes': length}
        results['session_count'] = size
    engine.dispose()
    return results


if __name__ == '__main__':
    print(json.dumps({'kind': 'synthetic_local_sqlite_not_production', 'runs': [run(1000), run(10000)]}, ensure_ascii=False, indent=2))
