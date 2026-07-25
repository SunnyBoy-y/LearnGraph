import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowRight,
  Check,
  Download,
  Filter,
  RefreshCcw,
  Sparkles,
  X,
} from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import {
  answerExercise,
  decideEvidence,
  generateExercises,
  getCurrentUser,
  getMastery,
  listEvidence,
  listExercises,
  listFiles,
  listMasteryReviewJobs,
  listMasterySchedules,
  listMasterySessionStates,
  runMasteryReview,
  tickMasteryScheduler,
} from "@/api";
import {
  ErrorState,
  GrowthStars,
  LoadingState,
  MetricStrip,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  AnswerResult,
  Evidence,
  Exercise,
  ExerciseQuestionType,
  MasterySchedulerTick,
} from "@/types/learning";
import {
  ExerciseAnswerCard,
  ExerciseBankCard,
} from "./exercise-cards";
import { questionTypeLabel } from "./exercise-labels";

export { RoadmapPlannerPage as RoadmapPage } from "./roadmap-page";

function downloadJson(name: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function MasteryPage() {
  const { workspaceId = "" } = useParams();
  const queryClient = useQueryClient();
  const mastery = useQuery({ queryKey: ["mastery"], queryFn: getMastery });
  const operator = useQuery({ queryKey: ["current-user"], queryFn: getCurrentUser });
  const isSystemAdmin = operator.data?.is_system_admin === true;
  const reviewJobs = useQuery({
    queryKey: ["mastery-review-jobs"],
    queryFn: listMasteryReviewJobs,
    enabled: isSystemAdmin,
  });
  const sessionStates = useQuery({
    queryKey: ["mastery-session-states"],
    queryFn: listMasterySessionStates,
    enabled: isSystemAdmin,
  });
  const review = useMutation({
    mutationFn: () => runMasteryReview(),
    onSuccess: () => {
      toast.success("掌握度更新已完成");
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery-review-jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery-session-states"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const tick = useMutation({
    mutationFn: tickMasteryScheduler,
    onSuccess: async (result) => {
      toast.success(
        `调度完成：入队 ${result.enqueued_job_ids.length}、完成 ${result.completed_job_ids.length}、失败 ${result.failed_job_ids.length}`,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["mastery"] }),
        queryClient.invalidateQueries({ queryKey: ["mastery-review-jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["mastery-session-states"] }),
        queryClient.invalidateQueries({ queryKey: ["mastery-schedules"] }),
      ]);
    },
    onError: (error) => toast.error(error.message),
  });
  const [filter, setFilter] = useState("all");
  if (mastery.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (mastery.isError)
    return (
      <PageFrame>
        <ErrorState message={mastery.error.message} />
      </PageFrame>
    );
  const rows = mastery.data.filter(
    (node) =>
      filter === "all" ||
      node.retrieval_state === filter ||
      node.evidence_state === filter,
  );
  return (
    <PageFrame>
      <PageIntro
        description="成长星级只增不减；遗忘、到期复习和证据冲突通过独立状态表达，不显示误导性的掌握百分比。"
        eyebrow="Learning evidence"
        title="掌握度总览"
      />
      <Surface className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b p-5">
          <SectionHeading
            description={`${mastery.data.length} 个持久化节点 · 三层成长矩阵`}
            title="节点掌握状态"
          />
          <div className="flex gap-2">
            <Select onValueChange={setFilter} value={filter}>
              <SelectTrigger className="w-40">
                <Filter className="size-3.5" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="due">due</SelectItem>
                <SelectItem value="relearning">relearning</SelectItem>
                <SelectItem value="conflicted">conflicted</SelectItem>
              </SelectContent>
            </Select>
            <Button
              disabled={review.isPending}
              onClick={() => review.mutate()}
              size="sm"
              variant="outline"
            >
              {review.isPending ? "更新中…" : "立即更新"}
            </Button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-muted/35 text-xs text-muted-foreground">
              <tr>
                <th className="px-5 py-3">节点</th>
                <th className="px-5 py-3">成长星级</th>
                <th className="px-5 py-3">当前可提取性</th>
                <th className="px-5 py-3">证据状态</th>
                <th className="px-5 py-3">关注状态</th>
                <th className="px-5 py-3">下一步</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((node) => (
                <tr className="hover:bg-muted/20" key={node.node_id}>
                  <td className="px-5 py-4 font-medium">{node.label}</td>
                  <td className="px-5 py-4">
                    <GrowthStars value={node.mastery_stars} />
                  </td>
                  <td className="px-5 py-4">
                    <StatePill status={node.retrieval_state} />
                  </td>
                  <td className="px-5 py-4">
                    <StatePill status={node.evidence_state} />
                  </td>
                  <td className="px-5 py-4 text-xs">{node.attention_state}</td>
                  <td className="px-5 py-4">
                    <Button asChild size="xs" variant="ghost">
                      <Link
                        to={`/w/${encodeURIComponent(workspaceId)}/practice?node=${encodeURIComponent(node.node_id)}`}
                      >
                        {node.retrieval_state === "due"
                          ? "复习"
                          : node.retrieval_state === "relearning"
                            ? "重学"
                            : "综合题"}
                        <ArrowRight className="size-3" />
                      </Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Surface>
      <div className="grid gap-5 lg:grid-cols-[1.1fr_.9fr]">
        <Surface className="p-5">
          <SectionHeading
            description="按当前服务端快照展示，不伪造历史趋势"
            title="当前节点星级"
          />
          {mastery.data.length ? (
            <div className="mt-4 h-52">
              <ResponsiveContainer height="100%" width="100%">
                <LineChart
                  data={mastery.data.map((node) => ({
                    label: node.label,
                    stars: node.mastery_stars,
                  }))}
                >
                  <XAxis
                    axisLine={false}
                    dataKey="label"
                    fontSize={11}
                    tickLine={false}
                  />
                  <YAxis
                    axisLine={false}
                    domain={[0, 5]}
                    fontSize={11}
                    tickLine={false}
                    width={24}
                  />
                  <Tooltip />
                  <Line
                    dataKey="stars"
                    dot={{ fill: "var(--primary)" }}
                    stroke="var(--primary)"
                    strokeWidth={2.5}
                    type="monotone"
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="grid h-52 place-items-center text-sm text-muted-foreground">
              暂无掌握度节点
            </p>
          )}
        </Surface>
        <Surface className="p-5">
          <SectionHeading title="如何阅读这些状态" />
          <p className="mt-4 text-sm leading-7 text-muted-foreground">
            星级记录已经达到过的里程碑；当前能否顺畅复述由可提取性表示；证据是否充分、冲突或跨时间稳定由证据状态表示。答错不会扣除已经获得的星级。
          </p>
        </Surface>
      </div>
      {isSystemAdmin ? (
        <Surface className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b p-5">
            <SectionHeading
              description="只对系统管理员显示；Job 与 Session 游标均来自当前工作区的持久化调度事实，手动 Tick 会再次由服务端校验身份和工作区。"
              title="管理员调度诊断"
            />
            <div className="flex gap-2">
              <Button
                disabled={reviewJobs.isFetching || sessionStates.isFetching}
                onClick={() => {
                  void reviewJobs.refetch();
                  void sessionStates.refetch();
                }}
                size="sm"
                variant="outline"
              >
                <RefreshCcw className="size-4" />刷新状态
              </Button>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button disabled={tick.isPending} size="sm" variant="outline">
                    {tick.isPending ? "调度中…" : "运行 Scheduler Tick"}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>执行当前工作区的 Scheduler Tick？</AlertDialogTitle>
                    <AlertDialogDescription>
                      此操作会恢复过期租约、补偿阈值/空闲批次并处理到期节点。服务端用去重键避免同一活动版本或到期快照重复创建 Job。
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>取消</AlertDialogCancel>
                    <AlertDialogAction disabled={tick.isPending} onClick={() => tick.mutate()}>
                      {tick.isPending ? "执行中…" : "确认执行 Tick"}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>
          <div className="grid divide-y lg:grid-cols-2 lg:divide-x lg:divide-y-0">
            <section className="min-w-0 p-5">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold">复习调度 Job</p>
                <Badge variant="outline">{reviewJobs.data?.length ?? 0} 条</Badge>
              </div>
              {reviewJobs.isError ? (
                <p className="mt-4 text-sm text-destructive">{reviewJobs.error.message}</p>
              ) : reviewJobs.isPending ? (
                <p className="mt-4 text-sm text-muted-foreground">正在读取持久化 Job…</p>
              ) : (
                <div className="mt-4 max-h-[32rem] divide-y overflow-auto rounded-xl border">
                  {(reviewJobs.data ?? []).slice(0, 24).map((job) => (
                    <div className="p-3 text-xs" key={job.id}>
                      <div className="flex flex-wrap items-center gap-2">
                        <StatePill status={job.status} />
                        <span className="font-medium">{job.trigger}</span>
                        <span className="font-mono text-muted-foreground">attempt {job.attempt_count}</span>
                      </div>
                      <p className="mt-2 break-all font-mono text-[10px] text-muted-foreground">
                        Job {job.id} · dedupe {job.dedupe_key ?? "—"}
                      </p>
                      <p className="mt-1 text-muted-foreground">
                        节点 {job.node_ids.length} · 创建 {new Date(job.created_at).toLocaleString()} · 完成 {job.completed_at ? new Date(job.completed_at).toLocaleString() : "—"}
                      </p>
                      {job.last_error ? <p className="mt-1 break-all text-destructive">{job.last_error}</p> : null}
                      <details className="mt-2">
                        <summary className="cursor-pointer text-muted-foreground">查看规则报告</summary>
                        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-2 font-mono text-[10px]">{JSON.stringify(job.report, null, 2)}</pre>
                      </details>
                    </div>
                  ))}
                  {!(reviewJobs.data ?? []).length ? <p className="p-5 text-sm text-muted-foreground">当前工作区尚无持久化调度 Job。</p> : null}
                </div>
              )}
            </section>
            <section className="min-w-0 p-5">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-semibold">Session 分析游标</p>
                <Badge variant="outline">{sessionStates.data?.length ?? 0} 个 Session</Badge>
              </div>
              {sessionStates.isError ? (
                <p className="mt-4 text-sm text-destructive">{sessionStates.error.message}</p>
              ) : sessionStates.isPending ? (
                <p className="mt-4 text-sm text-muted-foreground">正在读取 Session 游标…</p>
              ) : (
                <div className="mt-4 max-h-[32rem] divide-y overflow-auto rounded-xl border">
                  {(sessionStates.data ?? []).map((state) => (
                    <div className="p-3 text-xs" key={state.id}>
                      <p className="break-all font-mono text-[10px] text-muted-foreground">Session {state.session_id}</p>
                      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
                        <span>待分析消息 {state.pending_message_count}</span>
                        <span>activity {state.activity_version}</span>
                        <span>processed {state.processed_version}</span>
                        <span>enqueued {state.enqueued_version}</span>
                      </div>
                      <p className="mt-1 text-muted-foreground">
                        空闲截止 {state.idle_due_at ? new Date(state.idle_due_at).toLocaleString() : "—"} · 上次处理 {state.last_processed_at ? new Date(state.last_processed_at).toLocaleString() : "—"}
                      </p>
                      {state.pending_node_ids.length ? <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">待处理节点：{state.pending_node_ids.join(", ")}</p> : null}
                    </div>
                  ))}
                  {!(sessionStates.data ?? []).length ? <p className="p-5 text-sm text-muted-foreground">当前工作区没有待观察的 Session 游标。</p> : null}
                </div>
              )}
            </section>
          </div>
          {tick.data ? <SchedulerTickSummary result={tick.data} /> : null}
        </Surface>
      ) : null}
    </PageFrame>
  );
}

function SchedulerTickSummary({ result }: { result: MasterySchedulerTick }) {
  const groups: Array<[string, string[]]> = [
    ["恢复 Job", result.recovered_job_ids],
    ["入队 Job", result.enqueued_job_ids],
    ["完成 Job", result.completed_job_ids],
    ["失败 Job", result.failed_job_ids],
    ["阈值 Session", result.threshold_session_ids],
    ["空闲 Session", result.idle_session_ids],
    ["到期节点", result.due_node_ids],
  ];
  return (
    <div className="border-t bg-muted/20 p-5">
      <p className="text-sm font-semibold">最近一次本页 Tick 返回</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {groups.map(([label, ids]) => (
          <div className="rounded-xl border bg-background/80 p-3 text-xs" key={label}>
            <p className="text-muted-foreground">{label}</p>
            <p className="mt-1 font-mono text-sm">{ids.length}</p>
            {ids.length ? <p className="mt-1 line-clamp-2 break-all font-mono text-[10px] text-muted-foreground">{ids.join(", ")}</p> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function EvidenceRow({
  item,
  onDecision,
  busy,
}: {
  item: Evidence;
  onDecision: (decision: "accepted" | "rejected") => void;
  busy: boolean;
}) {
  const status =
    item.status === "accepted"
      ? "approved"
      : item.status === "rejected"
        ? "failed"
        : item.metadata_json.conflicted
          ? "conflicted"
          : "pending";
  return (
    <div className="flex flex-col gap-4 py-5 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <StatePill
          label={
            item.status === "accepted"
              ? "自动接受"
              : item.metadata_json.conflicted
                ? "冲突"
                : "待审核"
          }
          status={status}
        />
        <p className="mt-2 text-sm font-semibold">{item.summary}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          节点：{item.node_id} · 来源：{item.source_type} · 置信度{" "}
          {item.confidence.toFixed(2)}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        <details className="relative">
          <summary className="cursor-pointer rounded-lg border px-3 py-1.5 text-sm">
            详情
          </summary>
          <pre className="absolute right-0 z-20 mt-2 max-h-72 w-80 overflow-auto whitespace-pre-wrap rounded-xl border bg-card p-3 font-mono text-[10px] shadow-lg">
            {JSON.stringify(item.metadata_json, null, 2)}
          </pre>
        </details>
        <Button
          disabled={busy || item.status === "accepted"}
          onClick={() => onDecision("accepted")}
          size="sm"
        >
          <Check className="size-4" />
          接受
        </Button>
        <Button
          disabled={busy || item.status === "rejected"}
          onClick={() => onDecision("rejected")}
          size="sm"
          variant="outline"
        >
          <X className="size-4" />
          拒绝
        </Button>
      </div>
    </div>
  );
}

export function EvidenceReviewPage() {
  const queryClient = useQueryClient();
  const evidence = useQuery({ queryKey: ["evidence"], queryFn: listEvidence });
  const decision = useMutation({
    mutationFn: ({
      id,
      choice,
    }: {
      id: string;
      choice: "accepted" | "rejected";
    }) =>
      decideEvidence(id, { decision: choice, reason: "用户在审核箱中确认" }),
    onSuccess: () => {
      toast.success("审核结果已写入证据日志");
      void queryClient.invalidateQueries({ queryKey: ["evidence"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
    },
  });
  const review = useMutation({
    mutationFn: () => runMasteryReview(),
    onSuccess: () => {
      toast.success("掌握度更新已完成");
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
    },
  });
  const batch = useMutation({
    mutationFn: async () =>
      Promise.all(
        evidence.data
          ?.filter((item) => item.status === "pending")
          .map((item) =>
            decideEvidence(item.id, {
              decision: "accepted",
              reason: "用户批量确认",
            }),
          ) ?? [],
      ),
    onSuccess: () => {
      toast.success("待审核证据已逐项接受");
      void queryClient.invalidateQueries({ queryKey: ["evidence"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
    },
  });
  if (evidence.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (evidence.isError)
    return (
      <PageFrame>
        <ErrorState message={evidence.error.message} />
      </PageFrame>
    );
  const evidenceRows: Evidence[] = evidence.data;
  return (
    <PageFrame>
      <PageIntro
        actions={
          <Button
            onClick={() =>
              downloadJson("learngraph-evidence.json", evidence.data)
            }
            size="sm"
            variant="outline"
          >
            <Download className="size-4" />
            导出证据链
          </Button>
        }
        description="客观低风险证据可以自动接受；开放解释、冲突证据和候选记忆需要人工审核并保留撤销记录。"
        eyebrow="Evidence log"
        title="证据日志与审核箱"
      />
      <Surface className="px-5">
        <div className="divide-y">
          {evidenceRows.map((item) => (
            <EvidenceRow
              busy={decision.isPending}
              item={item}
              key={item.id}
              onDecision={(choice) => decision.mutate({ id: item.id, choice })}
            />
          ))}
          {!evidenceRows.length ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              当前没有待审核证据。
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t py-4">
          <div className="flex gap-2">
            <Button
              disabled={review.isPending}
              onClick={() => review.mutate()}
              size="sm"
              variant="outline"
            >
              {review.isPending ? "更新中…" : "立即更新掌握度"}
            </Button>
            <Button
              disabled={
                batch.isPending ||
                !evidence.data.some((item) => item.status === "pending")
              }
              onClick={() => batch.mutate()}
              size="sm"
              variant="outline"
            >
              {batch.isPending ? "处理中…" : "批量接受"}
            </Button>
          </div>
          <Badge variant="secondary">所有接受/拒绝均可撤销</Badge>
        </div>
      </Surface>
    </PageFrame>
  );
}

export function PracticePage() {
  const { workspaceId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [wrongOnly, setWrongOnly] = useState(false);
  const [questionType, setQuestionType] =
    useState<ExerciseQuestionType>("mixed");
  const [nodeId, setNodeId] = useState(() => searchParams.get("node") ?? "");
  const [count, setCount] = useState(5);
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [activeBatchId, setActiveBatchId] = useState<string | null>(
    () => searchParams.get("batch") ?? null,
  );
  const [batchAnswers, setBatchAnswers] = useState<
    Record<string, string | string[]>
  >({});
  const [batchResults, setBatchResults] = useState<
    Record<string, AnswerResult>
  >({});
  const [submittingId, setSubmittingId] = useState<string | null>(null);

  const exercises = useQuery({
    queryKey: ["exercises", { wrongOnly, nodeId, activeBatchId }],
    queryFn: () =>
      listExercises({
        wrongOnly,
        nodeId: nodeId || undefined,
        batchId: activeBatchId || undefined,
      }),
  });
  const mastery = useQuery({ queryKey: ["mastery"], queryFn: getMastery });
  const schedules = useQuery({
    queryKey: ["mastery-schedules"],
    queryFn: listMasterySchedules,
  });
  const files = useQuery({ queryKey: ["files"], queryFn: () => listFiles() });

  const resolvedNodeId = nodeId || mastery.data?.[0]?.node_id || "";

  const generate = useMutation({
    mutationFn: () =>
      generateExercises({
        node_id: resolvedNodeId,
        question_type: questionType,
        count,
        file_ids: selectedFileIds,
      }),
    onSuccess: (items) => {
      toast.success(`已生成 ${items.length} 道练习`);
      const batchId = items[0]?.generation_batch_id ?? null;
      setActiveBatchId(batchId);
      setBatchAnswers({});
      setBatchResults({});
      void queryClient.invalidateQueries({ queryKey: ["exercises"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
    },
    onError: (error) => toast.error(error.message),
  });

  if (exercises.isPending || mastery.isPending || schedules.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (exercises.isError || mastery.isError || schedules.isError)
    return (
      <PageFrame>
        <ErrorState
          message={
            (exercises.error ?? mastery.error ?? schedules.error)?.message ??
            "练习读取失败"
          }
        />
      </PageFrame>
    );

  const dueNodeIds = new Set(
    (schedules.data ?? [])
      .filter(
        (item) =>
          item.next_review_at && new Date(item.next_review_at) <= new Date(),
      )
      .map((item) => item.node_id),
  );
  const queue = (mastery.data ?? []).filter(
    (node) =>
      dueNodeIds.has(node.node_id) ||
      ["due", "relearning", "due_soon"].includes(node.retrieval_state),
  );
  const selectedMastery = (mastery.data ?? []).find(
    (node) => node.node_id === resolvedNodeId,
  );
  const attempt = selectedMastery?.exercise_attempt_count ?? 0;
  const correct = selectedMastery?.exercise_correct_count ?? 0;
  const correctnessRate =
    attempt > 0 ? `${Math.round((correct / attempt) * 100)}%` : "—";
  const attentionCount = mastery.data.filter(
    (node) =>
      node.attention_state !== "normal" ||
      ["due", "relearning", "conflicted"].includes(node.retrieval_state) ||
      node.evidence_state === "conflicted",
  ).length;
  const scheduledCount = schedules.data.filter(
    (item) => item.next_review_at,
  ).length;
  const indexedFiles = (files.data ?? []).filter(
    (file) => file.parse_status === "indexed",
  );
  const batchItems = activeBatchId
    ? exercises.data.filter(
        (item) => item.generation_batch_id === activeBatchId,
      )
    : [];
  const bankItems = exercises.data;

  const submitOne = async (exercise: Exercise) => {
    const answer = batchAnswers[exercise.id];
    const hasAnswer = Array.isArray(answer)
      ? answer.length > 0
      : Boolean(answer?.trim());
    if (!hasAnswer) {
      toast.error("请先作答");
      return;
    }
    setSubmittingId(exercise.id);
    try {
      const result = await answerExercise(exercise.id, { answer });
      setBatchResults((current) => ({ ...current, [exercise.id]: result }));
      toast[result.is_correct ? "success" : "error"](result.feedback);
      void queryClient.invalidateQueries({ queryKey: ["exercises"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery-schedules"] });
      void queryClient.invalidateQueries({ queryKey: ["evidence"] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交失败");
    } finally {
      setSubmittingId(null);
    }
  };

  return (
    <PageFrame>
      <PageIntro
        description="按知识点与知识库生成针对性习题；同屏卡片作答后进入题库，练习正确率作为掌握度的可解释依据之一（不直接改写成长星级）。"
        eyebrow="Practice scheduler"
        title="复习调度与练习中心"
      />
      <MetricStrip
        items={[
          {
            label: "到期复习",
            value: queue.length,
            hint: "due / relearning",
            tone: "danger",
          },
          {
            label: "已保存练习",
            value: bankItems.length,
            hint: "当前筛选",
            tone: "positive",
          },
          {
            label: "练习正确率",
            value: correctnessRate,
            hint: selectedMastery
              ? `${correct}/${attempt} · ${selectedMastery.label}`
              : "选择节点后统计",
            tone: "info",
          },
          {
            label: "需关注节点",
            value: attentionCount,
            hint: "由真实状态筛选",
            tone: "warning",
          },
          {
            label: "已排期节点",
            value: scheduledCount,
            hint: "存在 next_review_at",
            tone: "info",
          },
        ]}
      />
      <div className="grid gap-5 xl:grid-cols-[1fr_1fr]">
        <Surface className="p-5">
          <SectionHeading
            description={
              queue.length > 4
                ? `${queue.length} 个节点 · 列表可滚动浏览`
                : undefined
            }
            title="待复习队列"
          />
          <div className="mt-4 max-h-[min(28rem,52vh)] space-y-3 overflow-y-auto overscroll-contain pr-1">
            {queue.map((node) => (
              <div
                className="flex items-center gap-3 rounded-xl border p-4"
                key={node.node_id}
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold">{node.label}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {node.retrieval_state} · {node.evidence_state}
                    {typeof node.exercise_attempt_count === "number" &&
                    node.exercise_attempt_count > 0
                      ? ` · 练习正确 ${node.exercise_correct_count ?? 0}/${node.exercise_attempt_count}`
                      : ""}
                  </p>
                </div>
                {bankItems.find((item) => item.node_id === node.node_id) ? (
                  <Button asChild size="sm">
                    <Link
                      to={`/w/${workspaceId}/practice/default/${
                        bankItems.find((item) => item.node_id === node.node_id)!
                          .id
                      }`}
                    >
                      开始
                    </Link>
                  </Button>
                ) : (
                  <Button
                    onClick={() => setNodeId(node.node_id)}
                    size="sm"
                    variant="outline"
                  >
                    选择生成
                  </Button>
                )}
              </div>
            ))}
            {!queue.length ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                当前没有到期复习。
              </p>
            ) : null}
          </div>
        </Surface>
        <Surface className="p-5">
          <SectionHeading
            description="远程模型结构化出题；无远程 Provider 时会明确失败，不会静默使用本地演示题。"
            title="练习生成器"
          />
          <div className="mt-5 space-y-4">
            <div className="grid grid-cols-[5rem_1fr] items-center gap-3">
              <Label>题型</Label>
              <Select
                onValueChange={(value) =>
                  setQuestionType(value as ExerciseQuestionType)
                }
                value={questionType}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="mixed">混合</SelectItem>
                  <SelectItem value="single_choice">单选题</SelectItem>
                  <SelectItem value="multiple_choice">多选题</SelectItem>
                  <SelectItem value="true_false">判断题</SelectItem>
                  <SelectItem value="fill_blank">填空题</SelectItem>
                  <SelectItem value="short_answer">简答题</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-[5rem_1fr] items-center gap-3">
              <Label>节点</Label>
              <Select onValueChange={setNodeId} value={resolvedNodeId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {mastery.data.map((node) => (
                    <SelectItem key={node.node_id} value={node.node_id}>
                      {node.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-[5rem_1fr] items-center gap-3">
              <Label htmlFor="count">数量</Label>
              <Input
                id="count"
                max={10}
                min={1}
                onChange={(event) =>
                  setCount(
                    Math.max(
                      1,
                      Math.min(10, Number(event.currentTarget.value) || 1),
                    ),
                  )
                }
                type="number"
                value={count}
              />
            </div>
            <div className="grid grid-cols-[5rem_1fr] items-start gap-3">
              <Label className="pt-2">资料</Label>
              <div className="max-h-40 space-y-2 overflow-auto rounded-xl border p-3">
                {indexedFiles.length ? (
                  indexedFiles.map((file) => {
                    const checked = selectedFileIds.includes(file.id);
                    return (
                      <label
                        className="flex cursor-pointer items-center gap-2 text-sm"
                        key={file.id}
                      >
                        <input
                          checked={checked}
                          className="size-4"
                          onChange={() =>
                            setSelectedFileIds((current) =>
                              checked
                                ? current.filter((id) => id !== file.id)
                                : [...current, file.id],
                            )
                          }
                          type="checkbox"
                        />
                        <span className="truncate">{file.original_name}</span>
                      </label>
                    );
                  })
                ) : (
                  <p className="text-xs text-muted-foreground">
                    暂无已索引文件；将仅按节点信息出题。可选关联资料以增强针对性。
                  </p>
                )}
              </div>
            </div>
            <div className="flex flex-wrap gap-2 pt-3">
              <Button
                disabled={generate.isPending || !resolvedNodeId}
                onClick={() => generate.mutate()}
              >
                <Sparkles className="size-4" />
                {generate.isPending ? "生成中…" : "生成练习"}
              </Button>
              <Button
                aria-pressed={wrongOnly}
                onClick={() => setWrongOnly((current) => !current)}
                variant={wrongOnly ? "default" : "outline"}
              >
                {wrongOnly ? "显示全部题目" : "只出错题"}
              </Button>
              {activeBatchId ? (
                <Button
                  onClick={() => {
                    setActiveBatchId(null);
                    setBatchAnswers({});
                    setBatchResults({});
                  }}
                  variant="outline"
                >
                  关闭本批作答
                </Button>
              ) : null}
              <Button
                disabled={!bankItems.length}
                onClick={() =>
                  downloadJson("learngraph-exercises.json", bankItems)
                }
                variant="outline"
              >
                <Download className="size-4" />
                导出题库
              </Button>
            </div>
          </div>
        </Surface>
      </div>

      {batchItems.length ? (
        <Surface className="p-5">
          <SectionHeading
            description={`批次 ${activeBatchId} · 按题型卡片同屏作答与批改`}
            title="本批自测"
          />
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            {batchItems.map((exercise) => (
              <div className="space-y-3" key={exercise.id}>
                <ExerciseAnswerCard
                  answer={
                    batchAnswers[exercise.id] ??
                    (exercise.question_type === "multiple_choice" ? [] : "")
                  }
                  disabled={Boolean(batchResults[exercise.id])}
                  exercise={exercise}
                  onAnswerChange={(value) =>
                    setBatchAnswers((current) => ({
                      ...current,
                      [exercise.id]: value,
                    }))
                  }
                  result={batchResults[exercise.id]}
                />
                {!batchResults[exercise.id] ? (
                  <Button
                    disabled={submittingId === exercise.id}
                    onClick={() => void submitOne(exercise)}
                    size="sm"
                  >
                    {submittingId === exercise.id ? "评分中…" : "提交本题"}
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        </Surface>
      ) : null}

      {bankItems.length ? (
        <Surface className="p-5">
          <SectionHeading title="题库" />
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {bankItems.map((exercise) => (
              <ExerciseBankCard
                exercise={exercise}
                href={`/w/${workspaceId}/practice/${
                  exercise.generation_batch_id || "default"
                }/${exercise.id}`}
                key={exercise.id}
                nodeLabel={
                  mastery.data.find((node) => node.node_id === exercise.node_id)
                    ?.label
                }
              />
            ))}
          </div>
        </Surface>
      ) : wrongOnly ? (
        <Surface className="border-dashed p-8 text-center">
          <p className="text-sm font-medium">当前没有持久化的错题记录</p>
          <p className="mt-2 text-xs text-muted-foreground">
            提交错误答案后，对应 AnswerRecord 会出现在这里。
          </p>
        </Surface>
      ) : null}
    </PageFrame>
  );
}

export function ExerciseAnswerPage() {
  const { questionId = "", workspaceId = "", setId = "default" } = useParams();
  const queryClient = useQueryClient();
  const exercises = useQuery({
    queryKey: ["exercises", { setId }],
    queryFn: () =>
      listExercises({
        batchId: setId !== "default" ? setId : undefined,
      }),
  });
  const mastery = useQuery({ queryKey: ["mastery"], queryFn: getMastery });
  const exercise = exercises.data?.find((item) => item.id === questionId);
  const [answer, setAnswer] = useState<string | string[]>("");
  const submit = useMutation({
    mutationFn: () => answerExercise(questionId, { answer }),
    onSuccess: (result) => {
      toast[result.is_correct ? "success" : "error"](result.feedback);
      void queryClient.invalidateQueries({ queryKey: ["exercises"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
      void queryClient.invalidateQueries({ queryKey: ["mastery-schedules"] });
      void queryClient.invalidateQueries({ queryKey: ["evidence"] });
    },
    onError: (error) => toast.error(error.message),
  });

  if (exercises.isPending)
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  if (exercises.isError)
    return (
      <PageFrame>
        <ErrorState message={exercises.error.message} />
      </PageFrame>
    );
  if (!exercise)
    return (
      <PageFrame>
        <ErrorState message="题目不存在" />
      </PageFrame>
    );

  const result = submit.data;
  const siblings = exercises.data ?? [];
  const exerciseIndex = siblings.findIndex((item) => item.id === exercise.id);
  const prev = exerciseIndex > 0 ? siblings[exerciseIndex - 1] : null;
  const next =
    exerciseIndex >= 0 && exerciseIndex < siblings.length - 1
      ? siblings[exerciseIndex + 1]
      : null;
  const hasAnswer = Array.isArray(answer)
    ? answer.length > 0
    : Boolean(answer.trim());
  const nodeLabel =
    mastery.data?.find((node) => node.node_id === exercise.node_id)?.label ??
    exercise.node_id;

  return (
    <PageFrame>
      <PageIntro
        actions={
          <StatePill
            label={`题目 ${exerciseIndex + 1}/${siblings.length || 1} · ${questionTypeLabel(exercise.question_type)}`}
            status="pending"
          />
        }
        description={`节点 ${nodeLabel}。先作答再显示讲解；每次提交生成 AnswerRecord，并回流可审计 Evidence。`}
        eyebrow="Exercise set"
        title="题目作答与讲解"
      />
      <ExerciseAnswerCard
        answer={
          answer === "" && exercise.question_type === "multiple_choice"
            ? []
            : answer
        }
        disabled={Boolean(result)}
        exercise={exercise}
        onAnswerChange={setAnswer}
        result={result}
      />
      <div className="flex flex-wrap justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          {prev ? (
            <Button asChild variant="outline">
              <Link to={`/w/${workspaceId}/practice/${setId}/${prev.id}`}>
                上一题
              </Link>
            </Button>
          ) : null}
          {next ? (
            <Button asChild variant="outline">
              <Link to={`/w/${workspaceId}/practice/${setId}/${next.id}`}>
                下一题
              </Link>
            </Button>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {!result ? (
            <Button
              disabled={!hasAnswer || submit.isPending}
              onClick={() => submit.mutate()}
            >
              {submit.isPending ? "评分中…" : "提交"}
            </Button>
          ) : null}
          <Button asChild variant="outline">
            <Link to={`/w/${workspaceId}/practice`}>返回练习中心</Link>
          </Button>
        </div>
      </div>
    </PageFrame>
  );
}
