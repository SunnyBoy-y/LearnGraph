"""Durable, workspace-scoped cron for reviewed Skill/MCP operations.

At-most-once dispatch per scheduled slot. Missed slots coalesce into one run;
interrupted side effects are recorded, never automatically replayed.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from croniter import croniter
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.extension_models import ScheduledExtensionTask as Task, ScheduledExtensionRun as Run
from app.domain.models import SandboxAgentCommand, SandboxJob, User, Workspace, AuditEvent, utc_now
from app.services.schedule_rules import config, next_occurrence, normalize_create, matches_event
from app.domain.schemas.extensions import MCPInvokeRequest, SkillInvokeRequest, SkillSandboxRunRequest
from app.domain.schemas.scheduled_tasks import ScheduledTaskCreate, ScheduledTaskPatch
from app.repositories.audit import AuditRepository
from app.services.authorization import AuthorizationService
from app.services.mcp_skills import MCPAndSkillService, _contains_sensitive_key
from app.services.skill_script_runtime import require_authorized_skill
from app.services.skill_sandbox_run import SkillSandboxRunService

logger = logging.getLogger(__name__)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def next_run(expression: str, zone: str, after: datetime) -> datetime:
    try:
        return croniter(expression, aware(after).astimezone(ZoneInfo(zone)), max_years_between_matches=5).get_next(datetime).astimezone(timezone.utc)
    except (ValueError, KeyError) as exc:
        raise AppError(422, "schedule_no_next_run", "Cron has no matching time within five years") from exc


class ScheduledTaskService:
    def __init__(self, db, workspace, principal, settings):
        self.db, self.workspace, self.principal, self.settings = db, workspace, principal, settings
        self.workspace_id = workspace.id
        self.audit = AuditRepository(db, workspace.id)

    def require_manage(self):
        if "workspace.manage" not in AuthorizationService(self.db, self.principal).workspace_permissions(self.workspace):
            raise AppError(403, "permission_denied", "Scheduled tasks require workspace.manage")

    def query(self):
        return select(Task).execution_options(populate_existing=True).where(Task.workspace_id == self.workspace_id)

    def list(self):
        self.require_manage()
        return list(self.db.scalars(self.query().order_by(Task.created_at.desc()).limit(500)))

    def require(self, task_id):
        self.require_manage()
        task = self.db.scalar(self.query().where(Task.id == task_id))
        if task is None:
            raise AppError(404, "scheduled_task_not_found", "Scheduled task was not found")
        return task

    def manager(self):
        return MCPAndSkillService(self.db, self.workspace_id, self.principal.user_id, self.settings,
                                  workspace=self.workspace, principal=self.principal)

    def validate_target(self, payload: ScheduledTaskCreate, *, require_grant=True):
        manager = self.manager()
        if _contains_sensitive_key(payload.payload_json):
            raise AppError(422, "schedule_secret_forbidden", "Use encrypted extension credentials, never schedule payloads")
        if payload.task_type == "prompt":
            if payload.target_session_id and not AuthorizationService(self.db, self.principal).can_access_resource(self.workspace, "session", payload.target_session_id, "write"):
                raise AppError(404, "session_not_found", "Target conversation was not found")
            return
        if payload.task_type == "skill":
            skill = manager.require_skill(payload.skill_id)
            if require_grant:
                require_authorized_skill(manager, skill.id)
            if manager._is_agent_skill_package(skill):
                request = SkillSandboxRunRequest.model_validate(payload.payload_json)
                if request.chat_session_id and not AuthorizationService(self.db, self.principal).can_access_resource(self.workspace, "session", request.chat_session_id, "write"):
                    raise AppError(404, "session_not_found", "Chat session was not found")
                SkillSandboxRunService(self.db, self.workspace_id, self.principal.user_id, self.settings)._script_bytes(skill, request.script_path)
        else:
            server = manager.require_server(payload.mcp_server_id)
            if payload.tool_name not in server.requested_tools:
                raise AppError(403, "mcp_tool_not_requested", "Tool is not in the reviewed MCP manifest")
            if require_grant:
                snapshot = manager._current_snapshot(server)
                grant = manager._usable_grant("mcp_server", server.id, manager._server_authorization_hash(server, snapshot)) if snapshot else None
                if not server.enabled or grant is None or grant.decision != "always":
                    raise AppError(403, "extension_authorization_required", "Scheduled MCP tasks require durable authorization")

    def create(self, payload, *, created_via="user"):
        self.require_manage()
        from sqlalchemy import func
        if self.db.scalar(select(func.count()).select_from(Task).where(Task.workspace_id==self.workspace_id)) >= 200:
            raise AppError(429,"scheduled_task_limit","Workspace task limit reached (200); remove unused tasks first")
        now = utc_now()
        payload = normalize_create(payload, now)
        self.validate_target(payload, require_grant=payload.enabled)
        task = Task(workspace_id=self.workspace_id, created_by=self.principal.user_id, created_via=created_via,
                    **payload.model_dump(), next_run_at=next_occurrence(payload,now,initial=True),event_cursor_at=now)
        self.db.add(task)
        self.db.flush()
        self.record("created", task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def update(self, task_id, patch: ScheduledTaskPatch):
        task = self.require(task_id)
        toggle_only = set(patch.model_fields_set) == {"enabled"}
        if task.lease_token and not toggle_only:
            raise AppError(409, "scheduled_task_running", "Wait for the active run before changing its definition")
        changed_fields = patch.model_dump(exclude_unset=True)
        base = config(task)
        if changed_fields.get('delay_seconds') is not None:
            base['run_at'] = None
        if 'run_at' in changed_fields:
            base['delay_seconds'] = None
        data = ScheduledTaskCreate.model_validate({**base, **changed_fields})
        exhausted = data.remaining_executions == 0
        if data.enabled and exhausted and patch.enabled is True:
            raise AppError(409,'scheduled_task_exhausted','Set a positive remaining_executions or null before re-enabling an exhausted task')
        if data.schedule_mode=='once' and data.run_at is not None and not changed_fields.get('delay_seconds'):
            data.run_at = aware(data.run_at)
        now = utc_now()
        if data.enabled and data.trigger_type=='time' and data.schedule_mode=='once':
            data = normalize_create(data, now)
        elif exhausted:
            data.enabled = False
        # The updater becomes the accountable execution owner; never silently
        # execute an edited task with a different user's privileges.
        if not (toggle_only and not data.enabled):
            self.validate_target(data, require_grant=data.enabled)
        values = {**data.model_dump(), "created_by": task.created_by if toggle_only else self.principal.user_id, "revision": task.revision + 1,
                  "next_run_at": next_occurrence(data, now, initial=True)}
        trigger_fields={'event_type','event_filters','trigger_type'}
        if trigger_fields.intersection(changed_fields) or (patch.enabled is True and not task.enabled):
            values.update(event_cursor_at=now,event_cursor_id=None)
        conditions = [Task.id == task.id, Task.workspace_id == self.workspace_id, Task.revision == task.revision]
        if not toggle_only:
            conditions.append(Task.lease_token.is_(None))
        changed = self.db.execute(update(Task).where(*conditions).values(**values).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            self.db.rollback()
            raise AppError(409, "scheduled_task_changed", "Task changed or started running; reload before editing")
        self.db.refresh(task)
        self.record("updated", task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def delete(self, task_id):
        task = self.require(task_id)
        if task.lease_token:
            raise AppError(409, "scheduled_task_running", "Cannot delete an executing task")
        self.record("deleted", task)
        removed = self.db.execute(delete(Task).where(Task.id == task.id, Task.workspace_id == self.workspace_id,
            Task.revision == task.revision, Task.lease_token.is_(None)).execution_options(synchronize_session=False))
        if removed.rowcount != 1:
            self.db.rollback()
            raise AppError(409, "scheduled_task_changed", "Task changed or started running; reload before deleting")
        self.db.execute(delete(Run).where(Run.task_id == task.id, Run.workspace_id == self.workspace_id))
        self.db.expunge(task)
        self.db.commit()

    def cancel(self, task_id):
        task = self.require(task_id)
        run = self.db.scalar(select(Run).where(Run.task_id == task.id, Run.workspace_id == self.workspace_id,
                             Run.status == "running").order_by(Run.started_at.desc()))
        if run is None:
            raise AppError(409, "scheduled_task_not_running", "Task has no active run")
        if task.task_type == 'prompt':
            run.cancel_requested = True
            self.record('cancel_requested',task)
            self.db.commit()
            return run
        job_id = (run.result_json or {}).get("job_id")
        if not job_id:
            raise AppError(409, "scheduled_cancel_unavailable", "This synchronous invocation cannot be safely cancelled; its execution timeout still applies")
        from app.services.sandbox_scheduler import SandboxSchedulerService
        scheduler = SandboxSchedulerService(self.db, self.settings)
        job = scheduler.get_job(job_id, workspace_id=self.workspace_id, owner_user_id=task.created_by)
        scheduler.cancel_job(job)
        run.result_json = {**run.result_json, "cancel_requested": True}
        self.record("cancel_requested", task)
        self.db.commit()
        reconcile_runs(self.db, settings=self.settings)
        self.db.refresh(run)
        return run

    def runs(self, task_id, limit=100):
        self.require(task_id)
        return list(self.db.scalars(select(Run).execution_options(populate_existing=True).where(Run.workspace_id == self.workspace_id, Run.task_id == task_id)
                                    .order_by(Run.started_at.desc()).limit(limit)))

    def record(self, action, task):
        self.audit.record(actor_id=self.principal.user_id, action=f"scheduled_task.{action}",
                          resource_type="scheduled_task", resource_id=task.id,
                          details={"task_type": task.task_type, "cron": task.cron_expression, "timezone": task.timezone})

    def claim(self, task_id, *, trigger="manual", now=None, event=None):
        task = self.require(task_id)
        now = aware(now or utc_now())
        automatic = trigger != 'manual'
        if automatic and (not task.enabled or task.remaining_executions == 0):return None
        if trigger == "scheduled" and (task.trigger_type != 'time' or task.next_run_at is None or aware(task.next_run_at) > now):
            return None
        if trigger in {'event','webhook','condition'}:
            if task.trigger_type != trigger or event is None or (task.last_run_at and aware(task.last_run_at)+timedelta(seconds=task.cooldown_seconds)>now):return None
        if task.lease_token:
            raise AppError(409, "scheduled_task_running", "Task already has an active run")
        slot = aware(task.next_run_at) if trigger == "scheduled" else now
        token = str(uuid4())
        values = {"lease_token": token, "lease_until": now + timedelta(seconds=task.timeout_seconds + 180),
                  "last_run_at": now, "last_status": "running", "revision": task.revision + 1}
        if automatic:
            remaining = None if task.remaining_executions is None else max(0,task.remaining_executions-1)
            terminal = remaining == 0 or (task.trigger_type=='time' and task.schedule_mode=='once')
            values.update(remaining_executions=remaining, execution_count=task.execution_count+1,
                          enabled=False if terminal else task.enabled)
            values['next_run_at'] = None if terminal else next_occurrence(task,now)
            if event is not None:
                values.update(event_cursor_at=event.created_at,event_cursor_id=event.id)
        claimed = self.db.execute(update(Task).where(Task.id == task.id, Task.workspace_id == self.workspace_id,
            Task.revision == task.revision, Task.lease_token.is_(None)).values(**values).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            self.db.rollback()
            raise AppError(409, "scheduled_task_running", "Task was claimed by another worker")
        run = Run(workspace_id=self.workspace_id, task_id=task.id, trigger=trigger,
                  dedup_key=('event:'+event.id) if event is not None else slot.isoformat() if trigger == "scheduled" else token,
                  trigger_payload=event_data(event) if event is not None else {},
                  status="running", scheduled_for=slot, started_at=now)
        self.db.add(run)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise AppError(409, "scheduled_slot_already_claimed", "This schedule slot was already dispatched")
        self.db.refresh(run)
        return run


def execution_context(db, task, settings):
    workspace = db.get(Workspace, task.workspace_id)
    user = db.get(User, task.created_by)
    if workspace is None or user is None or user.status != "active" or user.must_change_password or workspace.tenant_id != user.tenant_id:
        raise AppError(403, "schedule_owner_unavailable", "Task owner or workspace is unavailable")
    principal = Principal(user_id=user.id, username=user.username, tenant_id=user.tenant_id, session_id="scheduled-task")
    service = ScheduledTaskService(db, workspace, principal, settings)
    service.require_manage()  # membership/role revocations take effect at every run
    from app.domain.migration_models import MaintenanceLock
    if db.scalar(select(MaintenanceLock.id).where(MaintenanceLock.workspace_id == task.workspace_id, MaintenanceLock.status == "active")):
        raise AppError(503, "workspace_maintenance", "Scheduled execution is paused during workspace migration")
    return service


def execute_run(task_id, run_id, settings, session_factory):
    with session_factory() as db:
        task, run = db.get(Task, task_id), db.get(Run, run_id)
        if task is None or run is None or run.status != "running":
            return
        token = task.lease_token
        claimed = db.execute(update(Run).where(Run.id == run.id, Run.workspace_id == task.workspace_id,
            Run.status == "running", Run.dispatched_at.is_(None)).values(dispatched_at=utc_now()).execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            db.rollback()
            return  # A duplicate background callback must never replay side effects.
        db.commit()
        try:
            service = execution_context(db, task, settings)
            payload = ScheduledTaskCreate.model_validate(config(task))
            service.validate_target(payload)
            if run.cancel_requested:
                raise AppError(409,'scheduled_run_cancelled','Run cancelled before execution')
            if task.task_type == 'prompt':
                from app.services.scheduled_prompt import execute_prompt
                result = execute_prompt(service,task,run,session_factory)
            elif task.task_type == "skill":
                manager = service.manager()
                skill = manager.require_skill(task.skill_id)
                if manager._is_agent_skill_package(skill):
                    result = SkillSandboxRunService(db, task.workspace_id, task.created_by, settings,
                        workspace=service.workspace, principal=service.principal).run(skill.id,
                        SkillSandboxRunRequest.model_validate({**task.payload_json, "timeout_seconds": task.timeout_seconds, "yield_time_ms": 0}), require_authorization=True).model_dump(mode="json")
                else:
                    result = manager._invocation_data(manager.invoke_skill(skill.id, SkillInvokeRequest(input=task.payload_json)))
            else:
                manager = service.manager()
                import time
                manager.execution_timeout_ms = task.timeout_seconds * 1000
                manager.execution_deadline = time.monotonic() + task.timeout_seconds
                result = manager._invocation_data(manager.invoke_mcp(task.mcp_server_id,
                    MCPInvokeRequest(tool_name=task.tool_name, arguments=task.payload_json)))
            run.result_json = result
            status = result.get("status")
            run.status = "running" if status in {"pending", "running", "queued"} else "succeeded" if status == "succeeded" else "failed"
            if run.status == "failed":
                run.error_code = result.get("error_code") or "scheduled_execution_failed"
                run.error_message = result.get("error_message") or "The extension did not succeed"
        except AppError as exc:
            run.status, run.error_code, run.error_message = "failed", exc.code, exc.message[:1000]
        except Exception:
            logger.exception("Scheduled extension execution failed: %s", run_id)
            db.rollback()
            run = db.get(Run, run_id)
            if run is None:
                return
            run.status, run.error_code, run.error_message = "failed", "scheduled_execution_failed", "Execution failed; inspect backend logs"
        if run.status != "running":
            run.finished_at = utc_now()
            db.execute(update(Task).where(Task.id == task_id, Task.lease_token == token).values(
                lease_token=None, lease_until=None, last_status=run.status))
        db.commit()
        if run.status != 'running':
            record_run_outcome(db,task,run)
            db.commit()


def reconcile_runs(db, now=None, settings=None):
    from app.core.config import get_settings
    settings = settings or get_settings()
    now = aware(now or utc_now())
    for run, task in db.execute(select(Run, Task).join(Task, Task.id == Run.task_id).where(Run.status == "running")):
        job_id = (run.result_json or {}).get("job_id")
        job = db.get(SandboxJob, job_id) if job_id else None
        if job is not None and job.workspace_id != task.workspace_id:
            job = None
        if job is not None:
            invocation_id = run.result_json.get("invocation_id")
            if invocation_id:
                invocation = SkillSandboxRunService(db, task.workspace_id, task.created_by, settings).refresh_invocation(invocation_id)
                run.result_json = {**run.result_json, **invocation.result_json, "status": invocation.status}
        command_id = (run.result_json or {}).get("command_id")
        command = db.get(SandboxAgentCommand, command_id) if command_id else None
        if job is not None and job.status in {"FAILED", "CANCELLED", "EXPIRED"}:
            run.status, run.error_code, run.error_message = "failed", job.error_class or "sandbox_job_failed", job.error_message
        elif command is not None and command.workspace_id == task.workspace_id and command.status not in {"queued", "pending", "running"}:
            run.status = "succeeded" if command.status in {"completed", "succeeded"} and command.exit_code == 0 else "failed"
            run.error_code, run.error_message = command.error_class, command.error_message
            run.result_json = {**run.result_json, "status": run.status, "exit_code": command.exit_code,
                               "stdout_summary": command.stdout_summary, "stderr_summary": command.stderr_summary}
        elif task.lease_until and aware(task.lease_until) <= now:
            if job is not None and job.status in {"QUEUED", "STARTING", "RUNNING", "PAUSED"}:
                from app.services.sandbox_scheduler import SandboxSchedulerService
                SandboxSchedulerService(db, settings).cancel_job(job)
                # Do not release the lease until termination is actually observed.
                continue
            run.status, run.error_code = "failed", "scheduled_run_interrupted"
            run.error_message = "Run exceeded its lease or the worker restarted; side effects are not automatically replayed"
        else:
            continue
        run.finished_at = now
        task.lease_token, task.lease_until, task.last_status = None, None, run.status
        record_run_outcome(db,task,run)
    db.commit()


def claim_due(settings, session_factory, limit=4):
    claimed = []
    with session_factory() as db:
        reconcile_runs(db, settings=settings)
        ids = list(db.scalars(select(Task.id).where(Task.enabled.is_(True), Task.next_run_at <= utc_now(),
                              Task.trigger_type=='time',Task.lease_token.is_(None)).order_by(Task.next_run_at).limit(limit)))
        for task_id in ids:
            task = db.get(Task, task_id)
            try:
                service = execution_context(db, task, settings)
                run = service.claim(task_id, trigger="scheduled")
                if run:
                    claimed.append((task_id, run.id))
            except AppError as exc:
                db.rollback()
                # A revoked execution owner disables the schedule rather than
                # creating an unbounded stream of unauthorized attempts.
                if exc.code in {"permission_denied", "schedule_owner_unavailable"}:
                    db.execute(update(Task).where(Task.id == task_id).values(enabled=False, next_run_at=None, last_status="denied"))
                    db.commit()
        if len(claimed)<limit:
            claimed.extend(claim_event_tasks(db,settings,limit-len(claimed)))
    return claimed


def record_run_outcome(db,task,run):
    import hashlib
    identifier=hashlib.sha256(('automation-outcome:'+run.id).encode()).hexdigest()[:36]
    if db.get(AuditEvent,identifier) is not None:return
    event=AuditEvent(id=identifier,workspace_id=task.workspace_id,actor_id=task.created_by,
        action='automation.run.'+run.status,resource_type='scheduled_task',resource_id=task.id,
        outcome=run.status,details={'run_id':run.id,'task_id':task.id,'status':run.status})
    try:
        with db.begin_nested():
            db.add(event);db.flush()
    except IntegrityError:
        pass  # Other worker already recorded this exact terminal run.


def event_data(event):
    import json
    from app.services.mcp_skills import _redact_sensitive_keys
    payload=_redact_sensitive_keys(event.details or {})
    if len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))>16384:
        payload={'truncated':True,'notice':'Event metadata exceeded the automation input budget'}
    return {'id':event.id,'event_type':event.action,'actor_id':event.actor_id,
            'resource_type':event.resource_type,'resource_id':event.resource_id,'outcome':event.outcome,'payload':payload}


def claim_event_tasks(db,settings,limit):
    from sqlalchemy import and_
    claimed=[]
    tasks=list(db.scalars(select(Task).where(Task.enabled.is_(True),Task.lease_token.is_(None),
        Task.trigger_type.in_(['event','condition','webhook'])).order_by(Task.last_run_at,Task.created_at).limit(200)))
    for task in tasks:
        if len(claimed)>=limit:break
        if task.last_run_at and aware(task.last_run_at)+timedelta(seconds=task.cooldown_seconds)>utc_now():continue
        cursor=task.event_cursor_at or task.created_at
        action='automation.webhook.'+task.id if task.trigger_type=='webhook' else task.event_type
        events=list(db.scalars(select(AuditEvent).where(AuditEvent.workspace_id==task.workspace_id,
            AuditEvent.action==action,or_(AuditEvent.created_at>cursor,
                and_(AuditEvent.created_at==cursor,AuditEvent.id>(task.event_cursor_id or ''))))
            .order_by(AuditEvent.created_at,AuditEvent.id).limit(100)))
        for event in events:
            # Definition writes never trigger event automations. Only explicit
            # business/custom events and run outcomes are eligible.
            if event.action.startswith('scheduled_task.') or not matches_event(task.event_filters or [],event_data(event)):
                db.execute(update(Task).where(Task.id==task.id,Task.revision==task.revision,Task.lease_token.is_(None))
                    .values(event_cursor_at=event.created_at,event_cursor_id=event.id))
                db.commit();continue
            try:
                service=execution_context(db,task,settings)
                from app.services.authorization import RESOURCE_MODELS
                resource_type={'chat_session':'session'}.get(event.resource_type,event.resource_type)
                authz=AuthorizationService(db,service.principal)
                readable=True
                if resource_type in RESOURCE_MODELS and event.resource_id:
                    readable=authz.can_access_resource(service.workspace,resource_type,event.resource_id,'read')
                elif event.resource_type in {'node','graph_node'} and event.resource_id:
                    readable=authz.can_access_bindings(service.workspace,'read',node_id=event.resource_id)
                if not readable:
                    db.execute(update(Task).where(Task.id==task.id,Task.revision==task.revision,Task.lease_token.is_(None))
                        .values(event_cursor_at=event.created_at,event_cursor_id=event.id));db.commit();continue
                run=service.claim(task.id,trigger=task.trigger_type,event=event)
                if run:claimed.append((task.id,run.id))
            except AppError:
                db.rollback()
            break
    return claimed


def publish_event(db,workspace_id,actor_id,event_type,payload,*,event_id=None,webhook_task_id=None):
    import hashlib,json
    from app.services.mcp_skills import _contains_sensitive_key
    if len(json.dumps(payload,ensure_ascii=False).encode('utf-8'))>16384 or _contains_sensitive_key(payload):
        raise AppError(422,'schedule_event_invalid','Event payload is too large or contains credentials')
    key=hashlib.sha256(f'{workspace_id}:{webhook_task_id or event_type}:{event_id}'.encode()).hexdigest() if event_id else None
    if key:
        existing=db.get(AuditEvent,key[:36])
        if existing:return existing
    event=AuditEvent(workspace_id=workspace_id,actor_id=actor_id,action=('automation.webhook.'+webhook_task_id) if webhook_task_id else 'event.'+event_type,
        resource_type='scheduled_task' if webhook_task_id else 'automation_event',resource_id=webhook_task_id or '',details=payload)
    if key:event.id=key[:36]
    db.add(event)
    try:db.commit()
    except IntegrityError:
        db.rollback()
        return db.get(AuditEvent,key[:36])
    return event


async def scheduled_task_worker(stop: asyncio.Event, settings=None, session_factory=None):
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    settings, session_factory = settings or get_settings(), session_factory or SessionLocal
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="extension-schedule")
    pending = set()
    try:
        while not stop.is_set():
            pending = {job for job in pending if not job.done()}
            try:
                slots = max(0, 4 - len(pending))
                if slots:
                    for task_id, run_id in await asyncio.to_thread(claim_due, settings, session_factory, slots):
                        pending.add(pool.submit(execute_run, task_id, run_id, settings, session_factory))
            except Exception:
                logger.exception("Scheduled task polling failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.scheduled_tasks_poll_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        await asyncio.to_thread(pool.shutdown, wait=True)
