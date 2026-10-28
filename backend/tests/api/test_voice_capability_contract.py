"""Focused HTTP contracts for the voice Agent capability boundary."""

from __future__ import annotations

from uuid import uuid4

import pytest


def _voice_session(client, register_user, auth_headers):
    token, workspace_id, *_ = register_user(f"voice_caps_{uuid4().hex[:8]}")
    headers = auth_headers(token, workspace_id)
    chat = client.post(
        "/api/v1/sessions", headers=headers, json={"title": "voice capability contract"}
    )
    assert chat.status_code in (200, 201), chat.text
    voice = client.post(
        "/api/v1/voice/sessions",
        headers=headers,
        json={"chat_session_id": chat.json()["id"], "max_thinking_mode": "off"},
    )
    assert voice.status_code == 200, voice.text
    return headers, voice.json()["id"]


def test_capabilities_api_exposes_server_owned_availability(
    client, register_user, auth_headers
):
    headers, _voice_id = _voice_session(client, register_user, auth_headers)

    response = client.get("/api/v1/voice/capabilities", headers=headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload["runtime_ready"], bool)
    capabilities = {item["id"]: item for item in payload["agent_capabilities"]}
    assert {"research", "practice_prepare", "learning_evidence", "graph_proposal"} <= capabilities.keys()
    assert capabilities["graph_proposal"]["requires_confirmation"] is False
    assert capabilities["practice_prepare"]["requires_confirmation"] is False
    assert "voice_practice_create" in capabilities["practice_prepare"]["tools"]
    assert "lg_learning_evidence_record" in capabilities["learning_evidence"]["tools"]
    for item in capabilities.values():
        assert isinstance(item["available"], bool)
        assert isinstance(item["blocked_by"], list)
        assert "required_permission" in item


def test_write_capability_starts_without_interactive_approval(
    client, register_user, auth_headers
):
    headers, voice_id = _voice_session(client, register_user, auth_headers)

    started = client.post(
        f"/api/v1/voice/sessions/{voice_id}/tasks",
        headers=headers,
        json={
            "prompt": "Propose a graph change for this concept.",
            "title": "Graph proposal",
            "capability_id": "graph_proposal",
            "role_key": "tool",
            "thinking_mode": "off",
        },
    )
    assert started.status_code == 200, started.text
    pending = started.json()
    assert pending["status"] == "queued"
    assert pending["approval_status"] == "none"


@pytest.mark.parametrize("output_contract", [None, {"required": ["short_answer"]}])
def test_reasoning_capability_accepts_optional_output_contract(
    client, register_user, auth_headers, output_contract
):
    headers, voice_id = _voice_session(client, register_user, auth_headers)
    body = {
        "prompt": "Explain why binary search halves the candidate range.",
        "capability_id": "reasoning",
        "thinking_mode": "off",
        "idempotency_key": "reasoning-optional-contract",
    }
    if output_contract is not None:
        body["output_contract"] = output_contract
    path = f"/api/v1/voice/sessions/{voice_id}/tasks"
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["status"] == "queued"
    assert task["approval_status"] == "none"
    repeated = client.post(path, headers=headers, json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["subagent_id"] == task["subagent_id"]
