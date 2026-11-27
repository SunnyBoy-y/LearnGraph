import { useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowLeft, ArrowUpRight, CalendarDays, CalendarPlus, Check, ChevronDown, ChevronRight, CircleCheck, Ellipsis, Network, Plus, RotateCcw, SquareCheck, UserRound } from "lucide-react";
import { Tabs } from "radix-ui";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";

import { createAction, listActions, listGraphs, updateAction } from "@/api";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import { ErrorState, LoadingState, PageFrame } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useListSelection } from "@/lib/list-selection";
import { toast } from "@/lib/notifications";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { ActionItem } from "@/types/workflow";
import { partitionPlan, planPath, shortDate } from "./dashboard-model";
import "./learning-plan.css";

const tabs = [
  { id: "current", label: "当前任务" },
  { id: "history", label: "历史任务" },
  { id: "archived", label: "已归档" },
] as const;
type Tab = (typeof tabs)[number]["id"];
const sections = [
  { id: "today", label: "今天" },
  { id: "next", label: "接下来" },
  { id: "unscheduled", label: "未安排日期" },
] as const;
type Section = (typeof sections)[number]["id"];
type DateChoice = "today" | "tomorrow" | "custom" | "none";
type Draft = { section: Section; title: string; graphId: string; dateChoice: DateChoice; date: string };

function dateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function localDate(date: string) {
  return new Date(`${date}T00:00:00`);
}

function newDraft(section: Section): Draft {
  return { section, title: "", graphId: "", dateChoice: section === "next" ? "tomorrow" : section === "unscheduled" ? "none" : "today", date: "" };
}

function currentSection(item: ActionItem, today: string): Section {
  const date = new Date(item.due_at ?? "");
  return !Number.isFinite(+date) ? "unscheduled" : dateKey(date) === today ? "today" : "next";
}

function taskDate(value: string, now: Date, isCurrent: boolean) {
  const date = new Date(value);
  if (!Number.isFinite(+date)) return "";
  if (isCurrent && dateKey(date) === dateKey(now) && (date.getHours() || date.getMinutes())) {
    return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
  }
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  return isCurrent && dateKey(date) === dateKey(tomorrow) ? "明天" : shortDate(value, now);
}

function taskTitle(item: ActionItem) {
  return item.title.replace(/^\s*(?:学习\s*[:：]\s*)+/u, "").trim() || item.title;
}

function PlanGroup({ title, items, path, personal, initiallyOpen, renderRow }: {
  title: string; items: ActionItem[]; path?: string; personal: boolean; initiallyOpen: boolean;
  renderRow: (item: ActionItem) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(initiallyOpen);
  const listId = useId();
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const Icon = personal ? UserRound : Network;
  return <li className="learning-plan__group">
    <div className="learning-plan__group-heading">
      <button type="button" className="learning-plan__group-toggle" aria-expanded={expanded} aria-controls={listId} onClick={() => setExpanded(!expanded)}>
        <Chevron size={14} aria-hidden="true" /><Icon size={17} aria-hidden="true" />
        <span className="learning-plan__truncate" title={title}>{title}</span><span className="learning-plan__count">{items.length}<span className="sr-only"> 项任务</span></span>
      </button>
      {path && <Link className="learning-plan__icon-button" to={path} aria-label={`打开图谱计划：${title}`} title="打开图谱计划"><ArrowUpRight size={15} aria-hidden="true" /></Link>}
    </div>
    <ul id={listId} className="learning-plan__branch" hidden={!expanded}>{expanded && items.map(renderRow)}</ul>
  </li>;
}

function GroupedTasks({ items, graphTitles, base, search, renderRow }: {
  items: ActionItem[]; graphTitles: Map<string, string>; base: string; search?: string;
  renderRow: (item: ActionItem) => ReactNode;
}) {
  const query = search?.trim().toLocaleLowerCase() ?? "";
  const groups = new Map<string | null, ActionItem[]>();
  function groupTitle(id: string | null) {
    return id ? graphTitles.get(id) ?? "学习图谱" : "个人任务";
  }
  for (const item of items) {
    const id = item.graph_id ?? null;
    if (query && !taskTitle(item).toLocaleLowerCase().includes(query) && !groupTitle(id).toLocaleLowerCase().includes(query)) continue;
    const group = groups.get(id);
    if (group) group.push(item);
    else groups.set(id, [item]);
  }
  if (!groups.size) return <p className="learning-plan__empty">{query ? "没有匹配的任务。" : search === undefined ? "暂无已归档的学习任务。" : "暂无历史任务。"}</p>;
  return <ul className="learning-plan__groups">{[...groups].map(([id, tasks]) => <PlanGroup
    key={`${query ? "search" : "browse"}:${id ?? "personal"}`} title={groupTitle(id)} items={tasks}
    path={id && graphTitles.has(id) ? planPath(base, id) : undefined} personal={!id} initiallyOpen={!!query} renderRow={renderRow}
  />)}</ul>;
}

export function LearningPlanPage() {
  const { workspaceId = "" } = useParams();
  const { hash } = useLocation();
  return <LearningPlan key={`${workspaceId}:${hash}`} workspaceId={workspaceId} addOnEntry={hash === "#add-task"} />;
}

function LearningPlan({ workspaceId, addOnEntry }: { workspaceId: string; addOnEntry: boolean }) {
  const base = `/w/${workspaceId}`;
  const client = useQueryClient();
  const actionsKey = workspaceQueryKey(workspaceId, "actions");
  const actions = useQuery({ queryKey: actionsKey, queryFn: listActions });
  const graphs = useQuery({ queryKey: workspaceQueryKey(workspaceId, "graphs"), queryFn: listGraphs, staleTime: 30_000 });
  const graphTitles = useMemo(
    () => new Map(graphs.data?.map((graph) => [graph.id, graph.title])),
    [graphs.data],
  );
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab") || "current";
  const tab: Tab = tabParam === "history" || tabParam === "archived" ? tabParam : "current";
  const search = searchParams.get("q") ?? "";
  const updatePlanParams = (patch: Record<string, string>, replace = false) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace, preventScrollReset: true });
    }
  };
  const setTab = (nextTab: Tab) => updatePlanParams({ tab: nextTab === "current" ? "" : nextTab, q: "" });
  const setSearch = (nextSearch: string) => updatePlanParams({ q: nextSearch }, true);
  const [draft, setDraft] = useState<Draft | null>(() => addOnEntry ? newDraft("today") : null);
  const [reschedule, setReschedule] = useState<{ id: string; date: string } | null>(null);
  const [batchDate, setBatchDate] = useState("");
  const titleInput = useRef<HTMLInputElement>(null);
  const newTaskButton = useRef<HTMLButtonElement>(null);
  const now = new Date();
  const today = dateKey(now);
  const nextDay = new Date(now);
  nextDay.setDate(nextDay.getDate() + 1);
  const tomorrow = dateKey(nextDay);

  function refresh(action: ActionItem) {
    client.setQueryData<ActionItem[]>(actionsKey, (items) => items && (items.some((item) => item.id === action.id)
      ? items.map((item) => item.id === action.id ? action : item) : [...items, action]));
    void client.invalidateQueries({ queryKey: actionsKey });
    void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "dashboard") });
    if (action.goal_id) void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "roadmap", action.goal_id) });
    if (action.graph_id) void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graph", action.graph_id) });
  }
  function closeEditor() {
    setDraft(null);
    newTaskButton.current?.focus();
  }
  const create = useMutation({
    mutationFn: ({ title, graphId, due }: { title: string; graphId: string; due?: string }) => createAction(title, graphId || undefined, due),
    onSuccess: (action) => { closeEditor(); refresh(action); toast.success("已添加学习任务"); },
    onError: (error) => toast.error(error.message),
  });
  const update = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Parameters<typeof updateAction>[1] }) => updateAction(id, changes),
    onSuccess: (action) => {
      setReschedule((editing) => editing?.id === action.id ? null : editing);
      refresh(action);
      toast.success(action.status === "archived" ? "任务已归档" : action.status === "completed" ? "已完成学习任务" : action.status === "pending" ? "任务已恢复" : "已重新安排");
    },
    onError: (error) => toast.error(error.message),
  });
  const all = useMemo(() => actions.data ?? [], [actions.data]);
  const { current, historical } = partitionPlan(all, now);
  const history = useMemo(
    () => [...historical, ...all.filter((item) => item.status === "completed")],
    [all, historical],
  );
  const archived = useMemo(
    () => all.filter((item) => item.status === "archived"),
    [all],
  );
  const currentGroups: Record<Section, ActionItem[]> = { today: [], next: [], unscheduled: [] };
  for (const item of current) currentGroups[currentSection(item, today)].push(item);

  const tabItems = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    const list = tab === "current" ? current : tab === "history" ? history : archived;
    if (!query || tab === "current") return list;
    return list.filter((item) => {
      const gTitle = item.graph_id ? graphTitles.get(item.graph_id) ?? "学习图谱" : "个人任务";
      return taskTitle(item).toLocaleLowerCase().includes(query) || gTitle.toLocaleLowerCase().includes(query);
    });
  }, [archived, current, graphTitles, history, search, tab]);
  const visiblePlanIds = useMemo(() => tabItems.map((item) => item.id), [tabItems]);
  const selection = useListSelection(visiblePlanIds);
  const selectedPlanItems = useMemo(
    () => (actions.data ?? []).filter((item) => selection.selectedIds.has(item.id)),
    [actions.data, selection.selectedIds],
  );
  const batchUpdate = useMutation({
    mutationFn: async ({
      targets,
      changes,
      label,
    }: {
      targets: ActionItem[];
      changes: Parameters<typeof updateAction>[1];
      label: string;
    }) => {
      const results = await Promise.allSettled(
        targets.map((item) => updateAction(item.id, changes)),
      );
      const updated: ActionItem[] = [];
      for (const result of results) {
        if (result.status === "fulfilled") updated.push(result.value);
      }
      return { updated, failed: targets.length - updated.length, label };
    },
    onSuccess: ({ updated, failed, label }) => {
      for (const item of updated) refresh(item);
      selection.removeIds(updated.map((item) => item.id));
      if (failed === 0) toast.success(`已${label} ${updated.length} 项任务`);
      else if (updated.length > 0) toast.warning(`已${label} ${updated.length} 项任务，${failed} 项失败`);
      else toast.error(`批量${label}失败`);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "批量操作失败"),
  });
  const busy = update.isPending || batchUpdate.isPending;

  function startEditor(section?: Section) {
    setTab("current");
    if (draft && (!section || draft.section === section)) titleInput.current?.focus();
    else setDraft({ ...newDraft(section ?? "today"), title: draft?.title ?? "", graphId: draft?.graphId ?? "" });
  }

  function editor() {
    if (!draft) return null;
    const chosen = draft.dateChoice === "today" ? today : draft.dateChoice === "tomorrow" ? tomorrow : draft.date;
    const due = draft.dateChoice === "none" ? undefined : localDate(chosen);
    const valid = !due || (chosen >= today && Number.isFinite(+due));
    return <form id="add-task" className="learning-plan__editor" aria-label="添加学习任务" onKeyDown={(event) => {
      if (event.key === "Escape" && !create.isPending) { event.stopPropagation(); closeEditor(); }
    }} onSubmit={(event) => {
      event.preventDefault();
      if (draft.title.trim() && valid && !create.isPending) create.mutate({ title: draft.title.trim(), graphId: draft.graphId, due: due?.toISOString() });
    }}>
      <input autoFocus ref={titleInput} className="learning-plan__editor-title" aria-label="学习任务名称" placeholder="想学什么？" maxLength={240}
        value={draft.title} disabled={create.isPending} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
      <div className="learning-plan__editor-footer">
        <div className="learning-plan__editor-options">
          <label className="learning-plan__picker"><CalendarDays size={15} aria-hidden="true" />
            <select aria-label="任务日期" value={draft.dateChoice} disabled={create.isPending} onChange={(event) => setDraft({ ...draft, dateChoice: event.target.value as DateChoice, date: draft.date || (draft.section === "next" ? tomorrow : today) })}>
              <option value="today">今天</option><option value="tomorrow">明天</option><option value="custom">选择日期</option><option value="none">不设日期</option>
            </select><ChevronDown size={12} aria-hidden="true" />
          </label>
          {draft.dateChoice === "custom" && <input className="learning-plan__date-input" type="date" aria-label="自定义任务日期" min={today} required value={draft.date} disabled={create.isPending} onChange={(event) => setDraft({ ...draft, date: event.target.value })} />}
          <label className="learning-plan__picker learning-plan__picker--graph"><Network size={15} aria-hidden="true" />
            <select aria-label="所属图谱" value={draft.graphId} disabled={create.isPending} onChange={(event) => setDraft({ ...draft, graphId: event.target.value })}>
              <option value="">个人任务</option>
              {graphs.data?.filter((graph) => graph.status === "published").map((graph) => <option key={graph.id} value={graph.id}>{graph.title}</option>)}
            </select><ChevronDown size={12} aria-hidden="true" />
          </label>
        </div>
        <div className="learning-plan__editor-buttons">
          <Button size="sm" type="button" variant="ghost" disabled={create.isPending} onClick={closeEditor}>取消</Button>
          <Button size="sm" type="submit" disabled={!draft.title.trim() || !valid || create.isPending}>{create.isPending ? "正在添加…" : "添加任务"}</Button>
        </div>
      </div>
    </form>;
  }

  function renderRow(item: ActionItem, variant: Tab) {
    const active = variant === "current";
    const title = active ? item.title : taskTitle(item);
    const completed = item.status === "completed";
    const actionable = variant !== "archived" && !completed;
    const date = completed ? item.completed_at ?? item.due_at : item.due_at;
    const shownDate = date && taskDate(date, now, active);
    const graphTitle = item.graph_id ? graphTitles.get(item.graph_id) ?? "学习图谱" : "个人任务";
    const path = item.graph_id ? planPath(base, item.graph_id) : item.goal_id ? `${base}/goals/${encodeURIComponent(item.goal_id)}/roadmap` : undefined;
    const rescheduleLabel = `重新安排：${title}`;
    const editing = reschedule?.id === item.id ? reschedule : null;
    const selected = selection.multiSelect && selection.selectedIds.has(item.id);
    function beginReschedule() { setReschedule({ id: item.id, date: active && item.due_at ? dateKey(new Date(item.due_at)) : today }); }
    return <li key={item.id} className={`learning-plan__item ${selected ? "rounded-md bg-primary/5" : ""}`}>
      <div className="flex items-center gap-2">
        {selection.multiSelect ? (
          <Checkbox
            aria-label={`选择任务：${title}`}
            className="ml-1 shrink-0"
            disabled={busy}
            {...selection.checkboxProps(item.id)}
          />
        ) : null}
        <div className={`learning-plan__row min-w-0 flex-1${active ? " learning-plan__row--current" : ""}`}>
          {active && <button className="learning-plan__icon-button learning-plan__check" type="button" aria-label={`完成任务：${title}`} title="标记完成" disabled={busy} onClick={() => update.mutate({ id: item.id, changes: { status: "completed" } })}>
            <span className="learning-plan__check-mark"><Check size={12} aria-hidden="true" /></span>
          </button>}
          <div className="learning-plan__row-text">
            <span className="learning-plan__task-title learning-plan__truncate" title={title}>{title}</span>
            {active && <span className="learning-plan__meta learning-plan__truncate">{item.graph_id && path ? <Link to={path} title={graphTitle}>{graphTitle}</Link> : graphTitle}{item.status === "in_progress" && " · 进行中"}</span>}
          </div>
          {completed && <span className="learning-plan__meta learning-plan__completed"><Check size={13} aria-hidden="true" /><span>已完成</span></span>}
          <span className="learning-plan__date">{date && shownDate && <time dateTime={date}>{shownDate}</time>}</span>
          <div className="learning-plan__row-actions">
            {variant === "archived" && (
              <Button
                aria-label={`恢复任务：${title}`}
                disabled={busy}
                onClick={() => update.mutate({ id: item.id, changes: { status: "pending" } })}
                size="icon-xs"
                title="恢复到当前计划"
                type="button"
                variant="ghost"
              >
                <RotateCcw className="size-3.5" />
              </Button>
            )}
            {!active && actionable && !editing && (item.roadmap_id ? path && <Link className="learning-plan__icon-button" to={path} aria-label={rescheduleLabel} title="前往计划重新安排"><CalendarPlus size={16} aria-hidden="true" /></Link> : <button type="button" className="learning-plan__icon-button" aria-label={rescheduleLabel} title="重新安排" disabled={busy} onClick={beginReschedule}><CalendarPlus size={16} aria-hidden="true" /></button>)}
            {actionable && <DropdownMenu modal={false}>
              <DropdownMenuTrigger className="learning-plan__icon-button" aria-label={`更多操作：${title}`} title="更多操作" disabled={busy}><Ellipsis size={17} aria-hidden="true" /></DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {active && (item.roadmap_id ? path && <DropdownMenuItem asChild><Link to={path}><CalendarPlus aria-hidden="true" />重新安排</Link></DropdownMenuItem> : <DropdownMenuItem onSelect={beginReschedule}><CalendarPlus aria-hidden="true" />重新安排</DropdownMenuItem>)}
                <DropdownMenuItem onSelect={() => update.mutate({ id: item.id, changes: { status: "archived" } })}><Archive aria-hidden="true" />归档</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>}
          </div>
        </div>
      </div>
      {editing && <form className="learning-plan__reschedule" onSubmit={(event) => {
        event.preventDefault();
        const due = localDate(editing.date);
        if (!busy && editing.date >= today && Number.isFinite(+due)) update.mutate({ id: item.id, changes: { due_at: due.toISOString() } });
      }}>
        <input autoFocus className="learning-plan__date-input" type="date" aria-label={`重新安排日期：${title}`} min={today} required disabled={busy} value={editing.date} onChange={(event) => setReschedule({ id: item.id, date: event.target.value })} />
        <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setReschedule(null)}>取消</Button>
        <Button size="sm" type="submit" disabled={busy}>保存</Button>
      </form>}
    </li>;
  }

  const quickAdd = (section: Section) => <button type="button" className="learning-plan__quick-add" disabled={create.isPending} onClick={() => startEditor(section)}><Plus size={16} aria-hidden="true" />添加任务</button>;
  const visibleSections = sections.filter(({ id }) => currentGroups[id].length || draft?.section === id);
  const counts = { current: current.length, history: history.length, archived: archived.length };
  const completableSelected = selectedPlanItems.filter((item) => item.status !== "completed" && item.status !== "archived");
  const archivableSelected = selectedPlanItems.filter((item) => item.status !== "archived");
  const restorableSelected = selectedPlanItems.filter((item) => item.status === "archived");
  const reschedulableSelected = selectedPlanItems.filter((item) => item.status !== "archived" && item.status !== "completed" && !item.roadmap_id);

  return <PageFrame className="learning-plan">
    <Link to={`${base}/home`} className="learning-plan__back"><ArrowLeft size={15} aria-hidden="true" />我的学习空间</Link>
    <header className="learning-plan__header">
      <h1 data-page-title>学习计划</h1>
      <div className="flex flex-wrap items-center gap-2">
        {all.length > 0 ? (
          <Button
            aria-pressed={selection.multiSelect}
            disabled={busy}
            onClick={selection.toggleMultiSelect}
            size="sm"
            type="button"
            variant={selection.multiSelect ? "secondary" : "outline"}
          >
            <SquareCheck size={15} aria-hidden="true" />
            {selection.multiSelect ? "退出多选" : "多选"}
          </Button>
        ) : null}
        <Button ref={newTaskButton} size="sm" variant="outline" type="button" disabled={!actions.data || create.isPending} onClick={() => startEditor()}><Plus size={15} aria-hidden="true" />新建任务</Button>
      </div>
    </header>
    {selection.multiSelect && tabItems.length > 0 ? (
      <div
        aria-label="学习任务批量操作"
        className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 px-3 py-2 text-xs"
        role="region"
      >
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            aria-label="全选当前列表"
            checked={selection.visibleCheckboxState}
            disabled={busy}
            onCheckedChange={(checked) => selection.toggleVisible(checked === true)}
          />
          全选当前列表
        </label>
        <span className="tabular-nums text-muted-foreground">
          已选 {selection.selectedCount} 项
          {selection.otherSelectedCount > 0 ? `（另有 ${selection.otherSelectedCount} 项在其他标签或筛选）` : ""}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {tab !== "archived" ? (
            <>
              <Button
                disabled={busy || !completableSelected.length}
                onClick={() => batchUpdate.mutate({ targets: completableSelected, changes: { status: "completed" }, label: "完成" })}
                size="xs"
                type="button"
                variant="outline"
              >
                <Check className="size-3.5" />
                批量完成
              </Button>
              {reschedulableSelected.length > 0 ? (
                <div className="flex items-center gap-1">
                  <input
                    aria-label="批量改期日期"
                    className="learning-plan__date-input !h-7 !min-h-0 text-xs"
                    min={today}
                    onChange={(event) => setBatchDate(event.target.value)}
                    type="date"
                    value={batchDate || tomorrow}
                  />
                  <Button
                    disabled={busy}
                    onClick={() => {
                      const targetDate = batchDate || tomorrow;
                      const due = localDate(targetDate);
                      if (targetDate >= today && Number.isFinite(+due)) {
                        batchUpdate.mutate({
                          targets: reschedulableSelected,
                          changes: { due_at: due.toISOString() },
                          label: "改期",
                        });
                      }
                    }}
                    size="xs"
                    type="button"
                    variant="outline"
                  >
                    批量改期
                  </Button>
                </div>
              ) : null}
              <Button
                disabled={busy || !archivableSelected.length}
                onClick={() => batchUpdate.mutate({ targets: archivableSelected, changes: { status: "archived" }, label: "归档" })}
                size="xs"
                type="button"
                variant="outline"
              >
                <Archive className="size-3.5" />
                批量归档
              </Button>
            </>
          ) : (
            <Button
              disabled={busy || !restorableSelected.length}
              onClick={() => batchUpdate.mutate({ targets: restorableSelected, changes: { status: "pending" }, label: "恢复" })}
              size="xs"
              type="button"
              variant="outline"
            >
              <RotateCcw className="size-3.5" />
              批量恢复
            </Button>
          )}
          {selection.selectedCount > 0 ? (
            <Button disabled={busy} onClick={selection.clear} size="xs" type="button" variant="ghost">
              取消选择
            </Button>
          ) : null}
        </div>
      </div>
    ) : null}
    <Tabs.Root value={tab} onValueChange={(value) => { setTab(value as Tab); setReschedule(null); }}>
      <Tabs.List className="learning-plan__tabs" aria-label="学习任务">
        {tabs.map(({ id, label }) => {
          const count = actions.data && id !== "archived" ? counts[id] : undefined;
          return <Tabs.Trigger key={id} className="learning-plan__tab" value={id} aria-label={count === undefined ? label : `${label}，${count} 项`}>{label}{count !== undefined && <span className="learning-plan__count" aria-hidden="true">{count}</span>}</Tabs.Trigger>;
        })}
      </Tabs.List>
      {actions.isPending ? <LoadingState label="正在读取学习计划…" /> : actions.isError && !actions.data ? <ErrorState message={actions.error.message} onRetry={() => void actions.refetch()} /> : <>
        <Tabs.Content className="learning-plan__panel" value="current">
          {!current.length ? <>
            <p className="learning-plan__empty"><CircleCheck size={16} aria-hidden="true" />暂无安排中的学习任务</p>
            {draft ? editor() : quickAdd("today")}
          </> : visibleSections.map(({ id, label }) => <section className="learning-plan__section" key={id} aria-label={label}>
            <h2 className="learning-plan__section-title">{label}<span className="learning-plan__count">{currentGroups[id].length} 项任务</span></h2>
            {currentGroups[id].length > 0 && <ul className="learning-plan__current-list">{currentGroups[id].map((item) => renderRow(item, "current"))}</ul>}
            {draft?.section === id ? editor() : quickAdd(id)}
          </section>)}
        </Tabs.Content>
        <Tabs.Content className="learning-plan__panel" value="history">
          {history.length > 0 && <div className="learning-plan__history-tools">
            <ClearableSearchInput
              aria-label="搜索历史任务"
              containerClassName="w-64 max-w-full"
              inputClassName="h-8 text-xs"
              onChange={setSearch}
              placeholder="搜索历史任务"
              type="search"
              value={search}
            />
            <span className="learning-plan__meta">按学习图谱分组</span>
          </div>}
          <GroupedTasks items={history} graphTitles={graphTitles} base={base} search={search} renderRow={(item) => renderRow(item, "history")} />
        </Tabs.Content>
        <Tabs.Content className="learning-plan__panel" value="archived">
          {archived.length > 0 && <div className="learning-plan__history-tools">
            <ClearableSearchInput
              aria-label="搜索已归档任务"
              containerClassName="w-64 max-w-full"
              inputClassName="h-8 text-xs"
              onChange={setSearch}
              placeholder="搜索已归档任务"
              type="search"
              value={search}
            />
            <span className="learning-plan__meta">按学习图谱分组</span>
          </div>}
          <GroupedTasks items={archived} graphTitles={graphTitles} base={base} search={search || undefined} renderRow={(item) => renderRow(item, "archived")} />
        </Tabs.Content>
      </>}
    </Tabs.Root>
  </PageFrame>;
}
