import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  ClipboardCheck,
  Clock3,
  ListTodo,
  Network,
  Plus,
  Sparkles,
  Target,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  createAction,
  getDashboard,
  listSessionMessages,
  listSessions,
  updateAction,
} from "@/api";
import { MessageResponse } from "@/components/ai-elements/message";
import { ActivityHeatmap } from "@/components/shared/activity-heatmap";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  Surface,
} from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

const SUMMARY_CHARACTER_LIMIT = 100;

function truncateMarkdown(value: string, limit = SUMMARY_CHARACTER_LIMIT) {
  const normalized = value.trim().replace(/\r\n/g, "\n").replace(/\n{3,}/g, "\n\n");
  const characters = Array.from(normalized);
  if (characters.length <= limit) return normalized;

  const prefix = characters.slice(0, limit - 1).join("");
  const sentenceCut = Math.max(
    prefix.lastIndexOf("。"),
    prefix.lastIndexOf("！"),
    prefix.lastIndexOf("？"),
    prefix.lastIndexOf("\n"),
  );
  const excerpt = sentenceCut >= Math.floor(limit * 0.55)
    ? prefix.slice(0, sentenceCut + 1)
    : prefix;
  return `${excerpt.trimEnd()}…`;
}

function isSameLocalDay(value: string | null | undefined, date: Date) {
  if (!value) return false;
  const candidate = new Date(value);
  return (
    !Number.isNaN(candidate.getTime()) &&
    candidate.getFullYear() === date.getFullYear() &&
    candidate.getMonth() === date.getMonth() &&
    candidate.getDate() === date.getDate()
  );
}

function formatDueAt(value: string | null) {
  if (!value) return "未设置截止时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "截止时间待确认";
  const today = new Date();
  const time = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
  if (isSameLocalDay(value, today)) return `今天 ${time}`;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function DashboardPage() {
  const { workspaceId = "" } = useParams();
  const auth = useAuth();
  const base = `/w/${workspaceId}`;
  const dashboard = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "dashboard"),
    queryFn: getDashboard,
  });
  const sessions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "sessions"),
    queryFn: listSessions,
  });
  const recentSession = sessions.data?.[0];
  const recentMessages = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "messages",
      recentSession?.id,
      "dashboard-preview",
    ),
    queryFn: () => listSessionMessages(recentSession!.id, { limit: 8 }),
    enabled: Boolean(recentSession),
    gcTime: 30_000,
  });
  const recentAssistant = recentMessages.data
    ?.filter((item) => item.role === "assistant")
    .at(-1);
  const recentAssistantContent =
    recentAssistant?.content.trim() ||
    recentAssistant?.parts
      .filter((part) => part.type === "text")
      .map((part) => part.content?.trim() ?? "")
      .filter(Boolean)
      .join("\n") ||
    "";
  const recentSummary = truncateMarkdown(recentAssistantContent);
  const queryClient = useQueryClient();
  const [todo, setTodo] = useState("");
  const createTodo = useMutation({
    mutationFn: () => createAction(todo.trim()),
    onSuccess: () => {
      setTodo("");
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "dashboard"),
      });
    },
    onError: (error) => toast.error(error.message),
  });
  const complete = useMutation({
    mutationFn: (id: string) => updateAction(id, { status: "completed" }),
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "dashboard"),
      }),
    onError: (error) => toast.error(error.message),
  });

  if (dashboard.isPending)
    return (
      <PageFrame>
        <LoadingState label="正在准备工作区首页…" />
      </PageFrame>
    );
  if (dashboard.isError)
    return (
      <PageFrame>
        <ErrorState
          message={dashboard.error.message}
          onRetry={() => void dashboard.refetch()}
        />
      </PageFrame>
    );

  const actions = dashboard.data.next_actions;
  const primaryAction = actions[0];
  const metricByKey = new Map(
    dashboard.data.metrics.map((metric) => [metric.key, metric]),
  );
  const overview = [
    {
      key: "active_goals",
      label: "活跃目标",
      hint: "正在推进",
      icon: Target,
      iconClass: "text-primary",
    },
    {
      key: "graphs",
      label: "学习图谱",
      hint: "可审核与学习",
      icon: Network,
      iconClass: "text-emerald-600 dark:text-emerald-400",
    },
    {
      key: "pending_evidence",
      label: "待审证据",
      hint: "等待确认",
      icon: ClipboardCheck,
      iconClass: "text-amber-600 dark:text-amber-400",
    },
  ].map((item) => ({ ...item, value: metricByKey.get(item.key)?.value ?? "—" }));

  const today = new Date();
  const todayActivities = [
    ...actions
      .filter((action) => isSameLocalDay(action.due_at, today))
      .map((action) => ({
        id: `action-${action.id}`,
        title: action.title,
        detail: `计划行动 · ${formatDueAt(action.due_at)}`,
      })),
    ...(sessions.data ?? [])
      .filter((session) => isSameLocalDay(session.created_at, today))
      .map((session) => ({
        id: `session-${session.id}`,
        title: session.title,
        detail: "今天创建的会话",
      })),
  ].slice(0, 4);
  const todayLabel = new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(today);

  return (
    <PageFrame>
      <PageIntro
        actions={
          <Button asChild size="sm">
            <Link to={`${base}/goals/new/clarify`}>
              <Sparkles className="size-4" />
              创建学习目标
            </Link>
          </Button>
        }
        description="按目标约束、到期安排与证据缺口，聚焦现在最值得推进的学习任务。"
        eyebrow={auth.workspaceName || "当前工作区"}
        title="继续你的学习计划"
      />

      <Surface aria-label="工作区概览" className="grid overflow-hidden sm:grid-cols-3">
        {overview.map((item, index) => {
          const Icon = item.icon;
          return (
            <div
              className={cn(
                "flex items-center gap-3 px-5 py-4",
                index > 0 && "border-t sm:border-l sm:border-t-0",
              )}
              key={item.key}
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted">
                <Icon className={`size-4 ${item.iconClass}`} />
              </span>
              <div className="min-w-0">
                <p className="text-xs font-medium text-muted-foreground">{item.label}</p>
                <p className="mt-0.5 text-xl font-semibold tabular-nums">
                  {item.value}
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    {item.hint}
                  </span>
                </p>
              </div>
            </div>
          );
        })}
      </Surface>

      <div className="grid items-stretch gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(280px,.55fr)]">
        <Surface className="flex flex-col p-5 sm:p-6">
          <SectionHeading
            description="来自已发布路线和手工待办"
            title="下一步行动"
          />
          {primaryAction ? (
            <div className="mt-5 flex flex-1 flex-col">
              <div className="flex items-start gap-3">
                <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                  <ListTodo className="size-5" />
                </span>
                <div className="min-w-0 flex-1">
                  <h2 className="break-words text-lg font-semibold leading-7">
                    {primaryAction.title}
                  </h2>
                  {primaryAction.description ? (
                    <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted-foreground">
                      {primaryAction.description}
                    </p>
                  ) : null}
                </div>
              </div>
              <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-4 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Clock3 className="size-3.5" />
                  {formatDueAt(primaryAction.due_at)}
                </span>
                <span>
                  {primaryAction.source === "user" ? "手工待办" : "路线安排"}
                </span>
                <Button
                  className="ml-auto"
                  disabled={complete.isPending}
                  onClick={() => complete.mutate(primaryAction.id)}
                  size="sm"
                  variant="outline"
                >
                  <Check className="size-4" />
                  标记完成
                </Button>
              </div>
              {actions.length > 1 ? (
                <div className="mt-3 divide-y border-t">
                  {actions.slice(1, 4).map((action) => (
                    <div className="flex items-center gap-3 py-3" key={action.id}>
                      <span className="size-1.5 shrink-0 rounded-full bg-muted-foreground/60" />
                      <p className="min-w-0 flex-1 truncate text-sm">{action.title}</p>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {formatDueAt(action.due_at)}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="grid flex-1 place-items-center py-6 text-center">
              <div>
                <Check className="mx-auto size-6 text-muted-foreground" />
                <p className="mt-2 text-sm font-medium">当前没有待处理行动</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  可以添加一项普通待办，或从学习路线生成行动。
                </p>
              </div>
            </div>
          )}
          <form
            className="mt-4 flex gap-2 border-t pt-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (todo.trim()) createTodo.mutate();
            }}
          >
            <Input
              aria-label="新建待办"
              onChange={(event) => setTodo(event.target.value)}
              placeholder="添加普通待办…"
              value={todo}
            />
            <Button
              disabled={!todo.trim() || createTodo.isPending}
              size="sm"
              type="submit"
              variant="outline"
            >
              <Plus className="size-4" />
              添加
            </Button>
          </form>
        </Surface>

        <Surface className="flex flex-col p-5 sm:p-6">
          <SectionHeading description={todayLabel} title="今日活动" />
          {todayActivities.length ? (
            <ul className="mt-4 divide-y">
              {todayActivities.map((activity) => (
                <li className="py-3 first:pt-0 last:pb-0" key={activity.id}>
                  <p className="line-clamp-1 text-sm font-medium">{activity.title}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{activity.detail}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-sm leading-6 text-muted-foreground">
              今天暂无待办安排或新会话。
            </p>
          )}
        </Surface>
      </div>

      <Surface aria-label="学习活动热力图" className="p-5 sm:p-6">
        <ActivityHeatmap variant="panel" />
      </Surface>

      <Surface className="p-5 sm:p-6">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground">
              最近会话 · 学习助手正在延续你的上下文
            </p>
            <h2 className="mt-1 truncate text-base font-semibold">
              {recentSession?.title ?? "还没有学习会话"}
            </h2>
          </div>
          {recentSession ? (
            <Button asChild className="shrink-0" size="sm" variant="outline">
              <Link to={`${base}/chat/${recentSession.id}`}>
                打开会话
                <ArrowRight className="size-4" />
              </Link>
            </Button>
          ) : null}
        </div>
        <div className="mt-4 border-t pt-4">
          {recentSummary ? (
            <MessageResponse className="text-sm leading-6 text-muted-foreground [&_h1]:text-sm [&_h2]:text-sm [&_h3]:text-sm [&_pre]:max-h-20">
              {recentSummary}
            </MessageResponse>
          ) : (
            <p className="text-sm leading-6 text-muted-foreground">
              {recentMessages.isPending && recentSession
                ? "正在读取最近回复…"
                : recentMessages.isError
                  ? "最近回复暂时无法读取。"
                  : recentSession
                    ? "这个会话还没有助手回复。"
                    : "创建会话后，最近的学习摘要会显示在这里。"}
            </p>
          )}
        </div>
      </Surface>
    </PageFrame>
  );
}
