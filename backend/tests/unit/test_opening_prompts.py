"""All models are local stubs; these tests never contact paid providers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import WorkspaceContext, workspace_context
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.core.errors import AppError
from app.core.security import Principal
from app.domain import models as m
from app.domain.memory_event_models import MemorySearchDocument
from app.domain.schemas.opening_prompts import OpeningPromptRequest
from app.services import opening_prompts as module
from app.services.opening_prompts import OpeningPromptService


@pytest.fixture()
def env(monkeypatch):
    from app.providers.factory import invalidate_provider_plan_cache
    invalidate_provider_plan_cache("ws")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        workspace = m.Workspace(id="ws", tenant_id="tenant", owner_user_id="alice", name="test")
        other = m.Workspace(id="other-ws", tenant_id="tenant", owner_user_id="bob", name="other")
        db.add_all([workspace, other])
        db.add(m.WorkspaceSetting(workspace_id="ws", key="memory.shared_policy", value={"workspace_enabled": True}))
        db.commit()
        principal = Principal(user_id="alice", username="alice", tenant_id="tenant", session_id="auth", display_name="Test", is_system_admin=False, must_change_password=False)
        context = WorkspaceContext(principal=principal, workspace=workspace, permissions=frozenset({"workspace.read", "workspace.write"}))
        settings = Settings(memory_read_mode="legacy")
        calls = []
        provider = SimpleNamespace(provider_id="stub", model_id="stub-model", available=True, remote_capability=True,
                                   max_output_tokens=1024, last_usage={})
        def factory(*args, **kwargs):
            calls.append(kwargs)
            return provider
        monkeypatch.setattr(module, "model_provider_for_workspace", factory)
        original_preflight = module.ChatService._preflight_model_call
        monkeypatch.setattr(module.ChatService, "_preflight_model_call", lambda *a, **kw: object())
        from app.services.billing import BillingService
        original_record_usage = BillingService.record_usage
        monkeypatch.setattr(BillingService, "record_usage", lambda *a, **kw: SimpleNamespace(id="usage"))
        prompts = []
        def generate(chat, prompt):
            prompts.append(prompt)
            return "\n".join(f"{i}. 如何练习Python第{i}种方法第{len(prompts)}组？" for i in range(1, 10))
        monkeypatch.setattr(module.ChatService, "_generate_suggested_prompt_text", generate)
        yield SimpleNamespace(db=db, context=context, settings=settings, calls=calls, prompts=prompts, provider=provider,
                              original_preflight=original_preflight, original_record_usage=original_record_usage,
                              service=OpeningPromptService(db, context, settings))
    engine.dispose()


def seed_memory(env, key="memory", content="我喜欢Python编程", **overrides):
    values = dict(id=key, workspace_id="ws", tenant_id="tenant", subject_user_id="alice", title="Python编程",
                  relative_path="", content_hash="hash", source="user", namespace="workspace")
    values.update(overrides)
    record = m.MemoryRecord(**values)
    env.db.add(record)
    env.db.flush()
    env.db.add(m.MemoryRevision(workspace_id=record.workspace_id, memory_id=record.id, revision=1, title=record.title,
                               content=content, content_hash="hash", operation="ADD", actor_id="alice"))
    env.db.commit()
    return record


def seed_projection(env, key="memory", content="我喜欢Python编程", **overrides):
    values = dict(id="doc-" + key, target_type="memory", target_id=key, subject="Python编程", content=content,
                  tenant_id="tenant", workspace_id="ws", subject_user_id="alice", status="active",
                  source_event_id="event-" + key, content_hash="hash", memory_type="semantic_memory")
    values.update(overrides)
    document = MemorySearchDocument(**values)
    env.db.add(document)
    env.db.commit()
    return document


def seed_project(env, key="project", **overrides):
    values = dict(id=key, workspace_id="ws", title="Python基础学习")
    values.update(overrides)
    env.db.add(m.Project(**values))
    env.db.commit()


def test_empty_has_no_fabrication_or_model_call(env):
    response = env.service.generate(OpeningPromptRequest())
    assert response.prompts == []
    assert response.metadata["reason"] == "no_authorized_context"
    assert env.calls == []


def test_memory_grounded_pool_reused_without_session_message_batch(env):
    seed_memory(env)
    first = env.service.generate(OpeningPromptRequest())
    second = env.service.generate(OpeningPromptRequest())
    assert len(first.prompts) == len(second.prompts) == 3
    assert first.prompts == second.prompts
    assert len(first.pool) == len(second.pool) == 9
    assert second.metadata["cached"] is True
    assert "我喜欢Python编程" in env.prompts[0]
    assert len(env.prompts) == len(env.calls) == 1
    assert first.metadata["memory_used"] is True
    for model in (m.ChatSession, m.Message, m.SuggestedPromptBatch):
        assert env.db.scalar(select(func.count()).select_from(model)) == 0
    assert env.calls[0]["thinking_mode"] == "off"
    assert env.calls[0]["search_route"] == "disabled"


def test_project_fallback_and_explicit_model(env):
    seed_project(env)
    result = env.service.generate(OpeningPromptRequest(provider_id="chosen", model_id="model", count=1))
    assert len(result.prompts) == 1 and not result.metadata["memory_used"]
    assert "Python基础学习" in env.prompts[0]
    assert env.calls[0]["provider_id"] == "chosen"
    assert env.calls[0]["model_id"] == "model"


def test_task_model_setting_when_selection_absent(env):
    seed_project(env)
    env.db.add(m.WorkspaceSetting(workspace_id="ws", key="chat.suggested_prompts_model",
                                value={"provider_id": "task", "model_id": "task-model"}))
    env.db.commit()
    env.service.generate(OpeningPromptRequest())
    assert env.calls[0]["provider_id"] == "task"
    assert env.calls[0]["model_id"] == "task-model"


@pytest.mark.parametrize("overrides", [
    {"workspace_id": "other-ws"}, {"tenant_id": "other-tenant"}, {"subject_user_id": "bob"},
    {"state": "deleted"}, {"lifecycle_status": "archived"}, {"ledger_status": "retracted"},
    {"auto_recall_suppressed": True}, {"child_agent_denied": True},
    {"zone": "archive"}, {"summary_eligibility": "excluded"},
    {"scope_type": "session"}, {"task_id": "another-task"},
    {"temporal_status": "cancelled"}, {"valid_until": datetime.now(timezone.utc) - timedelta(days=1)},
    {"valid_from": datetime.now(timezone.utc) + timedelta(days=1)}, {"sensitivity": "secret"},
    {"namespace": "session"}, {"project_id": "missing"},
    {"structured_payload": {"provenance": {"authorship": "assistant"}}},
    {"structured_payload": {"usage_policy": {"allowed_effect": "none"}}},
])
def test_rejects_out_of_scope_or_inapplicable_memory(env, overrides):
    seed_memory(env, **overrides)
    assert env.service.generate(OpeningPromptRequest()).prompts == []
    assert not env.calls


def test_shared_and_owner_only_visibility(env, monkeypatch):
    seed_memory(env, subject_user_id=None, audience_type="owner_only")
    env.context.workspace.owner_user_id = "bob"
    env.db.commit()
    assert not env.service.generate(OpeningPromptRequest()).prompts
    record = env.db.get(m.MemoryRecord, "memory")
    record.audience_type = "shared"
    env.db.commit()
    assert env.service.generate(OpeningPromptRequest()).metadata["memory_used"]
    policy = env.db.scalar(select(m.WorkspaceSetting).where(m.WorkspaceSetting.key == "memory.shared_policy"))
    policy.value = {"workspace_enabled": True, "access_mode": "owner_only"}
    env.db.commit()
    assert not env.service.generate(OpeningPromptRequest()).prompts


def test_context_revoked_during_generation_not_returned(env, monkeypatch):
    seed_memory(env)
    def generate(*args):
        env.db.get(m.MemoryRecord, "memory").state = "deleted"
        env.db.commit()
        return "\n".join(f"{i}. 如何设计Python第{i}种学习练习？" for i in range(1, 10))
    monkeypatch.setattr(module.ChatService, "_generate_suggested_prompt_text", generate)
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest())
    assert error.value.code == "opening_prompt_context_changed"


def test_task_episode_not_inferred_as_preference(env):
    seed_memory(env, content="我正在为客户开发Python项目", record_kind="event_summary")
    assert not env.service.generate(OpeningPromptRequest()).prompts


def test_unverified_preference_and_unsafe_text_excluded(env):
    seed_memory(env, content="我喜欢Python编程", source="draft:learning_agent")
    assert not env.service.generate(OpeningPromptRequest()).prompts


def test_disabled_memory_uses_only_project(env):
    seed_memory(env)
    seed_project(env)
    setting = env.db.scalar(select(m.WorkspaceSetting).where(m.WorkspaceSetting.key == "memory.shared_policy"))
    setting.value = {"workspace_enabled": True, "workspace_recall_enabled": False}
    env.db.commit()
    result = env.service.generate(OpeningPromptRequest())
    assert not result.metadata["memory_used"]
    assert "我喜欢Python编程" not in env.prompts[0]


def test_events_mode_no_legacy_fallback_and_canonical_validation(env):
    env.settings.memory_read_mode = "events"
    seed_memory(env)
    assert not env.service.generate(OpeningPromptRequest()).prompts
    doc = seed_projection(env)
    assert env.service.generate(OpeningPromptRequest()).metadata["memory_used"]
    doc.content = "其他已变化内容"
    env.db.commit()
    assert not env.service.generate(OpeningPromptRequest()).prompts


def test_event_only_facts_and_personal_preferences(env):
    env.settings.memory_read_mode = "events"
    seed_projection(env, content="Python列表支持索引与切片")
    assert env.service.generate(OpeningPromptRequest()).metadata["memory_used"]
    env.db.query(MemorySearchDocument).one().content = "我喜欢Python编程"
    env.db.commit()
    assert not env.service.generate(OpeningPromptRequest()).prompts


def test_private_project_and_source_binding_hidden(env, monkeypatch):
    seed_project(env)
    seed_memory(env, project_id="project")
    monkeypatch.setattr(env.service.authorization, "can_access_bindings", lambda *a, **kw: not kw.get("project_id"))
    assert not env.service.generate(OpeningPromptRequest()).prompts
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest(project_id="project"))
    assert error.value.status_code == 404


def test_other_project_memory_not_in_selected_project(env):
    seed_project(env)
    seed_project(env, key="another")
    seed_memory(env, project_id="another")
    result = env.service.generate(OpeningPromptRequest(project_id="project"))
    assert not result.metadata["memory_used"]
    assert "我喜欢Python编程" not in env.prompts[0]


@pytest.mark.parametrize("text", ["", "1. 太短", "1. 如何开始Python学习？\n2. 如何开始Python学习？"])
def test_invalid_ai_response_never_templates(env, monkeypatch, text):
    seed_project(env)
    monkeypatch.setattr(module.ChatService, "_generate_suggested_prompt_text", lambda *a: text)
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest())
    assert error.value.code == "opening_prompt_invalid_response"


def test_provider_error_sanitized(env, monkeypatch):
    seed_project(env)
    def fail(*a):
        raise RuntimeError("secret-credential-must-not-leak")
    monkeypatch.setattr(module.ChatService, "_generate_suggested_prompt_text", fail)
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest())
    assert error.value.code == "opening_prompt_generation_failed"
    assert "secret-credential" not in str(error.value)


def test_provider_timeout_explicit(env, monkeypatch):
    seed_project(env)
    def fail(*a):
        raise TimeoutError()
    monkeypatch.setattr(module.ChatService, "_generate_suggested_prompt_text", fail)
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest())
    assert error.value.code == "opening_prompt_provider_timeout"


def test_no_demo_fallback(env):
    seed_project(env)
    env.provider.remote_capability = False
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest())
    assert error.value.code == "remote_model_required"
    assert not env.prompts


@pytest.mark.parametrize("payload", [{"count": 0}, {"count": 10}, {"count": "3"}, {"count": True},
                                        {"memory": "untrusted"}, {"context": "injected"}, {"provider_id": ""}])
def test_request_validation(payload):
    with pytest.raises(ValidationError):
        OpeningPromptRequest.model_validate(payload)


def test_real_billing_records_usage_without_conversation(env, monkeypatch):
    from app.services.billing import BillingService
    seed_project(env)
    monkeypatch.setattr(module.ChatService, "_preflight_model_call", env.original_preflight)
    monkeypatch.setattr(BillingService, "record_usage", env.original_record_usage)
    response = env.service.generate(OpeningPromptRequest())
    assert len(response.prompts) == 3
    event = env.db.scalar(select(m.UsageEvent))
    assert event is not None and event.feature == "chat_opening_prompts"
    assert env.db.scalar(select(func.count()).select_from(m.ChatSession)) == 0


def test_nine_questions_supported(env, monkeypatch):
    seed_project(env)
    monkeypatch.setattr(module.ChatService, "_generate_suggested_prompt_text",
                        lambda *a: "\n".join(f"{i}. Python第{i}种练习该如何开展？" for i in range(1, 10)))
    assert len(env.service.generate(OpeningPromptRequest(count=9)).prompts) == 9


def test_api_empty_and_errors_are_explicit(env, monkeypatch):
    from app.api.routers.chat import router
    from app.core.errors import install_error_handlers
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: env.db
    app.dependency_overrides[workspace_context] = lambda: env.context
    app.dependency_overrides[get_settings] = lambda: env.settings
    with TestClient(app) as client:
        response = client.post("/api/v1/sessions/opening-prompts", json={"project_id": None})
        assert response.status_code == 200 and response.json()["prompts"] == []
        assert client.post("/api/v1/sessions/opening-prompts", json={"project_id": "missing"}).status_code == 404
        seed_project(env)
        monkeypatch.setattr(module.ChatService, "_generate_suggested_prompt_text", lambda *a: "")
        response = client.post("/api/v1/sessions/opening-prompts", json={})
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "opening_prompt_invalid_response"
        env.provider.available = False
        from app.services.opening_prompt_cache import FAILURE_BACKOFF
        clock = module.time.time() + FAILURE_BACKOFF + 1
        monkeypatch.setattr(module.time, "time", lambda: clock)
        response = client.post("/api/v1/sessions/opening-prompts", json={})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "model_provider_unavailable"


def test_api_requires_authentication(env):
    from app.api.routers.chat import router
    from app.core.errors import install_error_handlers
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: env.db
    with TestClient(app) as client:
        response = client.post("/api/v1/sessions/opening-prompts", json={"count": 3}, headers={"X-Workspace-ID": "ws"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_api_actual_path_response_and_route_order(env):
    from app.api.routers.chat import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: env.db
    app.dependency_overrides[workspace_context] = lambda: env.context
    app.dependency_overrides[get_settings] = lambda: env.settings
    seed_project(env)
    with TestClient(app) as client:
        result = client.post("/api/v1/sessions/opening-prompts", json={"count": 3})
        assert result.status_code == 200, result.text
        assert len(result.json()["prompts"]) == 3
        assert set(result.json()["prompts"][0]) == {"id", "content", "display_text"}
        assert client.post("/api/v1/sessions/opening-prompts", json={"count": 10}).status_code == 422
        assert client.post("/api/v1/sessions/opening-prompts", json={"memory": "injection"}).status_code == 422
    paths = [route.path for route in router.routes]
    assert paths.index("/sessions/opening-prompts") < paths.index("/sessions/{session_id}/suggested-prompts")


def test_paired_display_text_and_full_prompt_separation(env, monkeypatch):
    seed_project(env, title="数据结构与算法")
    pairs = [
        ("循环数组的下标为什么会回到 0？", "结合循环数组取模运算与边界回绕，用从末尾绕回下标 0 的具体例子讲清推导过程。"),
        ("画图解释 HashMap 的哈希冲突", "用图示对比 HashMap 发生哈希冲突时，直接覆盖旧对象与链表/红黑树在查找路径上的差异。"),
        ("HashMap 在什么情况下会从红黑树退化为链表？", "针对 HashMap 树化后节点减少的退化阈值，带我做一组删除操作推演，理清树化与退化的边界条件。"),
        ("什么是负载因子，为什么默认是 0.75？", "解释 HashMap 负载因子 0.75 在空间利用率和冲突概率之间的权衡。"),
        ("画图演示循环队列的判空和判满", "画出循环队列中队头与队尾指针移动过程，说明判空与判满的区别。"),
        ("出几道 HashMap 扩容的易错题", "围绕 HashMap 扩容时容量翻倍和节点重哈希设计 3 道易错判断题并逐题讲解。"),
        ("红黑树为什么比普通二叉搜索树更稳？", "从最坏时间复杂度和旋转平衡机制讲清红黑树相对普通二叉搜索树的优势。"),
        ("用生活例子解释哈希表怎么查数据", "用一个贴近日常生活的类比说明哈希函数、桶定位和冲突处理流程。"),
        ("带我复盘一遍链表转红黑树的条件", "梳理 HashMap 链表长度阈值和数组容量阈值，通过具体例子带我核对每个边界条件。"),
    ]
    captured: list[str] = []

    def generate(_chat, prompt):
        captured.append(prompt)
        return "\n".join(
            f"{i}. {'展示文案：' if i == 1 else ''}{display} {'｜｜' if i == 2 else '||'} {'完整提示词：' if i == 1 else ''}{full}"
            for i, (display, full) in enumerate(pairs, 1)
        )

    monkeypatch.setattr(module.ChatService, "_generate_suggested_prompt_text", generate)
    response = env.service.generate(OpeningPromptRequest(count=3))
    assert len(response.prompts) == 3
    assert len(response.pool) == 9
    assert response.prompts[0].display_text == pairs[0][0]
    assert response.prompts[0].content == pairs[0][1]
    assert response.prompts[1].display_text == pairs[1][0]
    assert response.prompts[1].content == pairs[1][1]
    assert response.prompts[2].display_text == pairs[2][0]
    assert response.prompts[2].content == pairs[2][1]
    assert "15到30个汉字" in captured[0]
    assert "简短提问 || 完整提示词" in captured[0]
    assert "知识理解" in captured[0] and "可视化解释" in captured[0]
