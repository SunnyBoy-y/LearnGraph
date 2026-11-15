"""Persistent opener pools with database-backed generation leases."""
from __future__ import annotations

import hashlib
import json
import math
import time
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.domain.models import OpeningPromptCache

POOL_SIZE = 9
GENERATION_INTERVAL = 600
GENERATION_WINDOW = 3600
MAX_GENERATIONS = 3
LEASE_SECONDS = 180
FAILURE_BACKOFF = 30


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class OpeningPromptCacheStore:
    def __init__(self, db, context):
        self.db = db
        self.workspace_id = context.workspace_id
        self.id = fingerprint([context.principal.tenant_id, context.workspace_id, context.principal.user_id])
        self.token = None

    def row(self):
        row = self.db.scalar(select(OpeningPromptCache).where(OpeningPromptCache.id == self.id).execution_options(populate_existing=True))
        if row is None:
            try:
                with self.db.begin_nested():
                    row = OpeningPromptCache(id=self.id, workspace_id=self.workspace_id)
                    self.db.add(row)
                    self.db.flush()
                self.db.commit()
            except IntegrityError:
                row = self.db.get(OpeningPromptCache, self.id, populate_existing=True)
        return row

    def retry_after(self, row) -> int:
        now = time.time()
        until = max(row.next_generation_at, row.lease_until)
        if now < row.window_started_at + GENERATION_WINDOW and row.generation_count >= MAX_GENERATIONS:
            until = max(until, row.window_started_at + GENERATION_WINDOW)
        return max(0, math.ceil(until - now))

    def read(self, row, scope, context_hash, source_hashes=None):
        entry = row.pools.get(scope)
        if not entry:
            return None
        previous_sources = entry.get("source_hashes")
        if previous_sources is not None and source_hashes is not None:
            if any(source_hashes.get(key) != value for key, value in previous_sources.items()):
                return None
        elif entry.get("context_hash") != context_hash:
            return None
        return entry

    def claim(self) -> bool:
        for _ in range(3):
            row = self.row()
            now = time.time()
            if self.retry_after(row):
                self.db.commit()
                return False
            reset = now >= row.window_started_at + GENERATION_WINDOW
            token = str(uuid4())
            claimed = self.db.execute(update(OpeningPromptCache).where(
                OpeningPromptCache.id == self.id, OpeningPromptCache.version == row.version,
            ).values(
                version=row.version + 1,
                window_started_at=now if reset else row.window_started_at,
                generation_count=1 if reset else row.generation_count + 1,
                lease_token=token, lease_until=now + LEASE_SECONDS,
                next_generation_at=now + GENERATION_INTERVAL,
            ).execution_options(synchronize_session=False)).rowcount
            self.db.commit()
            if claimed:
                self.token = token
                return True
        return False

    def save(self, scope, context_hash, response, source_hashes=None):
        row = self.row()
        now = time.time()
        pools = {key: entry for key, entry in row.pools.items() if key != scope}
        pools = dict(sorted(pools.items(), key=lambda item: item[1]["generated_at"], reverse=True)[:7])
        pools[scope] = {"context_hash": context_hash, "source_hashes": source_hashes,
                        "generated_at": now, "response": response.model_dump()}
        changed = self.db.execute(update(OpeningPromptCache).where(
            OpeningPromptCache.id == self.id, OpeningPromptCache.lease_token == self.token,
        ).values(pools=pools, version=OpeningPromptCache.version + 1, lease_token=None,
                 lease_until=0, next_generation_at=now + GENERATION_INTERVAL)
            .execution_options(synchronize_session=False)).rowcount
        self.db.commit()
        self.token = None
        if not changed:
            raise AppError(409, "opening_prompt_lease_expired", "Opening prompt generation lease expired")

    def release(self):
        if self.token is None:
            return
        self.db.rollback()
        self.db.execute(update(OpeningPromptCache).where(
            OpeningPromptCache.id == self.id, OpeningPromptCache.lease_token == self.token,
        ).values(version=OpeningPromptCache.version + 1, lease_token=None, lease_until=0,
                 next_generation_at=time.time() + FAILURE_BACKOFF)
            .execution_options(synchronize_session=False))
        self.db.commit()
        self.token = None
