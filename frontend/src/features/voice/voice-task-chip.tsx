import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Brain,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  LoaderCircle,
  Search,
  Wrench,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import {
  voiceTaskActivityText,
  voiceTaskArtifactIsVerified,
  voiceTaskArtifactLabel,
  voiceTaskHasDetail,
  voiceTaskIsActive,
  voiceTaskSourceLabel,
  voiceTaskStatusLabel,
  type VoiceTask,
} from "./voice-tasks";

function taskIcon(task: VoiceTask) {
  if (task.status === "ready" || task.status === "partial") {
    return <CheckCircle2 aria-hidden="true" className="size-3.5" />;
  }
  if (task.status === "failed" || task.status === "interrupted") {
    return <AlertCircle aria-hidden="true" className="size-3.5" />;
  }
  if (task.status === "cancelled" || task.status === "stale") {
    return <XCircle aria-hidden="true" className="size-3.5" />;
  }
  if (voiceTaskIsActive(task)) {
    return <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" />;
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
  return [voiceTaskStatusLabel(task), elapsed, progress].filter(Boolean).join(" · ");
}

export interface VoiceTaskChipProps {
  tasks: VoiceTask[];
  onCancel?: (taskId: string) => void;
}

export function VoiceTaskChip({ tasks, onCancel }: VoiceTaskChipProps) {
  const hasActive = tasks.some(voiceTaskIsActive);
  const [now, setNow] = useState(() => Date.now());
  const visibleTasks = useMemo(() => {
    const ranked = [...tasks].sort((a, b) => {
      const activeDifference = Number(voiceTaskIsActive(b)) - Number(voiceTaskIsActive(a));
      if (activeDifference !== 0) return activeDifference;
      return b.updatedAt.localeCompare(a.updatedAt);
    });
    return ranked.slice(0, 3);
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
              aria-label={`${task.title}：${taskSummary(task, now)}`}
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
                <span>{task.title}</span>
              </span>
              <ChevronDown aria-hidden="true" className="size-3 shrink-0 opacity-60" />
            </button>
          </PopoverTrigger>
          <PopoverContent align="center" className="w-[min(20rem,calc(100vw-2rem))] p-3" side="top">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 text-muted-foreground">{taskIcon(task)}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{task.title}</p>
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

            {voiceTaskIsActive(task) && onCancel ? (
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
    </div>
  );
}
