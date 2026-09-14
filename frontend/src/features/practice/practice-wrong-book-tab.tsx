import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { createPracticeSession, getWrongBook } from "@/api";
import {
  ErrorState,
  Surface,
} from "@/components/shared/page-elements";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import { GraphNodeLink, RecentResultDots } from "./practice-shared";
import { errorTypeLabel, formatDateTime } from "./practice-format";

export function PracticeWrongBookTab() {
  const { workspaceId = "" } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const book = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "practice-wrong-book"),
    queryFn: getWrongBook,
  });

  const restart = useMutation({
    mutationFn: (nodeId: string) =>
      createPracticeSession({
        mode: "wrong_book",
        node_ids: [nodeId],
        question_type: "mixed",
      }),
    onSuccess: (view) => {
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
      <Surface className="flex flex-wrap items-center justify-between gap-3 p-5">
        <div>
          <p className="text-sm font-semibold">
            共 {data.total_wrong_questions} 道历史错题，涉及 {data.node_count} 个知识点
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            按知识点归集，重复出现的错误模式会单独标出；错题重练使用与今日练习相同的作答与证据链路。
          </p>
        </div>
        <Button
          onClick={() => void book.refetch()}
          size="sm"
          variant="outline"
        >
          刷新
        </Button>
      </Surface>

      {data.nodes.map((node) => (
        <Surface className="p-5" key={node.node_id}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-semibold">{node.label}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {node.wrong_question_count} 道相关错题 · 节点累计作答 {node.attempt_count} 次 ·
                最近 {formatDateTime(node.last_attempt_at)}
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
                disabled={restart.isPending}
                onClick={() => restart.mutate(node.node_id)}
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
      ))}
    </div>
  );
}
