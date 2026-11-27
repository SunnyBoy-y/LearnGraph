import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, FileCode2, X } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { toast } from '@/lib/notifications';

import {
  answerExercise,
  decideEvidence,
  getMastery,
  listEvidence,
  listExercises,
  runMasteryReview,
} from "@/api";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { useActionConfirmation } from "@/components/shared/use-action-confirmation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAuth } from "@/features/auth/auth-context-value";
import { useListSelection } from "@/lib/list-selection";
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
  selected,
  onSelectShift,
  onSelectChange,
  onInspect,
  onDecision,
  busy,
}: {
  item: Evidence;
  selected: boolean;
  onSelectShift: (shift: boolean) => void;
  onSelectChange: (checked: boolean) => void;
  onInspect: () => void;
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
    <div className={`flex flex-col gap-4 py-5 sm:flex-row sm:items-center ${selected ? "rounded-lg bg-primary/5 px-2" : ""}`}>
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <Checkbox
          aria-label={`选择证据：${item.summary}`}
          checked={selected}
          className="mt-1"
          disabled={busy}
          onClickCapture={(event) => onSelectShift(event.shiftKey)}
          onCheckedChange={(checked) => onSelectChange(checked === true)}
        />
        <div className="min-w-0 flex-1">
          <StatePill
            label={
              item.status === "accepted"
                ? "自动接受"
                : item.status === "rejected"
                  ? "已拒绝"
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
      </div>
      <div className="flex shrink-0 gap-2">
        <Button onClick={onInspect} size="sm" type="button" variant="outline">
          <FileCode2 className="size-4" />
          详情
        </Button>
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
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [inspectItem, setInspectItem] = useState<Evidence | null>(null);
  const { confirm, dialog } = useActionConfirmation();
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

  const allEvidenceRows = evidence.data ?? [];
  const filteredRows = useMemo(() => {
    const rows = evidence.data ?? [];
    const q = search.trim().toLocaleLowerCase();
    return rows.filter((item) => {
      if (statusFilter === "pending" && item.status !== "pending") return false;
      if (statusFilter === "accepted" && item.status !== "accepted") return false;
      if (statusFilter === "rejected" && item.status !== "rejected") return false;
      if (statusFilter === "conflicted" && !item.metadata_json.conflicted) return false;
      if (!q) return true;
      return `${item.summary} ${item.node_id} ${item.source_type}`
        .toLocaleLowerCase()
        .includes(q);
    });
  }, [evidence.data, search, statusFilter]);

  const visibleIds = useMemo(() => filteredRows.map((item) => item.id), [filteredRows]);
  const selection = useListSelection(visibleIds, { alwaysActive: true });
  const selectedRows = useMemo(
    () => (evidence.data ?? []).filter((item) => selection.selectedIds.has(item.id)),
    [evidence.data, selection.selectedIds],
  );

  const batchDecide = useMutation({
    mutationFn: async ({
      targets,
      choice,
    }: {
      targets: Evidence[];
      choice: "accepted" | "rejected";
    }) => {
      const results = await Promise.allSettled(
        targets.map((item) =>
          decideEvidence(item.id, {
            decision: choice,
            reason: choice === "accepted" ? "用户批量确认" : "用户批量拒绝",
          }),
        ),
      );
      const succeededIds: string[] = [];
      results.forEach((result, index) => {
        if (result.status === "fulfilled") succeededIds.push(targets[index].id);
      });
      return { choice, succeededIds, failed: targets.length - succeededIds.length };
    },
    onSuccess: ({ choice, succeededIds, failed }) => {
      selection.removeIds(succeededIds);
      const actionLabel = choice === "accepted" ? "接受" : "拒绝";
      if (failed === 0) toast.success(`已${actionLabel} ${succeededIds.length} 条证据`);
      else if (succeededIds.length > 0) toast.warning(`已${actionLabel} ${succeededIds.length} 条证据，${failed} 条失败`);
      else toast.error(`批量${actionLabel}失败`);
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
  const pendingRows = allEvidenceRows.filter((item) => item.status === "pending");
  const acceptableSelected = selectedRows.filter((item) => item.status !== "accepted");
  const rejectableSelected = selectedRows.filter((item) => item.status !== "rejected");
  const busy = decision.isPending || batchDecide.isPending;

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
      <Surface className="px-5 py-4">
        <div className="flex flex-wrap items-center gap-2 pb-4">
          <ClearableSearchInput
            aria-label="搜索证据"
            containerClassName="min-w-48 flex-1 sm:max-w-sm"
            inputClassName="h-9"
            onChange={setSearch}
            placeholder="搜索摘要、节点或来源类型…"
            value={search}
          />
          <Select onValueChange={setStatusFilter} value={statusFilter}>
            <SelectTrigger aria-label="按审核状态筛选" className="w-36" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="pending">待审核</SelectItem>
              <SelectItem value="conflicted">冲突</SelectItem>
              <SelectItem value="accepted">已接受</SelectItem>
              <SelectItem value="rejected">已拒绝</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {filteredRows.length > 0 ? (
          <div
            aria-label="证据批量审核"
            className="mb-2 flex flex-wrap items-center gap-2 rounded-xl border bg-muted/20 px-3 py-2 text-xs"
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
              已选 {selection.selectedCount} 条
              {selection.otherSelectedCount > 0 ? `（另有 ${selection.otherSelectedCount} 条不在当前筛选）` : ""}
            </span>
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <Button
                disabled={busy || !acceptableSelected.length}
                onClick={() =>
                  confirm({
                    title: `接受选中的 ${acceptableSelected.length} 条证据？`,
                    description: "这些证据将写入已接受状态并影响相关知识点掌握度计算。",
                    label: `接受 ${acceptableSelected.length} 条`,
                    run: () => batchDecide.mutate({ targets: acceptableSelected, choice: "accepted" }),
                  })
                }
                size="xs"
                type="button"
                variant="outline"
              >
                <Check className="size-3.5" />
                接受所选
              </Button>
              <Button
                disabled={busy || !rejectableSelected.length}
                onClick={() =>
                  confirm({
                    title: `拒绝选中的 ${rejectableSelected.length} 条证据？`,
                    description: "被拒绝的证据不会计入掌握度提升，后续仍可单独撤销恢复。",
                    label: `拒绝 ${rejectableSelected.length} 条`,
                    destructive: true,
                    run: () => batchDecide.mutate({ targets: rejectableSelected, choice: "rejected" }),
                  })
                }
                size="xs"
                type="button"
                variant="outline"
              >
                <X className="size-3.5" />
                拒绝所选
              </Button>
              {selection.selectedCount > 0 ? (
                <Button disabled={busy} onClick={selection.clear} size="xs" type="button" variant="ghost">
                  取消选择
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className="divide-y">
          {filteredRows.map((item) => (
            <EvidenceRow
              busy={busy}
              item={item}
              key={item.id}
              onDecision={(choice) => decision.mutate({ id: item.id, choice })}
              onInspect={() => setInspectItem(item)}
              onSelectChange={(checked) => selection.toggleItem(item.id, { checked })}
              onSelectShift={(shift) => selection.recordShift({ shiftKey: shift })}
              selected={selection.selectedIds.has(item.id)}
            />
          ))}
          {!filteredRows.length ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {allEvidenceRows.length ? "没有符合筛选条件的证据。" : "当前没有待审核证据。"}
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
              disabled={busy || !pendingRows.length}
              onClick={() =>
                confirm({
                  title: `全部接受 ${pendingRows.length} 条待审核证据？`,
                  description: "将一次性接受当前所有待审核证据，包含需要人工核对的条目，此操作后续可在日志中逐条撤销。",
                  label: `接受全部 ${pendingRows.length} 条`,
                  run: () => batchDecide.mutate({ targets: pendingRows, choice: "accepted" }),
                })
              }
              size="sm"
              variant="outline"
            >
              {batchDecide.isPending ? "处理中…" : `批量接受全部待审核 (${pendingRows.length})`}
            </Button>
          </div>
          <Badge variant="secondary">所有接受/拒绝均可撤销</Badge>
        </div>
      </Surface>

      <Dialog onOpenChange={(open) => { if (!open) setInspectItem(null); }} open={Boolean(inspectItem)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>证据技术详情</DialogTitle>
            <DialogDescription>{inspectItem?.summary}</DialogDescription>
          </DialogHeader>
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-xl border bg-muted/40 p-3 font-mono text-xs" dir="ltr">
            {inspectItem ? JSON.stringify(inspectItem.metadata_json, null, 2) : ""}
          </pre>
        </DialogContent>
      </Dialog>
      {dialog}
    </PageFrame>
  );
}

export function ExerciseAnswerPage() {
  const { questionId = "", workspaceId = "", setId = "default" } = useParams();
  return <ExerciseAnswerContent key={`${workspaceId}:${setId}:${questionId}`} />;
}

function ExerciseAnswerContent() {
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
