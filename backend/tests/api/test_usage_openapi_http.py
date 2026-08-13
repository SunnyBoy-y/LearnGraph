"""usage / dashboard / health 冒烟 + OpenAPI 全端点鉴权边界测试。

OpenAPI 冒烟覆盖 ~438 端点的两个不变量：
1. 公开白名单之外的所有端点，无 token → 401
2. 工作区资源无 X-Workspace-ID → 422（而非 200）
"""
from __future__ import annotations

# 公开端点白名单（无需认证即可合法访问）
PUBLIC_PREFIXES = (
    "/api/v1/health",
    "/api/v1/livez",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/demo-login",
    "/api/v1/artifact-share",
    "/api/v1/card-share",
    "/api/v1/docs",
    "/api/v1/openapi.json",
)


def _iter_operations(client):
    spec = client.get("/openapi.json").json()
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            yield path, method.upper(), op


def test_usage_summary_and_dashboard(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.get("/api/v1/usage/summary", headers=headers)
    assert r.status_code == 200, r.text
    r = client.get("/api/v1/dashboard", headers=headers)
    assert r.status_code == 200, r.text


def test_health_public(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_livez_public_and_database_free(client, monkeypatch):
    """Supervisor liveness remains available when DB readiness is congested."""
    from app.api import deps

    def fail_if_database_requested():
        raise AssertionError("livez must not open a database session")

    monkeypatch.setattr(deps, "get_db", fail_if_database_requested)
    r = client.get("/api/v1/livez")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "learngraph-backend"
    assert isinstance(payload["pid"], int)


def test_openapi_all_endpoints_reject_anonymous(client):
    """不变量 1：非白名单端点无 token → 401（认证强制回归）。"""
    failures = []
    checked = 0
    for path, method, _op in _iter_operations(client):
        if path.startswith(PUBLIC_PREFIXES):
            continue
        r = client.request(method, path)
        if r.status_code != 401:
            failures.append((method, path, r.status_code))
        checked += 1
    assert not failures, f"{len(failures)} 个端点未强制认证（首个：{failures[:5]}）"
    assert checked > 100, f"OpenAPI 遍历异常：仅检查到 {checked} 个端点"


def test_openapi_workspace_endpoints_require_workspace_header(client, register_user, auth_headers):
    """不变量 2：带 token 但缺 X-Workspace-ID → 422（工作区隔离强制）。"""
    token, _, _, _ = register_user()
    failures = []
    checked = 0
    for path, method, _op in _iter_operations(client):
        if path.startswith(PUBLIC_PREFIXES):
            continue
        if path.startswith(("/api/v1/auth/", "/api/v1/permissions", "/api/v1/users", "/api/v1/organizations", "/api/v1/workspaces")):
            continue  # 全局级端点不要求 workspace
        r = client.request(method, path, headers={"Authorization": f"Bearer {token}"})
        if r.status_code not in (401, 403, 404, 422):
            failures.append((method, path, r.status_code))
        checked += 1
    assert not failures, f"{len(failures)} 个端点缺 workspace 头未返回 4xx（首个：{failures[:5]}）"
    assert checked > 100
