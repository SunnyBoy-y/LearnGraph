import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { createPracticeSession, getPracticeLearningReport } from "@/api";
import {
  ErrorState,
  SectionHeading,
  Surface,
} from "@/components/shared/page-elements";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { PracticeWindow } from "@/types/practice";
import {
  GraphNodeLink,
  PracticeTrendChart,
  RecentResultDots,
} from "./practice-shared";
import { percent } from "./practice-format";

const WINDOWS: Array<{ id: PracticeWindow; label: string }> = [
  { id: "7d", label: "近 7 天" },
  { id: "30d", label: "近 30 天" },
  { id: "all", label: "全部" },
];

export function PracticeReportTab() {
  const { workspaceId = "" } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [window, setWindow] = useState<PracticeWindow>("7d");
  const report = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "practice-learning-report", { window }),
    queryFn: () => getPracticeLearningReport(window),
  });

  const practiseNode = useMutation({
    mutationFn: (nodeId: string) =>
      createPracticeSession({ mode: "node", node_ids: [nodeId], count: 5 }),
    onSuccess: (view) => {
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "practice-overview"),
      });
      navigate(`/w/${workspaceId}/practice/session/${view.session.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "无法开始练习"),
  });

  return (
    <div className="flex flex-col gap-5">
      <Surface className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div>
          <p className="text-sm font-semibold">学习报告</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            全部指标来自真实作答、证据与复习排期，不做估算外推。
          </p>
        </div>
        <div className="flex gap-1" role="tablist" aria-label="报告时间范围">
          {WINDOWS.map((item) => (
            <Button
              aria-selected={window === item.id}
              key={item.id}
              onClick={() => setWindow(item.id)}
              role="tab"
              size="sm"
              variant={window === item.id ? "default" : "outline"}
            >
              {item.label}
            </Button>
          ))}
        </div>
      </Surface>

      {report.isPending ? (
        <Surface className="space-y-3 p-5">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-48 w-full" />
        </Surface>
      ) : report.isError ? (
        <ErrorState
          message={report.error.message}
          onRetry={() => void report.refetch()}
        />
      ) : (
        <>
          <Surface className="p-5">
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
              {[
                { label: "总练习题数", value: report.data.answered },
                { label: "首次正确率", value: percent(report.data.first_try_accuracy) },
                { label: "最终正确率", value: percent(report.data.final_accuracy) },
                {
                  label: "练习时长",
                  value: report.data.minutes > 0 ? `${report.data.minutes} 分钟` : "—",
                },
                { label: "已巩固知识点", value: report.data.consolidated_node_count },
                { label: "需要关注节点", value: report.data.attention_node_count },
              ].map((item) => (
                <div key={item.label}>
                  <p className="text-xs text-muted-foreground">{item.label}</p>
                  <p className="mt-1 text-xl font-semibold tabular-nums">
                    {item.value}
                  </p>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              共 {report.data.sessions} 次练习会话；首次正确率以每题第一次作答为准，重试不会把它抬高。
            </p>
          </Surface>

          <Surface className="p-5">
            <SectionHeading
              description="首次正确率与最终正确率的逐日变化；没有练习的日期留空"
              title="趋势"
            />
            <div className="mt-4">
              <PracticeTrendChart points={report.data.trend} />
            </div>
          </Surface>

          <div className="grid gap-5 xl:grid-cols-12">
            <Surface className="p-5 xl:col-span-8">
              <SectionHeading
                description="按首次正确率从低到高排列，可直接回到图谱或立即练习"
                title="知识点表现"
              />
              {report.data.nodes.length ? (
                <ul className="mt-4 divide-y">
                  {report.data.nodes.map((node) => (
                    <li className="py-3" key={node.node_id}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-semibold">{node.label}</p>
                          <p className="mt-0.5 text-xs text-muted-foreground">
                            首次正确率 {percent(node.first_try_accuracy)} · 作答{" "}
                            {node.answered} 题 · 状态：{node.status_label}
                          </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <RecentResultDots results={node.recent_results} />
                          <GraphNodeLink
                            graphId={node.graph_id}
                            label="查看图谱"
                            nodeId={node.node_id}
                            workspaceId={workspaceId}
                          />
                          <Button
                            disabled={practiseNode.isPending}
                            onClick={() => practiseNode.mutate(node.node_id)}
                            size="sm"
                            variant="outline"
                          >
                            练习此节点
                          </Button>
                        </div>
                      </div>
                      {node.misconceptions.length ? (
                        <ul className="mt-2 space-y-0.5 text-xs text-amber-700 dark:text-amber-300">
                          {node.misconceptions.map((item) => (
                            <li key={item}>误区：{item}</li>
                          ))}
                        </ul>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">
                  该时间范围内还没有练习记录。
                </p>
              )}
            </Surface>

            <div className="flex flex-col gap-5 xl:col-span-4">
              <Surface className="p-5">
                <SectionHeading
                  description="同一知识点在 24 小时 / 7 天后的再次作答正确率"
                  title="延迟回忆"
                />
                {report.data.delayed_recall.available ? (
                  <dl className="mt-4 space-y-3">
                    <div className="flex items-baseline justify-between">
                      <dt className="text-sm text-muted-foreground">24 小时后回忆</dt>
                      <dd className="text-lg font-semibold tabular-nums">
                        {percent(report.data.delayed_recall.recall_24h)}
                      </dd>
                    </div>
                    <div className="flex items-baseline justify-between">
                      <dt className="text-sm text-muted-foreground">7 天后回忆</dt>
                      <dd className="text-lg font-semibold tabular-nums">
                        {percent(report.data.delayed_recall.recall_7d)}
                      </dd>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      样本量 {report.data.delayed_recall.sample_size} 次跨天复习
                    </p>
                  </dl>
                ) : (
                  <p className="mt-4 text-sm leading-6 text-muted-foreground">
                    暂不可用：{report.data.delayed_recall.reason}
                  </p>
                )}
              </Surface>

              <Surface className="p-5">
                <SectionHeading
                  description="最近 30 天的练习活动"
                  title="学习日历"
                />
                <ul className="mt-4 max-h-64 space-y-1.5 overflow-y-auto pr-1 text-xs">
                  {[...report.data.calendar]
                    .reverse()
                    .filter((day) => day.answered > 0)
                    .map((day) => (
                      <li className="flex justify-between" key={day.date}>
                        <span>{day.date}</span>
                        <span className="tabular-nums text-muted-foreground">
                          {day.answered} 题 · {day.minutes} 分钟 ·{" "}
                          {percent(day.first_try_accuracy)}
                        </span>
                      </li>
                    ))}
                  {report.data.calendar.every((day) => day.answered === 0) ? (
                    <li className="text-muted-foreground">最近 30 天没有练习记录。</li>
                  ) : null}
                </ul>
              </Surface>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
