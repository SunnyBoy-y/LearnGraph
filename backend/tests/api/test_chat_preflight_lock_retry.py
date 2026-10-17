import json
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.database import SessionLocal
from app.domain.models import UsageEvent
from app.services.billing import BillingService


@pytest.mark.parametrize(
    "failures,error_text,expected_calls,completed",
    [(2, "database is locked", 3, True),
     (10, "database is locked", 4, False),
     (1, "no such table: missing", 1, False)],
)
def test_chat_preflight_retries_only_transient_lock_before_creating_messages(
    client, register_user, auth_headers, stub_chat_service, monkeypatch,
    failures, error_text, expected_calls, completed,
):
    token, workspace_id, *_ = register_user("preflight")
    headers = auth_headers(token, workspace_id)
    session_id = client.post("/api/v1/sessions", headers=headers, json={"title": "preflight"}).json()["id"]
    original = BillingService.preflight_model_call
    calls = 0

    def interrupted_preflight(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= failures:
            # Perform a real write so recovery must roll back the interrupted
            # transaction before starting another budget reservation.
            self._lock_budget()
            raise OperationalError("UPDATE workspaces", {}, sqlite3.OperationalError(error_text))
        return original(self, **kwargs)

    monkeypatch.setattr(BillingService, "preflight_model_call", interrupted_preflight)
    response = client.post(
        f"/api/v1/sessions/{session_id}/messages/stream", headers=headers,
        json={"content": "preflight lock regression", "web_search": False},
    )
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert calls == expected_calls
    assert any(event["type"] == "message.completed" for event in events) is completed
    rows = client.get(f"/api/v1/sessions/{session_id}/messages", headers=headers).json()["items"]
    assert len(rows) == (2 if completed else 0)
    if completed:
        assert [row["role"] for row in rows] == ["user", "assistant"]
        assert rows[-1]["status"] == "completed"
    with SessionLocal() as db:
        charges = db.scalars(select(UsageEvent).where(
            UsageEvent.workspace_id == workspace_id, UsageEvent.feature == "chat",
        )).all()
        assert len(charges) == (1 if completed else 0)
