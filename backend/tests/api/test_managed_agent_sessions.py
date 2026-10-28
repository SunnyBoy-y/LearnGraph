import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import ChatSession, Message, SandboxAgentTask, SandboxJob
from app.providers.ports.model import ProviderStreamEvent
from app.services.chat import ChatService
from app.services.managed_agent_session import control_child_session, execute_managed_session


@pytest.fixture(scope='module')
def client():
    # Exercise the real HTTP/auth/chat routes without unrelated application schedulers.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routers import auth, chat, sandbox
    from app.core.database import init_database
    from app.core.errors import install_error_handlers
    init_database()
    application = FastAPI()
    install_error_handlers(application)
    for router in (auth.router, chat.router, sandbox.router):
        application.include_router(router, prefix='/api/v1')
    with TestClient(application) as transport:
        yield transport


class Provider:
    available = supports_structured_chat = supports_agent_tools = remote_capability = True
    provider_id, model_id, thinking_mode, search_route = 'stub-provider', 'stub-model', 'off', 'disabled'
    actual_reasoning_effort = None
    last_usage = {}
    def __init__(self, mode):
        self.mode, self.calls, self.history = mode, 0, []
    def stream_chat(self, messages, **options):
        self.calls += 1
        self.history.append(messages)
        if self.mode == 'recover' and self.calls == 2:
            from app.providers.remote.openai import ProviderTransportError
            raise ProviderTransportError('injected disconnect after committed UI')
        if self.mode in {'ui','recover','pause'} and not any(m.role == 'tool' for m in messages):
            yield ProviderStreamEvent('tool_calls', tool_calls=[{'id':'ui-one','type':'function','function':{
                'name':'ui_stream','arguments':json.dumps({'op':'mount','block_id':'numbers','component':'table',
                'props':{'title':'比较','columns':[{'key':'value','label':'数值'}]},
                'upsert':{'rows':[{'id':'a','label':'甲组','values':{'value':14}}, {'id':'b','label':'乙组','values':{'value':77}}]}})}}])
            yield ProviderStreamEvent('completed', finish_reason='tool_calls')
        else:
            yield ProviderStreamEvent('text_delta', content=json.dumps({'summary':'已完成比较', 'artifacts':[],
                'acceptance':[{'criterion':'比较完成','status':'passed'}]},ensure_ascii=False))
            yield ProviderStreamEvent('completed', finish_reason='stop')


def setup_task(client, register_user, auth_headers, monkeypatch, mode='reason'):
    from app.services import chat_service_factory as factory
    token, workspace, user, *_ = register_user('managed-'+mode)
    headers = auth_headers(token, workspace)
    parent = client.post('/api/v1/sessions', headers=headers, json={'title':'parent'}).json()['id']
    provider = Provider(mode)
    def build(db, *, workspace_context, settings, **options):
        runtime = factory.build_agent_tool_runtime(db, workspace_context, settings)
        return ChatService(db, workspace, workspace_context.principal.user_id, provider,
            agent_tool_runtime=runtime, settings=settings, retry_delays=(0,), retry_jitter_ratio=0)
    monkeypatch.setattr(factory, 'build_chat_service', build)
    from app.api.routers import chat as router
    monkeypatch.setattr(router, 'service', lambda db,context,settings,**kwargs:
        build(db,workspace_context=context,settings=settings,**kwargs))
    # Use the persisted user id, not register_user's display/login value.
    from app.services.auth import AuthService
    with SessionLocal() as db:
        from app.domain.models import User
        actor = db.scalar(select(User).where(User.username == user))
        if actor is None:
            actor_id = client.get('/api/v1/auth/me',headers=headers).json()['id']
        else:
            actor_id = actor.id
        task = SandboxAgentTask(workspace_id=workspace, owner_user_id=actor_id, chat_session_id=parent,
            task_id='managed-task', title='managed', role_key='tool' if mode!='reason' else 'reason',
            spec_json={'origin':'voice' if mode!='reason' else 'chat','requirement_version':1})
        db.add(task); db.flush()
        job = SandboxJob(workspace_id=workspace, owner_user_id=actor_id, chat_session_id=parent,
            kind='subagent', status='RUNNING',attempt=1,payload_json={'task_id':task.id,
                'role_key':task.role_key,'tools':['ui_stream'] if mode!='reason' else [],
                'prompt':'比较甲组14乙组77，生成交互表格','requirement_version':1,
                'budget':{'max_rounds':6,'max_seconds':60,'max_tokens':10000}})
        db.add(job); db.flush(); task.latest_job_id=job.id; db.commit()
        return headers, task.id, job.id, provider


@pytest.mark.parametrize('mode',['reason','ui'])
def test_worker_uses_real_chat_messages_tools_and_persistent_ui(client,register_user,auth_headers,monkeypatch,mode):
    headers, task_id, job_id, provider = setup_task(client,register_user,auth_headers,monkeypatch,mode)
    checkpoints, events = [], []
    with SessionLocal() as db:
        task,job=db.get(SandboxAgentTask,task_id),db.get(SandboxJob,job_id)
        outcome=execute_managed_session(get_settings(),job,task,
            emit_event=lambda kind,data:events.append((kind,data)),save_checkpoint=checkpoints.append)
        assert outcome.status=='SUCCEEDED', outcome
        db.refresh(task)
        child=db.get(ChatSession,task.spec_json['agent_session_id'])
        assert child.parent_session_id==task.chat_session_id and child.session_kind=='agent'
        messages=list(db.scalars(select(Message).where(Message.session_id==child.id)))
        assert {m.role for m in messages}=={'user','assistant'}
        assert next(m for m in messages if m.role=='assistant').status=='completed'
        if mode=='ui':
            visuals=outcome.deliverables['visuals']
            assert len(visuals)==1 and len(visuals[0]['rows'])==2
            assert checkpoints[-1]['tool_calls_total']==1
            assert len([kind for kind,_ in events if kind=='tool_call'])==1
        # Ordinary HTTP writes cannot race the managed worker.
        response=client.post(f'/api/v1/sessions/{child.id}/messages/stream',headers=headers,json={'content':'race','agent_mode':True})
        assert response.status_code==409, response.text


def test_takeover_only_after_safe_checkpoint_and_resume_preserves_history(client,register_user,auth_headers,monkeypatch):
    headers,task_id,job_id,_=setup_task(client,register_user,auth_headers,monkeypatch)
    with SessionLocal() as db:
        task,job=db.get(SandboxAgentTask,task_id),db.get(SandboxJob,job_id)
        task.status='QUEUED';job.status='QUEUED';db.commit()
        result=control_child_session(db,get_settings(),task,'takeover')
        assert result['controller']=='user' and task.status=='PAUSED'
        child=db.get(ChatSession,result['agent_session_id'])
        assert child.context_capsule['managed_agent']['controller']=='user'
        response=client.post(f'/api/v1/sessions/{child.id}/messages/stream',headers=headers,
            json={'content':'接管后请继续分析，保留原有边界','agent_mode':True})
        assert response.status_code==200 and 'message.completed' in response.text, response.text[-1000:]
        db.refresh(job)
        checkpoint=job.payload_json['_resume_checkpoint']
        assert any('接管后请继续' in (item.get('content') or '') for item in checkpoint['messages'])
        control_child_session(db,get_settings(),task,'resume')
        assert task.status==job.status=='QUEUED'
        assert db.get(ChatSession,child.id).context_capsule['managed_agent']['controller']=='agent'


def test_ordinary_model_retry_does_not_repeat_completed_ui_tool(client,register_user,auth_headers,monkeypatch):
    _,task_id,job_id,provider=setup_task(client,register_user,auth_headers,monkeypatch,'recover')
    events=[]
    with SessionLocal() as db:
        outcome=execute_managed_session(get_settings(),db.get(SandboxJob,job_id),db.get(SandboxAgentTask,task_id),
            emit_event=lambda kind,data:events.append((kind,data)),save_checkpoint=lambda state:None)
        assert outcome.status=='SUCCEEDED', outcome
        assert provider.calls==3
        assert len([kind for kind,_ in events if kind=='tool_call'])==1
        assert any(m.role=='tool' for m in provider.history[-1])


def test_fresh_worker_resumes_only_unanswered_suffix(client,register_user,auth_headers,monkeypatch):
    _,task_id,job_id,provider=setup_task(client,register_user,auth_headers,monkeypatch,'pause')
    original=provider.stream_chat
    def initial_batch(messages,**options):
        for event in original(messages,**options):
            if event.type=='tool_calls':
                event=ProviderStreamEvent('tool_calls', tool_calls=[
                    {'id':'clock','type':'function','function':{'name':'get_current_time','arguments':'{}'}}, *event.tool_calls])
            yield event
    provider.stream_chat=initial_batch
    checkpoints=[]
    def pause_after_clock(kind,data):
        if kind=='tool_call' and data['tool_name']=='get_current_time':
            with SessionLocal() as writer:
                row=writer.get(SandboxAgentTask,task_id)
                row.spec_json={**row.spec_json,'pause_requested':True}
                writer.commit()
    with SessionLocal() as db:
        job,task=db.get(SandboxJob,job_id),db.get(SandboxAgentTask,task_id)
        job.payload_json={**job.payload_json,'tools':['get_current_time','ui_stream']};db.commit()
        paused=execute_managed_session(get_settings(),job,task,emit_event=pause_after_clock,save_checkpoint=checkpoints.append)
        assert paused.status=='PAUSED', paused
        assert checkpoints[-1]['tool_calls_total']==1
        db.refresh(task)
        task.spec_json={**task.spec_json,'pause_requested':False}
        job.payload_json={**job.payload_json,'_resume_checkpoint':checkpoints[-1]}
        db.commit()
        events=[]
        completed=execute_managed_session(get_settings(),job,task,
            emit_event=lambda kind,data:events.append((kind,data)),save_checkpoint=checkpoints.append)
        assert completed.status=='SUCCEEDED',completed
        assert [data['tool_name'] for kind,data in events if kind=='tool_call']==['ui_stream']
        assert checkpoints[-1]['tool_calls_total']==2


def test_manager_requests_actual_delivery_after_plan_only_answer(client,register_user,auth_headers,monkeypatch):
    _,task_id,job_id,provider=setup_task(client,register_user,auth_headers,monkeypatch,'ui')
    original=provider.stream_chat
    def plan_then_deliver(messages,**options):
        if not provider.history:
            provider.history.append(messages)
            yield ProviderStreamEvent('text_delta',content='好的，我先挂载表格。')
            yield ProviderStreamEvent('completed',finish_reason='stop')
        else:
            yield from original(messages,**options)
    provider.stream_chat=plan_then_deliver
    events=[]
    with SessionLocal() as db:
        task,job=db.get(SandboxAgentTask,task_id),db.get(SandboxJob,job_id)
        task.spec_json={**task.spec_json,'capability_id':'artifact_create'};db.commit()
        outcome=execute_managed_session(get_settings(),job,task,
            emit_event=lambda kind,data:events.append((kind,data)),save_checkpoint=lambda state:None)
        assert outcome.status=='SUCCEEDED',outcome
        assert len(outcome.deliverables['visuals'])==1
        assert [kind for kind,_ in events].count('acceptance.retry')==1
        assert [kind for kind,_ in events].count('tool_call')==1


def test_event_sequence_allocates_from_database_when_control_has_stale_task(client,register_user,auth_headers,monkeypatch):
    from app.domain.models import SandboxAgentEvent
    from app.services.sandbox_scheduler import append_agent_event
    _,task_id,_,_=setup_task(client,register_user,auth_headers,monkeypatch)
    with SessionLocal() as worker, SessionLocal() as control:
        task=worker.get(SandboxAgentTask,task_id)
        stale=control.get(SandboxAgentTask,task_id)
        append_agent_event(worker,task,'worker',{});worker.commit()
        append_agent_event(control,stale,'pause',{});control.commit()
        append_agent_event(worker,task,'worker.paused',{});worker.commit()
        rows=worker.scalars(select(SandboxAgentEvent).where(SandboxAgentEvent.task_id==task_id)
            .order_by(SandboxAgentEvent.seq)).all()
        assert [(row.seq,row.event_type) for row in rows]==[(1,'worker'),(2,'pause'),(3,'worker.paused')]


def test_authorized_continuation_keeps_ordinary_resolved_tool_history(client,register_user,auth_headers,monkeypatch):
    from app.providers.ports.model import ProviderChatMessage
    from app.services.managed_agent_session import SessionControl, DelegatedProvider
    _,task_id,job_id,provider=setup_task(client,register_user,auth_headers,monkeypatch)
    with SessionLocal() as db:
        task,job=db.get(SandboxAgentTask,task_id),db.get(SandboxJob,job_id)
        control=SessionControl(db,task,job,lambda state:None,lambda *_args:None,interactive=True)
        control.state['messages']=[{'role':'user','content':'old checkpoint'}]
        ordinary=[ProviderChatMessage(role='user',content='original request'),
            ProviderChatMessage(role='assistant',tool_calls=[{'id':'gate','type':'function','function':{'name':'web_search','arguments':'{}'}}]),
            ProviderChatMessage(role='tool',tool_call_id='gate',content='approved actual result')]
        list(DelegatedProvider(provider,control).stream_chat(ordinary,tools=None))
        assert provider.history[-1][-1].content=='approved actual result'
        assert all(item.content!='old checkpoint' for item in provider.history[-1])


def test_voice_turn_reads_current_completed_task_instead_of_old_acceptance_receipt(client,register_user,auth_headers,monkeypatch):
    from app.domain.models import VoiceSessionRecord
    from app.services.chat_service_factory import build_background_workspace_context
    from app.voice.coordinator import VoiceTaskCoordinator, capture_voice_task_outcome
    from app.voice.task_context import load_task_context
    _,task_id,job_id,_=setup_task(client,register_user,auth_headers,monkeypatch,'ui')
    with SessionLocal() as db:
        task=db.get(SandboxAgentTask,task_id)
        context=build_background_workspace_context(db,workspace_id=task.workspace_id,actor_id=task.owner_user_id)
        voice_id='vs_managed_context'
        db.add(VoiceSessionRecord(id=voice_id,workspace_id=task.workspace_id,owner_user_id=task.owner_user_id,
            chat_session_id=task.chat_session_id,tenant_id=context.principal.tenant_id))
        db.commit()
        coordinator=VoiceTaskCoordinator(db,workspace_id=task.workspace_id,tenant_id=context.principal.tenant_id)
        link,_=coordinator.reserve_link(voice_session_id=voice_id,chat_session_id=task.chat_session_id,
            prompt='生成表格',title='比较',role_key='tool',thinking_mode='off',trigger_turn_id=None,idempotency_key='task-context')
        coordinator.complete_link(link,subagent_id=task.task_id,job_id=job_id,status='queued')
        task.status='SUCCEEDED';task.result_text='甲组14，乙组77，数据由用户口述'
        task.deliverables_json={'summary':task.result_text,'artifacts':[]}
        capture_voice_task_outcome(db,task);db.commit()
    current=load_task_context(voice_id)
    assert 'SUCCEEDED' in current and '甲组14' in current and '77' in current
    assert '优先于历史的受理超时' in current


def test_resume_reconciles_user_committed_ui_newer_than_worker_checkpoint(client,register_user,auth_headers,monkeypatch):
    _,task_id,job_id,_=setup_task(client,register_user,auth_headers,monkeypatch,'ui')
    checkpoints=[]
    with SessionLocal() as db:
        task,job=db.get(SandboxAgentTask,task_id),db.get(SandboxJob,job_id)
        first=execute_managed_session(get_settings(),job,task,emit_event=lambda *_args:None,save_checkpoint=checkpoints.append)
        child=first.deliverables['agent_session_id']
        scene=json.loads(json.dumps(first.deliverables['visuals'][0]))
        scene['rows'][1]['values']['value']=88
        scene['revision']+=1
        db.add(Message(workspace_id=task.workspace_id,session_id=child,role='assistant',status='completed',content='按接管人的要求更新乙组为88',
            parts=[{'id':'takeover-ui','type':'ui','status':'completed','content':'','data':scene,'sequence':0}]))
        job.payload_json={**job.payload_json,'_resume_checkpoint':checkpoints[-1]};db.commit()
        completed=execute_managed_session(get_settings(),job,task,emit_event=lambda *_args:None,save_checkpoint=checkpoints.append)
        assert completed.status=='SUCCEEDED',completed
        assert completed.deliverables['visuals'][0]['rows'][1]['values']['value']==88


@pytest.mark.parametrize('takeover', [False, True])
def test_all_delegations_ignore_old_task_budgets_but_keep_usage(client, register_user, auth_headers, monkeypatch, takeover):
    import app.services.chat as chat_module
    # Cross the ordinary stream ceiling as well as all historical task caps.
    monkeypatch.setattr(chat_module, 'MAX_PROVIDER_STREAM_ATTEMPTS', 2)
    monkeypatch.setattr(chat_module, 'MAX_AGENT_TOOL_ROUNDS', 1)
    headers, task_id, job_id, provider = setup_task(client, register_user, auth_headers, monkeypatch)
    provider.last_usage = {'input_tokens': 100, 'output_tokens': 10}
    def tool_rounds(messages, **options):
        provider.calls += 1
        if provider.calls <= 3:
            yield ProviderStreamEvent('tool_calls', tool_calls=[{'id': f'clock-{provider.calls}', 'function': {
                'name': 'get_current_time', 'arguments': '{}'}}])
            yield ProviderStreamEvent('completed', finish_reason='tool_calls')
        else:
            yield ProviderStreamEvent('text_delta', content='已完成')
            yield ProviderStreamEvent('completed', finish_reason='stop')
    provider.stream_chat = tool_rounds
    with SessionLocal() as db:
        task, job = db.get(SandboxAgentTask, task_id), db.get(SandboxJob, job_id)
        task.status = job.status = 'QUEUED'
        job.payload_json = {**job.payload_json, 'tools': [], 'budget': {
            'max_tokens': 1, 'max_rounds': 1, 'max_tool_calls': 1, 'max_seconds': 1,
            'max_cost_usd': 0, 'max_cost_amounts': {'USD': '0', 'CNY': '0'}},
            '_resume_checkpoint': {'requirement_version': 1, 'used_tokens': 1_000_000,
                'rounds_used': 1000, 'tool_calls_total': 1000, 'active_seconds': 10000,
                'used_costs': {'USD': '100', 'CNY': '200'}}}
        db.commit()
        if takeover:
            child_id = control_child_session(db, get_settings(), task, 'takeover')['agent_session_id']
            response = client.post(f'/api/v1/sessions/{child_id}/messages/stream', headers=headers,
                json={'content': '继续', 'agent_mode': True})
            assert response.status_code == 200 and 'message.completed' in response.text, response.text[-1000:]
            db.refresh(job)
            checkpoint = job.payload_json['_resume_checkpoint']
        else:
            from app.services import chat_service_factory as factory
            original_build = factory.build_chat_service
            inherited_options = []
            def build_with_parent(*args, **options):
                inherited_options.append(options)
                return original_build(*args, **options)
            monkeypatch.setattr(factory, 'build_chat_service', build_with_parent)
            db.add(Message(workspace_id=task.workspace_id, session_id=task.chat_session_id,
                role='assistant', content='委派任务', status='completed', provider_trace={
                    'provider_id': provider.provider_id, 'model_id': provider.model_id,
                    'search_route': 'disabled', 'web_search': False}))
            db.commit()
            checkpoints = []
            outcome = execute_managed_session(get_settings(), job, task,
                emit_event=lambda *_args: None, save_checkpoint=checkpoints.append)
            assert outcome.status == 'SUCCEEDED', outcome
            assert inherited_options[0]['provider_id'] == provider.provider_id
            assert inherited_options[0]['model_id'] == provider.model_id
            assert inherited_options[0]['search_route'] == 'disabled'
            checkpoint = checkpoints[-1]
        assert provider.calls == 4
        assert checkpoint['rounds_used'] == 1004
        assert checkpoint['tool_calls_total'] == 1003
        assert checkpoint['used_tokens'] > 1_000_000


@pytest.mark.parametrize('currency', ['USD', 'CNY'])
@pytest.mark.parametrize('feature', ['*', 'chat'])
def test_parent_and_sibling_agents_share_the_configured_api_budget(client, register_user, auth_headers, monkeypatch, currency, feature):
    from decimal import Decimal
    from uuid import uuid4
    from app.domain.models import ProviderConfig
    from app.services.billing import BillingService
    headers, task_id, job_id, provider = setup_task(client, register_user, auth_headers, monkeypatch)
    provider.provider_id = str(uuid4())
    provider.last_usage = {'input_tokens': 5, 'output_tokens': 5}
    with SessionLocal() as db:
        task, job = db.get(SandboxAgentTask, task_id), db.get(SandboxJob, job_id)
        db.add(ProviderConfig(id=provider.provider_id, workspace_id=task.workspace_id,
            display_name='shared-budget', provider_type='openai_compatible',
            base_url='https://unknown.example', capabilities={'billing_currency': currency},
            remote_capability=True, enabled=True))
        db.commit()
        billing = BillingService(db, task.workspace_id, task.owner_user_id)
        billing.create_price(provider_id=provider.provider_id, model_id=provider.model_id,
            currency=currency, input_per_million='0', output_per_million='0', fixed_per_call='0.1')
        billing.create_budget_policy(name='shared API budget', currency=currency, hard_limit='0.25', feature=feature)
        # The main Agent has already spent 0.1 from the same budget.
        quote = billing.preflight_model_call(provider_id=provider.provider_id, model_id=provider.model_id,
            feature='chat', estimated_input_tokens=5, estimated_output_tokens=5, remote_capability=True)
        billing.record_usage(quote, input_tokens=5, output_tokens=5, attempt=1)
        task.status = job.status = 'QUEUED'
        db.commit()
        child_id = control_child_session(db, get_settings(), task, 'takeover')['agent_session_id']
        response = client.post(f'/api/v1/sessions/{child_id}/messages/stream', headers=headers,
            json={'content': '完成任务', 'agent_mode': True})
        assert response.status_code == 200 and 'message.completed' in response.text, response.text[-1000:]
        assert provider.calls == 1
        # A different delegated task must not get a separate API allowance.
        sibling = SandboxAgentTask(workspace_id=task.workspace_id, owner_user_id=task.owner_user_id,
            chat_session_id=task.chat_session_id, task_id='sibling-task', role_key='reason', status='QUEUED', spec_json={})
        db.add(sibling); db.flush()
        sibling_job = SandboxJob(workspace_id=task.workspace_id, owner_user_id=task.owner_user_id,
            chat_session_id=task.chat_session_id, kind='subagent', status='QUEUED', payload_json={'prompt': '继续任务'})
        db.add(sibling_job); db.flush(); sibling.latest_job_id = sibling_job.id; db.commit()
        sibling_id = control_child_session(db, get_settings(), sibling, 'takeover')['agent_session_id']
        blocked = client.post(f'/api/v1/sessions/{sibling_id}/messages/stream', headers=headers,
            json={'content': '继续任务', 'agent_mode': True})
        assert blocked.status_code == 200 and 'event: message.failed' in blocked.text, blocked.text
        assert '"code": "budget_hard_limit_exceeded"' in blocked.text
        assert 'event: message.completed' not in blocked.text
        assert provider.calls == 1
        status = billing.budget_statuses(currency=currency)[0]
        assert Decimal(status['spent_amount']) == Decimal('0.2')


def test_child_inherits_normal_runtime_and_cannot_delegate_again(client, register_user, auth_headers, monkeypatch):
    from app.core.errors import AppError
    from app.domain.schemas.sandbox import SandboxAgentSubagentRequest
    from app.services.chat_service_factory import build_agent_tool_runtime, build_background_workspace_context
    from app.services.managed_agent_session import DelegatedRuntime, SessionControl, ensure_child_session
    _, task_id, job_id, _ = setup_task(client, register_user, auth_headers, monkeypatch)
    with SessionLocal() as db:
        task, job = db.get(SandboxAgentTask, task_id), db.get(SandboxJob, job_id)
        parent = db.get(ChatSession, task.chat_session_id)
        parent.memory_enabled = True
        db.commit()
        child = ensure_child_session(db, task, job)
        assert child.memory_enabled == parent.memory_enabled
        assert child.writeback_policy == parent.writeback_policy
        context = build_background_workspace_context(db, workspace_id=task.workspace_id, actor_id=task.owner_user_id)
        runtime = build_agent_tool_runtime(db, context, get_settings())
        control = SessionControl(db, task, job, lambda _: None, lambda *_: None)
        delegated = DelegatedRuntime(runtime, control)
        options = dict(agent_mode_enabled=True, web_search_enabled=True, memory_enabled=True,
            capability_families={'runtime', 'builtin_extension'})
        parent_names = {d['function']['name'] for d in runtime.definitions(**options)}
        child_names = {d['function']['name'] for d in delegated.definitions(**options)}
        assert 'sandbox_subagent' in parent_names
        assert child_names == parent_names - {'sandbox_subagent'}
        assert all(d['function_name'] != 'sandbox_subagent' for d in runtime.extensions.runtime_tool_descriptors)
        with pytest.raises(AppError, match='cannot create further agents'):
            delegated.execute({'id': 'nested', 'function': {'name': 'sandbox_subagent', 'arguments': '{"prompt":"nested"}'}},
                chat_session_id=child.id, allowed_domains=[])
        # Also reject a direct service/API call, not only model tool disclosure.
        with pytest.raises(AppError, match='cannot create further agents'):
            runtime.sandbox.toolkit_subagent(SandboxAgentSubagentRequest(chat_session_id=child.id, prompt='nested'))
        created = runtime.sandbox.toolkit_subagent(SandboxAgentSubagentRequest(
            chat_session_id=parent.id, prompt='新委派', role_key='reason', tools=['sandbox_write_file'],
            max_rounds=1, max_seconds=1,
            max_tool_calls=1, max_tokens=1, max_cost_usd=0, max_cost_amounts={'CNY': '0'}))
        new_task = db.scalar(select(SandboxAgentTask).where(SandboxAgentTask.task_id == created['task_id']))
        new_job = db.get(SandboxJob, new_task.latest_job_id)
        assert new_task.spec_json['budget'] == new_job.payload_json['budget'] == {}
        assert new_job.deadline_at is None
