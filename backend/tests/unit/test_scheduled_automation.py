from __future__ import annotations
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import Mock
import json
import pytest
from sqlalchemy import select,text,create_engine,inspect
from pydantic import ValidationError
from test_scheduled_tasks import env,payload
from app.domain.schemas.scheduled_tasks import ScheduledTaskCreate,ScheduledTaskPatch,ScheduledTaskView
from app.domain.extension_models import ScheduledExtensionTask as Task,ScheduledExtensionRun as Run
from app.domain.models import AuditEvent,ChatSession,Message,utc_now
from app.services.scheduled_tasks import claim_due,execute_run,publish_event,aware
from app.services.schedule_rules import next_occurrence,matches_event


def prompt(**extra):
    return ScheduledTaskCreate.model_validate({'name':'AI reminder','task_type':'prompt','prompt':'生成三条学习建议','schedule_mode':'daily','timezone':'Asia/Shanghai',**extra})


def test_once_delay_normalizes_to_absolute_and_disables_after_single_claim(env):
    task=env.service.create(prompt(schedule_mode='once',delay_seconds=120))
    assert 118<=task.next_trigger_in_seconds<=120
    assert task.delay_seconds is None and task.remaining_executions==1
    view=ScheduledTaskView.model_validate(task).model_dump(mode='json')
    assert view['schedule_summary'].startswith('一次性')
    now=aware(task.next_run_at)+timedelta(seconds=1)
    run=env.service.claim(task.id,trigger='scheduled',now=now)
    env.db.expire_all();task=env.service.require(task.id)
    assert task.enabled is False and task.remaining_executions==0 and task.next_run_at is None
    assert task.execution_count==1
    assert env.service.claim(task.id,trigger='scheduled',now=now) is None


def test_finite_interval_consumes_only_automatic_attempts(env):
    task=env.service.create(payload(env,schedule_mode='interval',interval_seconds=30,remaining_executions=2))
    manual=env.service.claim(task.id)
    execute_run(task.id,manual.id,env.settings,env.factory)
    assert env.service.require(task.id).remaining_executions==2
    for remaining in (1,0):
        task=env.service.require(task.id)
        run=env.service.claim(task.id,trigger='scheduled',now=aware(task.next_run_at)+timedelta(seconds=1))
        execute_run(task.id,run.id,env.settings,env.factory)
        assert env.service.require(task.id).remaining_executions==remaining
    task=env.service.require(task.id)
    assert not task.enabled and task.execution_count==2
    with pytest.raises(Exception,match='positive'):
        env.service.update(task.id,ScheduledTaskPatch(enabled=True))
    task=env.service.update(task.id,ScheduledTaskPatch(enabled=True,remaining_executions=3))
    assert task.enabled and task.next_run_at


def test_daily_and_weekly_timezone_modes(env):
    after=datetime(2026,11,12,0,tzinfo=timezone.utc)
    daily=prompt(time_of_day='09:30')
    assert next_occurrence(daily,after)==datetime(2026,11,12,1,30,tzinfo=timezone.utc)
    weekly=prompt(schedule_mode='weekly',time_of_day='09:00',weekdays=[4])
    assert next_occurrence(weekly,after)==datetime(2026,11,13,1,tzinfo=timezone.utc)


def test_event_filters_and_cursor_are_idempotent_and_workspace_scoped(env):
    task=env.service.create(payload(env,trigger_type='condition',event_type='event.report_ready',
        event_filters=[{'path':'payload.score','op':'gte','value':80}],remaining_executions=1))
    publish_event(env.db,'another-workspace','owner','report_ready',{'score':100})
    publish_event(env.db,'ws','owner','report_ready',{'score':20},event_id='low')
    high=publish_event(env.db,'ws','owner','report_ready',{'score':90},event_id='high')
    repeated=publish_event(env.db,'ws','owner','report_ready',{'score':90},event_id='high')
    assert repeated.id==high.id
    claims=claim_due(env.settings,env.factory)
    assert len(claims)==1
    execute_run(*claims[0],env.settings,env.factory)
    env.db.expire_all()
    run=env.db.get(Run,claims[0][1])
    assert run.trigger=='condition' and run.trigger_payload['payload']['score']==90
    assert env.service.require(task.id).remaining_executions==0
    assert claim_due(env.settings,env.factory)==[]


def test_native_run_completion_event_really_triggers_a_dependent_task(env):
    source=env.service.create(payload(env))
    dependent=env.service.create(payload(env,trigger_type='condition',event_type='automation.run.succeeded',
        event_filters=[{'path':'payload.task_id','op':'eq','value':source.id}],remaining_executions=1))
    run=env.service.claim(source.id)
    execute_run(source.id,run.id,env.settings,env.factory)
    claims=claim_due(env.settings,env.factory)
    assert len(claims)==1 and claims[0][0]==dependent.id
    actual=env.db.get(Run,claims[0][1])
    assert actual.trigger_payload['payload']['run_id']==run.id
    assert claim_due(env.settings,env.factory)==[]


def test_ai_runtime_directly_exposes_and_executes_task_creation(env):
    from app.services.agent_runtime import AgentToolRuntime
    runtime=AgentToolRuntime(workspace_id='ws',actor_id='owner',search_provider=None,
        extensions=env.manager,sandbox=None,sandbox_authorized=False,settings=env.settings)
    env.db.add(ChatSession(id='ai-source',workspace_id='ws',title='AI source'));env.db.commit()
    definitions=runtime.definitions(agent_mode_enabled=True,web_search_enabled=False,
        capability_families=set(),activated_capabilities=set())
    assert 'lg_scheduled_tasks' in {definition['function']['name'] for definition in definitions}
    result,meta,_=runtime.execute({'id':'create-task','function':{'name':'lg_scheduled_tasks','arguments':json.dumps({
        'action':'create','task':{'name':'AI planned','prompt':'整理今日知识','schedule_mode':'once','delay_seconds':300}})}},
        allowed_domains=[],chat_session_id='ai-source',disclosed_tool_names={'lg_scheduled_tasks'})
    data=json.loads(result)
    assert meta['status']=='completed' and data['created_via']=='ai'
    assert env.service.require(data['id']).target_session_id=='ai-source'


def test_event_conditions_never_evaluate_code():
    assert matches_event([{'path':'payload.name','op':'contains','value':'图谱'}],{'payload':{'name':'知识图谱'}})
    assert not matches_event([{'path':'payload.score','op':'gt','value':10}],{'payload':{'score':'__import__("os")'}})
    assert not matches_event([{'path':'absent','op':'eq','value':None}],{})


def test_model_crud_is_resident_and_inherits_source_conversation(env):
    from app.services.scheduled_task_tools import execute
    from app.services.runtime_tool_catalog import compact_definitions
    from app.services.scheduled_task_tools import tool_definition
    env.db.add(ChatSession(id='source-chat',workspace_id='ws',title='Source'));env.db.commit()
    runtime=SimpleNamespace(extensions=env.manager,_success=lambda data,meta,sources:data)
    created=execute(runtime,{'action':'create','task':{'name':'AI created','prompt':'复习提醒','schedule_mode':'once','delay_seconds':60}},chat_session_id='source-chat')
    assert created['created_via']=='ai' and created['target_session_id']=='source-chat'
    assert execute(runtime,{'action':'read','task_id':created['id']})['id']==created['id']
    assert not execute(runtime,{'action':'pause','task_id':created['id']})['enabled']
    updated=execute(runtime,{'action':'update','task_id':created['id'],'patch':{'prompt':'更新指令','delay_seconds':180,'enabled':True}})
    assert updated['prompt']=='更新指令' and updated['next_trigger_in_seconds']>=178
    definitions,_=compact_definitions([tool_definition()],management_names={'lg_scheduled_tasks'},active_ids=set(),active_families=set(),progressive=True)
    assert definitions[0]['function']['name']=='lg_scheduled_tasks'
    from jsonschema import Draft202012Validator
    schema=definitions[0]['function']['parameters']
    Draft202012Validator(schema).validate({'action':'create','task':{'name':'event task','prompt':'总结','trigger_type':'condition','event_type':'event.ready','event_filters':[{'path':'payload.score','op':'gte','value':80}]}})
    assert '$ref' not in json.dumps(schema)
    execute(runtime,{'action':'delete','task_id':created['id']})
    assert execute(runtime,{'action':'list'})=={'tasks':[]}


def test_prompt_worker_persists_actual_chat_and_includes_event_as_untrusted_data(env,monkeypatch):
    observed=[]
    class FakeChat:
        model_provider=SimpleNamespace(available=True)
        def create_stream(self,session_id,request,**kwargs):
            observed.append((session_id,request,kwargs))
            assert '非可信数据' in request.content
            env.db.add(Message(workspace_id='ws',session_id=session_id,role='user',content=request.content,status='completed'))
            env.db.add(Message(workspace_id='ws',session_id=session_id,role='assistant',content='三条实际学习建议',status='completed'))
            env.db.commit()
            yield 'event: done\ndata: {}\n\n'
    monkeypatch.setattr('app.services.chat_service_factory.build_chat_service',lambda *a,**kw:FakeChat())
    task=env.service.create(prompt(trigger_type='event',event_type='event.learn'))
    publish_event(env.db,'ws','owner','learn',{'topic':'图谱'},event_id='learn-1')
    claims=claim_due(env.settings,env.factory)
    execute_run(*claims[0],env.settings,env.factory)
    env.db.expire_all()
    run=env.db.get(Run,claims[0][1])
    assert run.status=='succeeded',run.error_message
    assert run.result_json['text']=='三条实际学习建议'
    session=env.db.get(ChatSession,run.result_json['chat_session_id'])
    assert session.session_kind=='scheduled_task'
    assert observed[0][2]['idempotency_key']=='scheduled:'+run.id


def test_prompt_cancellation_is_not_falsely_reported_as_success(env,monkeypatch):
    class FakeChat:
        model_provider=SimpleNamespace(available=True)
        def create_stream(self,session_id,request,**kwargs):
            run=env.db.scalar(select(Run).where(Run.status=='running'))
            run.cancel_requested=True;env.db.commit()
            yield 'event: progress\ndata: {}\n\n'
        def cancel_message(self,*args):pass
    monkeypatch.setattr('app.services.chat_service_factory.build_chat_service',lambda *a,**kw:FakeChat())
    task=env.service.create(prompt())
    run=env.service.claim(task.id)
    execute_run(task.id,run.id,env.settings,env.factory)
    env.db.expire_all()
    actual=env.db.get(Run,run.id)
    assert actual.status=='failed' and actual.error_code=='scheduled_prompt_cancelled'
    assert actual.result_json['chat_session_id']


def test_prompt_needs_input_marker_is_marked_failed_not_succeeded(env,monkeypatch):
    from app.services.chat import ChatService
    from app.providers.ports.model import ProviderStreamEvent
    class Model:
        available=True;provider_id='schedule-test';model_id='schedule-test';remote_capability=True
        thinking_mode='off';actual_reasoning_effort=None;search_route='disabled'
        supports_structured_chat=True
        def stream_chat(self,messages,**kwargs):
            yield ProviderStreamEvent(type='text_delta',content='[NEEDS_INPUT]\n缺少需要汇总的目标项目名称。')
            yield ProviderStreamEvent(type='completed',finish_reason='stop')
        def generate_json(self,*args,**kwargs):return {}
    monkeypatch.setattr(
        'app.services.chat_service_factory.build_chat_service',
        lambda db,*,workspace_context,settings,**kwargs: ChatService(
            db,workspace_context.workspace_id,workspace_context.principal.user_id,Model(),settings=settings
        ),
    )
    task=env.service.create(prompt(agent_mode=False))
    run=env.service.claim(task.id)
    execute_run(task.id,run.id,env.settings,env.factory)
    env.db.expire_all()
    actual=env.db.get(Run,run.id)
    assert actual.status=='failed'
    assert actual.error_code=='scheduled_prompt_needs_input'
    assert '[NEEDS_INPUT]' in actual.result_json['text']


@pytest.mark.parametrize('session_mode', [None, 'new', 'existing'], ids=['no-parent', 'new-with-parent', 'existing'])
@pytest.mark.parametrize('trigger', ['manual', 'scheduled'])
@pytest.mark.parametrize('structured', [False, True], ids=['text-provider', 'structured-provider'])
def test_prompt_uses_real_chat_service_stream_and_persisted_messages(env,monkeypatch,session_mode,trigger,structured):
    from app.services.chat import ChatService
    from app.providers.ports.model import ProviderStreamEvent
    observed=[]
    parent_text='PARENT-HISTORY-MUST-NOT-LEAK'
    class Model:
        available=True;provider_id='schedule-test';model_id='schedule-test';remote_capability=True
        thinking_mode='off';actual_reasoning_effort=None;search_route='disabled'
        supports_structured_chat=structured
        def stream_chat(self,messages,**kwargs):
            observed.append('\n'.join(message.content for message in messages))
            yield ProviderStreamEvent(type='text_delta',content='REAL-CHAT-PIPELINE')
            yield ProviderStreamEvent(type='completed',finish_reason='stop')
        def stream_answer(self,prompt):
            observed.append(prompt)
            yield 'REAL-CHAT-PIPELINE'
        def generate_json(self,*args,**kwargs):return {}
    def factory(db,*,workspace_context,settings,**kwargs):
        return ChatService(db,workspace_context.workspace_id,workspace_context.principal.user_id,Model(),settings=settings)
    monkeypatch.setattr('app.services.chat_service_factory.build_chat_service',factory)
    options={}
    if session_mode is not None:
        env.db.add(ChatSession(id='prompt-source',workspace_id='ws',title='Original conversation'))
        env.db.flush()
        env.db.add(Message(workspace_id='ws',session_id='prompt-source',role='user',content=parent_text,status='completed'))
        env.db.commit()
        options={'target_session_id':'prompt-source','session_mode':session_mode}
    task=env.service.create(prompt(agent_mode=False,**options))
    run=env.service.claim(task.id,trigger=trigger,now=aware(task.next_run_at)+timedelta(seconds=1) if trigger=='scheduled' else None)
    assert run is not None
    execute_run(task.id,run.id,env.settings,env.factory)
    env.db.expire_all()
    run=env.db.get(Run,run.id)
    assert run.status=='succeeded',run.error_message
    assert 'REAL-CHAT-PIPELINE' in run.result_json['text']
    assert observed and all((parent_text in content)==(session_mode=='existing') for content in observed)
    session=env.db.get(ChatSession,run.result_json['chat_session_id'])
    if session_mode=='existing':
        assert session.id=='prompt-source'
    else:
        assert session.session_kind=='scheduled_task' and session.source_message_id is None
        assert session.parent_session_id==('prompt-source' if session_mode=='new' else None)
        assert session.memory_enabled is False
    messages=list(env.db.scalars(select(Message).where(Message.session_id==session.id)))
    assert any(message.role=='user' and '生成三条学习建议' in message.content for message in messages)
    assert any(message.role=='assistant' and message.status=='completed' for message in messages)
    chat=factory(env.db,workspace_context=SimpleNamespace(workspace_id='ws',principal=env.service.principal),settings=env.settings)
    assert {message.id for message in chat.list_messages(session.id)}=={message.id for message in messages}


def test_webhook_token_is_scoped_rotatable_and_event_id_is_deduplicated(env,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import WorkspaceContext,workspace_context
    from app.api.routers.scheduled_tasks import router
    from app.core.database import get_db
    from app.core.config import get_settings
    from app.core.errors import install_error_handlers
    task=env.service.create(prompt(trigger_type='webhook',remaining_executions=1))
    application=FastAPI();application.include_router(router,prefix='/api/v1');install_error_handlers(application)
    application.dependency_overrides[get_db]=lambda:env.db
    application.dependency_overrides[workspace_context]=lambda:WorkspaceContext(env.service.principal,env.service.workspace,frozenset({'workspace.manage'}))
    application.dependency_overrides[get_settings]=lambda:env.settings
    with TestClient(application) as client:
        base=f'/api/v1/scheduled-tasks/{task.id}'
        assert client.post(base+'/trigger',json={}).status_code==403
        response=client.post(base+'/webhook-token');assert response.status_code==200,response.text
        token=response.json()['token'];headers={'X-Automation-Token':token}
        packet={'event_type':'build.done','payload':{'result':'ok'},'event_id':'build-1'}
        first=client.post(base+'/trigger',json=packet,headers=headers)
        second=client.post(base+'/trigger',json=packet,headers=headers)
        assert first.status_code==202,first.text
        assert first.json()['event_id']==second.json()['event_id']
        client.post(base+'/webhook-token')
        assert client.post(base+'/trigger',json=packet,headers=headers).status_code==403
    assert len(claim_due(env.settings,env.factory))==1


def test_additive_migration_preserves_existing_cron_rows(tmp_path):
    from app.core.migrations.scheduled_automation import apply_scheduled_automation
    from app.core.database import Base
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE scheduled_extension_tasks (id VARCHAR(36) PRIMARY KEY, name VARCHAR(160), cron_expression VARCHAR(120))')
        connection.exec_driver_sql("INSERT INTO scheduled_extension_tasks VALUES ('old','Old schedule','0 9 * * *')")
        # Minimal legacy run table is sufficient for additive column inspection.
        connection.exec_driver_sql('CREATE TABLE scheduled_extension_runs (id VARCHAR(36) PRIMARY KEY)')
        # Supply the required legacy columns before testing the newly expanded
        # columns; old production tables already have these.
        for table in (Task.__table__,Run.__table__):
            present={c['name'] for c in inspect(connection).get_columns(table.name)}
            for column in table.columns:
                if column.name not in present and column.server_default is None and not column.nullable and column.name!='id':
                    from sqlalchemy.schema import CreateColumn
                    ddl=str(CreateColumn(column).compile(dialect=connection.dialect))
                    default=" DEFAULT '{}'" if column.name.endswith('_json') else " DEFAULT 0" if str(column.type)=='INTEGER' else " DEFAULT ''"
                    connection.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}{default}')
        apply_scheduled_automation(connection);apply_scheduled_automation(connection)
        row=connection.exec_driver_sql("SELECT name,cron_expression,trigger_type,schedule_mode,execution_count FROM scheduled_extension_tasks WHERE id='old'").one()
        assert tuple(row)==('Old schedule','0 9 * * *','time','cron',0)
    engine.dispose()
