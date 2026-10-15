import json

import httpx
from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.models import ChatSession, Goal, Message, MessagePartRecord
from app.providers.ports.model import ProviderStreamEvent
from app.providers.remote.openai import ProviderTransportError
from app.services.chat import ChatService


class ToolContinuationProvider:
    available = True
    remote_capability = True
    provider_id = 'stub-provider'
    model_id = 'stub-model'
    supports_structured_chat = True
    supports_agent_tools = True
    thinking_mode = 'medium'
    actual_reasoning_effort = 'medium'
    search_route = 'disabled'
    last_usage = {}

    def __init__(self, *, tool_rounds=1, permanent=False):
        self.tool_rounds = tool_rounds
        self.permanent = permanent
        self.calls = 0
        self.histories = []

    def stream_chat(self, messages, **kwargs):
        self.calls += 1
        self.histories.append([message.as_payload() for message in messages])
        if self.calls <= self.tool_rounds:
            yield ProviderStreamEvent('reasoning_delta', content='Read the goal state.')
            yield ProviderStreamEvent('text_delta', content='准备学习目标。')
            yield ProviderStreamEvent('tool_calls', tool_calls=[{
                'id': f'call_{self.calls}', 'type': 'function',
                'function': {'name': 'lg_goal_create', 'arguments': json.dumps({'title': f'RAG {self.calls}'})},
            }])
            yield ProviderStreamEvent('completed', finish_reason='tool_calls')
            return
        if self.permanent or self.calls == self.tool_rounds + 1:
            raise ProviderTransportError('DeepSeek chat stream transport failed (RemoteProtocolError)') from httpx.RemoteProtocolError('Server disconnected without sending a response.')
        yield ProviderStreamEvent('text_delta', content='目标草稿已创建，请确认。')
        yield ProviderStreamEvent('completed', finish_reason='stop')


def _events(body):
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith('data:')]


def _install(monkeypatch, provider, *, real_goals=False):
    from app.api.routers import chat as router
    executed = []

    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id,
            provider, settings=settings, retry_delays=(0,), retry_jitter_ratio=0)
        service._agent_skill_package_instructions = lambda **kwargs: 'goal-learning-route'
        service._build_fast_preface_provider = lambda: None
        service._agent_tool_definitions = lambda *args, **kwargs: [{'type': 'function', 'function': {'name': 'lg_goal_create', 'parameters': {'type': 'object'}}}]

        def execute(call, domains, session_id, **kwargs):
            executed.append(call['id'])
            session = db.get(ChatSession, session_id)
            if not session.goal_id:
                goal = Goal(workspace_id=context.workspace_id, title='RAG', raw_prompt='学习 RAG', status='clarifying')
                db.add(goal)
                db.flush()
                session.goal_id = goal.id
                db.commit()
            return json.dumps({'goal_id': session.goal_id}), {'status': 'completed'}, []

        if real_goals:
            from app.services.chat_service_factory import build_agent_tool_runtime
            service.agent_tool_runtime = build_agent_tool_runtime(db, context, settings)
        else:
            service._execute_agent_tool = execute
        return service

    monkeypatch.setattr(router, 'service', factory)
    return executed


def _start(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    sid = client.post('/api/v1/sessions', headers=headers, json={'title': 'transport regression'}).json()['id']
    return headers, sid


def test_goal_continuation_recovers_without_reexecuting_tools(client, register_user, auth_headers, monkeypatch):
    provider = ToolContinuationProvider()
    executed = _install(monkeypatch, provider)
    headers, sid = _start(client, register_user, auth_headers)
    response = client.post(f'/api/v1/sessions/{sid}/messages/stream', headers=headers,
        json={'content': '学习 RAG', 'agent_mode': True, 'goal_mode': True})
    assert response.status_code == 200, response.text
    types = [event['type'] for event in _events(response.text)]
    assert 'provider.retry.scheduled' in types
    assert 'message.completed' in types
    assert executed == ['call_1']
    assert provider.histories[1] == provider.histories[2]
    with SessionLocal() as db:
        assert db.get(ChatSession, sid).goal_id is not None
        mid = _events(response.text)[-1]['message_id']
        assert db.get(Message, mid).provider_trace['attempts'] == 3
    assistant = client.get(f'/api/v1/sessions/{sid}/messages', headers=headers).json()['items'][-1]
    assert all(part['status'] == 'completed' for part in assistant['parts'])


def test_real_goal_tool_binds_one_draft_across_transport_retry(client, register_user, auth_headers, monkeypatch):
    provider = ToolContinuationProvider()
    _install(monkeypatch, provider, real_goals=True)
    headers, sid = _start(client, register_user, auth_headers)
    response = client.post(f'/api/v1/sessions/{sid}/messages/stream', headers=headers,
        json={'content': '学习 RAG', 'agent_mode': True, 'goal_mode': True})
    assert response.status_code == 200, response.text
    assert any(event['type'] == 'message.completed' for event in _events(response.text))
    with SessionLocal() as db:
        session = db.get(ChatSession, sid)
        goal = db.get(Goal, session.goal_id)
        assert goal.title == 'RAG 1'
        assert goal.raw_prompt == '学习 RAG'
        assert goal.status == 'clarifying'
        assert len(list(db.scalars(select(Goal).where(Goal.workspace_id == session.workspace_id)))) == 1


def test_exhausted_connection_error_keeps_completed_parts_terminal(client, register_user, auth_headers, monkeypatch):
    provider = ToolContinuationProvider(permanent=True)
    executed = _install(monkeypatch, provider)
    headers, sid = _start(client, register_user, auth_headers)
    response = client.post(f'/api/v1/sessions/{sid}/messages/stream', headers=headers,
        json={'content': '学习 RAG', 'agent_mode': True, 'goal_mode': True})
    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert any(e['type'] == 'provider.retry.exhausted' for e in events)
    assert any(e['type'] == 'message.failed' for e in events)
    assert executed == ['call_1']
    assistant = client.get(f'/api/v1/sessions/{sid}/messages', headers=headers).json()['items'][-1]
    assert assistant['status'] == 'failed'
    with SessionLocal() as db:
        parts = list(db.scalars(select(MessagePartRecord).where(MessagePartRecord.message_version_id == events[-1]['message_version_id'])))
        assert not any(part.status in {'pending', 'streaming'} for part in parts)
        assert all(part.status == 'completed' for part in parts if part.part_type in {'tool_call', 'agent_step', 'reasoning_content'})


def test_manual_retry_tool_rounds_do_not_spend_connection_retry_budget(client, register_user, auth_headers, monkeypatch):
    provider = ToolContinuationProvider(permanent=True)
    _install(monkeypatch, provider)
    headers, sid = _start(client, register_user, auth_headers)
    initial = client.post(f'/api/v1/sessions/{sid}/messages/stream', headers=headers,
        json={'content': '学习 RAG', 'agent_mode': True, 'goal_mode': True})
    mid = _events(initial.text)[-1]['message_id']
    provider = ToolContinuationProvider(tool_rounds=6)
    executed = _install(monkeypatch, provider)
    response = client.post(f'/api/v1/sessions/{sid}/messages/{mid}/retry', headers=headers, json={})
    assert response.status_code == 200, response.text
    types = [event['type'] for event in _events(response.text)]
    assert 'provider.retry.scheduled' in types
    assert 'message.completed' in types
    assert len(executed) == 6
    assert provider.histories[-2] == provider.histories[-1]
    assistant = client.get(f'/api/v1/sessions/{sid}/messages', headers=headers).json()['items'][-1]
    with SessionLocal() as db:
        assert db.get(Message, mid).provider_trace['attempts'] == 8
    assert all(part['status'] == 'completed' for part in assistant['parts'])
