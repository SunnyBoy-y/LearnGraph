"""Retired package routes cannot invoke a configured generation model.

Historical workspace settings remain valid so existing backups still import.
"""
from unittest.mock import Mock

import pytest

from app.domain.settings import LEARNING_PACKAGE_MODEL_SETTING_KEY


@pytest.fixture()
def learning_headers(client, register_user):
    token, workspace_id, *_ = register_user("learning-retired")
    return {"Authorization": f"Bearer {token}", "X-Workspace-ID": workspace_id}


def test_half_written_setting_is_rejected(client, learning_headers):
    saved = client.put(
        f"/api/v1/settings/{LEARNING_PACKAGE_MODEL_SETTING_KEY}",
        json={"value": {"provider_id": "prov-1", "model_id": None}},
        headers=learning_headers,
    )
    assert saved.status_code == 422, saved.text


@pytest.mark.parametrize("method,path", [
    ("GET", "/graphs/graph/policy"),
    ("PATCH", "/graphs/graph/policy"),
    ("GET", "/graphs/graph/builds"),
    ("GET", "/nodes/node"),
    ("POST", "/nodes/node/build"),
    ("POST", "/builds/build/retry"),
    ("POST", "/builds/build/cancel"),
    ("POST", "/nodes/node/start"),
    ("PATCH", "/enrollments/enrollment"),
    ("POST", "/nodes/node/attempts"),
    ("GET", "/attempts/attempt"),
    ("PATCH", "/attempts/attempt/answers"),
    ("PATCH", "/attempts/attempt/activity"),
    ("POST", "/attempts/attempt/submit"),
])
def test_retired_routes_never_resolve_a_provider(client, learning_headers, monkeypatch, method, path):
    import app.providers.factory as provider_factory

    provider = Mock(side_effect=AssertionError("Retired package route invoked a provider"))
    monkeypatch.setattr(provider_factory, "model_provider_for_workspace", provider)
    bodies = {
        "/nodes/node/build": {},
        "/enrollments/enrollment": {"expected_revision": 0, "reset_activity": True},
        "/nodes/node/attempts": {"request_key": "request-01"},
        "/attempts/attempt/answers": {"expected_revision": 0, "answers": {}},
        "/attempts/attempt/activity": {"expected_revision": 0, "reset_activity": True},
    }
    response = client.request(method, f"/api/v1/node-activities{path}", headers=learning_headers, json=bodies.get(path))
    assert response.status_code == 404, response.text
    provider.assert_not_called()
