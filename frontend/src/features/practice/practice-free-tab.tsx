import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Minus, Plus, Sparkles } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import {
  createPracticeSession,
  generateExercises,
  getMastery,
  listFiles,
} from "@/api";
import { ApiError } from "@/api/client";
import {
  ErrorState,
  SectionHeading,
  Surface,
} from "@/components/shared/page-elements";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { PracticeSessionMode } from "@/types/practice";
import { evidenceStateLabel, retrievalStateLabel } from "./practice-format";
import {
  PRACTICE_MODEL_ERROR_CODES,
  PracticeModelSettingLink,
} from "./practice-shared";

const QUESTION_TYPES = [
  { value: "mixed", label: "混合" },
  { value: "single_choice", label: "单选题" },
  { value: "multiple_choice", label: "多选题" },
  { value: "true_false", label: "判断题" },
  { value: "fill_blank", label: "填空题" },
  { value: "short_answer", label: "简答题" },
];

const DIFFICULTIES = [
  { value: "auto", label: "自适应" },
  { value: "easy", label: "简单" },
  { value: "medium", label: "中等" },
  { value: "hard", label: "困难" },
];

const COUNT_PRESETS = [5, 10, 15, 20];

export function PracticeFreeTab() {
  const { workspaceId = "" } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const presetNode = searchParams.get("node") ?? "";
  const [nodeId, setNodeId] = useState(presetNode);
  const [questionType, setQuestionType] = useState("mixed");
  const [difficulty, setDifficulty] = useState("auto");
  const [count, setCount] = useState(5);
  const [fileIds, setFileIds] = useState<string[]>([]);
  const [batchId, setBatchId] = useState<string | null>(null);
  // 模型侧失败（没有可用模型 / 模型被 Provider 拒绝）时，光弹一个 toast 用户
  // 不知道该改哪里：把原因留在页面上，并给出直达「设置 → 功能模型」的入口。
  const [modelError, setModelError] = useState<string | null>(null);

  useEffect(() => {
    if (presetNode) setNodeId(presetNode);
  }, [presetNode]);

  const mastery = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "mastery"),
    queryFn: getMastery,
  });
  const files = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "files"),
    queryFn: () => listFiles(),
  });

  const resolvedNodeId = nodeId || mastery.data?.[0]?.node_id || "";
  const indexedFiles = useMemo(
    () => (files.data ?? []).filter((file) => file.parse_status === "indexed"),
    [files.data],
  );

  const start = useMutation({
    mutationFn: (mode: PracticeSessionMode) =>
      createPracticeSession({
        mode,
        node_ids: [resolvedNodeId],
        question_type: questionType,
        count,
        difficulty:
          difficulty === "auto" ? null : (difficulty as "easy" | "medium" | "hard"),
        file_ids: fileIds,
        generation_batch_id: mode === "material" ? batchId : null,
      }),
    onSuccess: (view) => {
      setModelError(null);
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "practice-overview"),
      });
      navigate(`/w/${workspaceId}/practice/session/${view.session.id}`);
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "无法开始练习";
      setModelError(
        error instanceof ApiError && PRACTICE_MODEL_ERROR_CODES.has(error.code)
          ? message
          : null,
      );
      toast.error(message);
    },
  });

  const generateOnly = useMutation({
    mutationFn: () =>
      generateExercises({
        node_id: resolvedNodeId,
        question_type: questionType as
          | "single_choice"
          | "multiple_choice"
          | "true_false"
          | "fill_blank"
          | "short_answer"
          | "mixed",
        count: Math.min(10, count),
        difficulty: difficulty === "auto" ? "medium" : (difficulty as "easy" | "medium" | "hard"),
        file_ids: fileIds,
      }),
    onSuccess: (items) => {
      const batch = items[0]?.generation_batch_id ?? null;
      setBatchId(batch);
      setModelError(null);
      toast.success(`已生成 ${items.length} 道题目，可以直接开始练习`);
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "exercises"),
      });
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "题目生成失败";
      setModelError(
        error instanceof ApiError && PRACTICE_MODEL_ERROR_CODES.has(error.code)
          ? message
          : null,
      );
      toast.error(message);
    },
  });

  if (mastery.isPending) {
    return (
      <Surface className="space-y-3 p-5">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
      </Surface>
    );
  }
  if (mastery.isError) {
    return <ErrorState message={mastery.error.message} />;
  }
  if (!mastery.data.length) {
    return (
      <Surface className="p-6 text-center">
        <p className="text-sm font-medium">还没有可练习的知识点</p>
        <p className="mt-1 text-xs text-muted-foreground">
          先创建学习目标，让 LearnGraph 生成知识图谱后即可开始练习。
        </p>
      </Surface>
    );
  }

  return (
    <div className="grid gap-5 xl:grid-cols-12">
      <Surface className="p-5 xl:col-span-7">
        <SectionHeading
          description="选择你想强化的内容；没有题库的知识点会先用远程模型出题。"
          title="自由练习"
        />
        <div className="mt-5 space-y-4">
          <div className="grid gap-2 sm:grid-cols-[6rem_1fr] sm:items-center">
            <Label htmlFor="practice-node">知识点</Label>
            <Select onValueChange={setNodeId} value={resolvedNodeId}>
              <SelectTrigger id="practice-node">
                <SelectValue placeholder="选择节点" />
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

          <div className="grid gap-2 sm:grid-cols-[6rem_1fr] sm:items-center">
            <Label htmlFor="practice-type">题型</Label>
            <Select onValueChange={setQuestionType} value={questionType}>
              <SelectTrigger id="practice-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {QUESTION_TYPES.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2 sm:grid-cols-[6rem_1fr] sm:items-center">
            <Label htmlFor="practice-difficulty">难度</Label>
            <Select onValueChange={setDifficulty} value={difficulty}>
              <SelectTrigger id="practice-difficulty">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DIFFICULTIES.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2 sm:grid-cols-[6rem_1fr] sm:items-center">
            <Label htmlFor="practice-count">题量</Label>
            <div className="flex items-center gap-2">
              <Button
                aria-label="减少题量"
                disabled={count <= 1}
                onClick={() => setCount((current) => Math.max(1, current - 5))}
                size="icon"
                type="button"
                variant="outline"
              >
                <Minus className="size-4" />
              </Button>
              <span
                aria-live="polite"
                className="w-14 text-center text-sm font-semibold tabular-nums"
                id="practice-count"
              >
                {count}
              </span>
              <Button
                aria-label="增加题量"
                disabled={count >= 30}
                onClick={() => setCount((current) => Math.min(30, current + 5))}
                size="icon"
                type="button"
                variant="outline"
              >
                <Plus className="size-4" />
              </Button>
              <div className="ml-1 flex flex-wrap gap-1.5">
                {COUNT_PRESETS.map((preset) => (
                  <Button
                    aria-pressed={count === preset}
                    className="h-9 px-3"
                    key={preset}
                    onClick={() => setCount(preset)}
                    size="sm"
                    type="button"
                    variant={count === preset ? "default" : "outline"}
                  >
                    {preset}
                  </Button>
                ))}
              </div>
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-[6rem_1fr] sm:items-start">
            <Label className="sm:pt-2">参考资料</Label>
            <div className="max-h-40 space-y-2 overflow-auto rounded-xl border p-3">
              {indexedFiles.length ? (
                indexedFiles.map((file) => {
                  const checked = fileIds.includes(file.id);
                  return (
                    <label
                      className="flex cursor-pointer items-center gap-2 text-sm"
                      key={file.id}
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() =>
                          setFileIds((current) =>
                            checked
                              ? current.filter((id) => id !== file.id)
                              : [...current, file.id],
                          )
                        }
                      />
                      <span className="truncate">{file.original_name}</span>
                    </label>
                  );
                })
              ) : (
                <p className="text-xs text-muted-foreground">
                  暂无已索引资料；将按节点信息出题。
                </p>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t pt-4">
            <Button
              disabled={start.isPending || !resolvedNodeId}
              onClick={() => start.mutate(batchId ? "material" : "custom")}
            >
              <Sparkles className="size-4" />
              {start.isPending ? "准备中…" : "开始练习"}
            </Button>
            <Button
              disabled={generateOnly.isPending || !resolvedNodeId}
              onClick={() => generateOnly.mutate()}
              variant="outline"
            >
              {generateOnly.isPending ? "生成中…" : "只生成题目"}
            </Button>
            {batchId ? (
              <Badge variant="secondary">已生成题目，可直接开始练习</Badge>
            ) : null}
          </div>
          {modelError ? (
            <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-3">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
                出题被模型挡住，暂时无法生成题目
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {modelError}
              </p>
              <PracticeModelSettingLink className="mt-3" workspaceId={workspaceId} />
            </div>
          ) : null}
          <p className="text-xs leading-5 text-muted-foreground">
            远程模型不可用时，出题会明确失败并给出原因，不会用本地演示题代替。
          </p>
        </div>
      </Surface>

      <Surface className="p-5 xl:col-span-5">
        <SectionHeading
          description="按最近练习情况排序，可直接开始对应知识点的练习"
          title="知识点一览"
        />
        <ul className="mt-4 max-h-[28rem] divide-y overflow-y-auto pr-1">
          {mastery.data.map((node) => (
            <li
              className="flex items-center justify-between gap-3 py-3"
              key={node.node_id}
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{node.label}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {retrievalStateLabel(node.retrieval_state)} ·{" "}
                  {evidenceStateLabel(node.evidence_state)} · 练习正确{" "}
                  {node.exercise_correct_count ?? 0}/{node.exercise_attempt_count ?? 0}
                </p>
              </div>
              <Button
                onClick={() => setNodeId(node.node_id)}
                size="sm"
                variant="outline"
              >
                选择
              </Button>
            </li>
          ))}
        </ul>
      </Surface>
    </div>
  );
}
