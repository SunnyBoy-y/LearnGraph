"""Audited workspace config update: switch the transcription Provider's default
realtime model id from the MaaS-catalog alias (qwen3-asr-flash-realtime, which
the DashScope WS gateway rejects with ModelNotFound) to a verified realtime
model name (paraformer-realtime-v2, confirmed by scripts/probe_realtime_*.py to
task-start and finish cleanly on the same provider host).

Goes through ProviderManagementService.update() so the change is audited with
the owning user as the actor — not a raw row mutation.
"""

from __future__ import annotations

import json
import sqlite3
import sys

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.schemas.management import ProviderUpdateRequest
from app.services.management import ProviderService

WORKSPACE_ID = "db22583c-63a8-4c8c-accd-ffc0e884b934"
PROVIDER_ID = "a4fc1d86-ee37-4779-bbb9-e8bb2520c0b2"
ACTOR_ID = "2bd1f74c-52cc-4ffd-889d-1571776d5fa3"  # goodhelloworlds233
NEW_MODEL_ID = "paraformer-realtime-v2"
DB_PATH = "data/learngraph.db"


def current_value() -> str | None:
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT capabilities FROM provider_configs WHERE id = ?",
            (PROVIDER_ID,),
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return None
    return json.loads(row[0]).get("default_transcription_model_id")


def main() -> None:
    before = current_value()
    print(f"BEFORE: default_transcription_model_id = {before!r}")
    if before == NEW_MODEL_ID:
        print("Already set; nothing to do.")
        return

    settings = get_settings()
    db = SessionLocal()
    try:
        svc = ProviderService(db, WORKSPACE_ID, ACTOR_ID, settings)
        payload = ProviderUpdateRequest(default_transcription_model_id=NEW_MODEL_ID)
        provider = svc.update(PROVIDER_ID, payload)
        caps = provider.capabilities if isinstance(provider.capabilities, dict) else json.loads(provider.capabilities or "{}")
        print(f"AFTER:  capabilities role   = {caps.get('provider_role')}")
        print(f"AFTER:  new model id        = {caps.get('default_transcription_model_id')!r}")
    finally:
        db.close()

    after = current_value()
    print(f"DB readback: {after!r}")
    if after != NEW_MODEL_ID:
        print("!! readback mismatch")
        sys.exit(1)


if __name__ == "__main__":
    main()
