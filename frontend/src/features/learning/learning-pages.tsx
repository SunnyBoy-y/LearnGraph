import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, X } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  answerExercise,
  decideEvidence,
  getMastery,
  listEvidence,
  listExercises,
  runMasteryReview,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import { saveBlobViaNative } from "@/lib/native-download";
import type { Evidence } from "@/types/learning";
import { ExerciseAnswerCard } from "./exercise-cards";
import { questionTypeLabel } from "./exercise-labels";

export { RoadmapPlannerPage as RoadmapPage } from "./roadmap-page";

function downloadJson(name: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  // 移动端 WebView：纯前端生成的 blob 交给原生 base64 通道
  void saveBlobViaNative(blob, name).then((handled) => {
    if (handled) return;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    URL.revokeObjectURL(url);
  });
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
  const { workspaceId } = useAuth();
  const queryClient = useQueryClient();
  const evidence = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "evidence"),
    queryFn: listEvidence,
  });
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
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "evidence"),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "mastery"),
      });
    },
  });
  const review = useMutation({
    mutationFn: () => runMasteryReview(),
    onSuccess: () => {
      toast.success("掌握度更新已完成");
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "mastery"),
      });
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
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "evidence"),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "mastery"),
      });
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

export function ExerciseAnswerPage() {
  const { questionId = "", workspaceId = "", setId = "default" } = useParams();
  const queryClient = useQueryClient();
  const exercises = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "exercises", { setId }),
    queryFn: () =>
      listExercises({
        batchId: setId !== "default" ? setId : undefined,
      }),
  });
  const mastery = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "mastery"),
    queryFn: getMastery,
  });
  const exercise = exercises.data?.find((item) => item.id === questionId);
  const [answer, setAnswer] = useState<string | string[]>("");
  const submit = useMutation({
    mutationFn: () => answerExercise(questionId, { answer }),
    onSuccess: (result) => {
      toast[result.is_correct ? "success" : "error"](result.feedback);
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "exercises"),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "mastery"),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "mastery-schedules"),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "evidence"),
      });
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
