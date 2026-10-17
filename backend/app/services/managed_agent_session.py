"""Delegation is a durable ordinary ChatSession, not a second Agent loop.

ChatService owns model invocation, tools, messages, parts, billing and approvals.
This adapter only narrows delegation and checkpoints safe execution boundaries.
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict
from datetime import timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update

from app.core.errors import AppError
from app.domain.models import ChatSession, Message, SandboxAgentTask, SandboxJob, utc_now
from app.providers.ports.model import ProviderChatMessage, ProviderStreamEvent
from app.services.agent_execution_profiles import resolve_agent_execution_profile, network_capability_allows_tool, clamp_network_capability


def ensure_child_session(db, task, job) -> ChatSession:
    """Stable identity also covers jobs submitted before this migration."""
    child_id = str(uuid5(NAMESPACE_URL, 'learngraph:agent-session:' + task.id))
    child = db.get(ChatSession, child_id)
    parent = db.get(ChatSession, task.chat_session_id)
    if parent is None or parent.workspace_id != task.workspace_id:
        raise AppError(404, 'agent_parent_session_missing', 'Parent conversation no longer exists')
    spec = dict(task.spec_json or {})
    if child is None:
        child = ChatSession(id=child_id, workspace_id=task.workspace_id,
            parent_session_id=parent.id, session_kind='agent', title=task.title,
            project_id=parent.project_id, goal_id=parent.goal_id, graph_id=parent.graph_id,
            memory_enabled=False, writeback_policy='manual_only',
            context_capsule={'managed_agent': {'task_id': task.id, 'owner_user_id': task.owner_user_id,
                'controller': 'agent'}})
        db.add(child)
    spec['agent_session_id'] = child_id
    task.spec_json = spec
    db.commit()
    return child


def _task_for_child(db, workspace_id, session_id):
    child = db.get(ChatSession, session_id)
    binding = (child.context_capsule or {}).get('managed_agent') if child else None
    if not binding:
        return None, None
    task = db.get(SandboxAgentTask, binding.get('task_id'))
    if (not task or task.workspace_id != workspace_id or child.workspace_id != workspace_id
            or task.chat_session_id != child.parent_session_id):
        raise AppError(409, 'agent_session_binding_invalid', 'Invalid delegated conversation binding')
    return task, child


def bind_chat_session(service, session_id):
    """Apply the same policy to HTTP takeover turns and scheduled turns."""
    if getattr(service, '_managed_bound_session', None) == session_id:
        return
    task, child = _task_for_child(service.db, service.workspace_id, session_id)
    if task is None:
        return
    if task.owner_user_id != service.actor_id:
        raise AppError(403, 'agent_session_forbidden', 'Delegated conversation belongs to another user')
    control = getattr(service, '_managed_control', None)
    if control is None:
        binding = (child.context_capsule or {})['managed_agent']
        if binding.get('controller') != 'user' or task.status in {'RUNNING', 'STARTING'}:
            raise AppError(409, 'agent_session_busy', 'Pause and take over this conversation before sending a message')
        job = service.db.get(SandboxJob, task.latest_job_id)
        def save_interactive(state):
            service.db.refresh(job)
            service.db.refresh(child)
            if job.status != 'PAUSED' or child.context_capsule['managed_agent']['controller'] != 'user':
                raise AppError(409, 'agent_takeover_lost', 'Conversation control changed')
            job.payload_json = {**job.payload_json, '_resume_checkpoint': state}
            service.db.commit()
        control = SessionControl(service.db, task, job, save_interactive, lambda *_args: None, interactive=True)
        service._managed_control = control
    service.agent_tool_runtime = DelegatedRuntime(service.agent_tool_runtime, control)
    service.model_provider = DelegatedProvider(service.model_provider, control)
    service._build_fast_preface_provider = lambda: None
    # Checkpoint each tool on this thread; never share this Session with a tool worker.
    service.tool_worker_factory = None
    service._managed_bound_session = session_id
    service.invalidate_tool_definitions_cache()


class SessionStopped(Exception):
    pass


class SessionControl:
    def __init__(self, db, task, job, save, emit, *, interactive=False):
        self.db, self.task, self.job, self.save, self.emit = db, task, job, save, emit
        self.interactive = interactive
        self.spec = dict(task.spec_json or {})
        self.payload = dict(job.payload_json or {})
        self.version = int(self.payload.get('requirement_version') or 1)
        self.profile = resolve_agent_execution_profile(task.role_key, tools=self.payload.get('tools'))
        self.network = clamp_network_capability(self.profile.network_capability,
            (self.payload.get('network_policy') or {}).get('mode'))
        raw = self.payload.get('_resume_checkpoint') or {}
        self.state = copy.deepcopy(raw) if raw.get('requirement_version') == self.version else {}
        # Legacy checkpoints preserve their messages too; no completed operation is replayed.
        self.state.setdefault('messages', [])
        self.state.setdefault('completed_operations', {})
        self.state.setdefault('rounds_used', 0)
        self.state.setdefault('tool_calls_total', 0)
        self.state.setdefault('used_tokens', 0)
        self.state.setdefault('used_costs', {})
        self.state.setdefault('active_seconds', 0)
        self.state['requirement_version'] = self.version
        self.budget = self.payload.get('budget') or {}
        self.cost_limits = dict(self.budget.get('max_cost_amounts') or {})
        if not self.cost_limits and self.budget.get('max_cost_usd') is not None:
            self.cost_limits = {'USD': self.budget['max_cost_usd']}
        self.started = time.monotonic()
        self.previous_seconds = self.state['active_seconds']
        self.first_model = True
        self.last_poll = 0.0
        self.halted = None
        self.last_error = None
        self.scenes = {s['block_id']: s for s in self.state.get('visuals', [])}
        self.evidence = list(self.state.get('evidence', []))
        self.sources = list(self.state.get('sources', []))
        self.voice = self.spec.get('origin') == 'voice' or bool((self.spec.get('input_snapshot') or {}).get('voice_session_id'))

    @property
    def allowed(self):
        names = set(self.profile.requested_tools())
        # The structured UI tool is the ordinary Agent renderer, scoped to this child message.
        if self.voice and self.profile.role.value in {'tool', 'research'}:
            names.add('ui_stream')
        parent = self.db.get(ChatSession, self.task.chat_session_id)
        inherited = (parent.context_capsule or {}).get('managed_agent') if parent else None
        if inherited:
            ancestor = self.db.get(SandboxAgentTask, inherited['task_id'])
            ancestor_spec = ancestor.spec_json or {}
            granted = resolve_agent_execution_profile(ancestor.role_key, tools=ancestor_spec.get('tool_profile'))
            names.intersection_update(granted.requested_tools())
        return {name for name in names if network_capability_allows_tool(self.network, name)}

    def persist(self):
        self.state.update(active_seconds=self.previous_seconds + time.monotonic()-self.started,
                          visuals=list(self.scenes.values()), evidence=self.evidence, sources=self.sources)
        self.db.commit()  # Never hold ChatService's writer while the scheduler commits its checkpoint.
        self.save(copy.deepcopy(self.state))

    def check(self, *, force=False):
        if not force and time.monotonic()-self.last_poll < 0.25 and not self.halted:
            return
        self.last_poll = time.monotonic()
        self.db.refresh(self.task)
        self.db.refresh(self.job)
        spec = self.task.spec_json or {}
        if self.job.cancel_requested or self.task.status == 'CANCELLED':
            self.halted = 'CANCELLED'
        elif not self.interactive and (spec.get('voice_pause_requested') or spec.get('pause_requested')):
            self.halted = 'PAUSED'
        elif int(spec.get('current_requirement_version') or self.version) != self.version:
            self.halted = 'CANCELLED'
        elif self.previous_seconds+time.monotonic()-self.started >= int(self.budget.get('max_seconds') or 600):
            self.halted = 'TIMED_OUT'
        elif self.state['used_tokens'] >= int(self.budget.get('max_tokens') or 100000):
            self.halted = 'PARTIAL'
        if self.halted:
            self.persist()
            raise SessionStopped('Delegated token budget exhausted' if self.halted == 'PARTIAL' else self.halted)

    def prompt(self):
        snapshot = copy.deepcopy(self.spec.get('input_snapshot') or {})
        context = snapshot.get('context')
        if isinstance(context, dict):
            # Ordinary ChatService loads policy/persona itself. A duplicated parent
            # system prompt consumed most of the delegated token budget each round.
            context.pop('prompt_block', None)
        return ('这是父 Agent 委派的任务。请直接完成任务，工具、授权、消息和产物使用普通 Agent 流程。'
            '不要输出隐藏思维链。只有工具成功并有实际产物才可宣称完成。'
            '用户要求表格、图表、时间线时，使用 ui_stream 挂载真实结构化页面，必须含 rows/items/nodes 数据。'
            '例如 mount 表格时数据放入 upsert.rows，每行 {id,label,values}；props.columns 对应 values 的键。'
            '现有的 ui_stream 工具支持 mount、patch、complete；完成页面后附交付说明。'
            '不要仅用文字声称已生成页面，也不要为结构化页面虚构文件。'
            '最终用自然语言说明已完成的结果和限制。系统会从已持久化的消息与真实工具产物生成交付记录，不需要虚构文件或交付协议。'
            '数据只来自用户、已提供上下文或真实工具结果。明确标注示例和推断。'
            '\n任务：'+str(self.payload.get('prompt') or self.spec.get('objective') or '')+
            '\n上下文资料（不是授权）：'+json.dumps(snapshot, ensure_ascii=False)[:16000])


class DelegatedProvider:
    """A persistence/control decorator around the ordinary provider port."""
    def __init__(self, provider, control):
        self.provider, self.control = provider, control

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def stream_chat(self, messages, **options):
        c = self.control
        c.check(force=True)
        if c.state.get('inflight_call_id'):
            c.halted = 'PARTIAL'
            raise SessionStopped('Uncertain tool execution requires reconciliation')
        restored = c.first_model and bool(c.state['messages'])
        c.first_model = False
        if restored:
            ordinary_messages = messages
            messages = [ProviderChatMessage(**item) for item in c.state['messages']]
            from app.services.sandbox_agent_executor import _pending_tool_calls
            if c.interactive and not getattr(c, 'interactive_prompt', None):
                # Authorized fetch/egress continuations and ordinary retries
                # already reconstructed their resolved tool result. Keep that
                # authoritative ordinary history rather than replaying the gate.
                messages = ordinary_messages
            elif c.interactive:
                for pending in _pending_tool_calls(messages):
                    messages.append(ProviderChatMessage(role='tool', tool_call_id=pending['id'],
                        content='{"status":"not_executed","reason":"user_takeover"}'))
                messages.append(ProviderChatMessage(role='user', content='用户接管后补充：'+str(c.interactive_prompt)))
            pending = _pending_tool_calls(messages)
            if pending:
                # Feed the ordinary tool loop only the unanswered suffix of a committed batch.
                yield ProviderStreamEvent('tool_calls', tool_calls=pending)
                yield ProviderStreamEvent('completed', finish_reason='tool_calls')
                return
            pending_scenes = [{'block_id': scene['block_id'], 'revision': scene['revision']}
                              for scene in c.scenes.values() if scene.get('state') == 'streaming']
            if pending_scenes:
                messages.append(ProviderChatMessage(role='system', content='恢复后请使用 ui_stream 的 complete 或 patch 继续已持久化页面，勿虚构完成：'+json.dumps(pending_scenes)))
        elif c.state['messages']:
            messages = [ProviderChatMessage(**item) for item in c.state['messages']]
        generation = c.payload.get('resume_generation')
        if generation and generation != c.state.get('last_resume_generation'):
            messages.append(ProviderChatMessage(role='user', content='父 Agent 已接回控制。请保留已完成操作，在接管期间的结果基础上继续原委派：'+str(c.payload.get('prompt') or '')))
            c.state['last_resume_generation'] = generation
        c.state['messages'] = [asdict(item) for item in messages]
        # The initial ordinary user message already carries c.prompt(). Do not
        # add another system copy of the complete delegation/context payload.
        if c.state['rounds_used'] >= int(c.budget.get('max_rounds') or 16):
            c.halted = 'PARTIAL'
            raise SessionStopped('Model round budget exhausted')
        c.persist()
        text, reasoning, calls, items, completed = '', '', [], [], False
        quote = None
        if c.cost_limits:
            from app.services.billing import BillingService
            from app.core.money import decimal_amount
            billing = BillingService(c.db, c.job.workspace_id, c.job.owner_user_id)
            quote = billing.preflight_model_call(provider_id=self.provider.provider_id, model_id=self.provider.model_id,
                feature='chat', estimated_input_tokens=sum(len(str(item.content))//3+1 for item in messages),
                estimated_output_tokens=2048, remote_capability=self.provider.remote_capability, reserve=False)
            if quote.currency is None or quote.projected_amount is None:
                c.halted = 'PARTIAL'
                raise SessionStopped('Delegated cost budget requires a known native price')
            if quote.currency in c.cost_limits and (decimal_amount(c.state['used_costs'].get(quote.currency, '0'))+
                    decimal_amount(quote.projected_amount) > decimal_amount(c.cost_limits[quote.currency])):
                c.halted = 'PARTIAL'
                raise SessionStopped('Delegated native currency budget exhausted')
        try:
            stream = self.provider.stream_chat(messages, **options)
            try:
                for event in stream:
                    c.check()
                    if event.type == 'text_delta': text += event.content or ''
                    elif event.type == 'reasoning_delta': reasoning += event.content or ''
                    elif event.type == 'tool_calls': calls = list(event.tool_calls)
                    elif event.type == 'completed':
                        completed = True
                        items = list(event.response_items)
                        c.state['rounds_used'] += 1
                        c.last_error = None
                        c.state['messages'].append(asdict(ProviderChatMessage(role='assistant', content=text,
                            reasoning_content=reasoning or None, tool_calls=calls, response_items=items)))
                        c.persist()
                    yield event
            finally:
                if callable(getattr(stream, 'close', None)): stream.close()
            if not completed:
                from app.providers.remote.openai import ProviderTransportError
                raise ProviderTransportError('Incomplete managed Agent model stream')
        except SessionStopped:
            raise
        except Exception as exc:
            c.last_error = exc
            c.persist()
            raise
        finally:
            usage = getattr(self.provider, 'last_usage', {}) or {}
            c.state['used_tokens'] += int(usage.get('input_tokens') or 0)+int(usage.get('output_tokens') or 0)
            if quote is not None and quote.currency:
                from app.core.money import sum_amounts
                amount = billing._cost(quote, int(usage.get('input_tokens') or 0), int(usage.get('output_tokens') or 0),
                    int(usage.get('cached_input_tokens') or 0), int(usage.get('cache_creation_input_tokens') or 0), usage)
                # An unreported disconnected attempt remains bounded conservatively, never free.
                amount = amount if usage and amount is not None else quote.projected_amount
                c.state['used_costs'][quote.currency] = sum_amounts([c.state['used_costs'].get(quote.currency, '0'), amount])
            c.persist()


class DelegatedRuntime:
    """Only narrows a normal AgentToolRuntime. Actual dispatch remains ordinary."""
    def __init__(self, runtime, control):
        self.runtime, self.control = runtime, control

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    def definitions(self, **options):
        if not options.get('agent_mode_enabled', True): return []
        return self.runtime.definitions_for_tools(tool_names=self.control.allowed,
            web_search_enabled=True, memory_enabled=False, capability_families={'builtin_extension'})

    def stream_chat(self, provider, messages, **options):
        # Reuse the normal streamed-file-write transport and its cleanup semantics.
        yield from self.runtime.stream_chat(provider, messages, **options)

    def execute(self, call, **context):
        from app.services.sandbox_agent_executor import _extract_tool_call, _write_allowed
        c = self.control
        c.check(force=True)
        call_id, name, arguments = _extract_tool_call(call)
        if name not in c.allowed:
            raise AppError(403, 'delegated_tool_denied', 'Tool is outside the delegated scope')
        if not _write_allowed(tuple(c.payload.get('write_set') or [f'work/subagents/{c.task.id}']), name, arguments):
            raise AppError(403, 'delegated_write_denied', 'Path is outside the delegated write scope')
        signature = json.dumps({'name': name, 'arguments': arguments}, sort_keys=True, ensure_ascii=False)
        previous = c.state['completed_operations'].get(call_id)
        if previous:
            if previous['signature'] != signature:
                raise AppError(409, 'agent_tool_id_conflict', 'Tool call identity changed')
            c.state['messages'].append(asdict(ProviderChatMessage(role='tool', tool_call_id=call_id, content=previous['result'])))
            c.persist()
            return previous['result'], previous.get('meta', {}), previous.get('sources', [])
        if c.state['tool_calls_total'] >= int(c.budget.get('max_tool_calls') or 200):
            c.halted = 'PARTIAL'
            raise SessionStopped('Tool budget exhausted')
        c.state['inflight_call_id'] = call_id
        c.persist()
        c.emit('tool_started', {'tool_name': name, 'ordinal': c.state['tool_calls_total']+1})
        result, meta, sources = self.runtime.execute(call, **context)
        # Commit the result before exposing completion to ChatService's next model turn.
        needs_approval = bool(meta.get('fetch_authorization_required') or meta.get('egress_authorization_required'))
        if not needs_approval:
            c.state['completed_operations'][call_id] = {'signature': signature, 'result': result, 'meta': meta, 'sources': sources}
        c.state['messages'].append(asdict(ProviderChatMessage(role='tool', tool_call_id=call_id, content=result)))
        c.state['inflight_call_id'] = None
        c.state['tool_calls_total'] += 1
        c.sources.extend(sources)
        c.evidence.append({'tool': name, 'status': meta.get('status', 'completed')})
        c.persist()
        c.emit('tool_call', {'tool_name': name, 'ordinal': c.state['tool_calls_total']})
        return result, meta, sources


def execute_managed_session(settings, job, task, *, emit_event, save_checkpoint):
    from app.core.database import SessionLocal
    from app.domain.schemas.chat import MessageCreateRequest
    from app.services.chat_service_factory import build_background_workspace_context, build_chat_service
    from app.services.sandbox_agent_executor import SubagentRunOutcome, build_agent_task_result, finalize_deliverables, retryable_model_error
    with SessionLocal() as db:
        job, task = db.get(SandboxJob, job.id), db.get(SandboxAgentTask, task.id)
        child = ensure_child_session(db, task, job)
        # Worker and scheduler use separate Sessions; refresh the outer row before checkpoint writes.
        emit_event('session.bound', {'agent_session_id': child.id, 'parent_session_id': child.parent_session_id})
        context = build_background_workspace_context(db, workspace_id=job.workspace_id, actor_id=job.owner_user_id)
        from app.services.authorization import AuthorizationService
        if not AuthorizationService(db, context.principal).can_access_resource(context.workspace, 'session', task.chat_session_id, 'write'):
            raise AppError(403, 'agent_parent_access_revoked', 'Delegating user no longer has access to the parent conversation')
        control = SessionControl(db, task, job, save_checkpoint, emit_event)
        from app.services.agent_execution_profiles import fallback_thinking_modes
        requested = (job.payload_json or {}).get('thinking_mode')
        for thinking in fallback_thinking_modes(requested, default=control.profile.default_thinking_mode):
            chat = build_chat_service(db, workspace_context=context, settings=settings,
                thinking_mode=thinking, agent_mode=True)
            if getattr(chat.model_provider, 'available', True): break
        chat._managed_control = control
        bind_chat_session(chat, child.id)
        # No extra acknowledgement model in a background child; the voice parent owns feedback.
        chat._build_fast_preface_provider = lambda: None
        if control.state['messages']:
            emit_event('resumed', {'agent_session_id': child.id, 'completed_tool_calls': control.state['tool_calls_total']})
        before = set(db.scalars(select(Message.id).where(Message.session_id == child.id)))
        error = None
        try:
            prompt = control.prompt()
            for delivery_attempt in range(2):
                for frame in chat.create_stream(child.id, MessageCreateRequest(content=prompt, agent_mode=True)):
                    # Process details remain ordinary persisted message parts/SSE.
                    for line in frame.splitlines():
                        if line.startswith('data:'):
                            try: event = json.loads(line[5:])
                            except ValueError: continue
                            part = (event.get('payload') or {}).get('part') or event.get('part') or {}
                            if part.get('type') == 'ui' and part.get('data'):
                                scene = part['data']
                                if scene.get('block_id'):
                                    control.scenes[scene['block_id']] = scene
                                    control.persist()
                                    emit_event('artifact.upsert', {'snapshot': scene, 'requirement_version': control.version})
                capability = control.spec.get('capability_id') or (control.spec.get('input_snapshot') or {}).get('capability_id')
                latest = db.scalar(select(Message).where(Message.session_id == child.id,
                    Message.role == 'assistant').order_by(Message.created_at.desc()).limit(1))
                has_artifact = any(scene.get('state') == 'completed' for scene in control.scenes.values()) or any(
                    part.get('type') in {'component', 'sandbox_artifact', 'subapp_artifact'} and part.get('status') == 'completed'
                    for row in db.scalars(select(Message).where(Message.session_id == child.id)) for part in row.parts or [])
                if (delivery_attempt or capability != 'artifact_create' or has_artifact or control.halted
                        or not latest or latest.status != 'completed'
                        or any(part.get('status') == 'pending' for part in latest.parts or [])):
                    break
                # The manager supplies observable acceptance feedback through another
                # ordinary chat turn. A promise to create a page isn't a deliverable.
                prompt = ('验收未通过：当前只有文字说明，尚无可查看的真实产物。请现在使用已授权工具完成原任务，'
                    '保留已完成操作；已有页面用 patch/complete 继续，不要重复创建。若确实受阻，请明确说明原因。')
                control.state['messages'].append(asdict(ProviderChatMessage(role='user', content=prompt)))
                control.persist()
                emit_event('acceptance.retry', {'reason': 'missing_artifact', 'agent_session_id': child.id})
        except Exception as exc:
            error = exc
        rows = list(db.scalars(select(Message).where(Message.session_id == child.id, Message.role == 'assistant').order_by(Message.created_at)))
        message = next((row for row in reversed(rows) if row.id not in before), None)
        final_text = message.content if message else ''
        # Read the ordinary committed parts even if the stream ended between a write and event delivery.
        for stored_message in rows:
            # A human takeover uses ordinary messages too. Their committed UI
            # can be newer than the worker's last checkpoint, even if the
            # resumed model only writes a final answer.
            for part in stored_message.parts or []:
                if part.get('type') == 'ui' and (part.get('data') or {}).get('block_id'):
                    scene = part['data']
                    control.scenes[scene['block_id']] = scene
        from app.domain.schemas.stream_ui import Snapshot
        verified_scenes = {}
        for key, raw in control.scenes.items():
            try:
                scene = Snapshot.model_validate(raw)
                collection = {'table':'rows', 'chart':'rows', 'graph':'nodes', 'metric':'items', 'timeline':'items', 'document':'blocks'}[scene.component]
                if scene.state == 'completed' and getattr(scene, collection):
                    verified_scenes[key] = scene.model_dump(mode='json')
            except (ValueError, KeyError, TypeError):
                pass
        if not control.halted and message and message.status == 'completed':
            control.scenes = verified_scenes
        published_parts = [part for row in rows for part in (row.parts or [])
            if part.get('type') in {'component', 'sandbox_artifact', 'subapp_artifact'} and part.get('status') == 'completed']
        def file_exists(path):
            from app.domain.schemas.sandbox import SandboxAgentFileListRequest
            sandbox = getattr(chat.agent_tool_runtime, 'sandbox', None)
            if sandbox is None: return False
            listing = sandbox.list_files(SandboxAgentFileListRequest(chat_session_id=child.id, pattern=path))
            return any(item.get('path') == path for item in listing.get('files', []))
        control.persist()
        status, code, detail = 'SUCCEEDED', None, None
        deliverables, complete = finalize_deliverables(final_text, default_output_root=f'work/subagents/{task.id}/outputs',
            file_exists=file_exists,
            require_artifacts=control.profile.role.value not in {'reason', 'research'} and not control.scenes and not published_parts)
        if not deliverables.get('handoff_parse') and final_text.strip():
            # Ordinary Agents answer naturally. Completion is derived from persisted
            # artifacts/results, rather than requiring a second private JSON protocol.
            capability = (control.spec.get('input_snapshot') or {}).get('capability_id')
            observable = bool(control.scenes or published_parts)
            if control.profile.role.value == 'reason': observable = True
            elif control.profile.role.value == 'research': observable = bool(control.sources)
            elif capability != 'artifact_create':
                observable = observable or any(item.get('status') == 'completed' for item in control.evidence)
            if observable:
                complete = True
                deliverables = {'handoff_source':'ordinary_message', 'summary':final_text[:4000], 'artifacts':[],
                    'acceptance':[{'criterion':'普通 Agent 消息已完成，所需产物或工具结果已持久化','status':'passed'}]}
        failed_stream = not message or message.status != 'completed'
        if control.halted:
            status = control.halted
            if status not in {'PAUSED', 'CANCELLED'}:
                code, detail = 'ExecutionBudgetOrBoundary', str(error or control.last_error or status)[:500]
        elif message and any(part.get('type') in {'fetch_authorization','egress_authorization','user_confirmation'}
                and part.get('status') == 'pending' for part in message.parts or []):
            status, code, detail = 'PAUSED', 'WaitingApproval', 'Approve the ordinary authorization card in the child conversation'
        elif failed_stream:
            exc = control.last_error or error
            status = 'RETRYING' if exc and retryable_model_error(exc) else 'FAILED'
            code, detail = type(exc).__name__ if exc else 'AgentMessageFailed', str(exc or 'Ordinary Agent message did not complete')[:500]
        elif not complete:
            status, code, detail = 'PARTIAL', 'HandoffIncomplete', 'No verified deliverable or structured acceptance'
        # A successful ordinary UI part is a real deliverable. Do not require a fictitious file.
        deliverables = dict(deliverables or {})
        deliverables['visuals'] = list(control.scenes.values())
        deliverables['agent_session_id'] = child.id
        deliverables['published_parts'] = published_parts
        if status in {'PAUSED', 'RETRYING', 'PARTIAL'}: deliverables['_checkpoint'] = copy.deepcopy(control.state)
        deliverables['agent_result'] = build_agent_task_result(task_id=task.id, profile=control.profile,
            status='partial' if status in {'PAUSED', 'RETRYING'} else status.lower(), result_text=final_text,
            deliverables=deliverables, sources=control.sources, evidence=control.evidence,
            requirement_version=control.version, thinking_mode_requested=(job.payload_json or {}).get('thinking_mode'),
            thinking_mode_effective=getattr(chat.model_provider, 'thinking_mode', None), reasoning_effort_effective=None,
            error_class=code, error_message=detail)
        emit_event('task.paused' if status == 'PAUSED' else 'succeeded' if status == 'SUCCEEDED' else 'partial' if status == 'PARTIAL' else 'execution.stopped',
                   {'agent_session_id': child.id, 'status': status.lower()})
        return SubagentRunOutcome(status=status, event_type=status.lower(), summary=deliverables.get('summary') or final_text,
            deliverables=deliverables, error_class=code, error_message=detail,
            attempt_record={'attempt': job.attempt, 'status': status, 'rounds': control.state['rounds_used'],
                'tool_calls': control.state['tool_calls_total'], 'token_usage': control.state['used_tokens'],
                'cost_amounts': control.state['used_costs'], 'finished_at': time.time()})


def control_child_session(db, settings, task, operation):
    from app.services.sandbox_scheduler import append_agent_event, notify_job_change
    job = db.get(SandboxJob, task.latest_job_id)
    if not job or job.workspace_id != task.workspace_id or job.owner_user_id != task.owner_user_id:
        raise AppError(409, 'agent_job_missing', 'No active delegated job')
    child = ensure_child_session(db, task, job)
    spec = dict(task.spec_json or {})
    binding = dict(child.context_capsule['managed_agent'])
    if operation in {'pause', 'takeover'}:
        if task.status not in {'QUEUED', 'STARTING', 'RUNNING', 'PAUSED', 'PARTIAL', 'FAILED', 'SUCCEEDED'}:
            raise AppError(409, 'agent_not_controllable', 'Task is not available for control')
        spec['pause_requested'] = True
        spec.setdefault('paused_at', utc_now().isoformat())
        if operation == 'takeover': spec['takeover_requested'] = True
        frozen = db.execute(update(SandboxJob).where(SandboxJob.id == job.id, SandboxJob.status == 'QUEUED').values(status='PAUSED')).rowcount
        if frozen or task.status in {'PAUSED', 'PARTIAL', 'FAILED', 'SUCCEEDED'}:
            task.status = 'PAUSED'
            job.status = 'PAUSED'
            if operation == 'takeover': binding['controller'] = 'user'
        else:
            task.status_reason = 'takeover_requested' if operation == 'takeover' else 'pause_requested'
    elif operation == 'resume':
        if job.status != 'PAUSED' or task.status != 'PAUSED':
            raise AppError(409, 'agent_not_paused', 'Wait for the safe pause checkpoint')
        if db.scalar(select(Message.id).where(Message.session_id == child.id,
                Message.role == 'assistant', Message.status.in_(['pending', 'streaming'])).limit(1)):
            raise AppError(409, 'agent_takeover_turn_running', 'Wait for the current user turn before handing control back')
        binding['controller'] = 'agent'
        spec.pop('pause_requested', None)
        spec.pop('takeover_requested', None)
        spec.pop('voice_pause_requested', None)
        paused = spec.pop('paused_at', None)
        if paused and job.deadline_at:
            from datetime import datetime
            job.deadline_at += utc_now()-datetime.fromisoformat(paused)
        job.status, task.status = 'QUEUED', 'QUEUED'
        job.payload_json = {**job.payload_json, 'resume_generation': utc_now().isoformat()}
        job.available_at, job.finished_at, task.finished_at = utc_now(), None, None
        task.status_reason = None
        job.attempt = max(0, job.attempt-1)
    else:
        raise AppError(422, 'agent_control_invalid', 'Unsupported control operation')
    task.spec_json = spec
    child.context_capsule = {**child.context_capsule, 'managed_agent': binding}
    append_agent_event(db, task, 'session.'+operation, {'agent_session_id': child.id, 'status': task.status.lower()})
    db.commit()
    notify_job_change()
    return {'task_id': task.task_id, 'agent_session_id': child.id, 'status': task.status.lower(),
            'controller': binding['controller']}
