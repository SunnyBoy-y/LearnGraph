from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "learngraph.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

tables = [
    r[0]
    for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
]
print("TABLES:", tables)

for t in tables:
    if any(x in t.lower() for x in ["image", "provider", "message_stream", "usage", "attempt"]):
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})").fetchall()]
        print(t, cols)

# Recent failed image tasks
for table in tables:
    if "image" in table.lower():
        print("\n=== sample from", table, "===")
        rows = cur.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 5").fetchall()
        for row in rows:
            print(dict(row))

# Search stream events / parts for the error text
needles = [
    "unsupported SSE event type",
    "图片生成失败",
    "image_generation",
    "gpt-image",
]
for table in tables:
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    text_cols = [
        c
        for c in cols
        if any(
            k in c.lower()
            for k in ("content", "payload", "error", "status", "trace", "message", "detail", "data")
        )
    ]
    if not text_cols:
        continue
    for col in text_cols:
        for needle in needles:
            try:
                rows = cur.execute(
                    f"SELECT rowid, {col} FROM {table} WHERE CAST({col} AS TEXT) LIKE ? ORDER BY rowid DESC LIMIT 5",
                    (f"%{needle}%",),
                ).fetchall()
            except Exception as exc:  # noqa: BLE001
                print("query fail", table, col, exc)
                continue
            if rows:
                print(f"\nHIT {table}.{col} ~ {needle}")
                for row in rows:
                    value = row[1]
                    if isinstance(value, str) and len(value) > 2000:
                        value = value[:2000] + "...<truncated>"
                    print(row[0], value)

# Provider configs
for table in tables:
    if "provider" in table.lower():
        print("\n=== providers in", table, "===")
        rows = cur.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 20").fetchall()
        for row in rows:
            d = dict(row)
            for k in list(d):
                if "secret" in k.lower() or "key" in k.lower() or "token" in k.lower():
                    if d[k]:
                        d[k] = "***"
            print(d)
