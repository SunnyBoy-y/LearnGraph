"""auth 接口补测：注册/登录/会话/错误码/锁定（B1-1 触发面、S1-3、T1-2 回归）。"""
from __future__ import annotations


def test_register_login_me_workspaces_flow(client, register_user, auth_headers):
    token, ws, username, _ = register_user()
    # me
    r = client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["username"] == username
    # workspaces
    r = client.get("/api/v1/workspaces", headers=auth_headers(token))
    assert r.status_code == 200 and len(r.json()) >= 1
    # login
    r = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "ApiTest@Pass2026!x"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    # logout
    r = client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200


def test_invalid_token_fast_fail(client, auth_headers):
    r = client.get(
        "/api/v1/goals", headers=auth_headers("invalid.token.here", "ws-any")
    )
    assert r.status_code == 401
    assert "session_invalid" in r.text


def test_missing_workspace_header_422(client, register_user):
    token, _, _, _ = register_user()
    r = client.get("/api/v1/goals", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422
    assert "X-Workspace-ID" in r.text


def test_wrong_password_401(client, register_user):
    token, _, username, _ = register_user()
    r = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "wrong-pass-123"}
    )
    assert r.status_code == 401


def test_login_lockout_after_5_failures(client, register_user):
    """T1-2 回归：5 次错误密码后第 6 次 429（锁定生效）。"""
    token, _, username, password = register_user()
    for i in range(5):
        r = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong-pass-123"},
        )
        assert r.status_code == 401, f"attempt {i+1}: {r.status_code}"
    r = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 429, (
        f"第 6 次应被锁定返回 429，实际 {r.status_code}（若为 200 说明锁定未生效）"
    )


def test_change_password_flow(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    r = client.post(
        "/api/v1/auth/change-password",
        headers=auth_headers(token, ws),
        json={
            "current_password": "ApiTest@Pass2026!x",
            "new_password": "ApiTest@Pass2026!y",
        },
    )
    assert r.status_code == 200, r.text


def test_sessions_list_and_revoke(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    r = client.get("/api/v1/auth/sessions", headers=auth_headers(token))
    assert r.status_code == 200
    sessions = r.json()
    assert len(sessions) >= 1
    # 吊销第一个会话后该 token 应失效
    sid = sessions[0]["id"]
    r = client.delete(
        f"/api/v1/auth/sessions/{sid}", headers=auth_headers(token)
    )
    assert r.status_code == 200


def test_register_validation(client):
    """注册校验：弱密码/短用户名应 4xx。"""
    r = client.post(
        "/api/v1/auth/register",
        json={"username": "ab", "display_name": "x", "password": "short"},
    )
    assert r.status_code == 422


def test_register_password_policy_relaxed(client):
    """密码规则已放宽：≥8 位且含字母和数字即可；黑名单/含用户名限制已移除。"""

    def attempt(username, password):
        return client.post(
            "/api/v1/auth/register",
            json={"username": username, "display_name": username, "password": password},
        )

    # 仍拒绝：7 位（字母+数字）太短
    assert attempt("policy_a", "a1b2c3d").status_code == 422
    # 仍拒绝：8 位纯字母
    assert attempt("policy_b", "abcdefgh").status_code == 422
    # 仍拒绝：8 位纯数字
    assert attempt("policy_c", "12345678").status_code == 422
    # 放行：8 位字母+数字
    assert attempt("policy_d", "abcd1234").status_code == 201
    # 放行：密码包含用户名（原规则已移除）
    assert attempt("policy_e", "policy_e88").status_code == 201
    # 放行：原保留密码黑名单已移除
    assert attempt("policy_f", "password1234").status_code == 201
