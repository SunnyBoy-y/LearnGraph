"""Audited dual-model ASR configuration for the local Qwen Provider.

Stored files use the OpenAI-compatible HTTP transcription endpoint while live
microphone dictation uses the DashScope realtime WebSocket endpoint.  Update
through ProviderService so the change remains workspace-scoped and audited.
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
STORED_MODEL_ID = "qwen3-asr-flash"
REALTIME_MODEL_ID = "paraformer-realtime-v2"
DB_PATH = "data/learngraph.db"


def current_values() -> tuple[str | None, str | None]:
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT capabilities FROM provider_configs WHERE id = ?",
            (PROVIDER_ID,),
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return None, None
    capabilities = json.loads(row[0])
    return (
        capabilities.get("default_transcription_model_id"),
        capabilities.get("default_realtime_transcription_model_id"),
    )


def main() -> None:
    before = current_values()
    print(f"BEFORE: stored={before[0]!r}, realtime={before[1]!r}")
    target = (STORED_MODEL_ID, REALTIME_MODEL_ID)
    if before == target:
        print("Already configured; nothing to do.")
        return

    db = SessionLocal()
    try:
        provider = ProviderService(
            db, WORKSPACE_ID, ACTOR_ID, get_settings()
        ).update(
            PROVIDER_ID,
            ProviderUpdateRequest(
                default_transcription_model_id=STORED_MODEL_ID,
                default_realtime_transcription_model_id=REALTIME_MODEL_ID,
            ),
        )
        capabilities = dict(provider.capabilities or {})
        print(
            "AFTER: stored={!r}, realtime={!r}".format(
                capabilities.get("default_transcription_model_id"),
                capabilities.get("default_realtime_transcription_model_id"),
            )
        )
    finally:
        db.close()

    after = current_values()
    print(f"DB readback: stored={after[0]!r}, realtime={after[1]!r}")
    if after != target:
        print("!! readback mismatch")
        sys.exit(1)


if __name__ == "__main__":
    main()
