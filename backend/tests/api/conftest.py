"""Shared API-test fixtures used by the checked-in backend test suite.

- 强制 environment 密钥提供方（避免写入用户 OS keyring）
- 关闭全部后台调度器/队列（测试确定性）
- 提供 TestClient、注册用户、StubModelProvider（不调用真实模型）
"""
from __future__ import annotations

import os

# ---- 必须在 import app 之前设置 ----
os.environ.setdefault("LEARNGRAPH_SECRET_PROVIDER", "environment")
os.environ.setdefault("LEARNGRAPH_MASTER_KEY", "api-tests-master-key-v1")
# 关闭后台任务，保证测试确定性
os.environ.setdefault("LEARNGRAPH_DURABLE_QUEUE_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MASTERY_EMBEDDED_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MEMORY_RETENTION_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MEMORY_EXTRACTION_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_SANDBOX_CLEANUP_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MCP_STDIO_CLEANUP_SCHEDULER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_MEMORY_OUTBOX_WORKER_ENABLED", "false")
os.environ.setdefault("LEARNGRAPH_SANDBOX_ENABLED", "false")
# 认证限流（S1-3 已在工作树落地）：测试大量注册，调高阈值避免误伤
os.environ.setdefault("LEARNGRAPH_AUTH_RATE_LIMIT_MAX", "100000")
os.environ.setdefault("LEARNGRAPH_AUTH_RATE_LIMIT_WINDOW_SECONDS", "3600")

import pytest
from fastapi.testclient import TestClient


class StubModelProvider:
    """最小模型桩：不调用任何真实模型，返回固定文本/事件。"""

    available = True
    provider_id = "stub-provider"
    model_id = "stub-model"
    remote_capability = True
    thinking_mode = "off"
    actual_reasoning_effort = None
    search_route = "disabled"

    def stream_chat(self, messages, **kwargs):
        from app.providers.ports.model import ProviderStreamEvent

        yield ProviderStreamEvent(type="text_delta", content="你好")
        yield ProviderStreamEvent(type="text_delta", content="，世界")
        yield ProviderStreamEvent(type="completed", finish_reason="stop")

    def stream_answer(self, prompt):
        yield "你好，世界"

    def generate_json(self, prompt, schema_name, schema):
        return {}


@pytest.fixture(scope="session")
def client():
    """TestClient + lifespan（初始化临时库）。"""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def register_user(client):
    """注册一个全新用户，返回 (token, workspace_id, username, password)。"""

    def _register(prefix="api"):
        import time

        username = f"{prefix}_{int(time.time() * 1000)}"
        password = "ApiTest@Pass2026!x"
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "display_name": f"{prefix} user",
                "password": password,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        token = body["access_token"]
        workspaces = client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {token}"}
        )
        ws = workspaces.json()[0]["id"]
        return token, ws, username, password

    return _register


@pytest.fixture()
def auth_headers():
    def _headers(token, ws=None):
        h = {"Authorization": f"Bearer {token}"}
        if ws is not None:
            h["X-Workspace-ID"] = ws
        return h

    return _headers


@pytest.fixture()
def stub_chat_service(monkeypatch):
    """把 chat 路由的 service 工厂替换为注入 StubModelProvider 的实现。"""
    from app.api.routers import chat as chat_router
    from app.services.chat import ChatService

    def factory(
        db,
        context,
        settings,
        model_id=None,
        provider_id=None,
        thinking_mode=None,
        search_route=None,
        *,
        agent_mode=True,
    ):
        return ChatService(
            db,
            context.workspace_id,
            context.principal.user_id,
            StubModelProvider(),
            settings=settings,
        )

    monkeypatch.setattr(chat_router, "service", factory)
    return factory


@pytest.fixture()
def stub_goal_service(monkeypatch):
    """把 goals 路由的 service/service_with_model 替换为注入 StubModelProvider。"""
    from app.api.routers import goals as goals_router
    from app.services.goals import GoalService

    def factory(db, context, settings, **kwargs):
        return GoalService(
            db,
            context.workspace_id,
            context.principal.user_id,
            StubModelProvider(),
        )

    monkeypatch.setattr(goals_router, "service", factory)
    monkeypatch.setattr(goals_router, "service_with_model", factory)
    return factory
