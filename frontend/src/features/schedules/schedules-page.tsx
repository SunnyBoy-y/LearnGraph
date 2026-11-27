import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CalendarClock, History, MoreHorizontal, Pause, Pencil, Play, Square, SquareCheck, Trash2, X } from "lucide-react";
import { Dialog as Drawer } from "radix-ui";
import { listSessions } from "@/api/sessions";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { toast } from '@/lib/notifications';
import { listMcpServers, listSkills } from "@/api/extensions";
import { cancelScheduledTask, createScheduledTask, deleteScheduledTask, listScheduledTaskRuns, listScheduledTasks, runScheduledTask, updateScheduledTask, getTriggerCatalog, rotateScheduledWebhookToken } from "@/api/schedules";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import { ErrorState, LoadingState, PageFrame } from "@/components/shared/page-elements";
import { useActionConfirmation } from "@/components/shared/use-action-confirmation";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { useAuth } from "@/features/auth/auth-context-value";
import { useListSelection } from "@/lib/list-selection";
import { workspaceQueryKey } from "@/lib/query-keys";
import { adaptivePollInterval } from "@/lib/network-quality";
import type { ScheduledTask, ScheduledTaskInput, TaskKind } from "@/types/schedules";
import { ScheduleFields, inputOnly, parseUtcDate } from "./schedule-fields";
import { ScheduleDiscovery } from "./schedule-discovery";
import "./schedules.css";

const selectClass = "h-9 w-full rounded-md border border-input bg-background px-3 text-sm";
const statusText: Record<string, string> = { running: "执行中", succeeded: "成功", failed: "失败", denied: "权限失效", skipped: "已跳过" };
const isFailure = (status: string | null) => status === "failed" || status === "denied";
const cadence = (task: ScheduledTask) => task.schedule_summary
  ?.replace(/，剩余 [\d,]+ 次$|，持续运行$/u, "")
  .replace(`（${task.timezone}）`, "")
  .replace(/^每周 (?=[一二三四五六日])/u, "每周")
  || (task.trigger_type && task.trigger_type !== "time" ? "事件触发" : "按计划执行");
const timezoneLabel = (zone: string) => zone === "Asia/Shanghai" ? "北京时间" : zone;
const date = (value: string, timezone: string, precise = false) => {
  const valueDate = parseUtcDate(value);
  const format = (timeZone?: string) => {
    const local = timeZone ? { timeZone } : {};
    const day = valueDate.toLocaleDateString("zh-CN", { ...local, month: "long", day: "numeric", ...(precise ? { year: "numeric" } : {}) });
    const time = valueDate.toLocaleTimeString("zh-CN", { ...local, hour: "2-digit", minute: "2-digit", hourCycle: "h23", ...(precise ? { second: "2-digit" } : {}) });
    return `${day} ${time}`;
  };
  try { return format(timezone); }
  catch { return format(); }
};
type Inspector = { mode: "create" } | { mode: "details" | "edit" | "history"; task: ScheduledTask };
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
  const [inspector, setInspector] = useState<Inspector | null>(null);
  const inspectorOpener = useRef<HTMLElement | null>(null);
  const drawerTitle = useRef<HTMLHeadingElement>(null);
  const inspectorMode = inspector?.mode;
  useEffect(() => { if (inspectorMode) drawerTitle.current?.focus(); }, [inspectorMode]);
  const editor = inspector?.mode === "create" ? "new" : inspector?.mode === "edit" ? inspector.task : null;
  const [form, setForm] = useState<ScheduledTaskInput>(defaults);
  const [payloadText, setPayloadText] = useState('{}');
  const [scriptPath, setScriptPath] = useState('scripts/main.py');
  const [scriptArguments, setScriptArguments] = useState('');
  const [formError, setFormError] = useState("");
  const [webhookToken,setWebhookToken] = useState<{token:string;header:string;path:string;note:string}|null>(null);
  const [webhookTask,setWebhookTask] = useState<ScheduledTask|null>(null);
  const [remove, setRemove] = useState<ScheduledTask | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const filterParam = searchParams.get("status") || "all";
  const filter = ["all", "enabled", "paused"].includes(filterParam) ? filterParam : "all";
  const search = searchParams.get("q") ?? "";
  const updateScheduleParams = (patch: Record<string, string>, replace = false) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace, preventScrollReset: true });
    }
  };
  const setFilter = (next: string) => updateScheduleParams({ status: next === "all" ? "" : next });
  const setSearch = (next: string) => updateScheduleParams({ q: next }, true);
  const { confirm, dialog: confirmDialog } = useActionConfirmation();
  const tasks = useQuery({ queryKey: scheduleKey(workspaceId), queryFn: listScheduledTasks, refetchInterval: () => adaptivePollInterval(5000) });
  const selected = inspector && inspector.mode !== "create" ? inspector.task : null;
  const current = selected ? tasks.data?.find(task => task.id === selected.id) ?? selected : null;
  const runTaskId = inspector?.mode === "details" || inspector?.mode === "history" ? selected?.id : undefined;
  const catalog = useQuery({queryKey:workspaceQueryKey(workspaceId,"schedules","trigger-catalog"),queryFn:getTriggerCatalog,enabled:editor !== null});
  const webhook = useMutation({mutationFn:rotateScheduledWebhookToken,onSuccess:async data=>{setWebhookToken(data);await invalidate();},onError:(error:Error)=>toast.error(error.message)});
  const skills = useQuery({ queryKey: workspaceQueryKey(workspaceId, "skills"), queryFn: listSkills, enabled: editor !== null });
  const servers = useQuery({ queryKey: workspaceQueryKey(workspaceId, "mcp-servers"), queryFn: listMcpServers, enabled: editor !== null });
  const runs = useQuery({ queryKey: workspaceQueryKey(workspaceId, "schedules", runTaskId ?? "none", "runs"),
    queryFn: () => listScheduledTaskRuns(runTaskId!), enabled: Boolean(runTaskId), refetchInterval: () => adaptivePollInterval(runTaskId ? 3000 : false) });
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
    onSuccess: async (task) => {
      await invalidate();
      setInspector(current => current?.mode === "edit" ? { mode: "details", task } : null);
      toast.success("定时任务已保存", { scope: `${workspaceId}:${task.id}`, context: task.name });
    },
    onError: (error: Error) => setFormError(error.message) });
  const openEditor = (task: ScheduledTask | "new", opener?: HTMLElement) => {
    if (opener) inspectorOpener.current = opener;
    setInspector(task === "new" ? { mode: "create" } : { mode: "edit", task });
    setForm(task === "new" ? defaults() : inputOnly(task));
    setPromptExpanded(task === "new"); setSessionSearch("");
    const payload = task === "new" ? {} : task.payload_json;
    setPayloadText(JSON.stringify(payload, null, 2));
    setScriptPath(typeof payload.script_path === "string" ? payload.script_path : "scripts/main.py");
    setScriptArguments(Array.isArray(payload.argv_extra) ? payload.argv_extra.join("\n") : "");
    setFormError("");
  };
  const [promptExpanded, setPromptExpanded] = useState(false);
  const [sessionSearch, setSessionSearch] = useState("");
  const sessions = useQuery({ queryKey: workspaceQueryKey(workspaceId, "sessions"), queryFn: listSessions, enabled: editor !== null && form.task_type === "prompt" && form.session_mode === "existing" });
  const activeServer = servers.data?.find(server => server.id === form.mcp_server_id);
  const queryText = search.trim().toLocaleLowerCase();
  const visibleTasks = useMemo(
    () =>
      (tasks.data ?? []).filter(
        (task) =>
          (filter === "all" || task.enabled === (filter === "enabled")) &&
          (!queryText ||
            `${task.name} ${task.description ?? ""} ${task.schedule_summary ?? ""}`
              .toLocaleLowerCase()
              .includes(queryText)),
      ),
    [filter, queryText, tasks.data],
  );
  const visibleTaskIds = useMemo(() => visibleTasks.map((task) => task.id), [visibleTasks]);
  const selection = useListSelection(visibleTaskIds);
  const selectedTasks = useMemo(
    () => (tasks.data ?? []).filter((task) => selection.selectedIds.has(task.id)),
    [selection.selectedIds, tasks.data],
  );
  const batchAction = useMutation({
    mutationFn: async ({
      kind,
      targets,
    }: {
      kind: "enable" | "pause" | "delete";
      targets: ScheduledTask[];
    }) => {
      const results = await Promise.allSettled(
        targets.map((task) =>
          kind === "delete"
            ? deleteScheduledTask(task.id)
            : updateScheduledTask(task.id, { enabled: kind === "enable" }),
        ),
      );
      const succeededIds: string[] = [];
      results.forEach((result, index) => {
        if (result.status === "fulfilled") succeededIds.push(targets[index].id);
      });
      return { kind, succeededIds, failed: targets.length - succeededIds.length };
    },
    onSuccess: async ({ kind, succeededIds, failed }) => {
      if (kind === "delete") selection.removeIds(succeededIds);
      await invalidate();
      const label = kind === "delete" ? "删除" : kind === "enable" ? "开启" : "暂停";
      if (failed === 0) toast.success(`已${label} ${succeededIds.length} 个任务`);
      else if (succeededIds.length > 0) toast.warning(`已${label} ${succeededIds.length} 个任务，${failed} 个失败`);
      else toast.error(`批量${label}失败`);
    },
    onError: (error: Error) => toast.error(error.message || "批量操作失败"),
  });
  const isBusy = action.isPending || save.isPending || batchAction.isPending;

  const latest = runs.data?.[0];
  const showInspector = (task: ScheduledTask, mode: "details" | "history", opener?: HTMLElement) => {
    if (opener) inspectorOpener.current = opener;
    setInspector({ mode, task });
  };
  const closeInspector = () => { if (!save.isPending) setInspector(null); };
  const goToDetails = () => { if (current) showInspector(current, "details"); else closeInspector(); };
  const running = current?.last_status === "running";

  return <><PageFrame className="schedules-page">
    <header className="schedules-header"><h1 data-page-title>定时任务</h1></header>
    <ScheduleDiscovery workspaceId={workspaceId} onManualCreate={opener => openEditor("new", opener)}>
      <section className="schedule-tasks" aria-labelledby="schedule-tasks-title">
        <div className="schedule-tasks__header">
          <h2 id="schedule-tasks-title">我的任务</h2>
          {tasks.data?.length ? (
            <div className="flex flex-wrap items-center gap-2">
              <ClearableSearchInput
                aria-label="搜索定时任务"
                containerClassName="w-44 sm:w-52"
                inputClassName="h-8 text-xs"
                onChange={setSearch}
                placeholder="搜索任务名称…"
                value={search}
              />
              <Select value={filter} onValueChange={setFilter}>
                <SelectTrigger aria-label="任务状态" className="schedule-filter"><SelectValue /></SelectTrigger>
                <SelectContent position="popper" align="end">
                  <SelectItem value="all">全部任务</SelectItem><SelectItem value="enabled">已开启</SelectItem><SelectItem value="paused">已暂停</SelectItem>
                </SelectContent>
              </Select>
              <Button
                aria-pressed={selection.multiSelect}
                disabled={isBusy}
                onClick={selection.toggleMultiSelect}
                size="sm"
                type="button"
                variant={selection.multiSelect ? "secondary" : "ghost"}
              >
                <SquareCheck className="size-4" />
                {selection.multiSelect ? "退出多选" : "多选"}
              </Button>
            </div>
          ) : null}
        </div>
        {selection.multiSelect && visibleTasks.length > 0 ? (
          <div
            aria-label="定时任务批量操作"
            className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border bg-muted/25 px-3 py-2 text-xs"
            role="region"
          >
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                aria-label="全选当前列表"
                checked={selection.visibleCheckboxState}
                disabled={isBusy}
                onCheckedChange={(checked) => selection.toggleVisible(checked === true)}
              />
              全选当前列表
            </label>
            <span className="tabular-nums text-muted-foreground">
              已选 {selection.selectedCount} 项
              {selection.otherSelectedCount > 0 ? `（另有 ${selection.otherSelectedCount} 项不在当前筛选）` : ""}
            </span>
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <Button
                disabled={isBusy || !selectedTasks.some((item) => !item.enabled)}
                onClick={() =>
                  batchAction.mutate({
                    kind: "enable",
                    targets: selectedTasks.filter((item) => !item.enabled),
                  })
                }
                size="xs"
                type="button"
                variant="outline"
              >
                <Play className="size-3.5" />
                开启
              </Button>
              <Button
                disabled={isBusy || !selectedTasks.some((item) => item.enabled)}
                onClick={() =>
                  batchAction.mutate({
                    kind: "pause",
                    targets: selectedTasks.filter((item) => item.enabled),
                  })
                }
                size="xs"
                type="button"
                variant="outline"
              >
                <Pause className="size-3.5" />
                暂停
              </Button>
              <Button
                disabled={isBusy || !selectedTasks.length}
                onClick={() => {
                  const deletable = selectedTasks.filter((item) => item.last_status !== "running");
                  if (!deletable.length) {
                    toast.info("执行中的任务不可直接删除");
                    return;
                  }
                  confirm({
                    title: `删除选中的 ${deletable.length} 个定时任务？`,
                    description: "删除后这些任务及其执行记录将被移除，不会撤销已产生的外部操作。",
                    label: `删除 ${deletable.length} 项`,
                    destructive: true,
                    run: () => batchAction.mutate({ kind: "delete", targets: deletable }),
                  });
                }}
                size="xs"
                type="button"
                variant="destructive"
              >
                <Trash2 className="size-3.5" />
                删除
              </Button>
              {selection.selectedCount > 0 ? (
                <Button disabled={isBusy} onClick={selection.clear} size="xs" type="button" variant="ghost">
                  取消选择
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
        {tasks.isPending ? <LoadingState /> : tasks.isError ? <ErrorState message={tasks.error.message} onRetry={() => void tasks.refetch()} /> :
          visibleTasks.length ? <ul>{visibleTasks.map(task => <li key={task.id} className={`schedule-task ${selection.multiSelect && selection.selectedIds.has(task.id) ? "ring-1 ring-ring" : ""}`}>
            <div className="flex min-w-0 items-center gap-3">
              {selection.multiSelect ? (
                <Checkbox
                  aria-label={`选择任务：${task.name}`}
                  disabled={isBusy}
                  {...selection.checkboxProps(task.id)}
                />
              ) : null}
              <button type="button" className="schedule-task__open flex-1" aria-labelledby={`schedule-task-name-${task.id}`} aria-describedby={`schedule-task-cadence-${task.id}`} onClick={event => {
                if (selection.multiSelect) {
                  selection.toggleItem(task.id, { shiftKey: event.shiftKey });
                  return;
                }
                showInspector(task, "details", event.currentTarget);
              }}>
                <span className="schedule-task__icon"><CalendarClock className="size-5" aria-hidden="true" /></span>
                <span className="schedule-task__body"><span id={`schedule-task-name-${task.id}`} className="schedule-task__name">{task.name}</span><span id={`schedule-task-cadence-${task.id}`} className="schedule-task__meta" title={timezoneLabel(task.timezone)}>{cadence(task)}</span></span>
              </button>
            </div>
            <div className="schedule-task__actions">
              <Switch checked={task.enabled} disabled={isBusy} aria-label={`启用计划：${task.name}`} title={task.enabled ? "暂停" : "启用"} onCheckedChange={() => action.mutate({ kind: "toggle", task })} />
              <DropdownMenu><DropdownMenuTrigger asChild><Button size="icon-sm" variant="ghost" aria-label={`更多操作 ${task.name}`} onPointerDown={event => { inspectorOpener.current = event.currentTarget; }} onKeyDown={event => { if (["Enter", " ", "ArrowDown"].includes(event.key)) inspectorOpener.current = event.currentTarget; }}><MoreHorizontal className="size-4" /></Button></DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem disabled={isBusy || task.last_status === "running"} onSelect={() => action.mutate({ kind: "run", task })}><Play />立即运行</DropdownMenuItem>
                  {task.task_type !== "mcp" && task.last_status === "running" ? <DropdownMenuItem disabled={isBusy} onSelect={() => action.mutate({ kind: "cancel", task })}><Square />停止执行</DropdownMenuItem> : null}
                  <DropdownMenuItem onSelect={() => showInspector(task, "history")}><History />执行记录</DropdownMenuItem>
                  <DropdownMenuItem disabled={isBusy || task.last_status === "running"} onSelect={() => openEditor(task)}><Pencil />编辑任务</DropdownMenuItem>
                  {task.trigger_type === "webhook" ? <DropdownMenuItem onSelect={() => { setWebhookTask(task); setWebhookToken(null); }}>Webhook</DropdownMenuItem> : null}
                  <DropdownMenuItem variant="destructive" disabled={isBusy || task.last_status === "running"} onSelect={() => setRemove(task)}><Trash2 />删除任务</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            {isFailure(task.last_status) ? <div className="schedule-task__failure"><span>{task.last_status === "denied" ? "执行权限失效" : "上次执行失败"}</span><span aria-hidden="true"> · </span><button type="button" onClick={event => showInspector(task, "history", event.currentTarget)}>查看原因</button></div> : null}
          </li>)}</ul> : <p className="schedule-empty">{filter === "paused" ? "没有已暂停的任务" : filter === "enabled" ? "没有已开启的任务" : "还没有定时任务"}</p>}
      </section>
    </ScheduleDiscovery>
    </PageFrame>
    <Drawer.Root open={inspector !== null} onOpenChange={open => { if (!open) closeInspector(); }}>
      <Drawer.Portal>
        <Drawer.Overlay data-slot="dialog-overlay" className="schedule-inspector-backdrop" />
        <Drawer.Content data-slot="dialog-content" className="schedule-inspector" aria-describedby={undefined}
          onOpenAutoFocus={event => { event.preventDefault(); drawerTitle.current?.focus(); }}
          onCloseAutoFocus={event => { if (inspectorOpener.current?.isConnected) { event.preventDefault(); inspectorOpener.current.focus(); } }}>
      <header className="schedule-inspector__header">
        <div className="min-w-0">
          <div className="schedule-inspector__eyebrow">
            {current && inspector?.mode !== "details" ? <Button type="button" variant="ghost" size="icon-sm" aria-label="返回任务详情" disabled={save.isPending} onClick={goToDetails}><ArrowLeft className="size-4" /></Button> : null}
            <span>{editor === "new" ? "定时任务" : inspector?.mode === "edit" ? "编辑任务" : inspector?.mode === "history" ? "执行记录" : "任务详情"}</span>
          </div>
          <Drawer.Title ref={drawerTitle} tabIndex={-1} className="schedule-inspector__title">{editor === "new" ? "新建定时任务" : current?.name}</Drawer.Title>
        </div>
        <Button variant="ghost" size="icon" aria-label="关闭任务详情" disabled={save.isPending} onClick={closeInspector}><X className="size-4" /></Button>
      </header>
      {editor ? <>
        <form className="space-y-4" onCompositionEnd={() => { compositionEndedAt.current = performance.now(); }} onKeyDown={event => { if (event.key === "Enter" && (event.nativeEvent.isComposing || performance.now() - compositionEndedAt.current < 500)) event.preventDefault(); }} onSubmit={event => { event.preventDefault(); if (save.isPending) return;
          try {
            setFormError("");
            const parsed = form.task_type === "skill"
              ? { ...form.payload_json, script_path: scriptPath.trim(), argv_extra: scriptArguments.split("\n").filter(argument => argument.length > 0) }
              : form.task_type === "mcp" ? parseSchedulePayload(payloadText) : {};
            if (form.task_type === "skill" && !scriptPath.trim()) throw new Error("请填写技能脚本路径");
            if (form.trigger_type === "condition" && !form.event_filters?.length) throw new Error("请至少添加一项匹配条件");
            save.mutate({...inputOnly(form),payload_json:parsed}); }
          catch (error) { setFormError(error instanceof Error ? error.message : "JSON 格式错误"); } }}>
          <div className="schedule-inspector__body space-y-4">
          <h3 className="font-semibold">基本信息</h3>
          <div className="space-y-2"><Label htmlFor="schedule-name">名称</Label><Input id="schedule-name" value={form.name} required maxLength={160} onChange={e => setForm({ ...form, name: e.target.value })} /></div>
          <div className="space-y-2"><Label htmlFor="schedule-description">任务摘要</Label><Input id="schedule-description" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></div>
          <h3 className="font-semibold">执行计划</h3>
          <ScheduleFields form={form} onChange={setForm} eventTypes={catalog.data?.event_types ?? []} conditionPaths={catalog.data?.condition_paths ?? []}/>
          {form.task_type === "prompt" ? <div className="space-y-2">
            <div className="flex items-center justify-between gap-2"><h3 className="font-semibold">任务指令</h3><Button type="button" variant="ghost" size="sm" aria-expanded={promptExpanded} onClick={() => setPromptExpanded(!promptExpanded)}>{promptExpanded ? "收起编辑器" : "展开完整编辑器"}</Button></div>
            {promptExpanded ? <><Label htmlFor="schedule-prompt">触发后执行的 Prompt</Label><Textarea id="schedule-prompt" required className="min-h-32" maxLength={32000} value={form.prompt ?? ""} onChange={e=>setForm({...form,prompt:e.target.value})}/></> : <p className="line-clamp-2 break-words rounded-xl bg-muted p-3 text-sm text-muted-foreground">{form.prompt || "尚未填写任务指令"}</p>}
          </div> : null}
          <details className="schedule-inspector__advanced group"><summary className="cursor-pointer font-semibold">高级设置</summary><div className="mt-4 space-y-4">
          <div className="space-y-2"><Label htmlFor="schedule-type">执行类型</Label><select id="schedule-type" className={selectClass} value={form.task_type} onChange={e => { const type = e.target.value as TaskKind; setForm({ ...form, task_type: type, skill_id: null, mcp_server_id: null, tool_name: null, payload_json: {}, timeout_seconds:type === "prompt" ? form.timeout_seconds : Math.min(600,form.timeout_seconds) }); setPayloadText("{}"); setScriptPath("scripts/main.py"); setScriptArguments(""); }}><option value="prompt">Prompt（AI 指令）</option><option value="skill">Skill</option><option value="mcp">MCP 工具</option></select></div>
          {skills.isError || servers.isError ? <p role="alert" className="text-sm text-destructive">扩展列表读取失败，请刷新后重试。</p> : null}
          {form.task_type !== "prompt" ? <div className="space-y-2"><Label htmlFor="schedule-target">{form.task_type === "skill" ? "技能" : "MCP 服务"}</Label><select id="schedule-target" className={selectClass} required value={(form.task_type === "skill" ? form.skill_id : form.mcp_server_id) ?? ""} onChange={e => setForm(form.task_type === "skill" ? { ...form, skill_id: e.target.value } : { ...form, mcp_server_id: e.target.value, tool_name: null })}>
            <option value="">选择扩展</option>{form.task_type === "skill" ? skills.data?.map(skill => <option key={skill.id} value={skill.id}>{skill.name}{skill.enabled ? "" : "（未启用）"}</option>) : servers.data?.map(server => <option key={server.id} value={server.id}>{server.display_name}{server.enabled ? "" : "（未启用）"}</option>)}
          </select></div> : <>
            <div className="space-y-2"><Label htmlFor="schedule-session-mode">结果会话</Label><select id="schedule-session-mode" className={selectClass} value={form.session_mode ?? "new"} onChange={e=>setForm({...form,session_mode:e.target.value as "new"|"existing"})}><option value="new">每次新建结果会话</option><option value="existing">在指定会话继续</option></select></div>
            {form.session_mode === "existing" ? <div className="space-y-2"><Label htmlFor="schedule-session-search">搜索会话</Label><Input id="schedule-session-search" placeholder="搜索会话标题" value={sessionSearch} onChange={e => setSessionSearch(e.target.value)} /><Label htmlFor="schedule-session">关联会话</Label><select id="schedule-session" className={selectClass} required value={form.target_session_id ?? ""} onChange={e => setForm({...form, target_session_id:e.target.value || null})}><option value="">选择会话</option>{form.target_session_id && !sessions.data?.some(session => session.id === form.target_session_id) ? <option value={form.target_session_id}>当前关联会话</option> : null}{sessions.data?.filter(session => session.id === form.target_session_id || (session.title || "未命名会话").toLowerCase().includes(sessionSearch.trim().toLowerCase())).map(session => <option key={session.id} value={session.id}>{session.title || "未命名会话"}</option>)}</select>{sessions.isError ? <p className="text-sm text-destructive">无法读取会话列表</p> : null}</div> : null}
            <div className="flex flex-wrap gap-5"><label className="flex items-center gap-2 text-sm"><Switch checked={form.agent_mode ?? true} onCheckedChange={value=>setForm({...form,agent_mode:value})}/>允许已授权工具</label><label className="flex items-center gap-2 text-sm"><Switch checked={form.web_search ?? false} onCheckedChange={value=>setForm({...form,web_search:value})}/>允许联网检索</label></div>
          </>}
          {form.task_type === "mcp" ? <div className="space-y-2"><Label htmlFor="schedule-tool">工具</Label><select id="schedule-tool" className={selectClass} required value={form.tool_name ?? ""} onChange={e => setForm({ ...form, tool_name: e.target.value })}><option value="">选择已审核的工具</option>{activeServer?.requested_tools.map(tool => <option key={tool} value={tool}>{tool}</option>)}</select></div> : null}
          {form.task_type === "skill" ? <div className="space-y-2">
            <Label htmlFor="schedule-script-path">技能脚本路径</Label>
            <Input id="schedule-script-path" value={scriptPath} required maxLength={500} onChange={event => setScriptPath(event.target.value)} />
            <Label htmlFor="schedule-script-arguments">脚本参数（可选，每行一项）</Label>
            <Textarea id="schedule-script-arguments" rows={3} value={scriptArguments} onChange={event => setScriptArguments(event.target.value)} />
          </div> : null}
          {form.task_type === "mcp" ? <details className="space-y-2 rounded-lg border p-3">
            <summary className="cursor-pointer text-sm">自定义执行参数（可选）</summary>
            <Label htmlFor="schedule-payload">高级参数（JSON）</Label>
            <Textarea id="schedule-payload" className="min-h-28 font-mono" dir="ltr" value={payloadText} onChange={event => setPayloadText(event.target.value)} />
          </details> : null}
          <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="schedule-timeout">执行超时（秒）</Label><Input id="schedule-timeout" type="number" min={1} max={form.task_type === "prompt" ? 3600 : 600} required value={form.timeout_seconds} onChange={e => setForm({ ...form, timeout_seconds: Number(e.target.value) })} /></div><label className="flex items-center gap-2 text-sm"><Switch checked={form.enabled} onCheckedChange={value=>setForm({...form,enabled:value})}/>启用计划</label></div>
          </div></details>
          {formError ? <p role="alert" className="text-sm text-destructive">{formError}</p> : null}
          </div>
          <div className="schedule-inspector__footer"><Button type="button" variant="outline" disabled={save.isPending} onClick={goToDetails}>取消</Button><Button type="submit" disabled={save.isPending}>{save.isPending ? "保存中…" : "保存任务"}</Button></div>
        </form>
      </> : current && inspector?.mode === "details" ? <div key="details" className="schedule-inspector__body schedule-details">
        <div className="schedule-details__intro">
          <p className="text-sm text-muted-foreground">{cadence(current)}{!current.trigger_type || current.trigger_type === "time" ? ` · ${timezoneLabel(current.timezone)}` : ""}</p>
          <div className="flex flex-wrap gap-2">
            {running && current.task_type !== "mcp" ? <Button variant="outline" disabled={isBusy} onClick={() => action.mutate({ kind: "cancel", task: current })}><Square />停止执行</Button> : <Button variant="outline" disabled={isBusy || running} onClick={() => action.mutate({ kind: "run", task: current })}><Play />立即运行</Button>}
            <Button variant="ghost" disabled={isBusy || running} title={running ? "执行结束后可编辑" : undefined} onClick={() => openEditor(current)}><Pencil />编辑任务</Button>
          </div>
        </div>
        <section>
          <h3>任务内容</h3>
          {current.task_type === "prompt" ? <p className="schedule-details__instruction">{current.prompt || current.description || "尚未填写任务指令"}</p> : <>
            {current.description ? <p className="schedule-details__instruction">{current.description}</p> : null}
            <p className="schedule-details__instruction">{current.task_type === "skill" ? "执行技能脚本" : `调用 MCP 工具${current.tool_name ? `：${current.tool_name}` : ""}`}{current.task_type === "skill" && typeof current.payload_json.script_path === "string" ? `：${current.payload_json.script_path}` : ""}</p>
          </>}
        </section>
        <section>
          <h3>下一次执行</h3>
          <p>{!current.enabled ? "未启用" : current.next_run_at ? date(current.next_run_at, current.timezone) : current.trigger_type && current.trigger_type !== "time" ? "等待触发" : "暂无计划"}</p>
        </section>
        <section className="schedule-details__recent">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3>最近执行</h3>
            <Button variant="ghost" size="sm" onClick={() => showInspector(current, "history")}>全部记录</Button>
          </div>
          {runs.isPending ? <LoadingState /> : runs.isError ? <ErrorState message={runs.error.message} onRetry={() => void runs.refetch()} /> : latest ? <div className="space-y-1 text-sm">
            <p data-status={latest.status}>{latest.error_code === "scheduled_prompt_timeout" ? "执行超时" : statusText[latest.status] ?? latest.status}</p>
            <p className="text-muted-foreground">{date(latest.started_at, current.timezone)} · {latest.trigger === "manual" ? "手动触发" : latest.trigger === "scheduled" ? "定时触发" : "事件触发"}</p>
            {typeof latest.result_json.chat_session_id === "string" ? <Link className="inline-block underline" to={`/w/${workspaceId}/chat/${latest.result_json.chat_session_id}`}>查看执行会话</Link> : null}
          </div> : <p className="text-sm text-muted-foreground">尚无执行记录</p>}
        </section>
      </div> : current && inspector?.mode === "history" ? <div key="history" className="schedule-inspector__body">
      {runs.isPending ? <LoadingState /> : runs.isError ? <ErrorState message={runs.error.message} onRetry={() => void runs.refetch()} /> : !runs.data?.length ? <p className="text-sm text-muted-foreground">尚无执行记录</p> : <ul className="space-y-3">{runs.data.map((run, index) => <li key={run.id} className="rounded-xl border p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2"><strong data-status={run.status}>{statusText[run.status] ?? run.status}</strong><span className="text-muted-foreground">{date(run.started_at, current.timezone, true)} · {run.trigger === "manual" ? "手动" : run.trigger === "scheduled" ? "定时" : "事件"}</span></div>
        {run.error_message ? <p className="mt-2 break-words text-destructive">{run.error_message}</p> : isFailure(run.status) ? <p className="mt-2 text-muted-foreground">服务端未提供失败原因。</p> : null}
        {typeof run.result_json.chat_session_id === "string" ? <Link className="mt-2 block underline" to={`/w/${workspaceId}/chat/${run.result_json.chat_session_id}`}>{run.status === "succeeded" ? "查看结果" : "打开执行会话"}</Link> : null}
        {index === 0 && isFailure(run.status) ? <Button className="mt-2" variant="outline" size="sm" disabled={isBusy || running} onClick={() => action.mutate({ kind: "run", task: current })}>手动重试</Button> : null}
        <details className="mt-3"><summary className="cursor-pointer text-muted-foreground">技术详情</summary><pre dir="ltr" className="mt-2 max-h-64 overflow-auto bg-muted p-3 text-xs">{JSON.stringify({ run_id: run.id, error_code: run.error_code, result: run.result_json }, null, 2)}</pre></details>
      </li>)}</ul>}
      </div> : null}
      </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
    <Dialog open={webhookTask !== null} onOpenChange={open=>{if(!open){setWebhookTask(null);setWebhookToken(null);}}}><DialogContent><DialogHeader><DialogTitle>Webhook：{webhookTask?.name}</DialogTitle><DialogDescription>专用令牌只展示一次。重新生成会立即撤销旧令牌，请勿放入 Prompt 或 URL。</DialogDescription></DialogHeader><p className="break-all text-sm">POST /api/v1/scheduled-tasks/{webhookTask?.id}/trigger</p><Button disabled={webhook.isPending} onClick={()=>{if(webhookTask)webhook.mutate(webhookTask.id);}}>生成 / 轮换令牌</Button>{webhookToken ? <div className="space-y-2"><p className="text-sm">请求头：{webhookToken.header}</p><Input readOnly type="password" aria-label="Webhook 令牌" value={webhookToken.token}/><Button variant="outline" onClick={()=>void navigator.clipboard.writeText(webhookToken.token)}>复制令牌</Button><pre className="overflow-auto bg-muted p-3 text-xs" dir="ltr">{'{"event_type":"external_update","payload":{},"event_id":"unique-id"}'}</pre></div> : null}</DialogContent></Dialog>
    <AlertDialog open={remove !== null} onOpenChange={open => { if (!open) setRemove(null); }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除定时任务？</AlertDialogTitle><AlertDialogDescription>删除“{remove?.name}”及其执行记录，不会撤销已产生的外部操作。</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction disabled={action.isPending} onClick={e => { e.preventDefault(); if (remove) action.mutate({ kind: "delete", task: remove }); }}>删除任务</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    {confirmDialog}
  </>;
}
