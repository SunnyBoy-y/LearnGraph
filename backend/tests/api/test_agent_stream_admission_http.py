"""Agent-stream admission control over real HTTP (P0-4).

Proves the *user-visible* contract of the gate: a saturated deployment answers
a retryable 429 with a stable code, instead of blocking ~10s inside SQLAlchemy
pool checkout and then failing with an opaque ``QueuePool limit ... reached``.

Uses a stub model provider and runs in the regular CI suite.
"""

from __future__ import annotations

from app.core import admission as admission_module
from app.core.admission import AgentStreamAdmissionGate


def _create_session(client, headers, title="admission-session"):
    r = client.post("/api/v1/sessions", headers=headers, json={"title": title})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_saturated_gate_rejects_with_429_and_recovers(
    client, register_user, auth_headers, stub_chat_service, monkeypatch
):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)

    gate = AgentStreamAdmissionGate(1)
    monkeypatch.setattr(admission_module, "_gate", gate)

    # Baseline: with the gate open the stream works.
    ok = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "你好"},
    )
    assert ok.status_code == 200, ok.text
    assert gate.in_flight() == 0, "a finished generation must return its slot"

    # Saturate: hold the only slot as if a long generation were running.
    holder = gate.try_acquire()
    assert holder is not None

    rejected = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "你好"},
    )

    assert rejected.status_code == 429, rejected.text
    body = rejected.json()
    assert body["error"]["code"] == "agent_stream_capacity_exceeded"
    # The client gets numbers it can act on / report.
    assert body["error"]["details"]["limit"] == 1
    assert body["error"]["details"]["in_flight"] == 1
    # And critically: this is a rejection, not a pool blow-up.
    assert "QueuePool" not in rejected.text

    # Recovery: releasing the slot makes the endpoint usable again, so a
    # transient burst does not wedge the deployment.
    holder.release()
    recovered = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "你好"},
    )
    assert recovered.status_code == 200, recovered.text
    assert gate.in_flight() == 0


def test_disabled_admission_never_rejects(
    client, register_user, auth_headers, stub_chat_service, monkeypatch
):
    """The kill switch must be a true no-op, not a limit-of-1."""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)

    gate = AgentStreamAdmissionGate(None, enabled=False)
    monkeypatch.setattr(admission_module, "_gate", gate)

    for _ in range(3):
        r = client.post(
            f"/api/v1/sessions/{sid}/messages/stream",
            headers=headers,
            json={"content": "你好"},
        )
        assert r.status_code == 200, r.text

    snap = gate.snapshot()
    assert snap["enabled"] is False
    assert snap["limit"] is None
    assert snap["rejected"] == 0


def test_gate_dependency_is_solved_before_any_db_session_exists(
    client, register_user, auth_headers, stub_chat_service, monkeypatch
):
    """The ordering that makes the gate worth having.

    ``db: DB`` and ``context: CurrentWorkspace`` both run DB queries, so if the
    gate were solved after them a rejected request would already have spent a
    pooled connection — and under a burst the pool could still time out before
    the gate ever saw the request. Prove the gate is solved FIRST by asserting a
    rejection creates no session at all.
    """
    from app.api.routers.chat import _admit_agent_generation  # noqa: F401

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = _create_session(client, headers)

    from app.core import database as database_module

    real_session_local = database_module.SessionLocal
    opened: list[str] = []

    def spy_session_local(*args, **kwargs):
        opened.append("session")
        return real_session_local(*args, **kwargs)

    monkeypatch.setattr(database_module, "SessionLocal", spy_session_local)

    gate = AgentStreamAdmissionGate(1)
    monkeypatch.setattr(admission_module, "_gate", gate)

    # Sanity first (while the gate is free): the spy really does observe
    # sessions on a normal request.
    opened.clear()
    ok = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "你好"},
    )
    assert ok.status_code == 200, ok.text
    assert opened, "spy did not observe a DB session on a normal request"

    # Now saturate the gate.
    holder = gate.try_acquire()
    assert holder is not None

    # The real assertion: rejection must not open a session.
    opened.clear()
    rejected = client.post(
        f"/api/v1/sessions/{sid}/messages/stream",
        headers=headers,
        json={"content": "你好"},
    )
    assert rejected.status_code == 429, rejected.text
    assert opened == [], (
        "a rejected request still created "
        f"{len(opened)} DB session(s) -> the gate is solved after db, not before"
    )
    holder.release()


def test_failed_preflight_does_not_leak_slots(
    client, register_user, auth_headers, stub_chat_service, monkeypatch
):
    """A prelude failure must give the slot back, or the gate would drain away."""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)

    gate = AgentStreamAdmissionGate(2)
    monkeypatch.setattr(admission_module, "_gate", gate)

    # Unknown session id => require_session_access raises 404 *after* admission.
    for _ in range(5):
        r = client.post(
            "/api/v1/sessions/does-not-exist/messages/stream",
            headers=headers,
            json={"content": "你好"},
        )
        assert r.status_code == 404, r.text

    assert gate.in_flight() == 0, "prelude failures leaked admission slots"
    assert gate.snapshot()["rejected"] == 0
