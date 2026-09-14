import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Lightbulb, LogOut, XCircle } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  answerPracticeQuestion,
  completePracticeSession,
  getPracticeSession,
  requestPracticeHint,
  revealPracticeItem,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ExerciseResponseInput } from "@/features/learning/exercise-cards";
import { questionTypeLabel } from "@/features/learning/exercise-labels";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { PracticeAnswerResult, PracticeReveal } from "@/types/practice";
import { GraphNodeLink, PracticeModelSettingLink } from "./practice-shared";
import { formatDay } from "./practice-format";

export function PracticeSessionPage() {
  const { workspaceId: routeWorkspaceId = "", practiceSessionId = "" } = useParams();
  const auth = useAuth();
  const workspaceId = auth.workspaceId ?? routeWorkspaceId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const session = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "practice-session", {
      id: practiceSessionId,
    }),
    queryFn: () => getPracticeSession(practiceSessionId),
    refetchOnWindowFocus: false,
  });

  const [activeId, setActiveId] = useState<string | null>(null);
  const [answer, setAnswer] = useState<string | string[]>("");
  const [feedback, setFeedback] = useState<PracticeAnswerResult | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [reveal, setReveal] = useState<PracticeReveal | null>(null);
  const [exitOpen, setExitOpen] = useState(false);
  const startedAtRef = useRef<number>(Date.now());

  const sessionData = session.data;
  const items = useMemo(() => sessionData?.items ?? [], [sessionData]);
  const currentId = activeId ?? sessionData?.current_exercise_id ?? null;
  const current = useMemo(
    () => items.find((item) => item.exercise_id === currentId) ?? items[0],
    [items, currentId],
  );

  const answeredCount = items.filter((item) => item.attempts > 0).length;
  const total = items.length;
  const allAnswered = total > 0 && answeredCount === total;

  useEffect(() => {
    startedAtRef.current = Date.now();
    setAnswer(current?.question_type === "multiple_choice" ? [] : "");
    setFeedback(null);
    setReveal(null);
  }, [current?.exercise_id, current?.question_type]);

  useEffect(() => {
    if (current?.hints?.length) {
      setHint(current.hints[current.hints.length - 1]);
    }
  }, [current?.exercise_id, current?.hints]);

  const invalidateSession = async () => {
    await queryClient.invalidateQueries({
      queryKey: workspaceQueryKey(workspaceId, "practice-session", {
        id: practiceSessionId,
      }),
    });
    await queryClient.invalidateQueries({
      queryKey: workspaceQueryKey(workspaceId, "practice-overview"),
    });
  };

  const answerMutation = useMutation({
    mutationFn: () =>
      answerPracticeQuestion(practiceSessionId, {
        exercise_id: current.exercise_id,
        answer,
        duration_ms: Date.now() - startedAtRef.current,
      }),
    onSuccess: async (result) => {
      setFeedback(result);
      // 提交后必须停留在当前题：会话刷新会把未作答指针推进到下一题，
      // 不钉住的话用户就看不到错误诊断 / 重试 / 讲解。
      setActiveId(current.exercise_id);
      await invalidateSession();
      if (result.is_correct && current?.explanation_available) {
        try {
          setReveal(await revealPracticeItem(practiceSessionId, current.exercise_id));
        } catch {
          setReveal(null);
        }
      }
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "提交失败"),
  });

  const hintMutation = useMutation({
    mutationFn: () => requestPracticeHint(practiceSessionId, current.exercise_id),
    onSuccess: async (result) => {
      setHint(result.hint);
      await invalidateSession();
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "暂时没有可用提示"),
  });

  const revealMutation = useMutation({
    mutationFn: () => revealPracticeItem(practiceSessionId, current.exercise_id),
    onSuccess: (result) => setReveal(result),
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "讲解暂不可用"),
  });

  const completeMutation = useMutation({
    mutationFn: (abandon: boolean) =>
      completePracticeSession(practiceSessionId, abandon),
    onSuccess: async (report, abandon) => {
      await invalidateSession();
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "practice-wrong-book"),
      });
      if (abandon) {
        toast.message("已退出本次练习，作答记录会保留");
        navigate(`/w/${workspaceId}/practice`);
        return;
      }
      navigate(`/w/${workspaceId}/practice/report/${report.session.id}`);
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "无法完成练习"),
  });

  const nextUnanswered = useMemo(
    () =>
      items.find(
        (item) => item.attempts === 0 && item.position > (current?.position ?? -1),
      ) ??
      items.find((item) => item.attempts === 0) ??
      null,
    [items, current?.position],
  );

  if (session.isPending) {
    return (
      <PageFrame className="max-w-[880px]">
        <LoadingState label="正在恢复练习进度…" />
      </PageFrame>
    );
  }
  if (session.isError) {
    return (
      <PageFrame className="max-w-[880px]">
        <ErrorState
          message={session.error.message}
          onRetry={() => void session.refetch()}
        />
      </PageFrame>
    );
  }
  if (!items.length) {
    return (
      <PageFrame className="max-w-[880px]">
        <ErrorState message="本次练习没有可用题目" />
      </PageFrame>
    );
  }

  const progress = total ? Math.round((answeredCount / total) * 100) : 0;
  const durationSeconds = feedback
    ? Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000))
    : 0;
  const canSubmit = Array.isArray(answer)
    ? answer.length > 0
    : Boolean(answer.trim());

  return (
    <PageFrame className="max-w-[880px] pb-40 sm:pb-24">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
            {session.data.session.title || "练习"}
          </p>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">
            {answeredCount} / {total} 题
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <StatePill
            label={allAnswered ? "已完成" : "进行中"}
            status={allAnswered ? "approved" : "claimed"}
          />
          <Button
            onClick={() => setExitOpen(true)}
            size="sm"
            variant="outline"
          >
            <LogOut className="size-4" />
            退出
          </Button>
        </div>
      </header>

      <div className="flex items-center gap-3">
        <Progress aria-label="练习进度" className="h-2" value={progress} />
        <span className="w-10 shrink-0 text-right text-xs text-muted-foreground tabular-nums">
          {progress}%
        </span>
      </div>

      {session.data.warnings?.length ? (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-3">
          <p className="flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-400">
            <AlertTriangle className="size-3.5" />
            本次练习有知识点没能出题
          </p>
          <ul className="mt-1.5 space-y-1 text-xs leading-5 text-muted-foreground">
            {session.data.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
          {session.data.model_setting_required ? (
            <PracticeModelSettingLink className="mt-3" workspaceId={workspaceId} />
          ) : null}
        </div>
      ) : null}

      {allAnswered ? (
        <Surface className="p-6 text-center">
          <CheckCircle2 className="mx-auto size-8 text-primary" />
          <p className="mt-3 text-lg font-semibold">本次练习已答完</p>
          <p className="mt-1 text-sm text-muted-foreground">
            共 {total} 题，其中 {items.filter((item) => item.state === "unanswered").length}{" "}
            题尚未作答；首次正确率会按每题第一次作答统计。
          </p>
          <Button
            className="mt-4"
            disabled={completeMutation.isPending}
            onClick={() => completeMutation.mutate(false)}
            size="lg"
          >
            {completeMutation.isPending ? "正在生成报告…" : "完成练习并查看报告"}
          </Button>
        </Surface>
      ) : (
        <>
          <Surface className="p-5 sm:p-6">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary">{current.node_label}</Badge>
              <Badge variant="outline">{questionTypeLabel(current.question_type)}</Badge>
              {current.attempts > 0 ? (
                <Badge variant="outline">已作答 {current.attempts} 次</Badge>
              ) : null}
              {current.hint_count > 0 ? (
                <Badge variant="outline">已用提示 {current.hint_count} 次</Badge>
              ) : null}
              <GraphNodeLink
                className="ml-auto"
                graphId={current.graph_id}
                label="查看图谱"
                nodeId={current.node_id}
                workspaceId={workspaceId}
              />
            </div>

            <p className="mt-4 text-base font-semibold leading-7 sm:text-lg">
              {current.prompt}
            </p>

            <div className="mt-4">
              <ExerciseResponseInput
                answer={answer}
                disabled={Boolean(feedback)}
                idPrefix={`session-${current.exercise_id}`}
                onAnswerChange={setAnswer}
                options={current.options}
                questionType={current.question_type}
              />
            </div>

            {hint ? (
              <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50/60 p-3 dark:border-blue-900 dark:bg-blue-950/20">
                <p className="flex items-center gap-1.5 text-xs font-medium text-blue-700 dark:text-blue-300">
                  <Lightbulb className="size-3.5" />
                  提示
                </p>
                <p className="mt-1 text-sm leading-6">{hint}</p>
              </div>
            ) : null}

            {feedback ? (
              <div className="mt-5 border-t pt-5">
                {feedback.is_correct ? (
                  <div className="rounded-xl border border-primary/40 bg-primary/[.04] p-4">
                    <p className="flex items-center gap-2 text-sm font-semibold text-primary">
                      <CheckCircle2 className="size-4" />
                      {feedback.score_ratio >= 0.999
                        ? "回答正确"
                        : "回答正确（部分要点已覆盖）"}
                    </p>
                    {feedback.covered_points.length ? (
                      <>
                        <p className="mt-3 text-xs font-medium">你已经掌握：</p>
                        <ul className="mt-1 space-y-0.5 text-sm">
                          {feedback.covered_points.map((point) => (
                            <li key={point}>• {point}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {feedback.missing_points.length ? (
                      <>
                        <p className="mt-3 text-xs font-medium text-amber-600 dark:text-amber-400">
                          需要注意：
                        </p>
                        <ul className="mt-1 space-y-0.5 text-sm">
                          {feedback.missing_points.map((point) => (
                            <li key={point}>○ {point}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    {feedback.score_ratio < 0.999 ? (
                      <p className="mt-3 text-xs text-muted-foreground">
                        得分比例 {Math.round(feedback.score_ratio * 100)}%
                      </p>
                    ) : null}
                    <p className="mt-3 text-xs text-muted-foreground">
                      {feedback.is_first_try_correct ? "首次作答正确" : "重试后正确"}
                      {durationSeconds ? ` · 用时 ${durationSeconds} 秒` : ""}
                      {feedback.schedule_reason ? ` · ${feedback.schedule_reason}` : ""}
                      {feedback.next_review_at
                        ? ` · 下次复习 ${formatDay(feedback.next_review_at)}`
                        : ""}
                    </p>
                  </div>
                ) : (
                  <div className="rounded-xl border border-amber-300 bg-amber-50/60 p-4 dark:border-amber-900 dark:bg-amber-950/20">
                    <p className="flex items-center gap-2 text-sm font-semibold text-amber-700 dark:text-amber-300">
                      <XCircle className="size-4" />
                      还没有完全掌握
                    </p>
                    <p className="mt-3 text-xs font-medium">你的答案：</p>
                    <p className="mt-1 break-words text-sm">
                      {Array.isArray(answer) ? answer.join("、") : answer}
                    </p>
                    <p className="mt-3 text-xs font-medium">关键问题：</p>
                    <p className="mt-1 text-sm leading-6">
                      {feedback.feedback || "答案没有命中关键要点。"}
                    </p>
                    {feedback.missing_points.length ? (
                      <ul className="mt-2 space-y-0.5 text-sm">
                        {feedback.missing_points.map((point) => (
                          <li key={point}>○ 尚未覆盖：{point}</li>
                        ))}
                      </ul>
                    ) : null}
                    <p className="mt-3 text-xs text-muted-foreground">
                      先根据提示自己再想一次，比直接看答案更容易记住。
                      {feedback.schedule_reason ? ` ${feedback.schedule_reason}` : ""}
                    </p>
                  </div>
                )}

                {reveal ? (
                  <div className="mt-4 rounded-xl border bg-muted/25 p-4">
                    <p className="text-sm font-semibold">讲解</p>
                    {reveal.explanation ? (
                      <p className="mt-2 text-sm leading-6">{reveal.explanation}</p>
                    ) : (
                      <p className="mt-2 text-sm text-muted-foreground">
                        这道题没有生成讲解内容。
                      </p>
                    )}
                    {reveal.answer_display ? (
                      <p className="mt-2 text-sm">
                        参考答案：
                        <span className="font-medium">{reveal.answer_display}</span>
                      </p>
                    ) : null}
                    {current.source_refs.length ? (
                      <p className="mt-2 text-xs text-muted-foreground">
                        依据：{current.source_refs.length} 条资料片段
                        {typeof current.source_refs[0]?.filename === "string"
                          ? ` · 如 ${String(current.source_refs[0].filename)}`
                          : ""}
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
          </Surface>

          <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur sm:static sm:border-0 sm:bg-transparent sm:p-0 sm:backdrop-blur-none">
            <div className="mx-auto flex w-full max-w-[880px] flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                {!feedback ? (
                  <>
                    <span className="hidden text-xs text-muted-foreground sm:inline">
                      不知道？
                    </span>
                    <Button
                      disabled={hintMutation.isPending}
                      onClick={() => hintMutation.mutate()}
                      size="sm"
                      variant="outline"
                    >
                      <Lightbulb className="size-4" />
                      {hintMutation.isPending ? "正在准备…" : "给我一个提示"}
                    </Button>
                  </>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {feedback && !feedback.is_correct ? (
                  <>
                    <Button
                      onClick={() => {
                        setFeedback(null);
                        setAnswer(
                          current.question_type === "multiple_choice" ? [] : "",
                        );
                        startedAtRef.current = Date.now();
                      }}
                      size="sm"
                      variant="outline"
                    >
                      再试一次
                    </Button>
                    {!reveal ? (
                      <Button
                        disabled={revealMutation.isPending}
                        onClick={() => revealMutation.mutate()}
                        size="sm"
                        variant="ghost"
                      >
                        查看讲解
                      </Button>
                    ) : null}
                  </>
                ) : null}
                {!feedback ? (
                  <Button
                    disabled={!canSubmit || answerMutation.isPending}
                    onClick={() => answerMutation.mutate()}
                    size="lg"
                  >
                    {answerMutation.isPending ? "评分中…" : "提交答案"}
                  </Button>
                ) : (
                  <Button
                    disabled={completeMutation.isPending}
                    onClick={() => {
                      if (nextUnanswered) {
                        setActiveId(nextUnanswered.exercise_id);
                      } else {
                        completeMutation.mutate(false);
                      }
                    }}
                    size="lg"
                  >
                    {nextUnanswered ? "下一题 →" : "完成练习并查看报告"}
                  </Button>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      <AlertDialog onOpenChange={setExitOpen} open={exitOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>退出本次练习？</AlertDialogTitle>
            <AlertDialogDescription>
              已作答的 {answeredCount} 题会保存在本次练习中，可以稍后从练习中心继续；
              放弃后本次练习不会生成报告。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续练习</AlertDialogCancel>
            <AlertDialogAction onClick={() => completeMutation.mutate(false)}>
              保存并退出
            </AlertDialogAction>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => completeMutation.mutate(true)}
            >
              放弃本次练习
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageFrame>
  );
}
