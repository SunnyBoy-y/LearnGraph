import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Filter, History, MoreHorizontal, Pause, Pencil, Play, Square, Trash2 } from "lucide-react";
import { toast } from '@/lib/notifications';
import { listMcpServers, listSkills } from "@/api/extensions";
import { cancelScheduledTask, createScheduledTask, deleteScheduledTask, listScheduledTaskRuns, listScheduledTasks, runScheduledTask, updateScheduledTask, getTriggerCatalog, rotateScheduledWebhookToken } from "@/api/schedules";
import { ErrorState, LoadingState, PageFrame } from "@/components/shared/page-elements";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { ScheduledTask, ScheduledTaskInput, TaskKind, EventPredicate } from "@/types/schedules";
import { ScheduleFields, TriggerCountdown, inputOnly } from "./schedule-fields";
import { ScheduleDiscovery } from "./schedule-discovery";
import "./schedules.css";

const selectClass = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm";
const statusText: Record<string, string> = { running: "执行中", succeeded: "成功", failed: "失败", denied: "权限失效", skipped: "已跳过" };
const date = (value: string | null) => value ? new Date(value).toLocaleString() : "—";
export const scheduleKey = (workspaceId: string) => workspaceQueryKey(workspaceId, "schedules");
export function parseSchedulePayload(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("执行参数必须是 JSON 对象");
  return value as Record<string, unknown>;
}
const defaults = (): ScheduledTaskInput => ({ name: "", description: "", task_type: "prompt", trigger_type:"time",schedule_mode:"daily",cron_expression: "0 9 * * *",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", enabled: true,time_of_day:"09:00",weekdays:[0,1,2,3,4],remaining_executions:null,
  interval_seconds:3600,cooldown_seconds:60,event_filters:[],prompt:"",session_mode:"new",agent_mode:true,web_search:false,
  skill_id: null, mcp_server_id: null, tool_name: null, payload_json: {}, timeout_seconds: 300 });

export function SchedulesPage() {
  const { workspaceId } = useAuth();
  // Remount on scope changes: never retain another workspace's editable targets/logs.
  return <SchedulesWorkspace key={workspaceId} workspaceId={workspaceId} />;
}

function SchedulesWorkspace({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const compositionEndedAt = useRef(-Infinity);
  const [editor, setEditor] = useState<ScheduledTask | "new" | null>(null);
  const [form, setForm] = useState<ScheduledTaskInput>(defaults);
  const [payloadText, setPayloadText] = useState('{"script_path":"scripts/main.py","argv_extra":[]}');
  const [formError, setFormError] = useState("");
  const [filtersText,setFiltersText] = useState("[]");
  const [webhookToken,setWebhookToken] = useState<{token:string;header:string;path:string;note:string}|null>(null);
  const [webhookTask,setWebhookTask] = useState<ScheduledTask|null>(null);
  const [remove, setRemove] = useState<ScheduledTask | null>(null);
  const [logs, setLogs] = useState<ScheduledTask | null>(null);
  const [filter, setFilter] = useState("enabled");
  const tasks = useQuery({ queryKey: scheduleKey(workspaceId), queryFn: listScheduledTasks, refetchInterval: 5000 });
  const catalog = useQuery({queryKey:workspaceQueryKey(workspaceId,"schedules","trigger-catalog"),queryFn:getTriggerCatalog,enabled:editor !== null});
  const webhook = useMutation({mutationFn:rotateScheduledWebhookToken,onSuccess:async data=>{setWebhookToken(data);await invalidate();},onError:(error:Error)=>toast.error(error.message)});
  const skills = useQuery({ queryKey: workspaceQueryKey(workspaceId, "skills"), queryFn: listSkills, enabled: editor !== null });
  const servers = useQuery({ queryKey: workspaceQueryKey(workspaceId, "mcp-servers"), queryFn: listMcpServers, enabled: editor !== null });
  const runs = useQuery({ queryKey: workspaceQueryKey(workspaceId, "schedules", logs?.id ?? "none", "runs"),
    queryFn: () => listScheduledTaskRuns(logs!.id), enabled: Boolean(logs), refetchInterval: logs ? 3000 : false });
  const invalidate = () => client.invalidateQueries({ queryKey: scheduleKey(workspaceId) });
  const action = useMutation({ mutationFn: async ({ kind, task }: { kind: "toggle" | "run" | "delete" | "cancel"; task: ScheduledTask }) => {
    if (kind === "toggle") return updateScheduledTask(task.id, { enabled: !task.enabled });
    if (kind === "run") return runScheduledTask(task.id);
    if (kind === "cancel") return cancelScheduledTask(task.id);
    return deleteScheduledTask(task.id);
  }, onSuccess: async (_, { kind, task }) => { await invalidate();
    const options = { scope: `${workspaceId}:${task.id}`, context: task.name };
    if (kind === "run") toast.info("已受理执行请求", options);
    else if (kind === "cancel") toast.info("已请求停止，等待执行器确认", options);
    else toast.success(kind === "delete" ? "任务已删除" : "任务已更新", options);
    setRemove(null);
  },
  onError: (error: Error, { task }) => toast.error("任务操作未完成", { description: error.message, scope: `${workspaceId}:${task.id}`, context: task.name, action: { label: "查看任务", onClick: () => void navigate(`/w/${workspaceId}/schedules`) } }) });
  const save = useMutation({ mutationFn: (input: ScheduledTaskInput) => editor && editor !== "new" ? updateScheduledTask(editor.id, input) : createScheduledTask(input),
    onSuccess: async (task) => { await invalidate(); setEditor(null); toast.success("定时任务已保存", { scope: `${workspaceId}:${task.id}`, context: task.name }); },
    onError: (error: Error) => setFormError(error.message) });
  const open = (task: ScheduledTask | "new") => {
    setEditor(task); setForm(task === "new" ? defaults() : inputOnly(task));
    setFiltersText(task === "new" ? "[]" : JSON.stringify(task.event_filters ?? [],null,2));
    setPayloadText(task === "new" ? '{"script_path":"scripts/main.py","argv_extra":[]}' : JSON.stringify(task.payload_json, null, 2));
    setFormError("");
  };
  const activeServer = servers.data?.find(server => server.id === form.mcp_server_id);
  const isBusy = action.isPending || save.isPending;

  const visibleTasks = tasks.data?.filter(task => filter === "all" || task.enabled === (filter === "enabled")) ?? [];

  return <PageFrame className="schedules-page">
    <header className="schedules-header">
      <div className="min-w-0">
        <h1 data-page-title>定时任务</h1>
        <p>告诉 LearnGraph，让它安排任务、设置提醒或监控更新</p>
      </div>
      <Select value={filter} onValueChange={setFilter}>
        <SelectTrigger aria-label="任务状态" className="schedule-filter">
          <Filter className="size-4" aria-hidden="true" /><SelectValue />
        </SelectTrigger>
        <SelectContent position="popper" align="end" className="schedule-filter-menu p-1">
          <SelectItem value="enabled">已开启</SelectItem>
          <SelectItem value="all">全部任务</SelectItem>
          <SelectItem value="paused">已暂停</SelectItem>
        </SelectContent>
      </Select>
    </header>
    <ScheduleDiscovery workspaceId={workspaceId} onManualCreate={() => open("new")}>
      {tasks.isPending ? <LoadingState /> : tasks.isError ? <ErrorState message={tasks.error.message} onRetry={() => void tasks.refetch()} /> :
        visibleTasks.length ? <section className="schedule-tasks" aria-labelledby="schedule-tasks-title">
          <h2 id="schedule-tasks-title">{filter === "paused" ? "已暂停的任务" : "我的任务"}<span className="tabular-nums">{visibleTasks.length}</span></h2>
          <ul>{visibleTasks.map(task => <li key={task.id} className="schedule-task">
            <CalendarClock className="schedule-task__icon size-5" aria-hidden="true" />
            <div className="schedule-task__body">
              <button type="button" className="schedule-task__name" disabled={isBusy || task.last_status === "running"} onClick={() => open(task)}>{task.name}</button>
              {task.prompt || task.description ? <p className="schedule-task__description">{task.prompt || task.description}</p> : null}
              <div className="schedule-task__meta">
                <span>{task.schedule_summary?.replace(/，剩余 [\d,]+ 次$|，持续运行$/u, "") || task.cron_expression}</span>
                <span>{task.remaining_executions == null ? "持续执行" : `剩余 ${task.remaining_executions} 次`}</span>
                <span>{task.enabled ? "已启用" : "已暂停"}</span>
                {task.created_via === "ai" ? <span>AI 创建</span> : null}
                <span>{task.task_type === "prompt" ? "AI 指令" : task.task_type === "skill" ? "Skill" : "MCP"}</span>
              </div>
              <div className="schedule-task__meta">
                <span title={date(task.next_run_at)}><TriggerCountdown task={task} /></span>
                <span title={date(task.last_run_at)}>{task.last_status ? statusText[task.last_status] ?? task.last_status : "尚未执行"}</span>
                <span>已触发 {task.execution_count ?? 0} 次</span>
              </div>
            </div>
            <div className="schedule-task__actions">
              <Button size="icon" variant="ghost" disabled={isBusy || task.last_status === "running"} aria-label={`立即执行 ${task.name}`} title="立即执行" onClick={() => action.mutate({ kind: "run", task })}><Play className="size-4" /></Button>
              <Button size="icon" variant="ghost" disabled={isBusy} aria-label={`${task.enabled ? "暂停" : "启用"} ${task.name}`} title={task.enabled ? "暂停" : "启用"} onClick={() => action.mutate({ kind: "toggle", task })}>{task.enabled ? <Pause className="size-4" /> : <CalendarClock className="size-4" />}</Button>
              {task.task_type !== "mcp" && task.last_status === "running" ? <Button size="icon" variant="ghost" disabled={isBusy} aria-label={`停止 ${task.name}`} title="停止" onClick={() => action.mutate({ kind: "cancel", task })}><Square className="size-4" /></Button> : null}
              <Button size="icon" variant="ghost" aria-label={`记录 ${task.name}`} title="执行记录" onClick={() => setLogs(task)}><History className="size-4" /></Button>
              <DropdownMenu><DropdownMenuTrigger asChild><Button size="icon" variant="ghost" aria-label={`更多操作 ${task.name}`}><MoreHorizontal className="size-4" /></Button></DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem disabled={isBusy || task.last_status === "running"} onSelect={() => open(task)}><Pencil />编辑任务</DropdownMenuItem>
                  {task.trigger_type === "webhook" ? <DropdownMenuItem onSelect={() => { setWebhookTask(task); setWebhookToken(null); }}>Webhook</DropdownMenuItem> : null}
                  <DropdownMenuItem variant="destructive" disabled={isBusy || task.last_status === "running"} onSelect={() => setRemove(task)}><Trash2 />删除任务</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </li>)}</ul>
        </section> : tasks.data?.length ? <p className="schedule-empty">{filter === "paused" ? "没有已暂停的任务" : "没有已开启的任务"}</p> : null}
    </ScheduleDiscovery>
    <Dialog open={editor !== null} onOpenChange={open => { if (!open && !save.isPending) setEditor(null); }}>
      <DialogContent className="max-h-[min(90dvh,calc(100dvh-4rem))] overflow-y-auto shadow-lg sm:max-w-xl">
        <DialogHeader><DialogTitle>{editor === "new" ? "新建定时任务" : "编辑定时任务"}</DialogTitle><DialogDescription>应用服务运行时自动触发。结果保留在执行会话中，失败不自动重放；手动测试不消耗计划次数。</DialogDescription></DialogHeader>
        <form className="space-y-4" onCompositionEnd={() => { compositionEndedAt.current = performance.now(); }} onKeyDown={event => { if (event.key === "Enter" && (event.nativeEvent.isComposing || performance.now() - compositionEndedAt.current < 500)) event.preventDefault(); }} onSubmit={event => { event.preventDefault(); if (save.isPending) return;
          try { setFormError(""); const parsed = form.task_type === "prompt" ? {} : parseSchedulePayload(payloadText);
            const filters:unknown=JSON.parse(filtersText);if(!Array.isArray(filters))throw new Error("条件必须为 JSON 数组");
            save.mutate({...inputOnly(form),event_filters:filters as EventPredicate[],payload_json:parsed}); }
          catch (error) { setFormError(error instanceof Error ? error.message : "JSON 格式错误"); } }}>
          <div className="space-y-2"><Label htmlFor="schedule-name">名称</Label><Input id="schedule-name" value={form.name} required maxLength={160} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
          <div className="space-y-2"><Label htmlFor="schedule-type">执行类型</Label><select id="schedule-type" className={selectClass} value={form.task_type} onChange={e => { const type = e.target.value as TaskKind; setForm({ ...form, task_type: type, skill_id: null, mcp_server_id: null, tool_name: null, timeout_seconds:type === "prompt" ? form.timeout_seconds : Math.min(600,form.timeout_seconds) }); setPayloadText(type === "skill" ? '{"script_path":"scripts/main.py","argv_extra":[]}' : "{}"); }}><option value="prompt">Prompt（AI 指令）</option><option value="skill">Skill</option><option value="mcp">MCP 工具</option></select></div>
          {skills.isError || servers.isError ? <p role="alert" className="text-sm text-destructive">扩展列表读取失败，请刷新后重试。</p> : null}
          {form.task_type !== "prompt" ? <div className="space-y-2"><Label htmlFor="schedule-target">{form.task_type === "skill" ? "技能" : "MCP 服务"}</Label><select id="schedule-target" className={selectClass} required value={(form.task_type === "skill" ? form.skill_id : form.mcp_server_id) ?? ""} onChange={e => setForm(form.task_type === "skill" ? { ...form, skill_id: e.target.value } : { ...form, mcp_server_id: e.target.value, tool_name: null })}>
            <option value="">选择扩展</option>{form.task_type === "skill" ? skills.data?.map(skill => <option key={skill.id} value={skill.id}>{skill.name}{skill.enabled ? "" : "（未启用）"}</option>) : servers.data?.map(server => <option key={server.id} value={server.id}>{server.display_name}{server.enabled ? "" : "（未启用）"}</option>)}
          </select></div> : <>
            <div className="space-y-2"><Label htmlFor="schedule-prompt">触发后执行的 Prompt</Label><Textarea id="schedule-prompt" required className="min-h-32" maxLength={32000} value={form.prompt ?? ""} onChange={e=>setForm({...form,prompt:e.target.value})}/></div>
            <div className="grid gap-3 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="schedule-session-mode">结果会话</Label><select id="schedule-session-mode" className={selectClass} value={form.session_mode ?? "new"} onChange={e=>setForm({...form,session_mode:e.target.value as "new"|"existing"})}><option value="new">每次新建结果会话</option><option value="existing">在指定会话继续</option></select></div><div className="space-y-2"><Label htmlFor="schedule-session">关联会话 ID（可选）</Label><Input id="schedule-session" required={form.session_mode === "existing"} value={form.target_session_id ?? ""} onChange={e=>setForm({...form,target_session_id:e.target.value || null})}/></div></div>
            <div className="flex flex-wrap gap-5"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.agent_mode ?? true} onChange={e=>setForm({...form,agent_mode:e.target.checked})}/>允许已授权工具</label><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.web_search ?? false} onChange={e=>setForm({...form,web_search:e.target.checked})}/>允许联网检索</label></div>
          </>}
          {form.task_type === "mcp" ? <div className="space-y-2"><Label htmlFor="schedule-tool">工具</Label><select id="schedule-tool" className={selectClass} required value={form.tool_name ?? ""} onChange={e => setForm({ ...form, tool_name: e.target.value })}><option value="">选择已审核的工具</option>{activeServer?.requested_tools.map(tool => <option key={tool} value={tool}>{tool}</option>)}</select></div> : null}
          <ScheduleFields form={form} onChange={setForm} filtersText={filtersText} onFiltersChange={setFiltersText} eventTypes={catalog.data?.event_types ?? []}/>
          {form.task_type !== "prompt" ? <div className="space-y-2"><Label htmlFor="schedule-payload">执行参数（JSON）</Label><Textarea id="schedule-payload" className="min-h-28 font-mono" dir="ltr" value={payloadText} onChange={e => setPayloadText(e.target.value)} /></div> : null}
          <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="schedule-timeout">执行超时（秒）</Label><Input id="schedule-timeout" type="number" min={1} max={form.task_type === "prompt" ? 3600 : 600} required value={form.timeout_seconds} onChange={e => setForm({ ...form, timeout_seconds: Number(e.target.value) })} /></div><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />启用计划</label></div>
          {formError ? <p role="alert" className="text-sm text-destructive">{formError}</p> : null}
          <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={save.isPending} onClick={() => setEditor(null)}>取消</Button><Button type="submit" disabled={save.isPending}>{save.isPending ? "保存中…" : "保存任务"}</Button></div>
        </form>
      </DialogContent>
    </Dialog>
    <Dialog open={webhookTask !== null} onOpenChange={open=>{if(!open){setWebhookTask(null);setWebhookToken(null);}}}><DialogContent><DialogHeader><DialogTitle>Webhook：{webhookTask?.name}</DialogTitle><DialogDescription>专用令牌只展示一次。重新生成会立即撤销旧令牌，请勿放入 Prompt 或 URL。</DialogDescription></DialogHeader><p className="break-all text-sm">POST /api/v1/scheduled-tasks/{webhookTask?.id}/trigger</p><Button disabled={webhook.isPending} onClick={()=>{if(webhookTask)webhook.mutate(webhookTask.id);}}>生成 / 轮换令牌</Button>{webhookToken ? <div className="space-y-2"><p className="text-sm">请求头：{webhookToken.header}</p><Input readOnly type="password" aria-label="Webhook 令牌" value={webhookToken.token}/><Button variant="outline" onClick={()=>void navigator.clipboard.writeText(webhookToken.token)}>复制令牌</Button><pre className="overflow-auto bg-muted p-3 text-xs" dir="ltr">{'{"event_type":"external_update","payload":{},"event_id":"unique-id"}'}</pre></div> : null}</DialogContent></Dialog>
    <AlertDialog open={remove !== null} onOpenChange={open => { if (!open) setRemove(null); }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除定时任务？</AlertDialogTitle><AlertDialogDescription>删除“{remove?.name}”及其执行记录，不会撤销已产生的外部操作。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction disabled={action.isPending} onClick={e => { e.preventDefault(); if (remove) action.mutate({ kind: "delete", task: remove }); }}>删除任务</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    <Dialog open={logs !== null} onOpenChange={open => { if (!open) setLogs(null); }}><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>执行记录：{logs?.name}</DialogTitle><DialogDescription>状态与结果来自服务端执行记录。</DialogDescription></DialogHeader>
      {runs.isPending ? <LoadingState /> : runs.isError ? <ErrorState message={runs.error.message} onRetry={() => void runs.refetch()} /> : !runs.data?.length ? <p className="text-sm text-muted-foreground">尚无执行记录</p> : <div className="space-y-3">{runs.data.map(run => <details key={run.id} className="border-b pb-3"><summary className="cursor-pointer text-sm"><span className="font-medium">{statusText[run.status] ?? run.status}</span><span className="ml-3 tabular-nums">{date(run.started_at)}</span><span className="ml-3 text-muted-foreground">{run.trigger === "manual" ? "手动" : run.trigger === "scheduled" ? "定时" : "事件"}</span></summary>
        {run.error_message ? <p role="alert" className="mt-2 text-sm text-destructive">{run.error_code}：{run.error_message}</p> : null}
        {typeof run.result_json.chat_session_id === "string" ? <Link className="mt-2 block text-sm underline" to={`/w/${workspaceId}/chat/${run.result_json.chat_session_id}`}>打开执行会话</Link> : null}
        <pre dir="ltr" className="mt-2 max-h-64 overflow-auto bg-muted p-3 text-xs">{JSON.stringify(run.result_json, null, 2)}</pre>
      </details>)}</div>}
    </DialogContent></Dialog>
  </PageFrame>;
}
