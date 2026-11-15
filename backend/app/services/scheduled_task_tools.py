"""AI owns the same persisted task definitions the user can edit in the UI."""
from __future__ import annotations
from pydantic import ValidationError
from app.core.errors import AppError
from app.domain.schemas.scheduled_tasks import ScheduledTaskCreate,ScheduledTaskPatch,ScheduledTaskView,ScheduledTaskRunView
from app.services.scheduled_tasks import ScheduledTaskService,execute_run


def tool_schema(model):
    schema=model.model_json_schema()
    definitions=schema.pop('$defs',{})
    def inline(value):
        if isinstance(value,list):return [inline(item) for item in value]
        if not isinstance(value,dict):return value
        if '$ref' in value:
            key=value['$ref'].removeprefix('#/$defs/')
            return inline(definitions[key])
        return {key:inline(item) for key,item in value.items()}
    return inline(schema)


def tool_definition():
    return {'type':'function','function':{
        'name':'lg_scheduled_tasks',
        'description':(
            'Create, read, list, edit, pause/resume, delete and inspect your own persistent automations. '
            'Runs even when the user closes the page, as long as the LearnGraph backend is running. '
            'Use task_type=prompt with prompt instructions for future AI work; skills/MCP are also supported. '
            'trigger_type=time supports schedule_mode=once (delay_seconds or offset-aware run_at), interval '
            '(interval_seconds), daily (time_of_day), weekly (weekdays Monday=0) or five-field cron. '
            'remaining_executions=X runs automatically at most X times; null means unlimited, 0 disables. '
            'Use trigger_type=event/condition with an exact audited event_type and event_filters; action=catalog '
            'lists available events. Custom events use event.<name>; no arbitrary code/eval. Webhooks require '
            'a token configured by the user. Always state name, next trigger and remaining count after saving. '
            'Use read before updating unknown tasks; never claim a task exists without a successful tool result. '
            'Actions list/read/create/update/pause/resume/delete/runs/run/cancel/catalog. Failed runs are not automatically retried. '
            'Changing the schedule never grants extension permissions or bypasses workspace access.'),
        'parameters':{'type':'object','properties':{
            'action':{'type':'string','enum':['list','read','create','update','pause','resume','delete','runs','run','cancel','catalog']},
            'task_id':{'type':'string','maxLength':36},
            'task':tool_schema(ScheduledTaskCreate),
            'patch':tool_schema(ScheduledTaskPatch),
        },'required':['action'],'additionalProperties':False}}}


def execute(runtime,arguments,*,chat_session_id=None):
    manager=runtime.extensions
    workspace,principal,_=manager._runtime_context()
    service=ScheduledTaskService(manager.db,workspace,principal,manager.settings)
    action,task_id=arguments.get('action'),arguments.get('task_id')
    view=lambda item:ScheduledTaskView.model_validate(item).model_dump(mode='json')
    try:
        if action=='list':result={'tasks':[view(task) for task in service.list()]}
        elif action=='catalog':
            from sqlalchemy import select
            from app.domain.models import AuditEvent
            service.require_manage()
            result={'event_types':list(manager.db.scalars(select(AuditEvent.action).where(AuditEvent.workspace_id==workspace.id,
                        ~AuditEvent.action.like('scheduled_task.%')).distinct().limit(150))),
                    'custom_events':'Publish authenticated events; subscriptions use event.<name>',
                    'modes':['once','interval','daily','weekly','cron'],'timezone':'Use an explicit IANA timezone',
                    'condition_paths':['resource_id','resource_type','outcome','actor_id','payload.<key>']}
        elif action=='create':
            data=dict(arguments.get('task') or {})
            if data.get('task_type','prompt')=='prompt' and chat_session_id:
                data.setdefault('target_session_id',chat_session_id)
            task=service.create(ScheduledTaskCreate.model_validate(data),created_via='ai')
            result=view(task)
        elif action in {'read','update','pause','resume','delete','runs','run','cancel'}:
            if not isinstance(task_id,str) or not task_id:raise AppError(422,'scheduled_task_id_required','task_id is required')
            if action=='read':result=view(service.require(task_id))
            elif action in {'update','pause','resume'}:
                data={'enabled':action=='resume'} if action in {'pause','resume'} else arguments.get('patch')
                result=view(service.update(task_id,ScheduledTaskPatch.model_validate(data)))
            elif action=='delete':
                service.delete(task_id);result={'deleted':True,'task_id':task_id}
            elif action=='cancel':result=ScheduledTaskRunView.model_validate(service.cancel(task_id)).model_dump(mode='json')
            elif action=='runs':result={'runs':[ScheduledTaskRunView.model_validate(run).model_dump(mode='json') for run in service.runs(task_id)]}
            else:
                from sqlalchemy.orm import sessionmaker
                factory=sessionmaker(bind=manager.db.get_bind(),class_=type(manager.db),expire_on_commit=False)
                run=service.claim(task_id)
                execute_run(task_id,run.id,manager.settings,factory)
                manager.db.expire(run)
                result=ScheduledTaskRunView.model_validate(run).model_dump(mode='json')
        else:raise AppError(422,'scheduled_task_action_invalid','Unsupported schedule action')
    except (ValidationError,TypeError,ValueError) as exc:
        raise AppError(422,'scheduled_task_invalid','Invalid task, schedule, prompt or extension parameters') from exc
    return runtime._success(result,{'tool':'lg_scheduled_tasks','scheduled_task':{
        'id':result.get('id'),'name':result.get('name'),'next_run_at':result.get('next_run_at'),
        'summary':result.get('schedule_summary'),'action':action}},[])
