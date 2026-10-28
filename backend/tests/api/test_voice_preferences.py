def create_voice(client, headers, *, enabled=None):
    chat = client.post("/api/v1/sessions", headers=headers, json={"title": "voice preferences"})
    assert chat.status_code in (200, 201), chat.text
    payload = {"chat_session_id": chat.json()["id"]}
    if enabled is not None:
        payload["low_latency_mode"] = enabled
    response = client.post("/api/v1/voice/sessions", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_voice_preferences_default_off_and_can_be_enabled_on_creation(client, register_user, auth_headers):
    token, workspace, *_ = register_user("voice_low_latency_create")
    headers = auth_headers(token, workspace)
    assert create_voice(client, headers)["low_latency_mode"] is False
    voice = create_voice(client, headers, enabled=True)
    assert voice["low_latency_mode"] is True
    fetched = client.get(f"/api/v1/voice/sessions/{voice['id']}", headers=headers)
    assert fetched.json()["context_snapshot"]["low_latency_mode"] is True


def test_voice_preference_survives_refresh_and_emits_a_live_update(client, register_user, auth_headers):
    token, workspace, *_ = register_user("voice_low_latency_persist")
    headers = auth_headers(token, workspace)
    voice = create_voice(client, headers)
    path = f"/api/v1/voice/sessions/{voice['id']}/preferences"
    updated = client.patch(path, headers=headers, json={"low_latency_mode": True})
    assert updated.status_code == 200, updated.text
    assert updated.json()["low_latency_mode"] is True
    assert updated.json()["session_epoch"] == voice["session_epoch"]
    assert client.patch(path, headers=headers, json={"low_latency_mode": True}).status_code == 200
    refreshed = client.post("/api/v1/voice/sessions", headers=headers,
                            json={"chat_session_id": voice["chat_session_id"]})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["id"] == voice["id"]
    assert refreshed.json()["low_latency_mode"] is True
    events = client.get(f"/api/v1/voice/sessions/{voice['id']}/events", headers=headers).json()["events"]
    changes = [event for event in events if event["type"] == "context.updated"
               and event["payload"].get("reason") == "voice_preferences"]
    assert len(changes) == 1
    assert changes[0]["payload"]["low_latency_mode"] is True
    disabled = client.patch(path, headers=headers, json={"low_latency_mode": False})
    assert disabled.status_code == 200
    assert disabled.json()["low_latency_mode"] is False


def test_voice_preference_is_owned_and_accepts_only_booleans(client, register_user, auth_headers):
    token, workspace, *_ = register_user("voice_low_latency_owner")
    headers = auth_headers(token, workspace)
    voice = create_voice(client, headers)
    path = f"/api/v1/voice/sessions/{voice['id']}/preferences"
    for invalid in ("false", 1, None):
        assert client.patch(path, headers=headers, json={"low_latency_mode": invalid}).status_code == 422
    other_token, other_workspace, *_ = register_user("voice_low_latency_other")
    assert client.patch(path, headers=auth_headers(other_token, other_workspace),
                        json={"low_latency_mode": True}).status_code in (403, 404)
    assert client.get(f"/api/v1/voice/sessions/{voice['id']}", headers=headers).json()["low_latency_mode"] is False
