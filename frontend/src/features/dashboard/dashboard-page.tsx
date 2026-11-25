import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, ArrowUpRight, ChevronRight, MessageCircle, Network, Plus } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { getDashboard, listActions, listGraphs, listPracticeSessions, updateAction } from "@/api";
import { listSessionPage, type SessionSummary } from "@/api/session-browser";
import { ActivityHeatmap } from "@/components/shared/activity-heatmap";
import { ErrorState, LoadingState, PageFrame } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { toast } from "@/lib/notifications";
import { workspaceQueryKey } from "@/lib/query-keys";
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
  const sessions = useRecentSessions(workspaceId);
  const graphs = useGraphs(workspaceId);
  return <PageFrame className="study-home">
    <Link className="study-text-link self-start" to={`${base}/home`}><ArrowLeft size={15} />我的学习空间</Link>
    <h1 data-page-title className="text-2xl font-semibold">全部会话</h1>
    {sessions.isPending ? <LoadingState label="正在读取会话…" /> : sessions.isError && !sessions.data ?
      <ErrorState message={sessions.error.message} onRetry={() => void sessions.refetch()} /> : sessions.data?.pages[0]?.items.length ?
        <div className="study-card study-card--list"><SessionRows items={sessions.data.pages.flatMap((page) => page.items)} graphs={new Map(graphs.data?.map((graph) => [graph.id, graph]))} base={base} today={new Date()} /></div> :
        <p className="study-empty">还没有会话。</p>}
    {sessions.hasNextPage && <Button variant="outline" className="self-center" disabled={sessions.isFetchingNextPage} onClick={() => void sessions.fetchNextPage()}>{sessions.isFetchNextPageError ? "重试加载" : sessions.isFetchingNextPage ? "正在加载…" : "加载更多会话"}</Button>}
  </PageFrame>;
}
