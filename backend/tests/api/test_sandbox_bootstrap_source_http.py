"""Sandbox bootstrap source API (GET/PUT /sandbox/bootstrap/source) 补测。

覆盖：普通成员可读、仅部署管理员可写、prebuilt 模式必填地址、非法地址拒绝、
持久化后 effective_mode 反映页面配置。测试库由 conftest 隔离，不触碰真实 .env。
"""
from __future__ import annotations

import pytest

from app.core.database import SessionLocal
from app.domain.models import User

ACR_REF = "crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph:1.0.0"
SOURCE_URL = "/api/v1/sandbox/bootstrap/source"


@pytest.fixture(autouse=True)
def _isolate_bootstrap_source():
    """每个测试前清理持久化 source 文件,保证从默认状态(auto, 未持久化)开始。

    该文件落在共享 data 目录(sandbox-runtime.json 旁),多个测试共享同一
    storage root,不清理会导致 test order 依赖。
    """
    from app.core.config import Settings
    from app.services.sandbox_runtime import bootstrap_source_path

    path = bootstrap_source_path(Settings())
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


def _make_system_admin(user_id: str) -> None:
    with SessionLocal() as db:
        db.query(User).filter(User.id == user_id).update({"is_system_admin": True})
        db.commit()


def _user_id(client, token, auth_headers) -> str:
    me = client.get("/api/v1/auth/me", headers=auth_headers(token)).json()
    return me["id"]


class TestBootstrapSourceGet:
    def test_member_can_read_default_source(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        resp = client.get(SOURCE_URL, headers=auth_headers(token, ws))
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "auto"
        assert body["effective_mode"] == "auto"
        assert body["persisted"] is False
        assert body["prebuilt_image"] is None


class TestBootstrapSourceUpdate:
    def test_member_cannot_update(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        resp = client.put(
            SOURCE_URL,
            headers=auth_headers(token, ws),
            json={"mode": "prebuilt", "prebuilt_image": ACR_REF},
        )
        assert resp.status_code == 403

    def test_admin_can_enable_prebuilt(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        _make_system_admin(_user_id(client, token, auth_headers))
        resp = client.put(
            SOURCE_URL,
            headers=auth_headers(token, ws),
            json={"mode": "prebuilt", "prebuilt_image": ACR_REF},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "prebuilt"
        assert body["effective_mode"] == "prebuilt"
        assert body["persisted"] is True
        assert body["prebuilt_image"] == ACR_REF

        # 再读：持久化生效
        get_resp = client.get(SOURCE_URL, headers=auth_headers(token, ws))
        assert get_resp.json()["effective_mode"] == "prebuilt"
        assert get_resp.json()["prebuilt_image"] == ACR_REF

    def test_admin_can_switch_to_build(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        _make_system_admin(_user_id(client, token, auth_headers))
        resp = client.put(
            SOURCE_URL,
            headers=auth_headers(token, ws),
            json={"mode": "build", "prebuilt_image": None},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["effective_mode"] == "build"
        assert body["prebuilt_image"] is None

    def test_prebuilt_mode_requires_ref(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        _make_system_admin(_user_id(client, token, auth_headers))
        resp = client.put(
            SOURCE_URL,
            headers=auth_headers(token, ws),
            json={"mode": "prebuilt", "prebuilt_image": None},
        )
        assert resp.status_code == 400

    def test_invalid_ref_rejected(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        _make_system_admin(_user_id(client, token, auth_headers))
        resp = client.put(
            SOURCE_URL,
            headers=auth_headers(token, ws),
            json={"mode": "prebuilt", "prebuilt_image": "learngraph-sandbox:$(rm -rf /)"},
        )
        assert resp.status_code == 400

    def test_auto_mode_allows_empty_ref(self, client, register_user, auth_headers) -> None:
        token, ws, _, _ = register_user()
        _make_system_admin(_user_id(client, token, auth_headers))
        resp = client.put(
            SOURCE_URL,
            headers=auth_headers(token, ws),
            json={"mode": "auto", "prebuilt_image": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["effective_mode"] == "auto"
