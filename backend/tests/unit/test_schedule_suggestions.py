"""Read-only schedule recommendation coverage. No network or paid models."""
from __future__ import annotations

import json
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
from app.core.errors import AppError, install_error_handlers
from app.core.security import Principal
from app.domain import models as m
from app.domain.extension_models import ScheduledExtensionTask, ScheduledExtensionRun
from app.domain.memory_event_models import MemorySearchDocument
from app.domain.schemas.schedule_suggestions import ScheduleSuggestionRequest
from app.services import schedule_suggestions as module
from app.services.schedule_suggestions import ScheduleSuggestionService


def suggestion(index=0, **overrides):
    return {"name": f"Python复习{index}", "prompt": f"每周复习Python列表与循环的知识点并提供练习{index}",
            "icon": "brain-circuit", **overrides}


@pytest.fixture()
def env(monkeypatch):
    from app.providers.factory import invalidate_provider_plan_cache
    from app.services.billing import BillingService
    invalidate_provider_plan_cache("ws")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        workspace = m.Workspace(id="ws", tenant_id="tenant", owner_user_id="alice", name="test")
        db.add_all([workspace, m.Workspace(id="other-ws", tenant_id="tenant", owner_user_id="bob", name="other")])
        db.add(m.WorkspaceSetting(workspace_id="ws", key="memory.shared_policy", value={"workspace_enabled": True}))
        db.commit()
        principal = Principal(user_id="alice", username="alice", tenant_id="tenant", session_id="auth")
        context = WorkspaceContext(principal=principal, workspace=workspace, permissions=frozenset({"workspace.read", "workspace.write"}))
        settings = Settings(_env_file=None, memory_read_mode="legacy")
        provider = SimpleNamespace(provider_id="stub", model_id="stub-model", available=True, remote_capability=True,
                                   supports_structured_chat=False, last_usage={"input_tokens": 12, "output_tokens": 8})
        state = SimpleNamespace(db=db, context=context, settings=settings, provider=provider,
                                calls=[], prompts=[], billing=[], preflights=[], output=json.dumps({"suggestions": [suggestion(i) for i in range(5)]}))
        def factory(*args, **kwargs):
            state.calls.append(kwargs)
            return provider
        def stream(prompt):
            state.prompts.append(prompt)
            yield state.output
        provider.stream_answer = stream
        monkeypatch.setattr(module, "model_provider_for_workspace", factory)
        def preflight(*args, **kwargs):
            state.preflights.append((args, kwargs))
            return "quote"
        monkeypatch.setattr(module.ChatService, "_preflight_model_call", preflight)
        monkeypatch.setattr(BillingService, "record_usage", lambda *a, **kw: state.billing.append((a, kw)))
        state.service = ScheduleSuggestionService(db, context, settings)
        yield state
    engine.dispose()


def project(env, **overrides):
    env.db.add(m.Project(**{"id": "project", "workspace_id": "ws", "title": "Python基础学习", **overrides}))
    env.db.commit()


def memory(env, content="我喜欢Python编程", **overrides):
    record = m.MemoryRecord(**{
        "id": "memory", "workspace_id": "ws", "tenant_id": "tenant", "subject_user_id": "alice",
        "title": "Python偏好", "relative_path": "", "content_hash": "hash", "source": "user", "namespace": "workspace",
        **overrides,
    })
    env.db.add(record)
    env.db.flush()
    env.db.add(m.MemoryRevision(workspace_id=record.workspace_id, memory_id=record.id, revision=1, title=record.title,
                               content=content, content_hash="hash", operation="ADD", actor_id="alice"))
    env.db.commit()
    return record


def task(env, **overrides):
    item = ScheduledExtensionTask(**{
        "id": "task", "workspace_id": "ws", "created_by": "alice", "name": "已有Python周报",
        "task_type": "prompt", "prompt": "已有的复习指令", "cron_expression": "0 9 * * *", **overrides,
    })
    env.db.add(item)
    env.db.commit()
    return item


def test_no_authorized_context_no_model_or_fixed_fallback(env):
    task(env)
    result = env.service.generate(ScheduleSuggestionRequest())
    assert result.suggestions == []
    assert result.metadata["reason"] == "no_authorized_context"
    assert env.calls == env.billing == []


def test_authorized_personalized_json_billing_and_no_side_effects(env):
    memory(env)
    project(env)
    result = env.service.generate(ScheduleSuggestionRequest())
    assert len(result.suggestions) == 5
    assert result.suggestions[0].icon == "brain-circuit"
    assert result.metadata["memory_used"] is True
    assert "我喜欢Python编程" in env.prompts[0] and "Python基础学习" in env.prompts[0]
    assert "不可信数据" in env.prompts[0] and "allowed_effect" in env.prompts[0]
    assert env.calls[0]["thinking_mode"] == "off"
    assert env.calls[0]["search_route"] == "disabled"
    assert env.calls[0]["prefer_fast"] is True
    assert env.preflights[0][0][2] == "schedule_suggestions"
    assert env.billing[0][0][1] == "quote"
    assert env.billing[0][1]["input_tokens"] == 12
    assert env.billing[0][1]["output_tokens"] == 8
    for model in (ScheduledExtensionTask, ScheduledExtensionRun, m.ChatSession, m.Message, m.SuggestedPromptBatch):
        assert env.db.scalar(select(func.count()).select_from(model)) == 0


def test_feature_model_setting_and_fresh_calls(env):
    project(env)
    env.db.add(m.WorkspaceSetting(workspace_id="ws", key="chat.suggested_prompts_model",
                                value={"provider_id": "task", "model_id": "task-model"}))
    env.db.commit()
    a = env.service.generate(ScheduleSuggestionRequest(count=1))
    b = env.service.generate(ScheduleSuggestionRequest(count=1))
    assert env.calls[0]["provider_id"] == "task" and env.calls[0]["model_id"] == "task-model"
    assert len(a.suggestions) == 1
    assert a.metadata["request_id"] != b.metadata["request_id"]
    assert len(env.prompts) == 2


@pytest.mark.parametrize("overrides", [
    {"workspace_id": "other-ws"}, {"tenant_id": "other-tenant"}, {"subject_user_id": "bob"},
    {"state": "deleted"}, {"lifecycle_status": "archived"}, {"ledger_status": "retracted"},
    {"auto_recall_suppressed": True}, {"zone": "archive"}, {"summary_eligibility": "excluded"},
    {"temporal_status": "cancelled"}, {"sensitivity": "secret"}, {"namespace": "session"},
    {"valid_until": datetime.now(timezone.utc) - timedelta(days=1)},
    {"valid_from": datetime.now(timezone.utc) + timedelta(days=1)}, {"project_id": "missing"},
    {"structured_payload": {"usage_policy": {"allowed_effect": "none"}}},
    {"structured_payload": {"provenance": {"authorship": "assistant"}}},
])
def test_memory_reader_policy_is_reused(env, overrides):
    memory(env, **overrides)
    assert env.service.generate(ScheduleSuggestionRequest()).suggestions == []
    assert not env.calls


@pytest.mark.parametrize("content,overrides", [
    ("我喜欢Python编程", {"source": "draft:learning_agent"}),
    ("我正在为客户开发Python项目", {"record_kind": "event_summary"}),
])
def test_unverified_preference_and_unrelated_episode_excluded(env, content, overrides):
    memory(env, content=content, **overrides)
    assert env.service.generate(ScheduleSuggestionRequest()).suggestions == []


def test_disabled_policy_still_uses_accessible_projects(env):
    memory(env)
    project(env)
    setting = env.db.scalar(select(m.WorkspaceSetting).where(m.WorkspaceSetting.key == "memory.shared_policy"))
    setting.value = {"workspace_enabled": True, "workspace_recall_enabled": False}
    env.db.commit()
    result = env.service.generate(ScheduleSuggestionRequest())
    assert result.metadata["memory_count"] == 0
    assert "我喜欢Python编程" not in env.prompts[0]


def test_event_projection_only_and_stale_projection_excluded(env):
    env.settings.memory_read_mode = "events"
    memory(env)
    assert not env.service.generate(ScheduleSuggestionRequest()).suggestions
    doc = MemorySearchDocument(id="doc", target_type="memory", target_id="memory", subject="Python偏好", content="我喜欢Python编程",
                               tenant_id="tenant", workspace_id="ws", subject_user_id="alice", status="active",
                               source_event_id="event", content_hash="hash", memory_type="semantic_memory")
    env.db.add(doc)
    env.db.commit()
    assert env.service.generate(ScheduleSuggestionRequest()).metadata["memory_used"]
    doc.content = "stale projection"
    env.db.commit()
    assert not env.service.generate(ScheduleSuggestionRequest()).suggestions


def test_private_project_binding_hidden(env, monkeypatch):
    project(env)
    from app.services.authorization import AuthorizationService
    monkeypatch.setattr(AuthorizationService, "can_access_bindings", lambda *a, **kw: False)
    assert not env.service.generate(ScheduleSuggestionRequest()).suggestions
    assert not env.calls


def test_manage_permission_checked_even_when_context_is_empty(env, monkeypatch):
    from app.services.authorization import AuthorizationService
    monkeypatch.setattr(AuthorizationService, "workspace_permissions", lambda *a: {"workspace.read"})
    with pytest.raises(AppError) as exc:
        env.service.generate(ScheduleSuggestionRequest())
    assert exc.value.status_code == 403
    assert not env.calls


def test_existing_task_summary_and_normalized_dedup(env):
    project(env)
    task(env, name="Python复习0", prompt="已有复习")
    task(env, id="foreign", workspace_id="other-ws", name="外部秘密", prompt="外部任务")
    env.output = json.dumps({"suggestions": [
        suggestion(0), suggestion(1, prompt="已有 复习！"), suggestion(2),
        suggestion(3, name="Python 复习２！"), suggestion(4, prompt=suggestion(2)["prompt"]),
        suggestion(5, icon="notebook-pen"),
    ]})
    result = env.service.generate(ScheduleSuggestionRequest())
    assert [item.name for item in result.suggestions] == ["Python复习2", "Python复习5"]
    assert "已有复习" in env.prompts[0] and "外部秘密" not in env.prompts[0]
    assert result.metadata["existing_task_count"] == 1
    assert env.db.scalar(select(func.count()).select_from(ScheduledExtensionTask)) == 2


def test_task_summaries_are_bounded_and_do_not_include_payload(env):
    project(env)
    for i in range(43):
        task(env, id=f"task-{i}", name=f"existing-{i}", prompt="x" * 2000,
             payload_json={"secret": "do-not-send"})
    env.service.generate(ScheduleSuggestionRequest(count=1))
    context = json.loads(env.prompts[0].split("\ncontext=", 1)[1])
    assert len(context["existing_tasks"]) == 40
    assert context["existing_tasks_omitted"] == 3
    assert all(len(item["prompt"]) == 320 for item in context["existing_tasks"])
    assert "do-not-send" not in env.prompts[0]


@pytest.mark.parametrize("output", [
    "", "not json", "```json\n{\"suggestions\": []}\n```", "[]", "{}",
    json.dumps({"suggestions": [{"name": "x", "prompt": "y", "icon": "BrainCircuit"}]}),
    json.dumps({"suggestions": [suggestion(name="n" * 81)]}),
    json.dumps({"suggestions": [suggestion(prompt="p" * 1001)]}),
    json.dumps({"suggestions": [suggestion(name="   ")]}),
    json.dumps({"suggestions": [suggestion(icon="<svg onload=alert(1)>")]}),
    json.dumps({"suggestions": [suggestion(enabled=True)]}),
    json.dumps({"suggestions": [suggestion(i) for i in range(8)]}),
    json.dumps({"suggestions": [], "metadata": {"secret": "x"}}),
])
def test_invalid_output_is_safe_and_billed_without_fallback(env, output):
    project(env)
    env.output = output
    with pytest.raises(AppError) as exc:
        env.service.generate(ScheduleSuggestionRequest())
    assert exc.value.code == "schedule_suggestion_invalid_response"
    assert len(env.billing) == 1
    assert env.db.scalar(select(func.count()).select_from(ScheduledExtensionTask)) == 0


def test_empty_model_output_is_not_replaced_by_templates(env):
    project(env)
    env.output = '{"suggestions": []}'
    assert env.service.generate(ScheduleSuggestionRequest()).suggestions == []


@pytest.mark.parametrize("icon", ["accessibility", "a-arrow-down", "book-open-check", "chart-no-axes-combined", "rotate-3d"])
def test_icons_are_not_a_fixed_small_mapping(env, icon):
    project(env)
    env.output = json.dumps({"suggestions": [suggestion(icon=icon)]})
    assert env.service.generate(ScheduleSuggestionRequest()).suggestions[0].icon == icon


@pytest.mark.parametrize("error,code,status", [
    (RuntimeError("secret-upstream-url"), "schedule_suggestion_generation_failed", 502),
    (TimeoutError("secret-upstream-url"), "schedule_suggestion_provider_timeout", 504),
    (AppError(502, "upstream", "secret-upstream-url"), "schedule_suggestion_generation_failed", 502),
    (AppError(502, "schedule_suggestion_invalid_response", "secret-upstream-url"), "schedule_suggestion_generation_failed", 502),
])
def test_provider_errors_do_not_leak_and_usage_still_recorded(env, error, code, status):
    project(env)
    def fail(*args):
        raise error
    env.provider.stream_answer = fail
    with pytest.raises(AppError) as exc:
        env.service.generate(ScheduleSuggestionRequest())
    assert exc.value.code == code and exc.value.status_code == status
    assert "secret" not in str(exc.value) and exc.value.details == {}
    assert len(env.billing) == 1


def test_no_remote_demo_fallback(env):
    project(env)
    env.provider.remote_capability = False
    with pytest.raises(AppError) as exc:
        env.service.generate(ScheduleSuggestionRequest())
    assert exc.value.code == "remote_model_required"
    assert not env.prompts and not env.billing


def test_unavailable_provider_reason_is_sanitized(env):
    project(env)
    env.provider.available = False
    env.provider.reason = "secret-provider-url"
    with pytest.raises(AppError) as exc:
        env.service.generate(ScheduleSuggestionRequest())
    assert exc.value.code == "model_provider_unavailable"
    assert "secret" not in str(exc.value) and exc.value.details == {}
    assert not env.prompts and not env.billing


@pytest.mark.parametrize("error", [RuntimeError("secret-config"), AppError(502, "upstream", "secret-config")])
def test_provider_setup_exception_is_sanitized(env, monkeypatch, error):
    project(env)
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(module, "model_provider_for_workspace", fail)
    with pytest.raises(AppError) as exc:
        env.service.generate(ScheduleSuggestionRequest())
    assert "secret" not in str(exc.value)


def test_billing_failure_is_sanitized(env, monkeypatch):
    project(env)
    from app.services.billing import BillingService
    def fail(*a, **kw):
        raise RuntimeError("secret-billing")
    monkeypatch.setattr(BillingService, "record_usage", fail)
    with pytest.raises(AppError) as exc:
        env.service.generate(ScheduleSuggestionRequest())
    assert exc.value.code == "schedule_suggestion_billing_failed"
    assert "secret" not in str(exc.value)


def test_stream_is_bounded_and_closed(env):
    project(env)
    closed = []
    def stream(prompt):
        try:
            yield "x" * (module.MAX_RESPONSE_CHARS + 1)
            pytest.fail("Oversized stream must not be consumed")
        finally:
            closed.append(True)
    env.provider.stream_answer = stream
    with pytest.raises(AppError):
        env.service.generate(ScheduleSuggestionRequest())
    assert closed == [True] and len(env.billing) == 1


def test_structured_transport_and_nontext_event_limit(env, monkeypatch):
    project(env)
    env.provider.supports_structured_chat = True
    messages = []
    def stream(items):
        messages.extend(items)
        yield SimpleNamespace(type="thinking_delta", content="private")
        yield SimpleNamespace(type="text_delta", content=env.output)
    env.provider.stream_chat = stream
    assert len(env.service.generate(ScheduleSuggestionRequest()).suggestions) == 5
    assert messages[0].role == "user"
    monkeypatch.setattr(module, "MAX_STREAM_EVENTS", 3)
    env.provider.stream_chat = lambda items: iter([SimpleNamespace(type="thinking_delta", content="") for _ in range(5)])
    with pytest.raises(AppError):
        env.service.generate(ScheduleSuggestionRequest())


@pytest.mark.parametrize("payload", [
    {"count": 0}, {"count": 8}, {"count": "5"}, {"count": True}, {"count": 1.5},
    {"memory": "injection"}, {"context": "injection"}, {"provider_id": "override"},
])
def test_request_validation(payload):
    with pytest.raises(ValidationError):
        ScheduleSuggestionRequest.model_validate(payload)


def test_http_empty_json_contract_validation_and_static_route_order(env):
    from app.api.routers.scheduled_tasks import router
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: env.db
    app.dependency_overrides[workspace_context] = lambda: env.context
    app.dependency_overrides[get_settings] = lambda: env.settings
    with TestClient(app) as client:
        empty = client.post("/api/v1/scheduled-tasks/suggestions", json={})
        assert empty.status_code == 200 and empty.json()["suggestions"] == []
        project(env)
        response = client.post("/api/v1/scheduled-tasks/suggestions", json={})
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"suggestions", "metadata"}
        assert len(body["suggestions"]) == 5
        assert set(body["suggestions"][0]) == {"name", "prompt", "icon"}
        for count in (1, 7):
            assert client.post("/api/v1/scheduled-tasks/suggestions", json={"count": count}).status_code == 200
        for payload in ({"count": 8}, {"count": True}, {"context": "injection"}):
            assert client.post("/api/v1/scheduled-tasks/suggestions", json=payload).status_code == 422
        env.output = "upstream-private-output"
        invalid = client.post("/api/v1/scheduled-tasks/suggestions", json={})
        assert invalid.status_code == 502 and "upstream-private-output" not in invalid.text
    paths = [route.path for route in router.routes]
    assert paths.index("/scheduled-tasks/suggestions") < paths.index("/scheduled-tasks/{task_id}")
