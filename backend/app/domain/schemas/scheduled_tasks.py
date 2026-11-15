from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TaskType = Literal['prompt','skill','mcp']
TriggerType = Literal['time','event','condition','webhook']
ScheduleMode = Literal['once','interval','daily','weekly','cron']


class EventPredicate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1,max_length=200,pattern=r'^[A-Za-z0-9_.-]+$')
    op: Literal['eq','ne','contains','in','gt','gte','lt','lte','exists'] = 'eq'
    value: Any = None


class ScheduledTaskCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=160)
    description: str = Field(default='',max_length=4000)
    task_type: TaskType = 'prompt'
    trigger_type: TriggerType = 'time'
    schedule_mode: ScheduleMode = 'cron'
    cron_expression: str = Field(default='0 9 * * *',max_length=120)
    timezone: str = Field(default='UTC',max_length=80)
    run_at: datetime | None = None
    delay_seconds: int | None = Field(default=None,ge=1,le=31536000)
    interval_seconds: int | None = Field(default=None,ge=10,le=31536000)
    time_of_day: str = Field(default='09:00',pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    weekdays: list[int] = Field(default_factory=lambda:[0,1,2,3,4],max_length=7)
    remaining_executions: int | None = Field(default=None,ge=0,le=100000)
    event_type: str | None = Field(default=None,max_length=120,pattern=r'^[A-Za-z0-9_.:-]+$')
    event_filters: list[EventPredicate] = Field(default_factory=list,max_length=20)
    cooldown_seconds: int = Field(default=60,ge=10,le=31536000)
    enabled: bool = True
    prompt: str = Field(default='',max_length=32000)
    target_session_id: str | None = Field(default=None,max_length=36)
    session_mode: Literal['new','existing'] = 'new'
    provider_id: str | None = Field(default=None,max_length=80)
    model_id: str | None = Field(default=None,max_length=160)
    agent_mode: bool = True
    web_search: bool = False
    skill_id: str | None = Field(default=None,max_length=36)
    mcp_server_id: str | None = Field(default=None,max_length=36)
    tool_name: str | None = Field(default=None,max_length=128)
    payload_json: dict[str,Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60,ge=1,le=3600)

    @field_validator('name')
    @classmethod
    def clean_name(cls,value):
        if not value.strip(): raise ValueError('Task name must not be blank')
        return value.strip()

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls,value):
        try: ZoneInfo(value)
        except (ZoneInfoNotFoundError,ValueError) as exc: raise ValueError('Use a valid IANA timezone') from exc
        return value

    @field_validator('weekdays')
    @classmethod
    def valid_weekdays(cls,value):
        if any(type(day) is not int or day<0 or day>6 for day in value): raise ValueError('weekdays use Monday=0 through Sunday=6')
        return sorted(set(value))

    @field_validator('run_at')
    @classmethod
    def aware_run_at(cls,value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None): raise ValueError('run_at must include an explicit timezone offset')
        return value

    @model_validator(mode='after')
    def valid_definition(self):
        from croniter import croniter
        if self.trigger_type=='time':
            if self.schedule_mode=='cron' and (len(self.cron_expression.split())!=5 or not croniter.is_valid(self.cron_expression)):
                raise ValueError('Use a valid five-field cron: minute hour day month weekday')
            if self.schedule_mode=='once' and self.run_at is None and self.delay_seconds is None: raise ValueError('One-time tasks need run_at or delay_seconds')
            if self.schedule_mode=='once' and self.run_at is not None and self.delay_seconds is not None: raise ValueError('Choose run_at or delay_seconds, not both')
            if self.schedule_mode=='interval' and self.interval_seconds is None: raise ValueError('Interval tasks need interval_seconds')
            if self.schedule_mode=='weekly' and not self.weekdays: raise ValueError('Choose at least one weekday')
        elif self.trigger_type in {'event','condition'} and not self.event_type:
            raise ValueError('Event and condition triggers require an exact event_type')
        if self.trigger_type=='condition' and not self.event_filters: raise ValueError('Condition triggers require at least one predicate')
        if self.task_type=='prompt':
            if not self.prompt.strip(): raise ValueError('Prompt tasks require a non-empty prompt')
            if self.skill_id or self.mcp_server_id or self.tool_name: raise ValueError('Prompt tasks cannot specify extension targets')
            if self.session_mode=='existing' and not self.target_session_id: raise ValueError('Existing-session prompts require target_session_id')
        elif self.task_type=='skill':
            if not self.skill_id or self.mcp_server_id or self.tool_name: raise ValueError('Skill tasks require only skill_id')
        elif not self.mcp_server_id or not self.tool_name or self.skill_id: raise ValueError('MCP tasks require mcp_server_id and tool_name')
        if self.task_type!='prompt' and self.timeout_seconds>600: raise ValueError('Extension task timeout cannot exceed 600 seconds')
        import json
        if len(json.dumps({'payload':self.payload_json,'filters':[p.model_dump() for p in self.event_filters]},ensure_ascii=False).encode('utf-8'))>65536: raise ValueError('Task payload and filters exceed 64 KiB')
        return self


class ScheduledTaskPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = None
    description: str | None = None
    task_type: TaskType | None = None
    trigger_type: TriggerType | None = None
    schedule_mode: ScheduleMode | None = None
    cron_expression: str | None = None
    timezone: str | None = None
    run_at: datetime | None = None
    delay_seconds: int | None = None
    interval_seconds: int | None = None
    time_of_day: str | None = None
    weekdays: list[int] | None = None
    remaining_executions: int | None = None
    event_type: str | None = None
    event_filters: list[EventPredicate] | None = None
    cooldown_seconds: int | None = None
    enabled: bool | None = None
    prompt: str | None = None
    target_session_id: str | None = None
    session_mode: Literal['new','existing'] | None = None
    provider_id: str | None = None
    model_id: str | None = None
    agent_mode: bool | None = None
    web_search: bool | None = None
    skill_id: str | None = None
    mcp_server_id: str | None = None
    tool_name: str | None = None
    payload_json: dict[str,Any] | None = None
    timeout_seconds: int | None = None


class ScheduledTaskView(ScheduledTaskCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_by: str
    created_via: str = 'user'
    execution_count: int = 0
    next_run_at: datetime | None
    next_trigger_in_seconds: int | None = None
    schedule_summary: str = ''
    last_run_at: datetime | None
    last_status: str | None
    webhook_configured: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator('run_at')
    @classmethod
    def aware_run_at(cls,value):
        from datetime import timezone
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class ScheduledTaskRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    task_id: str
    status: str
    trigger: str
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime | None
    result_json: dict[str,Any]
    trigger_payload: dict[str,Any] = Field(default_factory=dict)
    cancel_requested: bool = False
    error_code: str | None
    error_message: str | None


class ScheduledEventPublish(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_type: str = Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_.:-]+$')
    payload: dict[str,Any] = Field(default_factory=dict)
    event_id: str | None = Field(default=None,max_length=80)
