"""Refresh the bundled, offline models.dev capability catalogue.

Run from the repository root::

    uv run --project backend python backend/scripts/update_models_dev_snapshot.py

This updates the checked-in fallback only, not the runtime network cache or any
workspace/provider configuration. Bundled prices remain unavailable for billing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.providers.models_dev import MODELS_DEV_URL, build_snapshot

BUNDLED_PATH = Path(__file__).resolve().parents[1] / "app/providers/models_dev_snapshot.json"


def update_bundled_snapshot(*, timeout_seconds: float = 45.0) -> dict[str, object]:
    """Fetch and validate before replacing the bundled catalogue in place."""
    response = httpx.get(
        MODELS_DEV_URL,
        timeout=timeout_seconds,
        follow_redirects=True,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    raw = response.json(parse_float=str)
    if not isinstance(raw, dict) or not raw:
        raise ValueError("models.dev returned an unexpected payload shape")
    snapshot = build_snapshot(
        raw,
        origin="bundled",
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    if not snapshot["models"]:
        raise ValueError("models.dev returned an empty model catalogue")

    # Offline defaults must not turn into an independently published tariff.
    # Runtime billing requires a successful network refresh/persisted cache.
    snapshot.pop("pricing_records", None)
    snapshot["_meta"]["source_sha256"] = hashlib.sha256(response.content).hexdigest()

    previous = json.loads(BUNDLED_PATH.read_text(encoding="utf-8"))
    old_models = previous.get("models") or {}
    new_models = snapshot["models"]
    summary: dict[str, object] = {
        "previous": previous.get("_meta", {}),
        "current": snapshot["_meta"],
        "added_models": len(new_models.keys() - old_models.keys()),
        "removed_models": len(old_models.keys() - new_models.keys()),
        "changed_models": sum(
            old_models[key] != new_models[key]
            for key in old_models.keys() & new_models.keys()
        ),
    }
    # Match the existing compact checked-in format (including its lack of BOM).
    # No runtime settings or provider defaults are loaded by this script.
    BUNDLED_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()
    print(json.dumps(update_bundled_snapshot(timeout_seconds=args.timeout_seconds), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
