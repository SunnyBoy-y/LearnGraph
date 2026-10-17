import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Brain,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  Search,
  Wrench,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { TaskStatusIcon } from "@/components/shared/task-status-icon";
import {
  voiceTaskActivityText,
  voiceTaskArtifactIsVerified,
  voiceTaskArtifactLabel,
  voiceTaskHasDetail,
  voiceTaskIsActive,
  voiceTaskSourceLabel,
  voiceTaskStatusLabel,
  voiceTaskPlaybackLabel,
  voiceTaskTitle,
  type VoiceTask,
} from "./voice-tasks";

function taskIcon(task: VoiceTask) {
  if (task.status === "ready" || voiceTaskIsActive(task)) {
    return <TaskStatusIcon taskKey={task.taskId} status={task.status === "ready" ? "succeeded" : "running"} className="size-3.5" />;
  }
  if (task.status === "partial") {
    return <CheckCircle2 aria-hidden="true" className="size-3.5" />;
  }
  if (task.status === "failed" || task.status === "interrupted") {
    return <AlertCircle aria-hidden="true" className="size-3.5" />;
  }
  if (task.status === "cancelled" || task.status === "stale") {
    return <XCircle aria-hidden="true" className="size-3.5" />;
  }
  if (task.kind === "research") return <Search aria-hidden="true" className="size-3.5" />;
  if (task.kind === "reasoning") return <Brain aria-hidden="true" className="size-3.5" />;
  if (task.kind === "tool") return <Wrench aria-hidden="true" className="size-3.5" />;
  return <CircleDashed aria-hidden="true" className="size-3.5" />;
}

function elapsedText(createdAt: string, now: number): string {
  const started = new Date(createdAt).getTime();
  if (!Number.isFinite(started)) return "";
  const seconds = Math.max(0, Math.floor((now - started) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h`;
}

function taskSummary(task: VoiceTask, now: number): string {
  const elapsed = voiceTaskIsActive(task) ? elapsedText(task.createdAt, now) : "";
  const progress = task.progress !== undefined && task.progress < 100 ? `${task.progress}%` : "";
  const capability = task.capabilityId
      ? ({
        workspace_read: "读取工作区",
        learning_read: "学习状态",
        practice_prepare: "练习",
        learning_evidence: "学习证据",
        graph_proposal: "图谱提案",
        artifact_create: "子线程产物",
        workspace_task: "工作区任务",
        research: "资料研究",
        reasoning: "深度分析",
      } as Record<string, string>)[task.capabilityId] ?? ""
    : "";
  const playback = voiceTaskPlaybackLabel(task);
  return [capability, voiceTaskStatusLabel(task), playback, elapsed, progress].filter(Boolean).join(" · ");
}

function activityTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export interface VoiceTaskChipProps {
  tasks: VoiceTask[];
  onCancel?: (taskId: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
  onRetryApproval?: (taskId: string) => void;
  busyTaskIds?: ReadonlySet<string>;
}

export function VoiceTaskChip({ tasks, onCancel, onApprove, onReject, onRetryApproval, busyTaskIds }: VoiceTaskChipProps) {
  const hasActive = tasks.some(voiceTaskIsActive);
  const [now, setNow] = useState(() => Date.now());
  const visibleTasks = useMemo(() => {
    return tasks.slice(0, 3);
  }, [tasks]);

  useEffect(() => {
    if (!hasActive) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasActive]);

  if (visibleTasks.length === 0) return null;

  return (
    <div
      aria-label="后台任务"
      aria-live="polite"
      className="relative z-40 mt-1 flex max-w-[min(34rem,calc(100vw-2rem))] flex-wrap items-center justify-center gap-1.5 px-2"
    >
      {visibleTasks.map((task) => (
        <Popover key={task.taskId}>
          <PopoverTrigger asChild>
            <button
              aria-label={`${voiceTaskTitle(task)}：${taskSummary(task, now)}`}
              className={cn(
                "inline-flex h-7 max-w-56 items-center gap-1.5 rounded-full border border-border/70 bg-background/90 px-2.5 text-xs text-muted-foreground shadow-sm backdrop-blur transition-colors hover:bg-muted hover:text-foreground",
                task.status === "failed" && "border-destructive/40 text-destructive",
                task.status === "ready" && "border-emerald-500/30 text-foreground",
                task.status === "stale" && "opacity-70",
              )}
              type="button"
            >
              {taskIcon(task)}
              <span className="min-w-0 truncate">
                <span className="font-medium">{taskSummary(task, now)}</span>
                <span className="mx-1 opacity-50">·</span>
                <span>{voiceTaskTitle(task)}</span>
              </span>
              <ChevronDown aria-hidden="true" className="size-3 shrink-0 opacity-60" />
            </button>
          </PopoverTrigger>
          <PopoverContent align="center" className="w-[min(20rem,calc(100vw-2rem))] p-3" side="top">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 text-muted-foreground">{taskIcon(task)}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{voiceTaskTitle(task)}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{taskSummary(task, now)}</p>
              </div>
            </div>

            {task.progress !== undefined && voiceTaskIsActive(task) ? (
              <div
                aria-label={`进度 ${task.progress}%`}
                className="mt-3 h-1 overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-valuemax={100}
                aria-valuemin={0}
                aria-valuenow={task.progress}
              >
                <span
                  className="block h-full rounded-full bg-primary transition-[width] duration-500"
                  style={{ width: `${task.progress}%` }}
                />
              </div>
            ) : null}

            {task.summary ? (
              <p className="mt-3 whitespace-pre-wrap text-xs leading-relaxed text-foreground/90">
                {task.summary}
              </p>
            ) : null}

            {voiceTaskPlaybackLabel(task) ? (
              <p className="mt-2 text-xs text-muted-foreground" role="status">
                播放状态：{voiceTaskPlaybackLabel(task)}
                {task.playbackEndedAt ? ` · ${activityTime(task.playbackEndedAt)}` : ""}
              </p>
            ) : null}

            {task.approvalRequestPreview ? (
              <p className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">
                确认内容：{task.approvalRequestPreview}
              </p>
            ) : null}

            {task.reason ? (
              <p className="mt-3 whitespace-pre-wrap text-xs leading-relaxed text-destructive">
                {task.reason}
              </p>
            ) : null}

            {task.sources.length ? (
              <div className="mt-3">
                <p className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
                  来源
                </p>
                <ul className="mt-1 space-y-1">
                  {task.sources.slice(0, 6).map((source) => (
                    <li className="text-xs leading-snug" key={source.id}>
                      <span>{voiceTaskSourceLabel(source)}</span>
                      {source.domain ? (
                        <span className="ml-1 text-muted-foreground">· {source.domain}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {task.artifacts.length ? (
              <div className="mt-3">
                <p className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
                  产物
                </p>
                <ul className="mt-1 space-y-1">
                  {task.artifacts.slice(0, 6).map((artifact) => (
                    <li className="text-xs leading-snug" key={artifact.id}>
                      <span>{voiceTaskArtifactLabel(artifact)}</span>
                      {!voiceTaskArtifactIsVerified(artifact) ? (
                        <span className="ml-1 text-muted-foreground">· 未验证</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {task.evidenceRefs.length ? (
              <div className="mt-3">
                <p className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
                  学习证据
                </p>
                <ul className="mt-1 space-y-1">
                  {task.evidenceRefs.slice(0, 6).map((evidenceId) => (
                    <li className="text-xs leading-snug" key={evidenceId}>
                      {evidenceId}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {task.limitations.length ? (
              <div className="mt-3">
                <p className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
                  限制
                </p>
                <ul className="mt-1 list-disc space-y-1 pl-4 text-xs leading-snug text-muted-foreground">
                  {task.limitations.slice(0, 4).map((limitation) => (
                    <li key={limitation}>{limitation}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {!voiceTaskHasDetail(task) ? (
              <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                {voiceTaskActivityText(task)}
              </p>
            ) : null}

            {task.status === "waiting_approval" && (onApprove || onReject) ? (
              <div className="mt-3 flex justify-end gap-2">
                {task.approvalExpiresAt && new Date(task.approvalExpiresAt).getTime() <= now && onRetryApproval ? (
                  <Button
                    disabled={busyTaskIds?.has(task.taskId)}
                    onClick={() => onRetryApproval(task.taskId)}
                    size="sm"
                    type="button"
                  >
                    {busyTaskIds?.has(task.taskId) ? "处理中…" : "重新提交"}
                  </Button>
                ) : null}
                {onReject ? (
                  <Button
                    disabled={busyTaskIds?.has(task.taskId)}
                    onClick={() => onReject(task.taskId)}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    拒绝
                  </Button>
                ) : null}
                {onApprove ? (
                  <Button
                    disabled={busyTaskIds?.has(task.taskId) || Boolean(task.approvalExpiresAt && new Date(task.approvalExpiresAt).getTime() <= now)}
                    onClick={() => onApprove(task.taskId)}
                    size="sm"
                    type="button"
                  >
                    {busyTaskIds?.has(task.taskId) ? "处理中…" : "确认执行"}
                  </Button>
                ) : null}
              </div>
            ) : null}

            {voiceTaskIsActive(task) && task.status !== "waiting_approval" && onCancel ? (
              <div className="mt-3 flex justify-end">
                <Button
                  onClick={() => onCancel(task.taskId)}
                  size="sm"
                  type="button"
                  variant="destructive"
                >
                  取消任务
                </Button>
              </div>
            ) : null}
          </PopoverContent>
        </Popover>
      ))}
      {visibleTasks.some((task) => (task.activities?.length ?? 0) > 0) ? (
        <details className="basis-full rounded-lg border border-border/60 bg-background/80 text-left shadow-sm backdrop-blur">
          <summary className="cursor-pointer list-none px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground">
            <span className="mr-1.5">⌄</span>
            后台 Agent 执行详情
            <span className="ml-1 opacity-60">（结构化进度，不显示私有思考链）</span>
          </summary>
          <div className="max-h-64 space-y-3 overflow-y-auto border-t border-border/50 px-3 py-3">
            {visibleTasks.map((task) => {
              const activities = (task.activities ?? []).slice(-24);
              if (!activities.length) return null;
              return (
                <section key={`details-${task.taskId}`}>
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-xs font-medium text-foreground">{voiceTaskTitle(task)}</p>
                    <span className="shrink-0 text-[0.68rem] text-muted-foreground">
                      {voiceTaskStatusLabel(task)}
                    </span>
                  </div>
                  <ol className="mt-2 space-y-1.5 border-l border-border/70 pl-3">
                    {activities.map((activity, index) => (
                      <li className="relative text-xs leading-snug text-muted-foreground" key={
                        activity.eventId ?? `${task.taskId}-${activity.eventSeq ?? index}`
                      }>
                        <span className="absolute -left-[0.93rem] top-1.5 size-1.5 rounded-full bg-primary/70" />
                        <span className="text-foreground/90">{activity.text}</span>
                        {activity.createdAt ? (
                          <span className="ml-1 text-[0.68rem] opacity-60">
                            {activityTime(activity.createdAt)}
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </section>
              );
            })}
          </div>
        </details>
      ) : null}
    </div>
  );
}
