import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowLeft, ArrowRight, ArrowUpRight, ChevronRight, MessageCircle, Network, Plus, SquareCheck, Trash2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  archiveSession,
  deleteSession,
  deleteSessionsBatch,
  getDashboard,
  getSessionBatchDeleteImpact,
  getSessionDeleteImpact,
  listActions,
  listGraphs,
  listPracticeSessions,
  updateAction,
} from "@/api";
import { listSessionPage, type SessionSummary } from "@/api/session-browser";
import { ActivityHeatmap } from "@/components/shared/activity-heatmap";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import { DeleteImpactDialog } from "@/components/shared/delete-impact-dialog";
import { ErrorState, LoadingState, PageFrame } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useListSelection } from "@/lib/list-selection";
import { toast } from "@/lib/notifications";
import { workspaceQueryKey } from "@/lib/query-keys";
import { scheduleSessionRefresh } from "@/lib/session-refresh";
import type { GraphSummary } from "@/types/graphs";
import { partitionPlan, planPath, recommend, sessionScope, shortDate } from "./dashboard-model";
import "./dashboard.css";

function useRecentSessions(workspaceId: string) {
  return useInfiniteQuery({
    queryKey: workspaceQueryKey(workspaceId, "sessions", "dashboard-recent"),
    queryFn: ({ pageParam, signal }) => listSessionPage(sessionScope, pageParam, { signal }),
    initialPageParam: {} as { cursor?: string },
    getNextPageParam: (page) => page.has_more && page.next_cursor ? { cursor: page.next_cursor } : undefined,
    staleTime: 30_000,
  });
}

function useGraphs(workspaceId: string) {
  return useQuery({ queryKey: workspaceQueryKey(workspaceId, "graphs"), queryFn: listGraphs, staleTime: 30_000 });
}

function HeroArt({ tone }: { tone: "study" | "practice" | "start" }) {
  return <svg className="study-hero__art" viewBox="0 0 350 210" fill="none" aria-hidden="true">
    <circle cx="190" cy="105" r="103" stroke="currentColor" opacity=".14" />
    <circle cx="190" cy="105" r="81" stroke="currentColor" opacity=".28" />
    <circle cx="190" cy="105" r="58" stroke="currentColor" strokeDasharray="3 6" opacity=".45" />
    {tone === "practice" ? <>
      <rect x="155" y="53" width="70" height="104" rx="13" className="study-hero__node" />
      {[78, 104, 130].map((y, index) => <g key={y}>
        <rect x="168" y={y - 5} width="11" height="11" rx="3" stroke="currentColor" fill={index < 2 ? "currentColor" : "none"} />
        <path d={`M188 ${y}h22`} stroke="currentColor" strokeWidth="3" strokeLinecap="round" opacity=".44" />
      </g>)}
    </> : <>
      <path d="M190 105 111 54 126 157 190 105 266 151 259 52 190 105" stroke="currentColor" opacity=".45" />
      <circle cx="190" cy="105" r="43" stroke="currentColor" opacity=".24" />
      <circle cx="190" cy="105" r="33" fill="currentColor" />
      {[[111, 54], [126, 157], [266, 151], [259, 52]].map(([x, y], index) => <g key={x}>
        <circle cx={x} cy={y} r="13" className="study-hero__node" />
        {tone === "study" && (index === 0 || index === 3)
          ? <path d={`M${x - 4} ${y}l3 3 5-6`} stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          : <circle cx={x} cy={y} r="3.5" fill="currentColor" opacity=".55" />}
      </g>)}
      {tone === "start" ? <path d="M179 105h22m-11-11v22" className="study-hero__glyph" /> : <g className="study-hero__glyph">
        <rect x="182" y="87" width="16" height="12" rx="2" /><path d="M190 99v9m-12 5v-5h24v5" />
        <rect x="171" y="113" width="14" height="11" rx="2" /><rect x="195" y="113" width="14" height="11" rx="2" />
      </g>}
    </>}
  </svg>;
}

function SessionRows({ items, graphs, base, today }: { items: SessionSummary[]; graphs: Map<string, GraphSummary>; base: string; today: Date }) {
  return <ul className="study-list">
    {items.map((item, index) => {
      const graphTitle = item.graph_id ? graphs.get(item.graph_id)?.title ?? "图谱会话" : "普通会话";
      const Icon = item.graph_id ? Network : MessageCircle;
      return <li key={item.id}><Link className="study-row study-row--link" to={`${base}/chat/${encodeURIComponent(item.id)}`}>
        <span className="study-icon" data-tone={item.graph_id ? (index % 2 === 0 ? "blue" : "green") : "violet"}><Icon aria-hidden="true" size={17} /></span>
        <span className="min-w-0 flex-1"><span className="study-row__title" title={item.title}>{item.title || "未命名会话"}</span>
          <span className="study-row__meta">{[graphTitle, shortDate(item.updated_at, today)].filter(Boolean).join(" · ")}</span></span>
        <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
      </Link></li>;
    })}
  </ul>;
}

export function DashboardPage() {
  const { workspaceId = "" } = useParams();
  const base = `/w/${workspaceId}`;
  const today = new Date();
  const dashboard = useQuery({ queryKey: workspaceQueryKey(workspaceId, "dashboard"), queryFn: getDashboard, staleTime: 30_000 });
  const sessions = useRecentSessions(workspaceId);
  const graphs = useGraphs(workspaceId);
  const practice = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "practice", "home-active"),
    queryFn: () => listPracticeSessions({ status: "active", limit: 3 }),
  });
  const actionsKey = workspaceQueryKey(workspaceId, "actions");
  const actions = useQuery({ queryKey: actionsKey, queryFn: listActions });
  const client = useQueryClient();
  const complete = useMutation({
    mutationFn: (id: string) => updateAction(id, { status: "completed" }),
    onSuccess: (updated) => {
      client.setQueryData<typeof actions.data>(actionsKey, (items) => items?.map((item) => item.id === updated.id ? updated : item));
      void client.invalidateQueries({ queryKey: actionsKey });
      void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "dashboard") });
      if (updated.goal_id) void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "roadmap", updated.goal_id) });
      if (updated.graph_id) void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graph", updated.graph_id) });
    },
    onError: (error) => toast.error(error.message),
  });

  const graphList = graphs.data ?? [];
  const graphById = new Map(graphList.map((graph) => [graph.id, graph]));
  const recent = sessions.data?.pages[0]?.items ?? [];
  const { current } = partitionPlan(actions.data ?? [], today);
  // Keep the list short and group adjacent tasks by graph without expanding the panel.
  const tasks = [...new Set(current.slice(0, 3).map((item) => item.graph_id))].flatMap((id) => current.slice(0, 3).filter((item) => item.graph_id === id));
  const hero = graphs.isPending || sessions.isPending || practice.isPending || actions.isPending ? null :
    recommend({ base, now: today, graphs: graphList, sessions: recent, practice: practice.data ?? [], current });
  const metrics = new Map(dashboard.data?.metrics.map((metric) => [metric.key, metric.value]));
  const overview = dashboard.data ? `${metrics.get("active_goals") ?? 0} 个活跃目标 · ${metrics.get("graphs") ?? 0} 张学习图谱` : "你的目标、计划和学习记录";

  return <PageFrame className="study-home">
    <header className="study-header">
      <div>
        <p className="study-header__date">{new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(today)}</p>
        <h1 data-page-title>我的学习空间</h1>
        <p className="study-header__meta">{overview}</p>
      </div>
      <Button asChild size="sm" variant="outline"><Link to={`${base}/goals/new/clarify`}><Plus className="size-4" />创建学习目标</Link></Button>
    </header>

    <section aria-label="继续学习" className="study-hero" data-tone={hero?.tone ?? "study"}>
      <HeroArt tone={hero?.tone ?? "study"} />
      <div className="study-hero__content">
        {!hero ? <p role="status" className="text-sm text-muted-foreground">正在查找可以继续的学习…</p> : graphs.isError && hero.tone === "start" ? <>
          <h2>暂时无法读取学习图谱</h2><Button size="sm" variant="outline" onClick={() => void graphs.refetch()}>重试</Button>
        </> : <>
          <p className="study-hero__label">{hero.label}</p>
          <h2 title={hero.title}>{hero.title}</h2>
          <p className="study-hero__detail" title={hero.detail}>{hero.detail}</p>
          {hero.context && <p className="study-hero__context">{hero.context}</p>}
          <div className="study-hero__links">
            <Link className="study-hero__cta" to={hero.to}>{hero.cta}<ArrowRight aria-hidden="true" size={16} /></Link>
            {hero.graphTo && <Link className="study-text-link" to={hero.graphTo}>查看学习图谱<ArrowUpRight aria-hidden="true" size={13} /></Link>}
          </div>
        </>}
      </div>
    </section>

    <div className="study-columns">
      <section aria-labelledby="home-recent" className="study-section">
        <div className="study-section__heading"><h2 id="home-recent">最近学习</h2><Link className="study-text-link" to={`${base}/learning-sessions`}>全部会话<ChevronRight aria-hidden="true" size={14} /></Link></div>
        <div className="study-card study-card--list">
          {sessions.isPending ? <p className="study-empty" role="status">正在读取会话…</p> : sessions.isError ?
            <button type="button" className="study-empty study-text-link" onClick={() => void sessions.refetch()}>会话加载失败，重试</button> : recent.length ?
              <SessionRows items={recent.slice(0, 3)} graphs={graphById} base={base} today={today} /> : <p className="study-empty">还没有会话。开始学习后，可以从这里接着聊。</p>}
        </div>
      </section>
      <section aria-labelledby="home-plan" className="study-section">
        <div className="study-section__heading"><h2 id="home-plan">学习计划</h2><Link className="study-text-link" to={`${base}/learning-plan`}>查看全部<ChevronRight aria-hidden="true" size={14} /></Link></div>
        <div className="study-card study-card--list">
          {actions.isPending ? <p className="study-empty" role="status">正在读取计划…</p> : actions.isError ?
            <button type="button" className="study-empty study-text-link" onClick={() => void actions.refetch()}>计划加载失败，重试</button> : tasks.length ?
              <ul className="study-list">{tasks.map((item) => {
                const graph = item.graph_id ? graphById.get(item.graph_id) : undefined;
                return <li className="study-row" key={item.id}>
                  <span className="study-icon" data-tone="green"><Network aria-hidden="true" size={17} /></span>
                  <Link className="study-task-link min-w-0 flex-1" to={graph ? planPath(base, graph.id) : `${base}/learning-plan`}>
                    <span className="study-row__title" title={item.title}>{item.title}</span><span className="study-row__meta">{graph?.title ?? (item.graph_id ? "学习图谱" : "个人任务")}</span>
                  </Link>
                  <button aria-label={`完成任务：${item.title}`} title="标记完成" className="study-check" type="button" disabled={complete.isPending} onClick={() => complete.mutate(item.id)} />
                </li>;
              })}</ul> : <p className="study-empty">没有安排中的学习任务。</p>}
          <Link className="study-add study-text-link" to={`${base}/learning-plan#add-task`}><Plus aria-hidden="true" size={15} />添加学习任务</Link>
        </div>
      </section>
    </div>

    <section aria-labelledby="home-activity" className="study-section">
      <div className="study-section__heading"><h2 id="home-activity">学习活动</h2><Link className="study-text-link" to={`${base}/learning-report`}>学习报告<ChevronRight aria-hidden="true" size={14} /></Link></div>
      <div className="study-card study-activity"><ActivityHeatmap variant="panel" /></div>
    </section>
  </PageFrame>;
}

export function LearningSessionsPage() {
  const { workspaceId = "" } = useParams();
  const base = `/w/${workspaceId}`;
  const client = useQueryClient();
  const sessions = useRecentSessions(workspaceId);
  const graphs = useGraphs(workspaceId);
  const [search, setSearch] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<
    | { kind: "single"; session: SessionSummary }
    | { kind: "batch"; sessions: SessionSummary[] }
    | null
  >(null);

  const graphMap = useMemo(
    () => new Map(graphs.data?.map((graph) => [graph.id, graph])),
    [graphs.data],
  );
  const allItems = useMemo(
    () => sessions.data?.pages.flatMap((page) => page.items) ?? [],
    [sessions.data],
  );
  const filteredItems = useMemo(() => {
    const q = search.trim().toLocaleLowerCase();
    if (!q) return allItems;
    return allItems.filter((item) => {
      const title = (item.title || "未命名会话").toLocaleLowerCase();
      const graphTitle = (item.graph_id ? graphMap.get(item.graph_id)?.title : "")?.toLocaleLowerCase() ?? "";
      return title.includes(q) || graphTitle.includes(q);
    });
  }, [allItems, graphMap, search]);

  const visibleIds = useMemo(() => filteredItems.map((item) => item.id), [filteredItems]);
  const selection = useListSelection(visibleIds);
  const selectedSessions = useMemo(
    () => allItems.filter((item) => selection.selectedIds.has(item.id)),
    [allItems, selection.selectedIds],
  );

  const archiveMutation = useMutation({
    mutationFn: async (targets: SessionSummary[]) => {
      const results = await Promise.allSettled(targets.map((item) => archiveSession(item.id)));
      const archivedIds: string[] = [];
      results.forEach((result, index) => {
        if (result.status === "fulfilled") archivedIds.push(targets[index].id);
      });
      return { archivedIds, failed: targets.length - archivedIds.length };
    },
    onSuccess: ({ archivedIds, failed }) => {
      selection.removeIds(archivedIds);
      void scheduleSessionRefresh(client, workspaceId);
      void sessions.refetch();
      if (failed === 0) toast.success(archivedIds.length > 1 ? `已归档 ${archivedIds.length} 个会话` : "会话已归档");
      else if (archivedIds.length > 0) toast.warning(`已归档 ${archivedIds.length} 个会话，${failed} 个失败`);
      else toast.error("归档会话失败");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "归档会话失败"),
  });

  const deleteImpact = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "learning-sessions-delete-impact",
      deleteTarget?.kind === "single"
        ? deleteTarget.session.id
        : deleteTarget?.sessions.map((item) => item.id).join(",") ?? "",
    ),
    queryFn: () => {
      if (!deleteTarget) throw new Error("missing target");
      if (deleteTarget.kind === "single") return getSessionDeleteImpact(deleteTarget.session.id);
      return getSessionBatchDeleteImpact(deleteTarget.sessions.map((item) => item.id));
    },
    enabled: Boolean(deleteTarget),
  });

  const confirmDelete = useMutation({
    mutationFn: async (confirmationText: string) => {
      if (!deleteTarget) return [] as string[];
      if (deleteTarget.kind === "single") {
        await deleteSession(deleteTarget.session.id, confirmationText);
        return [deleteTarget.session.id];
      }
      const ids = deleteTarget.sessions.map((item) => item.id);
      await deleteSessionsBatch(ids, confirmationText);
      return ids;
    },
    onSuccess: (deletedIds) => {
      selection.removeIds(deletedIds);
      setDeleteTarget(null);
      void scheduleSessionRefresh(client, workspaceId);
      void sessions.refetch();
      toast.success(deletedIds.length > 1 ? `已删除 ${deletedIds.length} 个会话` : "会话已删除");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "删除会话失败"),
  });

  const busy = archiveMutation.isPending || confirmDelete.isPending;
  const today = new Date();

  return <PageFrame className="study-home">
    <Link className="study-text-link self-start" to={`${base}/home`}><ArrowLeft size={15} />我的学习空间</Link>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h1 data-page-title className="text-2xl font-semibold">全部会话</h1>
      {allItems.length ? (
        <Button
          aria-pressed={selection.multiSelect}
          onClick={selection.toggleMultiSelect}
          size="sm"
          variant={selection.multiSelect ? "secondary" : "outline"}
        >
          <SquareCheck className="size-4" />
          {selection.multiSelect ? "退出多选" : "多选"}
        </Button>
      ) : null}
    </div>
    {allItems.length ? (
      <div className="flex flex-wrap items-center gap-2">
        <ClearableSearchInput
          aria-label="搜索全部会话"
          containerClassName="min-w-52 flex-1 sm:max-w-sm"
          onChange={setSearch}
          placeholder="搜索会话标题或图谱…"
          value={search}
        />
        {selection.multiSelect ? (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              aria-label="全选当前列表"
              checked={selection.visibleCheckboxState}
              disabled={busy || !filteredItems.length}
              onCheckedChange={(checked) => selection.toggleVisible(checked === true)}
            />
            全选当前列表
          </label>
        ) : null}
      </div>
    ) : null}
    {selection.multiSelect && selection.selectedCount > 0 ? (
      <div
        aria-label="全部会话批量操作"
        className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 px-3 py-2 text-sm"
        role="region"
      >
        <span>
          已选 <strong>{selection.selectedCount}</strong> 个会话
          {selection.otherSelectedCount > 0 ? `（另有 ${selection.otherSelectedCount} 个不在当前筛选）` : ""}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Button
            disabled={busy}
            onClick={() => archiveMutation.mutate(selectedSessions)}
            size="xs"
            variant="outline"
          >
            <Archive className="size-3.5" />
            批量归档
          </Button>
          <Button
            disabled={busy}
            onClick={() => setDeleteTarget({ kind: "batch", sessions: selectedSessions })}
            size="xs"
            variant="destructive"
          >
            <Trash2 className="size-3.5" />
            批量删除
          </Button>
          <Button disabled={busy} onClick={selection.clear} size="xs" variant="ghost">
            取消选择
          </Button>
        </div>
      </div>
    ) : null}
    {sessions.isPending ? <LoadingState label="正在读取会话…" /> : sessions.isError && !sessions.data ?
      <ErrorState message={sessions.error.message} onRetry={() => void sessions.refetch()} /> : allItems.length ? (
        filteredItems.length ? (
          <div className="study-card study-card--list">
            <ul className="study-list">
              {filteredItems.map((item, index) => {
                const graphTitle = item.graph_id ? graphMap.get(item.graph_id)?.title ?? "图谱会话" : "普通会话";
                const Icon = item.graph_id ? Network : MessageCircle;
                const title = item.title || "未命名会话";
                const selected = selection.selectedIds.has(item.id);
                return (
                  <li className={`flex items-center gap-2 px-3 py-1.5 ${selected ? "bg-primary/5" : ""}`} key={item.id}>
                    {selection.multiSelect ? (
                      <Checkbox
                        aria-label={`选择会话 ${title}`}
                        disabled={busy}
                        {...selection.checkboxProps(item.id)}
                      />
                    ) : null}
                    <Link className="study-row study-row--link min-w-0 flex-1 !px-1" to={`${base}/chat/${encodeURIComponent(item.id)}`}>
                      <span className="study-icon" data-tone={item.graph_id ? (index % 2 === 0 ? "blue" : "green") : "violet"}><Icon aria-hidden="true" size={17} /></span>
                      <span className="min-w-0 flex-1">
                        <span className="study-row__title" title={title}>{title}</span>
                        <span className="study-row__meta">{[graphTitle, shortDate(item.updated_at, today)].filter(Boolean).join(" · ")}</span>
                      </span>
                    </Link>
                    {!selection.multiSelect ? (
                      <div className="flex shrink-0 items-center gap-1">
                        <Button
                          aria-label={`归档会话 ${title}`}
                          disabled={busy}
                          onClick={() => archiveMutation.mutate([item])}
                          size="icon-xs"
                          title="归档会话"
                          variant="ghost"
                        >
                          <Archive className="size-3.5" />
                        </Button>
                        <Button
                          aria-label={`删除会话 ${title}`}
                          disabled={busy}
                          onClick={() => setDeleteTarget({ kind: "single", session: item })}
                          size="icon-xs"
                          title="删除会话"
                          variant="ghost"
                        >
                          <Trash2 className="size-3.5 text-destructive" />
                        </Button>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : <p className="study-empty">没有匹配「{search.trim()}」的会话。</p>
      ) : <p className="study-empty">还没有会话。</p>}
    {sessions.hasNextPage && <Button variant="outline" className="self-center" disabled={sessions.isFetchingNextPage} onClick={() => void sessions.fetchNextPage()}>{sessions.isFetchNextPageError ? "重试加载" : sessions.isFetchingNextPage ? "正在加载…" : "加载更多会话"}</Button>}
    <DeleteImpactDialog
      confirmLabel="永久删除"
      error={
        (deleteImpact.error instanceof Error ? deleteImpact.error.message : undefined) ??
        (confirmDelete.error instanceof Error ? confirmDelete.error.message : undefined)
      }
      impact={deleteImpact.data}
      isConfirming={confirmDelete.isPending}
      isLoading={deleteImpact.isPending && Boolean(deleteTarget)}
      objectLabel={
        deleteTarget?.kind === "batch"
          ? `${deleteTarget.sessions.length} 个会话`
          : deleteTarget?.session.title || "未命名会话"
      }
      onConfirm={async () => {
        if (!deleteImpact.data) return;
        await confirmDelete.mutateAsync(deleteImpact.data.confirmation_text);
      }}
      onOpenChange={(open) => {
        if (!open && !confirmDelete.isPending) setDeleteTarget(null);
      }}
      open={Boolean(deleteTarget)}
      title={deleteTarget?.kind === "batch" ? `永久删除选中的 ${deleteTarget.sessions.length} 个会话？` : undefined}
    />
  </PageFrame>;
}
