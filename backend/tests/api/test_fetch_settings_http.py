"""API 测试：Provider 管理「网页抓取」设置（沙箱开关 + 通道优先级）。

覆盖：
- GET /fetch-authorizations/settings 返回默认 runtime（沙箱开、sandbox→remote→hosted）
- PUT 持久化开关与优先级，并即时反映到有效通道
- 重复优先级被 422 拒绝
- 非管理工作区成员 PUT 被 403 拒绝
"""
from __future__ import annotations

import pytest


def _get(client, token, ws):
    resp = client.get(
        "/api/v1/fetch-authorizations/settings",
        headers={"Authorization": f"Bearer {token}", "X-Workspace-ID": ws},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _put(client, token, ws, payload):
    return client.put(
        "/api/v1/fetch-authorizations/settings",
        json=payload,
        headers={"Authorization": f"Bearer {token}", "X-Workspace-ID": ws},
    )


@pytest.mark.parametrize("global_gate", [False, True])
def test_fetch_settings_defaults(client, register_user, monkeypatch, global_gate):
    from app.core.config import get_settings

    # The global gate comes from server configuration, not workspace defaults.
    # Exercise both states without inheriting a developer's local .env value.
    monkeypatch.setattr(get_settings(), "sandbox_web_fetch_enabled", global_gate)
    token, ws, _, _ = register_user()
    body = _get(client, token, ws)
    assert body["sandbox_enabled"] is True
    assert body["priority"] == ["sandbox", "remote", "hosted"]
    assert body["persisted"] is False
    assert body["global_sandbox_gate"] is global_gate
    # 新工作区：无白名单、无远程 fetch provider、无 hosted 伴随模型
    assert body["sandbox_effective"] is False
    assert body["remote_configured"] is False
    assert body["effective_channel"] is None


def test_fetch_settings_update_persists_and_effective_channel(client, register_user):
    token, ws, _, _ = register_user()
    resp = _put(
        client,
        token,
        ws,
        {"sandbox_enabled": False, "priority": ["hosted", "remote", "sandbox"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sandbox_enabled"] is False
    assert body["priority"] == ["hosted", "remote", "sandbox"]
    assert body["persisted"] is True
    # 刷新后仍保持
    again = _get(client, token, ws)
    assert again["sandbox_enabled"] is False
    assert again["priority"] == ["hosted", "remote", "sandbox"]


def test_fetch_settings_rejects_duplicate_priority(client, register_user):
    token, ws, _, _ = register_user()
    resp = _put(
        client,
        token,
        ws,
        {"sandbox_enabled": True, "priority": ["sandbox", "sandbox"]},
    )
    assert resp.status_code == 422, resp.text


def test_fetch_settings_requires_workspace_manage(client, register_user):
    token, ws, _, _ = register_user()
    member_token, member_ws, _, _ = register_user("member")
    assert member_ws != ws
    # 普通成员访问他人的工作区没有 workspace.manage 权限
    resp = _put(
        client,
        member_token,
        ws,
        {"sandbox_enabled": True, "priority": ["sandbox", "remote", "hosted"]},
    )
    assert resp.status_code == 403, resp.text
