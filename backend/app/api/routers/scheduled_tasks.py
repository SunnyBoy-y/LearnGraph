from __future__ import annotations
from fastapi import APIRouter, BackgroundTasks, Query, Response, Header, Request
from pydantic import ValidationError
from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.domain.schemas.scheduled_tasks import ScheduledTaskCreate, ScheduledTaskPatch, ScheduledTaskView, ScheduledTaskRunView, ScheduledEventPublish
from app.services.scheduled_tasks import ScheduledTaskService, execute_run
from app.domain.schemas.schedule_suggestions import ScheduleSuggestionRequest, ScheduleSuggestionResponse
from app.services.schedule_suggestions import ScheduleSuggestionService

router = APIRouter(prefix="/scheduled-tasks", tags=["scheduled-tasks"])


def service(db, context, settings):
    return ScheduledTaskService(db, context.workspace, context.principal, settings)


@router.get("", response_model=list[ScheduledTaskView])
def list_tasks(db: DB, context: CurrentWorkspace, settings: AppSettings):
    return service(db, context, settings).list()


@router.post("", response_model=ScheduledTaskView, status_code=201)
def create_task(payload: ScheduledTaskCreate, db: DB, context: CurrentWorkspace, settings: AppSettings):
    try:
        return service(db, context, settings).create(payload)
    except ValidationError as exc:
        raise AppError(422, "scheduled_task_invalid", "Invalid extension payload") from exc


@router.get('/trigger-catalog')
def trigger_catalog(db:DB,context:CurrentWorkspace,settings:AppSettings):
    service(db,context,settings).require_manage()
    from sqlalchemy import select
    from app.domain.models import AuditEvent
    actions=list(db.scalars(select(AuditEvent.action).where(AuditEvent.workspace_id==context.workspace_id,
        ~AuditEvent.action.like('scheduled_task.%')).distinct().limit(150)))
    return {'event_types':actions,'condition_paths':['resource_id','resource_type','outcome','actor_id','payload.<key>'],
            'operators':['eq','ne','contains','in','gt','gte','lt','lte','exists'],
            'modes':['once','interval','daily','weekly','cron'],'weekday_numbering':'Monday=0, Sunday=6'}


@router.post('/events',status_code=202)
def emit_event(payload:ScheduledEventPublish,
               db:DB,context:CurrentWorkspace,settings:AppSettings):
    from app.services.scheduled_tasks import publish_event
    service(db,context,settings).require_manage()
    event=publish_event(db,context.workspace_id,context.principal.user_id,payload.event_type,payload.payload,event_id=payload.event_id)
    return {'event_id':event.id,'event_type':event.action,'accepted':True}


@router.post("/suggestions", response_model=ScheduleSuggestionResponse)
def suggest_tasks(payload: ScheduleSuggestionRequest, db: DB, context: CurrentWorkspace, settings: AppSettings):
    """Generate read-only personalized drafts; never create or execute a task."""
    return ScheduleSuggestionService(db, context, settings).generate(payload)


@router.get("/{task_id}", response_model=ScheduledTaskView)
def get_task(task_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return service(db, context, settings).require(task_id)


@router.patch("/{task_id}", response_model=ScheduledTaskView)
def update_task(task_id: str, payload: ScheduledTaskPatch, db: DB, context: CurrentWorkspace, settings: AppSettings):
    try:
        return service(db, context, settings).update(task_id, payload)
    except ValidationError as exc:
        raise AppError(422, "scheduled_task_invalid", "Invalid schedule or extension payload") from exc


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    service(db, context, settings).delete(task_id)
    return Response(status_code=204)


@router.post("/{task_id}/run", response_model=ScheduledTaskRunView, status_code=202)
def run_task(task_id: str, background: BackgroundTasks, db: DB, context: CurrentWorkspace, settings: AppSettings):
    manager = service(db, context, settings)
    run = manager.claim(task_id)
    background.add_task(execute_run, task_id, run.id, settings, SessionLocal)
    return run


@router.post('/{task_id}/webhook-token')
def rotate_webhook_token(task_id:str,db:DB,context:CurrentWorkspace,settings:AppSettings):
    import secrets,hashlib
    manager=service(db,context,settings)
    task=manager.require(task_id)
    if task.trigger_type!='webhook':raise AppError(409,'schedule_not_webhook','Choose webhook trigger first')
    token=secrets.token_urlsafe(32)
    task.webhook_secret_hash=hashlib.sha256(token.encode()).hexdigest()
    manager.record('webhook_token_rotated',task)
    db.commit()
    return {'token':token,'header':'X-Automation-Token','path':f'/api/v1/scheduled-tasks/{task.id}/trigger',
            'note':'Shown once; previous tokens are immediately revoked. Do not place tokens in URLs or prompts.'}


@router.post('/{task_id}/trigger',status_code=202)
async def trigger_webhook(task_id:str,request:Request,db:DB,settings:AppSettings,
                          x_automation_token:str=Header(default='',max_length=200)):
    import hashlib,hmac,json
    from app.domain.extension_models import ScheduledExtensionTask
    from app.services.scheduled_tasks import publish_event
    task=db.get(ScheduledExtensionTask,task_id)
    supplied=hashlib.sha256(x_automation_token.encode()).hexdigest()
    if task is None or not task.webhook_secret_hash or not hmac.compare_digest(task.webhook_secret_hash,supplied):
        raise AppError(403,'schedule_webhook_forbidden','Invalid webhook credentials')
    if task.trigger_type!='webhook' or not task.enabled or task.remaining_executions==0:
        raise AppError(409,'schedule_webhook_disabled','Webhook task is paused or exhausted')
    # Read a bounded body before parsing; no unbounded authenticated upload.
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>16384:raise AppError(413,'schedule_event_too_large','Webhook body exceeds 16 KiB')
    try:
        value=json.loads(body or b'{}')
        payload=ScheduledEventPublish.model_validate(value)
    except (ValueError,ValidationError) as exc:raise AppError(422,'schedule_webhook_invalid','Use event_type, payload and optional event_id') from exc
    event=publish_event(db,task.workspace_id,task.created_by,payload.event_type,payload.payload,
        event_id=payload.event_id,webhook_task_id=task.id)
    return {'event_id':event.id,'accepted':True}


@router.post("/{task_id}/cancel", response_model=ScheduledTaskRunView)
def cancel_task(task_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings):
    return service(db, context, settings).cancel(task_id)


@router.get("/{task_id}/runs", response_model=list[ScheduledTaskRunView])
def list_runs(task_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings,
              limit: int = Query(default=100, ge=1, le=500)):
    return service(db, context, settings).runs(task_id, limit)
