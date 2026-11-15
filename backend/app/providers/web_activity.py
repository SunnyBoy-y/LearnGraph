"""Translate only actual upstream web-tool events, never intent, into UI activity."""
from __future__ import annotations

from typing import Any

from app.services.web_evidence import provider_label


def native_web_activity(event: dict[str, Any], provider: Any) -> dict[str, Any] | None:
    event_type = str(event.get("type") or "")
    item = event.get("item")
    item = item if isinstance(item, dict) else {}
    if item.get("type") != "web_search_call" and not event_type.startswith("response.web_search_call."):
        return None
    status = item.get("status") or event_type.rsplit(".", 1)[-1]
    if status in {"completed", "done"}:
        state = "completed"
    elif status in {"failed", "incomplete", "cancelled"}:
        state = "failed"
    elif status in {"in_progress", "searching"}:
        state = "streaming"
    else:
        state = "pending"
    action = item.get("action")
    action = action if isinstance(action, dict) else {}
    return {
        "id": str(item.get("id") or event.get("item_id") or "native-web")[:160],
        "status": state,
        "operation": "fetch" if action.get("type") in {"open_page", "find_in_page"} else "search",
        "provider_label": provider_label(provider),
        "provider_id": str(getattr(provider, "provider_id", "")),
        "target": str(action.get("url") or action.get("query") or "")[:500],
        "native": True,
    }
