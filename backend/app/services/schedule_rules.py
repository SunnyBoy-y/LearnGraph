"""Pure schedule/condition rules. No LLM inference, eval, or host execution."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from croniter import croniter
from app.core.errors import AppError


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def config(task):
    from app.domain.schemas.scheduled_tasks import ScheduledTaskCreate
    result = {key:getattr(task,key) for key in ScheduledTaskCreate.model_fields}
    if result.get('run_at') is not None:result['run_at']=aware(result['run_at'])
    return result


def next_occurrence(task,after,*,initial=False):
    if not task.enabled or task.remaining_executions==0 or task.trigger_type!='time': return None
    after=aware(after)
    if task.schedule_mode=='once':
        if not initial:return None
        if task.delay_seconds is not None:return after+timedelta(seconds=task.delay_seconds)
        if task.run_at is None:return None
        return aware(task.run_at)
    if task.schedule_mode=='interval':return after+timedelta(seconds=task.interval_seconds)
    expression=task.cron_expression
    if task.schedule_mode in {'daily','weekly'}:
        hour,minute=map(int,task.time_of_day.split(':'))
        weekday='*' if task.schedule_mode=='daily' else ','.join(str((day+1)%7) for day in task.weekdays)
        expression=f'{minute} {hour} * * {weekday}'
    try:
        return croniter(expression,after.astimezone(ZoneInfo(task.timezone)),max_years_between_matches=5).get_next(datetime).astimezone(timezone.utc)
    except (ValueError,KeyError) as exc:
        raise AppError(422,'schedule_no_next_run','No matching schedule time within five years') from exc


def normalize_create(payload,now):
    if payload.trigger_type=='time' and payload.schedule_mode=='once':
        if payload.delay_seconds is not None:
            payload.run_at=aware(now)+timedelta(seconds=payload.delay_seconds)
            payload.delay_seconds=None
        if payload.enabled and aware(payload.run_at)<=aware(now):
            raise AppError(422,'schedule_time_in_past','One-time run_at must be in the future')
        payload.remaining_executions=0 if payload.remaining_executions==0 else 1
    if payload.remaining_executions==0:payload.enabled=False
    return payload


def describe_schedule(task):
    suffix=f'，剩余 {task.remaining_executions:,} 次' if task.remaining_executions is not None else '，持续运行'
    if task.trigger_type in {'event','condition'}:
        return ('条件满足' if task.trigger_type=='condition' else '事件')+f'：{task.event_type}'+suffix
    if task.trigger_type=='webhook':return 'Webhook 触发'+suffix
    if task.schedule_mode=='once':return '一次性'+(f'：{aware(task.run_at).astimezone(ZoneInfo(task.timezone)).strftime("%Y-%m-%d %H:%M:%S")}' if task.run_at else '')
    if task.schedule_mode=='interval':return f'每 {task.interval_seconds:,} 秒'+suffix
    if task.schedule_mode=='daily':return f'每天 {task.time_of_day}（{task.timezone}）'+suffix
    if task.schedule_mode=='weekly':return f'每周 {"、".join("一二三四五六日"[day] for day in task.weekdays)} {task.time_of_day}（{task.timezone}）'+suffix
    return f'{task.cron_expression}（{task.timezone}）'+suffix


def matches_event(filters,event):
    def lookup(path):
        value=event
        for key in path.split('.'):
            if not isinstance(value,dict) or key not in value:return False,None
            value=value[key]
        return True,value
    for predicate in filters:
        predicate=predicate.model_dump() if hasattr(predicate,'model_dump') else predicate
        exists,value=lookup(predicate['path'])
        op,expected=predicate['op'],predicate.get('value')
        try:
            if op=='exists':matched=exists if expected is None else exists==bool(expected)
            elif not exists:matched=False
            elif op=='eq':matched=value==expected
            elif op=='ne':matched=value!=expected
            elif op=='contains':matched=isinstance(value,(str,list,dict)) and expected in value
            elif op=='in':matched=isinstance(expected,(list,str,dict)) and value in expected
            elif op=='gt':matched=value>expected
            elif op=='gte':matched=value>=expected
            elif op=='lt':matched=value<expected
            elif op=='lte':matched=value<=expected
            else:matched=False
        except (TypeError,ValueError):matched=False
        if not matched:return False
    return True
