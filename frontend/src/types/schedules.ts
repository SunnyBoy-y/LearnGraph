export type TaskKind = "prompt" | "skill" | "mcp";
export type TriggerKind = "time" | "event" | "condition" | "webhook";
export type ScheduleMode = "once" | "interval" | "daily" | "weekly" | "cron";
export interface EventPredicate { path:string; op:"eq"|"ne"|"contains"|"in"|"gt"|"gte"|"lt"|"lte"|"exists"; value?:unknown; }
export interface ScheduledTaskInput {
  name:string; description:string; task_type:TaskKind;
  trigger_type?:TriggerKind; schedule_mode?:ScheduleMode;
  cron_expression:string; timezone:string; enabled:boolean;
  run_at?:string|null; delay_seconds?:number|null; interval_seconds?:number|null;
  time_of_day?:string; weekdays?:number[]; remaining_executions?:number|null;
  event_type?:string|null; event_filters?:EventPredicate[]; cooldown_seconds?:number;
  prompt?:string; target_session_id?:string|null; session_mode?:"new"|"existing";
  provider_id?:string|null; model_id?:string|null; agent_mode?:boolean; web_search?:boolean;
  skill_id:string|null; mcp_server_id:string|null; tool_name:string|null;
  payload_json:Record<string,unknown>; timeout_seconds:number;
}
export interface ScheduledTask extends ScheduledTaskInput {
  id:string; created_by:string; created_via?:string; execution_count?:number;
  next_run_at:string|null; next_trigger_in_seconds?:number|null; schedule_summary?:string;
  last_run_at:string|null; last_status:string|null; webhook_configured?:boolean;
  created_at:string; updated_at:string;
}
export interface ScheduledTaskRun {
  id:string; task_id:string; status:string; trigger:string;
  scheduled_for:string; started_at:string; finished_at:string|null;
  result_json:Record<string,unknown>; trigger_payload?:Record<string,unknown>; cancel_requested?:boolean;
  error_code:string|null; error_message:string|null;
}
export interface TriggerCatalog { event_types:string[]; condition_paths:string[]; operators:string[]; modes:string[]; }
