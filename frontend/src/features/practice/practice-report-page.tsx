import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Layers, Plus } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  createPracticeSession,
  getPracticeSessionReport,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import { GraphNodeLink } from "./practice-shared";
import { formatDateTime, formatDay, percent } from "./practice-format";

export function PracticeReportPage() {
  const { workspaceId: routeWorkspaceId = "", practiceSessionId = "" } = useParams();
  const auth = useAuth();
  const workspaceId = auth.workspaceId ?? routeWorkspaceId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const report = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "practice-report", {
      id: practiceSessionId,
    }),
    queryFn: () => getPracticeSessionReport(practiceSessionId),
  });

  const again = useMutation({
    mutationFn: () => {
      const attention = report.data?.attention.map((item) => item.node_id) ?? [];
      return createPracticeSession({
        mode: attention.length ? "node" : "scheduled",
        node_ids: attention,
        count: 5,
      });
    },
    onSuccess: (view) => {
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "practice-overview"),
      });
      navigate(`/w/${workspaceId}/practice/session/${view.session.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "无法开始练习"),
  });

  if (report.isPending) {
    return (
      <PageFrame className="max-w-[980px]">
        <LoadingState label="正在生成练习报告…" />
      </PageFrame>
    );
  }
  if (report.isError) {
    return (
      <PageFrame className="max-w-[980px]">
        <ErrorState
          message={report.error.message}
          onRetry={() => void report.refetch()}
        />
      </PageFrame>
    );
  }

  const data = report.data;
  const session = data.session;
  const answeredItems = data.items.filter((item) => item.attempts > 0);

  return (
    <PageFrame className="max-w-[980px]">
      <PageIntro
        actions={
          <Button asChild size="sm" variant="outline">
            <Link to={`/w/${workspaceId}/practice`}>
              <ArrowLeft className="size-4" />
              返回练习中心
            </Link>
          </Button>
        }
        description={`${formatDateTime(session.completed_at ?? session.created_at)} · ${
          session.title || "练习"
        }`}
        eyebrow="Session report"
        title="本次练习完成"
      />

      <Surface className="p-5">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {[
            { label: "本次题数", value: `${session.planned_question_count}` },
            { label: "首次正确", value: `${session.first_try_correct_count}` },
            { label: "最终正确", value: `${session.final_correct_count}` },
            {
              label: "用时",
              value:
                session.duration_seconds > 0
                  ? `${Math.max(1, Math.round(session.duration_seconds / 60))} 分钟`
                  : "—",
            },
          ].map((item) => (
            <div key={item.label}>
              <p className="text-xs text-muted-foreground">{item.label}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{item.value}</p>
            </div>
          ))}
        </div>
        <div className="mt-5 grid gap-4 border-t pt-5 sm:grid-cols-2">
          <div>
            <p className="text-xs text-muted-foreground">首次正确率</p>
            <p className="mt-1 text-3xl font-semibold tabular-nums text-primary">
              {percent(session.first_try_accuracy)}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              每题第一次作答就答对的比例，重试不会抬高它
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">最终正确率</p>
            <p className="mt-1 text-3xl font-semibold tabular-nums">
              {percent(session.final_accuracy)}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              含重试后答对，共 {session.total_attempt_count} 次提交
            </p>
          </div>
        </div>
      </Surface>

      <div className="grid gap-5 lg:grid-cols-2">
        <Surface className="p-5">
          <SectionHeading
            description="本次产生了「合格回忆」：复习间隔已按证据重新排期"
            title="已巩固"
          />
          {data.consolidated.length ? (
            <ul className="mt-4 divide-y">
              {data.consolidated.map((item) => (
                <li className="py-3" key={item.node_id}>
                  <p className="flex items-center gap-2 text-sm font-semibold">
                    <CheckCircle2 className="size-4 text-primary" />
                    {item.label}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    本次 {item.first_try_correct} / {item.planned} 首次正确
                    {item.next_review_at
                      ? ` · 下一次复习：${formatDay(item.next_review_at)}`
                      : ""}
                  </p>
                  <GraphNodeLink
                    className="mt-1"
                    graphId={item.graph_id}
                    label="查看图谱"
                    nodeId={item.node_id}
                    workspaceId={workspaceId}
                  />
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">
              本次没有知识点产生「合格回忆」（需要作答正确并被重新排期）。
            </p>
          )}
        </Surface>

        <Surface className="p-5">
          <SectionHeading
            description="仍有缺口的知识点，以及系统给出的下一步建议"
            title="仍需加强"
          />
          {data.attention.length ? (
            <ul className="mt-4 divide-y">
              {data.attention.map((item) => (
                <li className="py-3" key={item.node_id}>
                  <p className="flex items-center gap-2 text-sm font-semibold">
                    <AlertTriangle className="size-4 text-amber-500" />
                    {item.label}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.reason} · 首次正确 {item.first_try_correct}/{item.planned} ·
                    提交 {item.attempts} 次
                  </p>
                  {item.detail ? (
                    <p className="mt-1 text-sm leading-6">{item.detail}</p>
                  ) : null}
                  {item.misconceptions.length ? (
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      连续遗漏：{item.misconceptions.join("、")}
                    </p>
                  ) : null}
                  <div className="mt-2 flex items-center gap-2">
                    <GraphNodeLink
                      graphId={item.graph_id}
                      label="回到图谱重新学习"
                      nodeId={item.node_id}
                      workspaceId={workspaceId}
                    />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">
              本次练习没有留下需要加强的知识点。
            </p>
          )}
        </Surface>
      </div>

      {data.misconceptions.length ? (
        <Surface className="p-5">
          <SectionHeading
            description="来自本次作答的真实评价结果（漏选、要点遗漏等）"
            title="发现的误区"
          />
          <ul className="mt-4 space-y-2">
            {data.misconceptions.map((item) => (
              <li
                className="flex flex-wrap items-center gap-2 rounded-xl border p-3 text-sm"
                key={`${item.node_id}-${item.summary}`}
              >
                <Badge variant="outline">{item.node_label}</Badge>
                <span className="min-w-0 flex-1">{item.summary}</span>
                {item.count && item.count > 1 ? (
                  <Badge variant="secondary">出现 {item.count} 次</Badge>
                ) : null}
                <GraphNodeLink
                  graphId={item.graph_id}
                  label="查看图谱"
                  nodeId={item.node_id}
                  workspaceId={workspaceId}
                />
              </li>
            ))}
          </ul>
        </Surface>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-2">
        <Surface className="p-5">
          <SectionHeading title="掌握度变化" />
          {data.mastery_changes.length ? (
            <ul className="mt-4 divide-y">
              {data.mastery_changes.map((item) => (
                <li className="flex items-center justify-between gap-3 py-3" key={item.node_id}>
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{item.node_label}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      掌握分 {percent(item.mastery_before)} → {percent(item.mastery_after)}
                      {item.stars_before !== null && item.stars_after !== null
                        ? ` · 成长星 ${item.stars_before} → ${item.stars_after}`
                        : ""}
                    </p>
                  </div>
                  <StatePill label="真实变化" status="approved" />
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-sm text-muted-foreground">
              本次没有产生可报告的掌握度变化（证据与掌握状态未改变，不显示伪指标）。
            </p>
          )}
        </Surface>

        <Surface className="p-5">
          <SectionHeading title="下一次建议复习" />
          <p className="mt-4 text-2xl font-semibold">
            {data.next_review.due_at ? formatDay(data.next_review.due_at) : "暂无排期"}
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {data.next_review.node_count
              ? `${data.next_review.node_count} 个知识点`
              : "本次练习没有生成排期"}
            {data.next_review.estimated_minutes
              ? ` · 约 ${data.next_review.estimated_minutes} 分钟`
              : ""}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              disabled={again.isPending}
              onClick={() => again.mutate()}
              size="sm"
            >
              <Plus className="size-4" />
              {again.isPending ? "正在准备…" : "继续加练"}
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to={`/w/${workspaceId}/graphs`}>
                <Layers className="size-4" />
                查看图谱
              </Link>
            </Button>
          </div>
        </Surface>
      </div>

      <Surface className="p-5">
        <SectionHeading
          description="每题的真实作答与评价结果"
          title="本次题目明细"
        />
        <ul className="mt-4 divide-y">
          {answeredItems.map((item) => (
            <li className="py-3" key={item.exercise_id}>
              <div className="flex flex-wrap items-center gap-2">
                <StatePill
                  label={
                    item.state === "correct"
                      ? "正确"
                      : item.state === "partial"
                        ? "部分正确"
                        : item.state === "incorrect"
                          ? "错误"
                          : "未作答"
                  }
                  status={
                    item.state === "correct"
                      ? "approved"
                      : item.state === "partial"
                        ? "pending"
                        : item.state === "incorrect"
                          ? "failed"
                          : "pending"
                  }
                />
                <Badge variant="outline">{item.node_label}</Badge>
                <span className="text-xs text-muted-foreground">
                  提交 {item.attempts} 次
                  {item.hint_count ? ` · 提示 ${item.hint_count} 次` : ""}
                  {item.duration_ms
                    ? ` · 用时 ${Math.max(1, Math.round(item.duration_ms / 1000))} 秒`
                    : ""}
                </span>
              </div>
              <p className="mt-1.5 text-sm">{item.prompt}</p>
              {item.last_feedback ? (
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {item.last_feedback}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </Surface>
    </PageFrame>
  );
}
