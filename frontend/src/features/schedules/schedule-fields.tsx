import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { ScheduleConditions } from "./schedule-conditions";
import type { ScheduledTask, ScheduledTaskInput, ScheduleMode, TriggerKind } from "@/types/schedules";
const selectClass="h-9 w-full rounded-md border border-input bg-background px-3 text-sm";

export function parseUtcDate(value: string): Date {
  // Treat naive ISO-8601 timestamps (without Z or +/-HH:MM) as UTC.
  const normalized = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value) ? `${value}Z` : value;
  return new Date(normalized);
}
export function formatCountdown(seconds:number) {
  if (seconds<=0)return "即将触发";
  const days=Math.floor(seconds/86400),hours=Math.floor(seconds%86400/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
  return `${days ? `${days}天 `:""}${hours ? `${hours}小时 `:""}${minutes ? `${minutes}分 `:""}${rest}秒后触发`;
}
export function TriggerCountdown({task}:{task:ScheduledTask}) {
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  if (task.remaining_executions===0)return <span>次数已用完</span>;
  if (!task.enabled)return <span>已暂停</span>;
  if (task.trigger_type && task.trigger_type!=="time")return <span>{task.trigger_type==="webhook" ? "等待 Webhook" : "等待事件满足条件"}</span>;
  if (!task.next_run_at)return <span>未安排触发</span>;
  return <span className="tabular-nums">{formatCountdown(Math.max(0,Math.ceil((parseUtcDate(task.next_run_at).getTime()-now)/1000)))}</span>;
}
export function inputOnly(task:ScheduledTaskInput):ScheduledTaskInput {
  const {name,description,task_type,trigger_type,schedule_mode,cron_expression,timezone,enabled,run_at,delay_seconds,interval_seconds,time_of_day,weekdays,remaining_executions,event_type,event_filters,cooldown_seconds,prompt,target_session_id,session_mode,provider_id,model_id,agent_mode,web_search,skill_id,mcp_server_id,tool_name,payload_json,timeout_seconds}=task;
  return {name,description,task_type,trigger_type,schedule_mode,cron_expression,timezone,enabled,run_at,delay_seconds,interval_seconds,time_of_day,weekdays,remaining_executions,event_type,event_filters,cooldown_seconds,prompt,target_session_id,session_mode,provider_id,model_id,agent_mode,web_search,skill_id,mcp_server_id,tool_name,payload_json,timeout_seconds};
}
function localDate(value?:string|null){if(!value)return "";const d=parseUtcDate(value);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
export function ScheduleFields({form,onChange,eventTypes,conditionPaths}:{form:ScheduledTaskInput;onChange:(form:ScheduledTaskInput)=>void;eventTypes:string[];conditionPaths:string[]}) {
  const trigger=form.trigger_type ?? "time",mode=form.schedule_mode ?? "cron";
  const field=(key:keyof ScheduledTaskInput,value:unknown)=>onChange({...form,[key]:value});
  const [onceInput,setOnceInput]=useState<"delay"|"date">(form.run_at ? "date":"delay");
  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const commonZones: Record<string, string> = {
    "UTC": "协调世界时（UTC）", "Asia/Shanghai": "北京时间", "Asia/Tokyo": "东京", "Asia/Singapore": "新加坡",
    "America/New_York": "纽约", "America/Chicago": "芝加哥", "America/Los_Angeles": "洛杉矶", "Europe/London": "伦敦", "Europe/Paris": "巴黎", "Australia/Sydney": "悉尼",
  };
  const zones = [...new Set([localZone, ...Object.keys(commonZones), form.timezone, ...(Intl.supportedValuesOf?.("timeZone") ?? [])])];
  return <>
    <div className="space-y-2"><Label htmlFor="schedule-trigger">触发条件</Label><select id="schedule-trigger" className={selectClass} value={trigger} onChange={e=>field("trigger_type",e.target.value as TriggerKind)}><option value="time">按时间</option><option value="event">特定事件</option><option value="condition">事件 + 条件</option><option value="webhook">外部 Webhook</option></select></div>
    {trigger==="time" ? <>
      <div className="space-y-2"><Label htmlFor="schedule-mode">执行模式</Label><select id="schedule-mode" className={selectClass} value={mode} onChange={e=>onChange({...form,schedule_mode:e.target.value as ScheduleMode,run_at:null,delay_seconds:e.target.value==="once" ? 600:null,remaining_executions:e.target.value==="once" ? 1:null,interval_seconds:form.interval_seconds ?? 3600})}><option value="once">一次性</option><option value="interval">固定间隔</option><option value="daily">每天</option><option value="weekly">每周</option><option value="cron">高级自定义时间（Cron）</option></select></div>
      {mode==="once" ? <div className="space-y-3"><div className="flex gap-2"><Button type="button" size="sm" variant={onceInput==="delay" ? "secondary":"outline"} onClick={()=>{setOnceInput("delay");onChange({...form,run_at:null,delay_seconds:600});}}>多久后</Button><Button type="button" size="sm" variant={onceInput==="date" ? "secondary":"outline"} onClick={()=>{setOnceInput("date");field("delay_seconds",null);}}>指定时间</Button></div>{onceInput==="delay" ? <div className="space-y-2"><Label htmlFor="schedule-delay">延迟（秒）</Label><Input id="schedule-delay" type="number" min={1} max={31536000} value={form.delay_seconds ?? 600} onChange={e=>onChange({...form,delay_seconds:Number(e.target.value),run_at:null})}/></div> : <div className="space-y-2"><Label htmlFor="schedule-date">本地日期时间</Label><Input id="schedule-date" required type="datetime-local" value={localDate(form.run_at)} onChange={e=>{if(e.target.value)onChange({...form,run_at:new Date(e.target.value).toISOString(),delay_seconds:null});}}/></div>}</div> : null}
      {mode==="interval" ? <div className="space-y-2"><Label htmlFor="schedule-interval">每隔多少秒</Label><Input id="schedule-interval" required type="number" min={10} max={31536000} value={form.interval_seconds ?? 3600} onChange={e=>field("interval_seconds",Number(e.target.value))}/></div> : null}
      {mode==="daily" || mode==="weekly" ? <div className="space-y-2"><Label htmlFor="schedule-time">触发时间</Label><Input id="schedule-time" required type="time" value={form.time_of_day ?? "09:00"} onChange={e=>field("time_of_day",e.target.value)}/></div> : null}
      {mode==="weekly" ? <fieldset><legend className="mb-2 text-sm font-medium">星期</legend><div className="flex flex-wrap gap-3">{"一二三四五六日".split("").map((day,index)=><label key={day} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={(form.weekdays ?? []).includes(index)} onChange={e=>field("weekdays",e.target.checked ? [...form.weekdays ?? [],index]:(form.weekdays ?? []).filter(value=>value!==index))}/>周{day}</label>)}</div></fieldset> : null}
      {mode === "cron" && <div className="space-y-2 rounded-lg border p-3">
        <Label htmlFor="schedule-cron">自定义时间规则</Label>
        <Input id="schedule-cron" dir="ltr" required value={form.cron_expression} onChange={event => field("cron_expression", event.target.value)} aria-describedby="schedule-cron-help" />
        <p id="schedule-cron-help" className="text-xs text-muted-foreground">按分钟、小时、日、月、星期填写。例如 <code>0 9 * * *</code> 表示每天上午 9 点。普通重复任务可直接选每天或每周。</p>
      </div>}
      {mode !== "once" && mode !== "interval" && <div className="space-y-2">
        <Label htmlFor="schedule-zone">执行时区</Label>
        <select id="schedule-zone" className={selectClass} value={form.timezone} onChange={event => field("timezone", event.target.value)}>
          {zones.map(zone => <option key={zone} value={zone}>{zone === localZone ? `本地时区（${commonZones[zone] ?? zone}）` : commonZones[zone] ?? zone.replaceAll("_", " ")}</option>)}
        </select>
      </div>}
    </> : <>
      {trigger!=="webhook" ? <div className="space-y-2"><Label htmlFor="schedule-event">事件名称</Label><Input id="schedule-event" required list="schedule-event-types" value={form.event_type ?? ""} placeholder="如 file.upload 或 event.report_ready" onChange={e=>field("event_type",e.target.value)}/><datalist id="schedule-event-types">{eventTypes.map(event=><option key={event} value={event}/>)}</datalist></div> : <p className="text-sm text-muted-foreground">保存后生成接收地址与专用令牌，令牌只展示一次。</p>}
      <ScheduleConditions conditions={form.event_filters ?? []} onChange={conditions => field("event_filters", conditions)} paths={conditionPaths} required={trigger === "condition"} />
      <div className="space-y-2"><Label htmlFor="schedule-cooldown">触发冷却（秒）</Label><Input id="schedule-cooldown" required type="number" min={10} value={form.cooldown_seconds ?? 60} onChange={e=>field("cooldown_seconds",Number(e.target.value))}/></div>
    </>}
    {mode!=="once" || trigger!=="time" ? <div className="grid gap-3 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="schedule-repeat">重复次数</Label><select id="schedule-repeat" className={selectClass} value={form.remaining_executions==null ? "unlimited":"limited"} onChange={e=>field("remaining_executions",e.target.value==="limited" ? 3:null)}><option value="unlimited">持续执行</option><option value="limited">执行 X 次后停止</option></select></div>{form.remaining_executions!=null ? <div className="space-y-2"><Label htmlFor="schedule-count">剩余执行次数</Label><Input id="schedule-count" required type="number" min={0} max={100000} value={form.remaining_executions} onChange={e=>field("remaining_executions",Number(e.target.value))}/></div> : null}</div> : null}
  </>;
}
