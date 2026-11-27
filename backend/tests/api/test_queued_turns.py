"""Server-backed queued chat turns: idempotency, edit, cancel, promote, atomic claim & drain."""
from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.models import DurableJob
from app.services.queued_turns import QUEUED_CHAT_TURN_JOB_KIND


def _create_session(client, headers) -> str:
    resp = client.post("/api/v1/sessions", headers=headers, json={"title": "Queue Test"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_queued_turn_idempotent_enqueue_edit_promote_and_cancel(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    session_id = _create_session(client, headers)

    # Enqueue first turn (and replay with identical client_request_id -> no duplicate)
    r1 = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns",
        headers=headers,
        json={"client_request_id": "req-1", "content": "first follow-up", "payload": {"agent_mode": True}},
    )
    assert r1.status_code == 201, r1.text
    turn1 = r1.json()
    assert turn1["status"] == "queued"
    assert turn1["content"] == "first follow-up"
    assert turn1["payload"]["agent_mode"] is True

    r1_replay = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns",
        headers=headers,
        json={"client_request_id": "req-1", "content": "first follow-up"},
    )
    assert r1_replay.status_code == 201
    assert r1_replay.json()["id"] == turn1["id"]

    # Enqueue second turn
    r2 = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns",
        headers=headers,
        json={"client_request_id": "req-2", "content": "second follow-up"},
    )
    assert r2.status_code == 201
    turn2 = r2.json()

    # Edit second turn
    r_edit = client.patch(
        f"/api/v1/sessions/{session_id}/queued-turns/{turn2['id']}",
        headers=headers,
        json={"content": "second follow-up (edited)"},
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["content"] == "second follow-up (edited)"

    # Promote second turn to front of queue
    r_promote = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns/{turn2['id']}/promote",
        headers=headers,
        json={"cancel_active": False},
    )
    assert r_promote.status_code == 200
    assert r_promote.json()["turn"]["id"] == turn2["id"]

    listed = client.get(f"/api/v1/sessions/{session_id}/queued-turns", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [turn2["id"], turn1["id"]]

    # Cancel first turn
    r_cancel = client.delete(
        f"/api/v1/sessions/{session_id}/queued-turns/{turn1['id']}",
        headers=headers,
    )
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "cancelled"

    listed_after = client.get(f"/api/v1/sessions/{session_id}/queued-turns", headers=headers)
    assert [item["id"] for item in listed_after.json()["items"]] == [turn2["id"]]


def test_queued_turn_atomic_claim_prevents_double_dispatch_and_supports_requeue(
    client, register_user, auth_headers
):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    session_id = _create_session(client, headers)

    created = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns",
        headers=headers,
        json={"client_request_id": "req-atomic", "content": "only send once"},
    ).json()
    turn_id = created["id"]

    # First tab wins atomic claim CAS
    claim1 = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns/{turn_id}/claim",
        headers=headers,
    )
    assert claim1.status_code == 200, claim1.text
    assert claim1.json()["status"] == "dispatching"

    # Second tab receives 409 Conflict
    claim2 = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns/{turn_id}/claim",
        headers=headers,
    )
    assert claim2.status_code == 409

    # Requeue on client-side validation error allows claiming again
    requeued = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns/{turn_id}/requeue",
        headers=headers,
    )
    assert requeued.status_code == 200
    assert requeued.json()["status"] == "queued"

    claim3 = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns/{turn_id}/claim",
        headers=headers,
    )
    assert claim3.status_code == 200

    completed = client.post(
        f"/api/v1/sessions/{session_id}/queued-turns/{turn_id}/complete",
        headers=headers,
        json={"dispatched_message_id": "msg-123"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "dispatched"
    assert completed.json()["dispatched_message_id"] == "msg-123"

    # Dispatched turns no longer appear in active queue
    listed = client.get(f"/api/v1/sessions/{session_id}/queued-turns", headers=headers)
    assert listed.json()["items"] == []

    # Verify a durable drain job was scheduled when enqueueing on an idle session
    with SessionLocal() as db:
        job = db.scalar(
            select(DurableJob).where(
                DurableJob.workspace_id == ws,
                DurableJob.kind == QUEUED_CHAT_TURN_JOB_KIND,
            )
        )
        assert job is not None
        assert job.payload["session_id"] == session_id
