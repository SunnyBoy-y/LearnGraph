"""子应用会话生命周期 HTTP 测试（T2.6 会话通道）。

守护目标：
- 会话 API 契约：create → get → states → event（token 轮换）→ terminate；
- 回归护栏：`deps.py workspace_context` 的通用资源 ACL 门把 subapp 的
  `{session_id}` 路径参数误当成 ChatSession，曾导致所有会话级 subapp 路由
  批量 404（"Resource not found in this workspace"）。这里显式断言路由确实
  到达 SubAppService —— 未知会话 id 应返回服务的 `subapp_session_not_found`，
  而不是 ACL 门的 `not_found`；
- 跨工作区隔离：会话只在创建它的工作区内可见。

说明：service 级测试绕过 deps.py，无法暴露这类路由层问题，必须走 HTTP。
"""
from __future__ import annotations

import uuid

from app.core.database import SessionLocal
from app.domain.models import (
    ComponentManifestVersion,
    PluginRecord,
    Workspace,
)

CONTRACT = {
    "event_schema": {
        "type": "object",
        "properties": {"action": {"type": "string"}},
        "required": ["action"],
    },
    "state_schema": {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
    },
    "agent_triggers": [],
}


def _seed_manifest(db, workspace_id: str) -> str:
    """Seed one published component manifest version with a full interaction contract."""
    workspace = db.get(Workspace, workspace_id)
    assert workspace is not None
    plugin = PluginRecord(
        id=f"plg_{uuid.uuid4().hex[:24]}",
        workspace_id=workspace_id,
        plugin_key=f"test.subapp.{uuid.uuid4().hex[:8]}",
        name="Test Subapp",
        version="1.0.0",
        plugin_type="trusted-component",
        status="configured",
        enabled=True,
        permissions=[],
        capabilities=[],
    )
    db.add(plugin)
    # 显式 flush 保证 plugin 行先落库：同批 flush 下 manifest 可能先于其外键
    # 父表插入，导致 SQLite 报 FOREIGN KEY constraint failed。
    db.flush()
    version_id = f"mv_{uuid.uuid4().hex[:28]}"
    manifest = ComponentManifestVersion(
        id=version_id,
        workspace_id=workspace_id,
        plugin_id=plugin.id,
        component_id="test.subapp.component",
        version="1.0.0",
        display_name="Test Subapp",
        renderer="react-sandbox-v1",
        source="http://localhost/test-subapp",
        author="test",
        package_hash="a" * 64,
        package_hash_status="verified",
        signature_status="unsigned",
        signature_info={},
        compatible_learngraph={},
        uninstall_behavior="retain_data",
        data_schema={"type": "object"},
        event_schema={"type": "object"},
        interaction_contract=CONTRACT,
        permissions={},
        size_limits={},
        skill_triggers=[],
        example_data={},
        schema_hash="b" * 64,
        permissions_hash="c" * 64,
        manifest_hash="d" * 64,
        trusted_bundle_eligible=False,
    )
    db.add(manifest)
    db.commit()
    return version_id


def _create_session(client, headers, version_id: str) -> dict:
    resp = client.post(
        "/api/v1/subapps/sessions",
        headers=headers,
        json={"artifact_version_id": version_id, "chat_session_id": None},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_subapp_session_lifecycle_http(client, register_user, auth_headers):
    """会话全生命周期：创建、读取、空状态、错误令牌拒绝、令牌轮换、终止。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    with SessionLocal() as db:
        version_id = _seed_manifest(db, ws)

    created = _create_session(client, headers, version_id)
    session_id = created["session_id"]
    assert isinstance(session_id, str) and len(session_id) == 36
    assert created["status"] == "active"
    assert created["state_version"] == 0
    assert created["state_sha256"] is None
    assert created["artifact_version_id"] == version_id
    assert isinstance(created["token"], str) and len(created["token"]) >= 32
    assert created["token_prefix"] == created["token"][:8]
    assert created["unlock_message"]["event_type"] == "renderer.unlock"
    assert created["unlock_message"]["payload"]["token"] == created["token"]

    # get session
    resp = client.get(
        f"/api/v1/subapps/sessions/{session_id}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["id"] == session_id
    assert view["status"] == "active"
    assert view["artifact_version_id"] == version_id

    # states: 初始为空（回归点：修复前此处 404）
    resp = client.get(
        f"/api/v1/subapps/sessions/{session_id}/states?offset=0&limit=1",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "offset": 0, "limit": 1, "total": 0}

    # 错误令牌被拒
    resp = client.post(
        f"/api/v1/subapps/sessions/{session_id}/events",
        headers=headers,
        json={"token": "wrong-token", "event_type": "submit", "payload": {"action": "go"}},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "subapp_token_invalid"

    # 正确令牌：202 接受并轮换令牌（回归点：修复前此处 404）
    resp = client.post(
        f"/api/v1/subapps/sessions/{session_id}/events",
        headers=headers,
        json={"token": created["token"], "event_type": "submit", "payload": {"action": "go"}},
    )
    assert resp.status_code == 202, resp.text
    accepted = resp.json()
    assert accepted["accepted"] is True
    assert accepted["session_id"] == session_id
    assert accepted["next_token"] != created["token"]
    assert accepted["event"]["event_type"] == "submit"
    assert accepted["event"]["payload"] == {"action": "go"}
    next_token = accepted["next_token"]

    # 旧令牌重放被拒（一次性轮换）
    resp = client.post(
        f"/api/v1/subapps/sessions/{session_id}/events",
        headers=headers,
        json={"token": created["token"], "event_type": "submit", "payload": {"action": "go"}},
    )
    assert resp.status_code == 401, resp.text

    # 终止
    resp = client.post(
        f"/api/v1/subapps/sessions/{session_id}/terminate", headers=headers
    )
    assert resp.status_code == 200, resp.text
    terminated = resp.json()
    assert terminated["status"] == "terminated"
    assert terminated["terminated_at"] is not None

    # 终止后事件被拒（409 非 active）
    resp = client.post(
        f"/api/v1/subapps/sessions/{session_id}/events",
        headers=headers,
        json={"token": next_token, "event_type": "submit", "payload": {"action": "go"}},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "subapp_session_not_active"

    # 终止后会话仍可读（行保留，status=terminated）
    resp = client.get(f"/api/v1/subapps/sessions/{session_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "terminated"


def test_subapp_session_unknown_id_reaches_service(client, register_user, auth_headers):
    """回归护栏：路由必须到达 SubAppService。

    未知会话 id 应返回服务层的 `subapp_session_not_found`。若通用 ACL 门
    （deps.py workspace_context）再次把 subapp 的 {session_id} 误判为
    ChatSession，这里会变成 ACL 门的 `not_found` —— 测试即失败。
    """
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    with SessionLocal() as db:
        version_id = _seed_manifest(db, ws)
    _create_session(client, headers, version_id)

    unknown_id = str(uuid.uuid4())
    resp = client.get(
        f"/api/v1/subapps/sessions/{unknown_id}/states?offset=0&limit=1",
        headers=headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "subapp_session_not_found", resp.text


def test_subapp_session_cross_workspace_isolated(client, register_user, auth_headers):
    """跨工作区隔离：会话只在创建它的工作区可见。"""
    token1, ws1, _, _ = register_user("subapp_owner")
    token2, ws2, _, _ = register_user("subapp_other")
    with SessionLocal() as db:
        version_id = _seed_manifest(db, ws1)

    created = _create_session(client, auth_headers(token1, ws1), version_id)
    session_id = created["session_id"]

    # 其他工作区：states / get / terminate 全部 404
    foreign = auth_headers(token2, ws2)
    resp = client.get(
        f"/api/v1/subapps/sessions/{session_id}/states?offset=0&limit=1",
        headers=foreign,
    )
    assert resp.status_code == 404, resp.text
    resp = client.get(f"/api/v1/subapps/sessions/{session_id}", headers=foreign)
    assert resp.status_code == 404, resp.text
    resp = client.post(f"/api/v1/subapps/sessions/{session_id}/terminate", headers=foreign)
    assert resp.status_code == 404, resp.text

    # 创建工作区仍可访问
    resp = client.get(
        f"/api/v1/subapps/sessions/{session_id}/states?offset=0&limit=1",
        headers=auth_headers(token1, ws1),
    )
    assert resp.status_code == 200, resp.text
