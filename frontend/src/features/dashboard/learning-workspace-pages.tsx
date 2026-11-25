import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight, Plus } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { createAction, listActions, listGraphs, updateAction } from "@/api";
import { ActivityHeatmap } from "@/components/shared/activity-heatmap";
import { ErrorState, LoadingState, PageFrame } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/lib/notifications";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { ActionItem } from "@/types/workflow";
import { partitionPlan, planPath, shortDate } from "./dashboard-model";
import "./dashboard.css";

function HomeLink({ base }: { base: string }) {
  return <Link className="study-text-link self-start" to={`${base}/home`}><ArrowLeft aria-hidden="true" size={15} />我的学习空间</Link>;
}

export function LearningReportPage() {
  const { workspaceId = "" } = useParams();
  return <PageFrame className="study-home">
    <HomeLink base={`/w/${workspaceId}`} />
    <header><h1 data-page-title className="text-2xl font-semibold">学习报告</h1>
      <p className="mt-1 text-sm text-muted-foreground">活动记录包含创建会话、计划行动和完成行动，反映使用活跃度，不代表知识掌握程度。</p></header>
    <div className="study-card study-activity"><ActivityHeatmap variant="report" /></div>
  </PageFrame>;
}

export function LearningPlanPage() {
  const { workspaceId = "" } = useParams();
  const base = `/w/${workspaceId}`;
  const client = useQueryClient();
  const actionsKey = workspaceQueryKey(workspaceId, "actions");
  const actions = useQuery({ queryKey: actionsKey, queryFn: listActions });
  const graphs = useQuery({ queryKey: workspaceQueryKey(workspaceId, "graphs"), queryFn: listGraphs, staleTime: 30_000 });
  const graphTitles = new Map(graphs.data?.map((graph) => [graph.id, graph.title]));
  const [title, setTitle] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [date, setDate] = useState("");
  const [graphId, setGraphId] = useState("");
  const now = new Date();
  const todayKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  function refresh(action: ActionItem) {
    void client.invalidateQueries({ queryKey: actionsKey });
    void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "dashboard") });
    if (action.goal_id) void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "roadmap", action.goal_id) });
    if (action.graph_id) void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graph", action.graph_id) });
  }
  const create = useMutation({
    mutationFn: () => createAction(title.trim(), graphId || undefined),
    onSuccess: (action) => { setTitle(""); refresh(action); toast.success("已添加学习任务"); },
    onError: (error) => toast.error(error.message),
  });
  const update = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: Parameters<typeof updateAction>[1] }) => updateAction(id, changes),
    onSuccess: (action) => { setEditingId(null); refresh(action); toast.success(action.status === "archived" ? "任务已归档" : action.status === "completed" ? "已完成学习任务" : "已重新安排"); },
    onError: (error) => toast.error(error.message),
  });
  const { current, historical } = partitionPlan(actions.data ?? [], now);

  function taskList(items: ActionItem[], history = false) {
    if (!items.length) return <p className="study-empty">{history ? "没有需要整理的历史任务。" : "没有安排中的学习任务。"}</p>;
    const groups = [...new Set(items.map((item) => item.graph_id))];
    return <div className="divide-y">{groups.map((id) => <div className="px-4 py-3" key={id ?? "personal"}>
      <h3 className="text-xs font-medium text-muted-foreground">{id && graphTitles.has(id) ? <Link className="study-text-link" to={planPath(base, id)}>{graphTitles.get(id)}<ArrowUpRight aria-hidden="true" size={12} /></Link> : id ? "学习图谱" : "个人任务"}</h3>
      <ul className="mt-1 divide-y">{items.filter((task) => task.graph_id === id).map((item) => <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
        <span className="min-w-0 break-words">{item.title}{history && item.due_at && <small className="ml-2 text-xs text-muted-foreground">{shortDate(item.due_at, now)}</small>}</span>
        {history ? <div className="flex flex-wrap items-center gap-2">
          {item.roadmap_id ? (item.graph_id || item.goal_id) && <Button asChild size="sm" variant="ghost"><Link to={item.graph_id ? planPath(base, item.graph_id) : `${base}/goals/${encodeURIComponent(item.goal_id!)}/roadmap`}>前往计划重新安排</Link></Button> : editingId === item.id ?
            <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => {
              event.preventDefault();
              const due = new Date(`${date}T12:00:00`);
              if (date >= todayKey && Number.isFinite(+due)) update.mutate({ id: item.id, changes: { due_at: due.toISOString() } });
            }}>
              <Input aria-label={`重新安排日期：${item.title}`} className="w-auto" type="date" required min={todayKey} value={date} onChange={(event) => setDate(event.target.value)} />
              <Button size="sm" type="submit" disabled={update.isPending}>保存</Button>
              <Button size="sm" type="button" variant="ghost" onClick={() => setEditingId(null)}>取消</Button>
            </form> : <Button size="sm" variant="ghost" onClick={() => { setDate(todayKey); setEditingId(item.id); }}>重新安排</Button>}
          <Button size="sm" variant="outline" disabled={update.isPending} onClick={() => update.mutate({ id: item.id, changes: { status: "archived" } })}>归档</Button>
        </div> : <Button size="sm" variant="ghost" disabled={update.isPending} onClick={() => update.mutate({ id: item.id, changes: { status: "completed" } })}>标记完成</Button>}
      </li>)}</ul>
    </div>)}</div>;
  }

  return <PageFrame className="study-home">
    <HomeLink base={base} />
    <h1 data-page-title className="text-2xl font-semibold">学习计划</h1>
    <section id="add-task" aria-label="添加学习任务" className="study-card p-4">
      <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => { event.preventDefault(); if (title.trim()) create.mutate(); }}>
        <Input className="min-w-40 flex-1" aria-label="学习任务名称" placeholder="想学什么？" maxLength={240} value={title} onChange={(event) => setTitle(event.target.value)} />
        <select aria-label="所属图谱" className="h-9 max-w-full rounded-md border bg-background px-2 text-sm sm:max-w-48" value={graphId} onChange={(event) => setGraphId(event.target.value)}>
          <option value="">个人任务</option>
          {graphs.data?.filter((graph) => graph.status === "published").map((graph) => <option key={graph.id} value={graph.id}>{graph.title}</option>)}
        </select>
        <Button size="sm" type="submit" disabled={!title.trim() || create.isPending}><Plus aria-hidden="true" size={15} />添加学习任务</Button>
      </form>
    </section>
    {actions.isPending ? <LoadingState label="正在读取学习计划…" /> : actions.isError ? <ErrorState message={actions.error.message} onRetry={() => void actions.refetch()} /> : <>
      <section className="study-section"><h2 className="study-section__heading">当前任务</h2><div className="study-card">{taskList(current)}</div></section>
      <details className="study-card"><summary className="cursor-pointer px-4 py-3 text-sm font-semibold">历史任务（{historical.length}）</summary>
        {taskList(historical, true)}
      </details>
    </>}
  </PageFrame>;
}
