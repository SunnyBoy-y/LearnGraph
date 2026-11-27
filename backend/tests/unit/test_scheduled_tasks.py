from __future__ import annotations
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.config import Settings
from app.core.database import Base
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.models import User, Workspace, utc_now
from app.domain.extension_models import ScheduledExtensionRun, ScheduledExtensionTask
from app.domain.schemas.extensions import SkillCreateRequest, PermissionDecisionRequest
from app.domain.schemas.scheduled_tasks import ScheduledTaskCreate, ScheduledTaskPatch
from app.services.mcp_skills import MCPAndSkillService
from app.services.scheduled_tasks import ScheduledTaskService, aware, claim_due, execute_run, next_run, reconcile_runs


@pytest.fixture()
def env(tmp_path):
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None, storage_root=tmp_path/'storage')
    principal = Principal(user_id='owner', username='owner', tenant_id='tenant', session_id='unit')
    with factory() as db:
        user = User(id='owner',tenant_id='tenant',username='owner',username_normalized='owner',password_hash='unused')
        workspace = Workspace(id='ws',tenant_id='tenant',owner_user_id='owner',name='unit')
        db.add_all([user,workspace]); db.commit()
        manager = MCPAndSkillService(db, 'ws', 'owner', settings, workspace=workspace,principal=principal)
        skill = manager.create_skill(SkillCreateRequest.model_validate({
            'skill_key':'due-review','name':'Due review','source':'test','version':'1',
            'manifest':{'schema_version':'1.0','kind':'declarative_review','instructions_markdown':'Read due reviews',
                'required_tools':['builtin.review.list_due'],'permissions':['mastery.read'],'allowed_components':[],
                'input_schema':{'type':'object'},'steps':[{'tool':'builtin.review.list_due','arguments':{}}]}}))
        manager.authorize_skill(skill.id, PermissionDecisionRequest(decision='always',permissions=['mastery.read'],reason='test'))
        service = ScheduledTaskService(db,workspace,principal,settings)
        yield SimpleNamespace(db=db,factory=factory,settings=settings,service=service,skill=skill,manager=manager)
    engine.dispose()


def payload(env, **overrides):
    return ScheduledTaskCreate.model_validate({'name':'Daily reviews','task_type':'skill','skill_id':env.skill.id,
        'cron_expression':'0 9 * * *','timezone':'Asia/Shanghai',**overrides})


def test_crud_and_workspace_isolation(env):
    task = env.service.create(payload(env))
    assert task.next_run_at is not None
    assert env.service.list()[0].id == task.id
    updated = env.service.update(task.id,ScheduledTaskPatch(name='Edited',enabled=False))
    assert updated.name == 'Edited' and updated.next_run_at is None
    foreign = Workspace(id='foreign',tenant_id='tenant',owner_user_id='owner',name='other')
    env.db.add(foreign); env.db.commit()
    other = ScheduledTaskService(env.db,foreign,env.service.principal,env.settings)
    with pytest.raises(AppError,match='not found'):
        other.require(task.id)
    env.service.delete(task.id)
    assert env.service.list() == []


@pytest.mark.parametrize('overrides', [
    {'cron_expression':'* * * * * *'}, {'cron_expression':'bad cron'}, {'cron_expression':'0 99 * * *'},
    {'timezone':'Mars/Olympus'}, {'timeout_seconds':601}, {'task_type':'mcp'}, {'name':'  '},
])
def test_invalid_schedules_rejected(env, overrides):
    with pytest.raises(ValidationError): payload(env,**overrides)


def test_timezone_next_run_and_dst():
    after = datetime(2026,11,11,0,30,tzinfo=timezone.utc)
    assert next_run('0 9 * * *','Asia/Shanghai',after) == datetime(2026,11,11,1,tzinfo=timezone.utc)
    assert next_run('0 9 * * *','America/New_York',after) == datetime(2026,11,11,14,tzinfo=timezone.utc)
    assert next_run('0 9 * * *','America/New_York',datetime(2026,3,7,15,tzinfo=timezone.utc)) == datetime(2026,3,8,13,tzinfo=timezone.utc)


def test_claim_is_exclusive_and_run_is_persisted(env):
    task = env.service.create(payload(env))
    run = env.service.claim(task.id)
    with env.factory() as db:
        service = ScheduledTaskService(db,db.get(Workspace,'ws'),env.service.principal,env.settings)
        with pytest.raises(AppError,match='active run'):
            service.claim(task.id)
    execute_run(task.id,run.id,env.settings,env.factory)
    env.db.expire_all()
    actual = env.db.get(ScheduledExtensionRun,run.id)
    assert actual.status == 'succeeded'
    assert actual.result_json['result']['steps'][0]['tool'] == 'builtin.review.list_due'
    assert env.service.require(task.id).lease_token is None


def test_overdue_slots_coalesce_and_are_not_replayed(env):
    task = env.service.create(payload(env,cron_expression='* * * * *'))
    task.next_run_at = utc_now()-timedelta(hours=2)
    env.db.commit()
    claims = claim_due(env.settings,env.factory)
    assert len(claims) == 1
    assert claim_due(env.settings,env.factory) == []
    env.db.expire_all()
    assert aware(env.service.require(task.id).next_run_at)>utc_now()


def test_content_change_revokes_scheduled_execution(env):
    task = env.service.create(payload(env))
    run = env.service.claim(task.id)
    env.skill.manifest_hash = 'a'*64
    env.db.commit()
    execute_run(task.id,run.id,env.settings,env.factory)
    env.db.expire_all()
    actual = env.db.get(ScheduledExtensionRun,run.id)
    assert actual.status == 'failed'
    assert actual.error_code == 'agent_tool_not_authorized'


def test_revoked_owner_is_not_impersonated(env):
    task = env.service.create(payload(env))
    run = env.service.claim(task.id)
    env.db.get(User,'owner').status='disabled'; env.db.commit()
    execute_run(task.id,run.id,env.settings,env.factory)
    env.db.expire_all()
    actual = env.db.get(ScheduledExtensionRun,run.id)
    assert actual.status == 'failed'
    assert actual.error_code == 'schedule_owner_unavailable'


def test_interrupted_run_is_recorded_not_retried(env):
    task = env.service.create(payload(env))
    run = env.service.claim(task.id)
    env.db.expire_all()
    task = env.service.require(task.id)
    task.lease_until = utc_now()-timedelta(seconds=1)
    run.dispatched_at = utc_now()-timedelta(minutes=10)
    env.db.commit()
    reconcile_runs(env.db)
    env.db.expire_all()
    assert env.db.get(ScheduledExtensionRun,run.id).error_code == 'scheduled_run_interrupted'
    assert env.service.require(task.id).lease_token is None


def test_worker_recovers_an_undispatched_slot_without_claiming_it_again(env):
    from app.services.scheduled_tasks import _schedule_dispatch_batch

    task = env.service.create(payload(env, remaining_executions=1, cron_expression="* * * * *"))
    task.next_run_at = utc_now() - timedelta(minutes=1)
    env.db.commit()
    assert len(claim_due(env.settings, env.factory)) == 1
    env.db.expire_all()
    task.lease_until = utc_now() - timedelta(seconds=1)
    env.db.commit()
    reconcile_runs(env.db, settings=env.settings)

    [(task_id, run_id, recovered)] = _schedule_dispatch_batch(env.settings, env.factory, 4, ())
    assert task_id == task.id and recovered is True
    execute_run(task_id, run_id, env.settings, env.factory, recovered=True)
    execute_run(task_id, run_id, env.settings, env.factory, recovered=True)
    env.db.expire_all()
    assert env.db.get(ScheduledExtensionRun, run_id).status == "succeeded"
    assert env.db.get(ScheduledExtensionTask, task_id).remaining_executions == 0
    assert _schedule_dispatch_batch(env.settings, env.factory, 4, ()) == []


def test_running_definition_cannot_be_edited_or_deleted(env):
    task = env.service.create(payload(env))
    env.service.claim(task.id)
    env.db.expire_all()
    with pytest.raises(AppError,match='active run'):
        env.service.update(task.id,ScheduledTaskPatch(name='racing edit'))
    with pytest.raises(AppError,match='executing'):
        env.service.delete(task.id)


def test_payload_credentials_rejected(env):
    with pytest.raises(AppError,match='credentials'):
        env.service.create(payload(env,payload_json={'api_key':'not-a-real-key'}))


def test_pausing_a_running_task_preserves_its_active_lease(env):
    task = env.service.create(payload(env))
    env.service.claim(task.id)
    env.db.expire_all()
    token = env.service.require(task.id).lease_token
    result = env.service.update(task.id, ScheduledTaskPatch(enabled=False))
    assert result.enabled is False and result.next_run_at is None
    assert result.lease_token == token


def test_duplicate_background_callbacks_never_execute_twice(env, monkeypatch):
    from app.services import scheduled_tasks as module
    task = env.service.create(payload(env))
    run = env.service.claim(task.id)
    env.db.expire_all()
    actual = env.db.get(ScheduledExtensionRun, run.id)
    actual.dispatched_at = utc_now()
    env.db.commit()
    invoked = []
    monkeypatch.setattr(module, "execution_context", lambda *args: invoked.append(True))
    execute_run(task.id, run.id, env.settings, env.factory)
    assert invoked == []


def test_cancel_requires_a_real_active_job(env):
    task = env.service.create(payload(env))
    with pytest.raises(AppError, match="no active"):
        env.service.cancel(task.id)
    env.service.claim(task.id)
    env.db.expire_all()
    with pytest.raises(AppError, match="cannot be safely cancelled"):
        env.service.cancel(task.id)


def test_http_crud_run_history_and_workspace_boundary(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import WorkspaceContext, workspace_context
    from app.api.routers import scheduled_tasks as router_module
    from app.core.database import get_db
    from app.core.config import get_settings
    from app.core.errors import install_error_handlers
    application = FastAPI()
    application.include_router(router_module.router, prefix="/api/v1")
    install_error_handlers(application)
    application.dependency_overrides[get_db] = lambda: env.db
    context = WorkspaceContext(env.service.principal,env.service.workspace,frozenset({"workspace.manage"}))
    application.dependency_overrides[workspace_context] = lambda:context
    application.dependency_overrides[get_settings] = lambda:env.settings
    monkeypatch.setattr(router_module,"SessionLocal",env.factory)
    with TestClient(application) as client:
        body = payload(env).model_dump(mode="json")
        created = client.post("/api/v1/scheduled-tasks",json=body)
        assert created.status_code == 201,created.text
        task_id = created.json()["id"]
        assert client.get("/api/v1/scheduled-tasks").json()[0]["id"] == task_id
        assert client.patch(f"/api/v1/scheduled-tasks/{task_id}",json={"timezone":"Invalid/Zone"}).status_code == 422
        assert client.patch(f"/api/v1/scheduled-tasks/{task_id}",json={"enabled":False}).status_code == 200
        response = client.post(f"/api/v1/scheduled-tasks/{task_id}/run")
        assert response.status_code == 202,response.text
        records = client.get(f"/api/v1/scheduled-tasks/{task_id}/runs").json()
        assert records[0]["status"] == "succeeded",records
        assert client.get("/api/v1/scheduled-tasks/not-in-workspace").status_code == 404
        assert client.delete(f"/api/v1/scheduled-tasks/{task_id}").status_code == 204
        assert client.get("/api/v1/scheduled-tasks").json() == []


def test_model_tool_uses_the_same_persistent_schedule_service(env):
    from app.services.scheduled_task_tools import execute, tool_definition
    runtime = SimpleNamespace(extensions=env.manager,_success=lambda data,meta,sources:data)
    created = execute(runtime,{"action":"create","task":payload(env).model_dump(mode="json")})
    run = execute(runtime,{"action":"run","task_id":created["id"]})
    assert run["status"] == "succeeded"
    history = execute(runtime,{"action":"runs","task_id":created["id"]})
    assert history["runs"][0]["id"] == run["id"]
    assert "cancel" in tool_definition()["function"]["parameters"]["properties"]["action"]["enum"]
    execute(runtime,{"action":"delete","task_id":created["id"]})
    assert execute(runtime,{"action":"list"}) == {"tasks":[]}


def test_nonmanager_cannot_read_or_schedule(env):
    nonowner = Principal(user_id='stranger',username='stranger',tenant_id='tenant',session_id='unit')
    service = ScheduledTaskService(env.db,env.service.workspace,nonowner,env.settings)
    with pytest.raises(AppError,match='workspace.manage'):
        service.list()


def test_default_timeout_and_utc_timestamps_and_timeout_result_status(env, monkeypatch):
    from datetime import timezone
    from app.domain.schemas.scheduled_tasks import ScheduledTaskCreate, ScheduledTaskView, ScheduledTaskRunView
    prompt_payload = ScheduledTaskCreate(
        name='Daily news', task_type='prompt', trigger_type='time', schedule_mode='daily',
        time_of_day='08:30', timezone='Asia/Shanghai', prompt='Summarize tech news',
    )
    assert prompt_payload.timeout_seconds == 300
    task = env.service.create(prompt_payload)
    task_view = ScheduledTaskView.model_validate(task)
    for dt in (task_view.next_run_at, task_view.created_at, task_view.updated_at):
        assert dt is not None and dt.tzinfo == timezone.utc

    run = env.service.claim(task.id)
    def fake_timeout(service, claimed_task, claimed_run, session_factory):
        claimed_run.result_json = {'chat_session_id': 'sess-timeout', 'status': 'running', 'task_id': claimed_task.id}
        service.db.commit()
        raise AppError(504, 'scheduled_prompt_timeout', 'Scheduled prompt exceeded its execution timeout')

    import app.services.scheduled_prompt as prompt_module
    monkeypatch.setattr(prompt_module, 'execute_prompt', fake_timeout)
    execute_run(task.id, run.id, env.settings, env.factory)
    env.db.expire_all()
    finished_run = env.service.runs(task.id)[0]
    assert finished_run.status == 'failed'
    assert finished_run.error_code == 'scheduled_prompt_timeout'
    assert finished_run.result_json['status'] == 'failed'
    assert finished_run.result_json['error_code'] == 'scheduled_prompt_timeout'
    run_view = ScheduledTaskRunView.model_validate(finished_run)
    for dt in (run_view.scheduled_for, run_view.started_at, run_view.finished_at):
        assert dt is not None and dt.tzinfo == timezone.utc
