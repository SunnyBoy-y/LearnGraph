import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  BookOpenCheck,
  CalendarClock,
  History,
  ListChecks,
  Play,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { createPracticeSession, getPracticeOverview } from "@/api";
import {
  EmptyState,
  ErrorState,
  MetricStrip,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { PracticeOverview, PracticePlanItem } from "@/types/practice";
import { PracticeFreeTab } from "./practice-free-tab";
import { PracticeReportTab } from "./practice-report-tab";
import { PracticeWrongBookTab } from "./practice-wrong-book-tab";
import {
  GraphNodeLink,
  PracticeCalendarDialog,
  PracticeDeltaBadge,
  PracticeTrendChart,
} from "./practice-shared";
import {
  dueLabel,
  formatDateTime,
  formatMinutes,
  percent,
} from "./practice-format";

const TABS = [
  { id: "today", label: "今日练习" },
  { id: "free", label: "自由练习" },
  { id: "wrong", label: "错题本" },
  { id: "report", label: "学习报告" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function PlanReasonChips({ item }: { item: PracticePlanItem }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <StatePill
        label={item.reason}
        status={
          ["overdue", "due_today", "relearning", "consecutive_wrong"].includes(
            item.reason_code,
          )
            ? "due"
            : item.reason_code === "weak_mastery"
              ? "pending"
              : "claimed"
        }
      />
      {item.detail ? (
        <span className="text-xs text-muted-foreground">{item.detail}</span>
      ) : null}
    </span>
  );
}

function TodayPlanCard({
  plan,
  pending,
  onStart,
  starting,
}: {
  plan: PracticeOverview["today_plan"];
  pending: boolean;
  onStart: () => void;
  starting: boolean;
}) {
  if (pending) {
    return (
      <Surface className="space-y-3 p-5">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-2/3" />
      </Surface>
    );
  }
  if (plan.mode === "empty") {
    return (
      <Surface className="p-5">
        <SectionHeading title="今日练习" />
        <div className="mt-4 rounded-xl border border-dashed p-5 text-center">
          <p className="text-sm font-medium">今天没有必须复习的内容 🎉</p>
          <p className="mt-1 text-xs text-muted-foreground">
            你仍可以自由练习，或挑战薄弱知识点。
          </p>
          <div className="mt-3 flex flex-wrap justify-center gap-2">
            <Button asChild size="sm" variant="outline">
              <Link to="?tab=free">自由练习</Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to="?tab=wrong">挑战薄弱节点</Link>
            </Button>
          </div>
        </div>
      </Surface>
    );
  }
  if (!plan.items.length) {
    // 计划里一道题都排不出来：不能给一个点了必然失败的「开始今日练习」按钮，
    // 直接把阻塞原因和出口放在这里。
    return (
      <Surface className="p-5">
        <SectionHeading title="今日练习" />
        <div className="mt-4 rounded-xl border border-dashed p-5">
          <p className="text-sm font-medium">今天暂时无法开始练习</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {plan.message ||
              "计划中的知识点还没有题库，需要先配置远程模型才能出题。"}
          </p>
          {plan.skipped_nodes.length ? (
            <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
              {plan.skipped_nodes.map((item) => (
                <li key={item.node_id}>
                  {item.label}：{item.detail}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button asChild size="sm" variant="outline">
              <Link to="?tab=free">去自由练习</Link>
            </Button>
            {plan.provider_available === false ? (
              <Button asChild size="sm" variant="outline">
                <Link to="../settings/providers">配置远程模型</Link>
              </Button>
            ) : null}
          </div>
        </div>
      </Surface>
    );
  }
  return (
    <Surface className="p-5">
      <SectionHeading
        description="根据复习到期、错题、当前目标与知识薄弱情况自动安排。"
        title="今日练习"
      />
      <div className="mt-4 rounded-xl border bg-muted/20 p-4">
        <p className="text-lg font-semibold tabular-nums">
          {plan.question_count} 道题 · 约 {plan.estimated_minutes} 分钟
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          覆盖 {plan.node_count} 个知识点 · 包含多种题型
        </p>
        {plan.message ? (
          <p className="mt-2 text-xs leading-5 text-amber-600 dark:text-amber-400">
            {plan.message}
          </p>
        ) : null}
        <Button
          className="mt-3 w-full sm:w-auto"
          disabled={starting}
          onClick={onStart}
          size="lg"
        >
          <Play className="size-4" />
          {starting ? "正在准备题目…" : "开始今日练习"}
          <ArrowRight className="size-4" />
        </Button>
      </div>

      <ul className="mt-4 divide-y">
        {plan.items.map((item) => (
          <li className="flex flex-wrap items-start gap-3 py-3" key={item.node_id}>
            <span className="mt-1.5 size-2 shrink-0 rounded-full bg-primary" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">{item.label}</p>
              <div className="mt-1">
                <PlanReasonChips item={item} />
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                上次练习：{item.last_practiced_at ? formatDateTime(item.last_practiced_at) : "尚无"}
                {" · "}
                预计 {item.planned_questions} 题
                {item.next_review_at ? ` · ${dueLabel(item.next_review_at)}` : ""}
              </p>
            </div>
          </li>
        ))}
      </ul>

      {plan.skipped_nodes.length ? (
        <div className="mt-3 rounded-xl border border-dashed p-3">
          <p className="flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400">
            <AlertTriangle className="size-3.5" />
            以下知识点本次无法出题
          </p>
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            {plan.skipped_nodes.map((item) => (
              <li key={item.node_id}>
                {item.label}：{item.detail}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Surface>
  );
}

function RecentSessionCard({
  overview,
  workspaceId,
}: {
  overview: PracticeOverview;
  workspaceId: string;
}) {
  const session = overview.recent_session;
  return (
    <Surface className="p-5">
      <SectionHeading title="最近一次练习" />
      {session ? (
        <div className="mt-3 space-y-2">
          <p className="text-3xl font-semibold tabular-nums">
            {percent(session.first_try_accuracy)}
          </p>
          <p className="text-sm text-muted-foreground tabular-nums">
            {session.final_correct_count} / {session.planned_question_count} 题最终正确 ·
            首次正确 {session.first_try_correct_count} 题
          </p>
          <p className="text-xs text-muted-foreground">
            {formatDateTime(session.completed_at ?? session.created_at)} ·{" "}
            {session.planned_question_count} 题 · 用时{" "}
            {formatMinutes(Math.round(session.duration_seconds / 60))}
          </p>
          {session.node_labels.length ? (
            <p className="text-xs text-muted-foreground">
              覆盖：{session.node_labels.slice(0, 3).join("、")}
              {session.node_labels.length > 3 ? " 等" : ""}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2 pt-1">
            <Button asChild size="sm">
              <Link to={`/w/${workspaceId}/practice/report/${session.id}`}>
                查看完整报告
              </Link>
            </Button>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">
          还没有完成过练习。开始今日练习后，这里会显示真实报告。
        </p>
      )}
    </Surface>
  );
}

export function PracticeCenterPage() {
  const { workspaceId = "" } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = (searchParams.get("tab") ?? "today") as TabId;
  const tab: TabId = TABS.some((item) => item.id === tabParam) ? tabParam : "today";
  const [startError, setStartError] = useState<string | null>(null);

  const overview = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "practice-overview"),
    queryFn: getPracticeOverview,
  });

  const start = useMutation({
    mutationFn: () =>
      createPracticeSession({ mode: "scheduled", question_type: "mixed" }),
    onSuccess: (view) => {
      setStartError(null);
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "practice-overview"),
      });
      navigate(`/w/${workspaceId}/practice/session/${view.session.id}`);
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "无法开始练习";
      setStartError(message);
      toast.error(message);
    },
  });

  const stats = overview.data?.stats;

  const metricItems = useMemo(() => {
    if (!stats) return [];
    return [
      {
        label: "待练知识点",
        value: stats.pending_node_count,
        hint: `${stats.due_node_count} 个已到期 · ${stats.weak_node_count} 个薄弱点`,
        tone: stats.due_node_count > 0 ? ("danger" as const) : ("default" as const),
      },
      {
        label: "预计用时",
        value: stats.estimated_minutes > 0 ? `${stats.estimated_minutes} 分钟` : "—",
        hint: "根据当前练习计划动态估算",
        tone: "info" as const,
      },
      {
        label: "近 7 天首次正确率",
        value: percent(stats.first_try_accuracy_7d),
        hint: `共练习 ${stats.answered_7d} 题`,
        tone: "positive" as const,
      },
      {
        label: "需要关注的节点",
        value: stats.attention_node_count,
        hint: "存在连续错误或掌握较低",
        tone: stats.attention_node_count > 0 ? ("warning" as const) : ("default" as const),
      },
    ];
  }, [stats]);

  if (overview.isPending) {
    return (
      <PageFrame className="max-w-[1280px]">
        <PageIntro
          description="基于遗忘曲线和智能调度，在合适的时间，通过练习真正掌握知识。"
          eyebrow="Practice & Review"
          title="复习与练习中心"
        />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton className="h-24 w-full" key={index} />
          ))}
        </div>
        <div className="grid gap-5 xl:grid-cols-12">
          <Skeleton className="h-72 w-full xl:col-span-8" />
          <Skeleton className="h-72 w-full xl:col-span-4" />
        </div>
      </PageFrame>
    );
  }

  if (overview.isError) {
    return (
      <PageFrame className="max-w-[1280px]">
        <PageIntro eyebrow="Practice & Review" title="复习与练习中心" />
        <ErrorState
          message={overview.error.message}
          onRetry={() => void overview.refetch()}
        />
      </PageFrame>
    );
  }

  const data = overview.data;

  return (
    <PageFrame className="max-w-[1280px]">
      <PageIntro
        actions={
          <PracticeCalendarDialog
            points={overview.data.trend}
            workspaceId={workspaceId}
          />
        }
        description="基于遗忘曲线和智能调度，在合适的时间，通过练习真正掌握知识。"
        eyebrow="Practice & Review"
        title="复习与练习中心"
      />

      <nav
        aria-label="练习中心分区"
        className="-mx-1 flex gap-1 overflow-x-auto pb-1"
        role="tablist"
      >
        {TABS.map((item) => (
          <button
            aria-selected={tab === item.id}
            className={
              tab === item.id
                ? "shrink-0 rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background"
                : "shrink-0 rounded-full border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
            }
            key={item.id}
            onClick={() => setSearchParams({ tab: item.id })}
            role="tab"
            type="button"
          >
            {item.label}
          </button>
        ))}
      </nav>

      {tab === "today" ? (
        <div className="flex flex-col gap-5">
          {data.active_session ? (
            <Surface className="flex flex-wrap items-center justify-between gap-3 border-primary/40 p-4">
              <div className="min-w-0">
                <p className="text-sm font-semibold">
                  有未完成的练习：{data.active_session.title || "练习"}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  已答 {data.active_session.completed_question_count}/
                  {data.active_session.planned_question_count} 题 ·
                  刷新页面也不会丢失进度
                </p>
              </div>
              <Button
                onClick={() =>
                  navigate(
                    `/w/${workspaceId}/practice/session/${data.active_session?.id}`,
                  )
                }
                size="sm"
              >
                <History className="size-4" />
                继续练习
              </Button>
            </Surface>
          ) : null}

          <MetricStrip items={metricItems} />

          {startError ? (
            <Surface className="border-amber-300 bg-amber-50/60 p-4 dark:border-amber-900 dark:bg-amber-950/20">
              <p className="flex items-center gap-2 text-sm font-medium text-amber-700 dark:text-amber-300">
                <AlertTriangle className="size-4" />
                暂时无法开始今日练习
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {startError}
              </p>
            </Surface>
          ) : null}

          <div className="grid gap-5 xl:grid-cols-12">
            <div className="xl:col-span-8">
              <TodayPlanCard
                onStart={() => start.mutate()}
                pending={false}
                plan={data.today_plan}
                starting={start.isPending}
              />
            </div>
            <div className="flex flex-col gap-5 xl:col-span-4">
              <Surface className="p-5">
                <SectionHeading
                  action={<Badge variant="outline">近 7 天</Badge>}
                  description="首次正确率反映真实掌握，不受重试影响"
                  title="学习状态趋势"
                />
                <div className="mt-4">
                  <PracticeTrendChart points={data.trend} height={200} />
                </div>
                <dl className="mt-4 grid grid-cols-3 gap-3 border-t pt-4 text-center">
                  <div>
                    <dt className="text-xs text-muted-foreground">本周练习题数</dt>
                    <dd className="mt-1 text-xl font-semibold tabular-nums">
                      {data.stats.answered_7d}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">已巩固知识点</dt>
                    <dd className="mt-1 text-xl font-semibold tabular-nums">
                      {data.stats.consolidated_node_count}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">需要继续关注</dt>
                    <dd className="mt-1 text-xl font-semibold tabular-nums">
                      {data.stats.attention_node_count}
                    </dd>
                  </div>
                </dl>
                <div className="mt-3 flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
                  <span>与上一个 7 天相比</span>
                  <PracticeDeltaBadge delta={data.stats.first_try_accuracy_delta_7d} />
                </div>
              </Surface>

              <RecentSessionCard overview={data} workspaceId={workspaceId} />
            </div>
          </div>

          <div className="grid gap-5 xl:grid-cols-12">
            <Surface className="p-5 xl:col-span-8">
              <SectionHeading
                description="存在连续错误、掌握较低或证据冲突的节点"
                title="需要重点复习"
              />
              {data.focus_nodes.length ? (
                <ul className="mt-4 divide-y">
                  {data.focus_nodes.map((item) => (
                    <li
                      className="flex flex-wrap items-center justify-between gap-3 py-3"
                      key={item.node_id}
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-semibold">{item.label}</p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {item.reason} · 最近练习：
                          {item.last_practiced_at
                            ? formatDateTime(item.last_practiced_at)
                            : "尚无"}
                          {item.mastery_score !== null &&
                          item.mastery_score !== undefined
                            ? ` · 掌握分 ${percent(item.mastery_score)}`
                            : ""}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <GraphNodeLink
                          graphId={item.graph_id}
                          label="查看图谱"
                          nodeId={item.node_id}
                          workspaceId={workspaceId}
                        />
                        <Button asChild size="sm" variant="outline">
                          <Link
                            to={`?tab=free&node=${encodeURIComponent(item.node_id)}`}
                          >
                            立即练习
                          </Link>
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">
                  目前没有需要重点复习的节点。
                </p>
              )}
            </Surface>

            <Surface className="p-5 xl:col-span-4">
              <SectionHeading title="快速开始" />
              <div className="mt-4 space-y-2">
                <Button asChild className="w-full justify-between" variant="outline">
                  <Link to="?tab=free">
                    <span className="flex items-center gap-2">
                      <Sparkles className="size-4" />
                      自由练习
                    </span>
                    <span className="text-xs text-muted-foreground">
                      选择知识点、题型、难度
                    </span>
                  </Link>
                </Button>
                <Button asChild className="w-full justify-between" variant="outline">
                  <Link to="?tab=wrong">
                    <span className="flex items-center gap-2">
                      <ListChecks className="size-4" />
                      错题重练
                    </span>
                    <span className="text-xs text-muted-foreground">
                      针对历史错题强化
                    </span>
                  </Link>
                </Button>
                <Button asChild className="w-full justify-between" variant="outline">
                  <Link to={`/w/${workspaceId}/graphs`}>
                    <span className="flex items-center gap-2">
                      <BookOpenCheck className="size-4" />
                      按知识点练习
                    </span>
                    <span className="text-xs text-muted-foreground">
                      从图谱节点开始
                    </span>
                  </Link>
                </Button>
                <Button asChild className="w-full justify-between" variant="outline">
                  <Link to="?tab=free&material=1">
                    <span className="flex items-center gap-2">
                      <CalendarClock className="size-4" />
                      导入资料出题
                    </span>
                    <span className="text-xs text-muted-foreground">
                      基于资料生成题目
                    </span>
                  </Link>
                </Button>
              </div>
              <Button
                className="mt-3 w-full"
                disabled={overview.isFetching}
                onClick={() => void overview.refetch()}
                size="sm"
                variant="ghost"
              >
                <RefreshCw className="size-4" />
                刷新计划
              </Button>
            </Surface>
          </div>
        </div>
      ) : null}

      {tab === "free" ? <PracticeFreeTab /> : null}

      {tab === "wrong" ? <PracticeWrongBookTab /> : null}

      {tab === "report" ? <PracticeReportTab /> : null}

      {!data.today_plan.items.length && tab === "today" ? (
        <EmptyState
          description="完成一次练习后，这里会显示真实的练习报告与薄弱点分析。"
          title="还没有练习数据"
        />
      ) : null}
    </PageFrame>
  );
}
