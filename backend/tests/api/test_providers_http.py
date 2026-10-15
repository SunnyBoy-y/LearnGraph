"""providers 接口补测：CRUD / catalog / 密钥状态 / 轮换 / 删除（environment 密钥提供方，不碰 OS keyring）。"""
from __future__ import annotations

import uuid


def test_providers_empty_and_catalog(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.get("/api/v1/providers", headers=headers)
    assert r.status_code == 200 and r.json() == []
    r = client.get("/api/v1/providers/catalog", headers=headers)
    assert r.status_code == 200
    types = [p.get("provider_type") for p in r.json() if isinstance(p, dict)]
    assert "openai_compatible_chat" in types


def test_provider_crud_cycle(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    name = f"补测 provider {uuid.uuid4().hex[:6]}"
    r = client.post(
        "/api/v1/providers",
        headers=headers,
        json={
            "display_name": name,
            "provider_type": "openai_compatible_chat",
            "base_url": "http://127.0.0.1:9/v1",  # 不可达，仅测 CRUD 不测探测
            "api_key": "test-provider-api-key-03",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    # 密钥必须被掩码，绝不回传明文（S 系列回归）
    assert "test-provider-api-key-03" not in r.text
    assert "****" in r.text
    # 列表/单查（单查端点不存在时跳过——405 属正常）
    r = client.get("/api/v1/providers", headers=headers)
    assert any(p["id"] == pid for p in r.json())
    # PATCH（需至少一个 provider 设置字段）
    r = client.patch(
        f"/api/v1/providers/{pid}",
        headers=headers,
        json={"display_name": name + "-改", "base_url": "http://127.0.0.1:9/v1"},
    )
    assert r.status_code == 200, r.text
    # secret-store 状态
    r = client.get("/api/v1/providers/secret-store/status", headers=headers)
    assert r.status_code == 200
    assert r.json()["available"] is True
    # 轮换密钥
    r = client.post(
        f"/api/v1/providers/{pid}/rotate-secret",
        headers=headers,
        json={"api_key": "test-rotated-key"},
    )
    assert r.status_code == 200, r.text
    # 删除（清理）
    r = client.delete(f"/api/v1/providers/{pid}", headers=headers)
    assert r.status_code == 200, r.text


def test_provider_models_discovery_unreachable(client, register_user, auth_headers):
    """模型发现对不可达上游应返回明确错误（4xx/5xx 带可读信息），不得挂死。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.post(
        "/api/v1/providers",
        headers=headers,
        json={
            "display_name": "不可达",
            "provider_type": "openai_compatible_chat",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "sk-test-dummy-key-000",
        },
    )
    pid = r.json()["id"]
    r = client.get(f"/api/v1/providers/{pid}/models", headers=headers)
    assert r.status_code >= 400, "不可达上游应返回错误而非 200"
    client.delete(f"/api/v1/providers/{pid}", headers=headers)


def test_provider_secret_not_in_error(client, register_user, auth_headers):
    """回归：错误响应也不得泄露密钥。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    secret = "sk-leak-check-abc123def456"
    r = client.post(
        "/api/v1/providers",
        headers=headers,
        json={
            "display_name": "泄漏检查",
            "provider_type": "openai_compatible_chat",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": secret,
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    r = client.get(f"/api/v1/providers/{pid}/models", headers=headers)
    assert secret not in r.text
    client.delete(f"/api/v1/providers/{pid}", headers=headers)


def test_image_generation_model_toggle_persists(
    client, register_user, auth_headers, monkeypatch
):
    """回归：图片生成 Provider 的模型启用/关闭开关必须持久化并真正生效。

    覆盖：发现快照 -> 批量开关 -> 持久化 -> 重新发现回读 -> 默认模型不可指向
    已停用模型（409）。
    """
    from app.services import management as mgmt

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.post(
        "/api/v1/providers",
        headers=headers,
        json={
            "display_name": "图片生成开关回归",
            "provider_type": "openai_images",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "test-provider-api-key-04",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # 不访问真实厂商：模型列表来自固定目录
    monkeypatch.setattr(
        mgmt.ProviderService,
        "_discover",
        lambda self, provider: ["gpt-image-1", "dall-e-3"],
    )
    try:
        # 发现快照
        r = client.get(f"/api/v1/providers/{pid}/models", headers=headers)
        assert r.status_code == 200, r.text
        assert [m["id"] for m in r.json()["models"]] == ["gpt-image-1", "dall-e-3"]

        # 启用供应商并设置默认模型
        r = client.patch(
            f"/api/v1/providers/{pid}",
            headers=headers,
            json={
                "enabled": True,
                "default_image_generation_model_id": "dall-e-3",
            },
        )
        assert r.status_code == 200, r.text

        # 关闭 gpt-image-1
        r = client.patch(
            f"/api/v1/providers/{pid}/models",
            headers=headers,
            json={"states": {"gpt-image-1": False, "dall-e-3": True}},
        )
        assert r.status_code == 200, r.text
        assert r.json()["states"] == {"gpt-image-1": False, "dall-e-3": True}
        assert r.json()["default_model"] == "dall-e-3"

        # 持久化：Provider 列表必须携带最新开关状态
        r = client.get("/api/v1/providers", headers=headers)
        provider = next(p for p in r.json() if p["id"] == pid)
        assert provider["capabilities"]["model_states"] == {
            "gpt-image-1": False,
            "dall-e-3": True,
        }

        # 重新发现：开关状态回读一致（前端开关不回跳的根基）
        r = client.get(f"/api/v1/providers/{pid}/models", headers=headers)
        assert r.status_code == 200, r.text
        by_id = {m["id"]: m for m in r.json()["models"]}
        assert by_id["gpt-image-1"]["enabled"] is False
        assert by_id["dall-e-3"]["enabled"] is True

        # 已停用模型不得被设为默认（与模型类 Provider 的 default_model 校验对齐）
        r = client.patch(
            f"/api/v1/providers/{pid}",
            headers=headers,
            json={"default_image_generation_model_id": "gpt-image-1"},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "provider_model_disabled"

        # 已启用模型仍可设为默认
        r = client.patch(
            f"/api/v1/providers/{pid}",
            headers=headers,
            json={"default_image_generation_model_id": "dall-e-3"},
        )
        assert r.status_code == 200, r.text
    finally:
        client.delete(f"/api/v1/providers/{pid}", headers=headers)


def test_qwen_image_search_provider_enable(
    client, register_user, auth_headers, monkeypatch
):
    """回归：文搜图/图搜图专用 provider（qwen_image_search）必须可以启用。

    之前该类型在 ProviderService 的启用分支里没有对应处理，落到兜底
    raise 422 unsupported_provider_type（"cannot be enabled by the current
    backend"）。启用需 base_url + 密钥 + default_model，并写入
    provider_role=image_search。
    """
    from app.services import management as mgmt

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)

    # catalog 必须声明该类型与角色
    r = client.get("/api/v1/providers/catalog", headers=headers)
    assert r.status_code == 200
    entry = next(
        (p for p in r.json() if p["provider_type"] == "qwen_image_search"), None
    )
    assert entry is not None, "catalog 缺少 qwen_image_search"
    assert entry["role"] == "image_search"

    r = client.post(
        "/api/v1/providers",
        headers=headers,
        json={
            "display_name": "文搜图/图搜图回归",
            "provider_type": "qwen_image_search",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "test-provider-api-key-02",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    monkeypatch.setattr(
        mgmt.ProviderService,
        "_discover",
        lambda self, provider: ["qwen3.7-plus", "qwen3.7-flash"],
    )
    try:
        # 未设置默认模型时启用应拒绝（409）
        r = client.patch(
            f"/api/v1/providers/{pid}",
            headers=headers,
            json={"enabled": True},
        )
        assert r.status_code == 409, r.text

        # 模型发现走 GET /models 路径（自动回填默认模型）
        r = client.get(f"/api/v1/providers/{pid}/models", headers=headers)
        assert r.status_code == 200, r.text
        assert [m["id"] for m in r.json()["models"]] == [
            "qwen3.7-plus",
            "qwen3.7-flash",
        ]

        # 设置默认模型后启用成功
        r = client.patch(
            f"/api/v1/providers/{pid}",
            headers=headers,
            json={"enabled": True, "default_model": "qwen3.7-plus"},
        )
        assert r.status_code == 200, r.text
        provider = r.json()
        assert provider["enabled"] is True
        assert provider["capabilities"]["provider_role"] == "image_search"
        assert provider["capabilities"]["remote_calls_enabled"] is True
    finally:
        client.delete(f"/api/v1/providers/{pid}", headers=headers)


def test_base_url_update_invalidates_runtime_cache(client, register_user, auth_headers):
    """回归：PATCH 更新 base_url 后必须立即失效运行时 Provider 计划缓存。

    之前 ProviderService.update() 提交后漏调 invalidate_provider_plan_cache，
    聊天/图像/搜索等运行时经 60s TTL 快照继续使用旧地址，UI 显示保存成功但
    实际调用仍打向旧 URL（“改了没效果”）。
    """
    from app.core.database import SessionLocal
    from app.providers.provider_plan_cache import (
        cached_provider_rows,
        clear_provider_plan_cache,
    )

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    r = client.post(
        "/api/v1/providers",
        headers=headers,
        json={
            "display_name": "缓存失效回归",
            "provider_type": "openai_compatible_chat",
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "test-provider-api-key-01",
        },
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    clear_provider_plan_cache()
    try:
        # 启用（密钥 + base_url + default_model 齐备）
        r = client.patch(
            f"/api/v1/providers/{pid}",
            headers=headers,
            json={"enabled": True, "default_model": "stub-model"},
        )
        assert r.status_code == 200, r.text

        # 预填充运行时缓存（等价于一次真实调用后的快照）
        with SessionLocal() as db:
            rows = cached_provider_rows(db, ws, None, provider_id=pid)
        assert rows and rows[0].base_url == "http://127.0.0.1:9/v1"

        # 修改 base_url（“供应商配置”对话框的保存连接路径）
        r = client.patch(
            f"/api/v1/providers/{pid}",
            headers=headers,
            json={"base_url": "http://127.0.0.1:10/v1"},
        )
        assert r.status_code == 200, r.text

        # 缓存必须已被失效：再次读取应直接拿到新地址；未失效会命中旧快照
        with SessionLocal() as db:
            rows = cached_provider_rows(db, ws, None, provider_id=pid)
        assert rows and rows[0].base_url == "http://127.0.0.1:10/v1"
    finally:
        client.delete(f"/api/v1/providers/{pid}", headers=headers)
