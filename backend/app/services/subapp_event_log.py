"""Best-effort JSONL analytics copy of sub-application interaction events.

The database is the single source of truth; this file is a rebuildable
analytics/audit copy used for offline analysis and export, and is NEVER used
for authorization. It follows the same single-process assumption as the rest of
the SQLite-backed app (append is atomic enough under one writer). Failures are
swallowed so the event-ingest path is never blocked by logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

SUBAPP_EVENT_LOG_DIR_ENV = "LEARNGRAPH_SUBAPP_EVENT_LOG_DIR"


class SubAppEventLogWriter:
    """Append one normalized event line per ingest into per-workspace JSONL."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = base_dir or os.environ.get(
            SUBAPP_EVENT_LOG_DIR_ENV, "/data/subapp-events"
        )

    def append(self, *, workspace_id: str, event: dict[str, Any]) -> None:
        try:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            directory = Path(self.base_dir) / str(workspace_id)
            directory.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with open(directory / f"{day}.jsonl", "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:  # noqa: BLE001 — logging must never break ingest
            return
