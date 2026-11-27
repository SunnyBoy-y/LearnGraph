import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RotateCcw, SquareCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from '@/lib/notifications';

import { createPracticeSession, getWrongBook } from "@/api";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import {
  ErrorState,
  Surface,
} from "@/components/shared/page-elements";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { GraphNodeLink, RecentResultDots } from "./practice-shared";
import { errorTypeLabel, formatDateTime } from "./practice-format";

export function PracticeWrongBookTab() {
  const { workspaceId = "" } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [errorFilter, setErrorFilter] = useState("all");
  const book = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "practice-wrong-book"),
    queryFn: getWrongBook,
  });

  const errorTypes = useMemo(() => {
    const types = new Set<string>();
    for (const node of book.data?.nodes ?? []) {
      for (const exercise of node.exercises) {
        if (exercise.error_type) types.add(exercise.error_type);
      }
    }
    return [...types];
  }, [book.data]);

  const filteredNodes = useMemo(() => {
    const nodes = book.data?.nodes ?? [];
    const q = search.trim().toLocaleLowerCase();
    return nodes.filter((node) => {
      if (
        errorFilter !== "all" &&
        !node.exercises.some((exercise) => exercise.error_type === errorFilter)
      ) {
        return false;
      }
      if (!q) return true;
      return (
        node.label.toLocaleLowerCase().includes(q) ||
        node.repeated_patterns.some((pattern) => pattern.toLocaleLowerCase().includes(q)) ||
        node.exercises.some((exercise) => exercise.prompt.toLocaleLowerCase().includes(q))
      );
    });
  }, [book.data, errorFilter, search]);

  const visibleNodeIds = useMemo(() => filteredNodes.map((node) => node.node_id), [filteredNodes]);
  const selection = useListSelection(visibleNodeIds);
  const selectedNodeIds = useMemo(
    () =>
      (book.data?.nodes ?? [])
        .map((node) => node.node_id)
        .filter((id) => selection.selectedIds.has(id)),
    [book.data, selection.selectedIds],
  );

  const restart = useMutation({
    mutationFn: (nodeIds: string[]) =>
      createPracticeSession({
        mode: "wrong_book",
        node_ids: nodeIds,
        question_type: "mixed",
      }),
    onSuccess: (view) => {
      selection.exitMultiSelect();
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "practice-overview"),
      });
      navigate(`/w/${workspaceId}/practice/session/${view.session.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "无法开始练习"),
  });

  if (book.isPending) {
    return (
      <Surface className="space-y-3 p-5">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </Surface>
    );
  }
  if (book.isError) {
    return <ErrorState message={book.error.message} onRetry={() => void book.refetch()} />;
  }
  const data = book.data;
  if (!data.nodes.length) {
    return (
      <Surface className="p-8 text-center">
        <p className="text-sm font-medium">错题本是空的</p>
        <p className="mt-1 text-xs text-muted-foreground">
          作答错误会自动记录在这里，并按知识点归集。
        </p>
      </Surface>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <Surface className="space-y-4 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold">
              共 {data.total_wrong_questions} 道历史错题，涉及 {data.node_count} 个知识点
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              按知识点归集，重复出现的错误模式会单独标出；可多选多个知识点合并组卷重练。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              aria-pressed={selection.multiSelect}
              disabled={restart.isPending}
              onClick={selection.toggleMultiSelect}
              size="sm"
              type="button"
              variant={selection.multiSelect ? "secondary" : "outline"}
            >
              <SquareCheck className="size-4" />
              {selection.multiSelect ? "退出多选" : "多选组卷"}
            </Button>
            <Button
              onClick={() => void book.refetch()}
              size="sm"
              variant="outline"
            >
              刷新
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <ClearableSearchInput
            aria-label="搜索错题知识点或题干"
            containerClassName="min-w-48 flex-1 sm:max-w-sm"
            inputClassName="h-9"
            onChange={setSearch}
            placeholder="搜索知识点、错因模式或题干…"
            value={search}
          />
          {errorTypes.length > 0 ? (
            <Select onValueChange={setErrorFilter} value={errorFilter}>
              <SelectTrigger aria-label="按错因筛选" className="w-40" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部错因</SelectItem>
                {errorTypes.map((type) => (
                  <SelectItem key={type} value={type}>
                    {errorTypeLabel(type)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
        </div>

        {selection.multiSelect && filteredNodes.length > 0 ? (
          <div
            aria-label="错题本批量重练"
            className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/25 px-3 py-2 text-xs"
            role="region"
          >
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                aria-label="全选当前错题知识点"
                checked={selection.visibleCheckboxState}
                disabled={restart.isPending}
                onCheckedChange={(checked) => selection.toggleVisible(checked === true)}
              />
              全选当前列表
            </label>
            <span className="tabular-nums text-muted-foreground">
              已选 {selection.selectedCount} 个知识点
              {selection.otherSelectedCount > 0 ? `（另有 ${selection.otherSelectedCount} 个不在当前筛选）` : ""}
            </span>
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <Button
                disabled={restart.isPending || !selectedNodeIds.length}
                onClick={() => restart.mutate(selectedNodeIds)}
                size="xs"
                type="button"
              >
                <RotateCcw className="size-3.5" />
                合并重练 ({selectedNodeIds.length})
              </Button>
              {selection.selectedCount > 0 ? (
                <Button disabled={restart.isPending} onClick={selection.clear} size="xs" type="button" variant="ghost">
                  取消选择
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
      </Surface>

      {!filteredNodes.length ? (
        <Surface className="p-8 text-center">
          <p className="text-sm font-medium">没有匹配的错题记录</p>
          <p className="mt-1 text-xs text-muted-foreground">尝试清空搜索关键词或切换错因筛选。</p>
        </Surface>
      ) : null}

      {filteredNodes.map((node) => {
        const selected = selection.multiSelect && selection.selectedIds.has(node.node_id);
        return (
          <Surface className={`p-5 ${selected ? "ring-1 ring-ring" : ""}`} key={node.node_id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-3">
                {selection.multiSelect ? (
                  <Checkbox
                    aria-label={`选择错题知识点 ${node.label}`}
                    className="mt-1"
                    disabled={restart.isPending}
                    {...selection.checkboxProps(node.node_id)}
                  />
                ) : null}
                <div className="min-w-0">
                  <p className="text-sm font-semibold">{node.label}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {node.wrong_question_count} 道相关错题 · 节点累计作答 {node.attempt_count} 次 ·
                    最近 {formatDateTime(node.last_attempt_at)}
                  </p>
                </div>
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
                  disabled={restart.isPending}
                  onClick={() => restart.mutate([node.node_id])}
                  size="sm"
                >
                  <RotateCcw className="size-4" />
                  针对这个问题练习
                </Button>
              </div>
            </div>

            {node.repeated_patterns.length ? (
              <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/60 p-3 dark:border-amber-900 dark:bg-amber-950/20">
                <p className="flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-300">
                  <AlertTriangle className="size-3.5" />
                  重复问题
                </p>
                <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                  {node.repeated_patterns.map((pattern) => (
                    <li key={pattern}>{pattern}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <ul className="mt-3 divide-y">
              {node.exercises.map((exercise) => (
                <li className="py-3" key={exercise.exercise_id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">错 {exercise.wrong_count} 次</Badge>
                    <Badge variant="secondary">作答 {exercise.attempt_count} 次</Badge>
                    {exercise.error_type ? (
                      <Badge variant="outline">{errorTypeLabel(exercise.error_type)}</Badge>
                    ) : null}
                    <span className="text-xs text-muted-foreground">
                      最近 {formatDateTime(exercise.last_wrong_at)}
                    </span>
                  </div>
                  <p className="mt-1.5 text-sm">{exercise.prompt}</p>
                  {exercise.last_feedback ? (
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      {exercise.last_feedback}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
            {node.wrong_question_count > node.exercises.length ? (
              <p className="mt-2 text-xs text-muted-foreground">
                仅列出最近 {node.exercises.length} 道错题，共 {node.wrong_question_count} 道
              </p>
            ) : null}
          </Surface>
        );
      })}
    </div>
  );
}
