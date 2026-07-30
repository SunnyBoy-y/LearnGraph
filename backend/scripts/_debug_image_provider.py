from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "learngraph.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== image providers ===")
for row in cur.execute(
    """
    SELECT id, display_name, provider_type, base_url, api_key_masked, enabled, status, capabilities, remote_capability
    FROM provider_configs
    WHERE provider_type = 'openai_images' OR display_name LIKE '%OpenAI%' OR display_name LIKE '%图%'
    ORDER BY updated_at DESC
    """
).fetchall():
    print(dict(row))

print("\n=== failed image tasks ===")
for row in cur.execute(
    """
    SELECT id, provider_id, model_id, status, error_code, error_message, created_at, prompt_summary
    FROM image_generation_tasks
    WHERE status = 'failed'
    ORDER BY created_at DESC
    LIMIT 20
    """
).fetchall():
    print(dict(row))

print("\n=== stream events for failed message ===")
msg_id = "39c39a3e-f908-459b-8997-c45fbdf6b926"
for row in cur.execute(
    """
    SELECT sequence, event_type, payload
    FROM message_stream_events
    WHERE message_id = ?
    ORDER BY sequence
    """,
    (msg_id,),
).fetchall():
    payload = row["payload"]
    if isinstance(payload, str) and len(payload) > 1500:
        payload = payload[:1500] + "...<truncated>"
    print(row["sequence"], row["event_type"], payload)

print("\n=== provider secrets presence ===")
for row in cur.execute(
    """
    SELECT provider_id, algorithm, key_provider, key_version, secret_version,
           length(ciphertext) AS cipher_len, revoked_at
    FROM provider_secrets
    WHERE provider_id IN (
      SELECT id FROM provider_configs WHERE provider_type = 'openai_images'
    )
    """
).fetchall():
    print(dict(row))
