import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, XCircle } from "lucide-react";
import { TaskStatusIcon } from "@/components/shared/task-status-icon";

import { Button } from "@/components/ui/button";
import { VoiceTaskScene } from "./voice-task-scene";
import { ManagedAgentControls } from "./managed-agent-controls";

import {
  voiceTaskIsActive,
  voiceTaskPlaybackLabel,
  voiceTaskSourceLabel,
  voiceTaskStatusLabel,
  voiceTaskTitle,
  type VoiceTask,
} from "./voice-tasks";

export interface VoiceTaskCanvasProps {
  tasks: VoiceTask[];
  label?: string;
  onCancel?: (taskId: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
  onRetryApproval?: (taskId: string) => void;
  onRetryPlayback?: (taskId: string) => void;
  onRender?: (taskId: string, anchors: string[], status?: "rendered" | "failed") => void | Promise<void>;
  onExplain?: (taskId: string, anchor: string) => void;
  onSetAutoDelivery?: (taskId: string, enabled: boolean) => void;
  onControlExecution?: (taskId: string, operation: "pause" | "resume") => void;
  busyTaskIds?: ReadonlySet<string>;
}

export function VoiceTaskCanvas({
  tasks,
  label = "后台 Agent 任务",
  onCancel,
  onApprove,
  onReject,
  onRetryApproval,
  onRetryPlayback,
  onRender,
  onExplain,
  onSetAutoDelivery,
  onControlExecution,
  busyTaskIds,
}: VoiceTaskCanvasProps) {
  const [now, setNow] = useState(() => Date.now());
  const [openTaskIds, setOpenTaskIds] = useState<Set<string>>(() => new Set());
  // Manual preference and the temporary voice reveal have separate ownership.
  const [manualFocusOverrides, setManualFocusOverrides] = useState<Record<string, string>>({});
  const orderRef = useRef<string[]>(tasks.map((task) => task.taskId));
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const present = new Set(tasks.map((task) => task.taskId));
    setOpenTaskIds((current) => {
      const next = new Set([...current].filter((id) => present.has(id)));
      return next.size === current.size && [...next].every((id) => current.has(id)) ? current : next;
    });
    orderRef.current = orderRef.current.filter((id) => present.has(id));
    const known = new Set(orderRef.current);
    for (const task of tasks) {
      if (!known.has(task.taskId)) orderRef.current.push(task.taskId);
    }
  }, [tasks]);
  const orderedTasks = useMemo(() => {
    const rank = new Map(orderRef.current.map((taskId, index) => [taskId, index]));
    return [...tasks].sort((a, b) => (rank.get(a.taskId) ?? Number.MAX_SAFE_INTEGER) - (rank.get(b.taskId) ?? Number.MAX_SAFE_INTEGER));
  }, [tasks]);
  if (!tasks.length) return null;

  return (
    <section aria-label={label} className="w-full">
      <div className="space-y-4">
        {orderedTasks.map((task) => {
          const focusKey = task.focusTurnId || task.focusId || (task.playbackStatus === "speaking" ? `speech:${task.speechId ?? task.resultId ?? task.taskId}` : undefined);
          const revealing = Boolean(focusKey && task.status !== "stale" && manualFocusOverrides[task.taskId] !== focusKey);
          const open = openTaskIds.has(task.taskId) || revealing;
          return (
          <details
            className="group py-2"
            key={task.taskId}
            onToggle={(event) => {
              const userOpen = event.currentTarget.open;
              if (open === userOpen) return;
              if (focusKey) setManualFocusOverrides(current => ({ ...current, [task.taskId]: focusKey }));
              setOpenTaskIds((current) => {
                const next = new Set(current);
                if (userOpen) next.add(task.taskId);
                else next.delete(task.taskId);
                return next;
              });
            }}
            open={open}
            data-voice-revealed={revealing ? "true" : undefined}
          >
            <summary className="flex cursor-pointer list-none items-start gap-2 [&::-webkit-details-marker]:hidden">
              <ChevronRight aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
              {voiceTaskIsActive(task) || task.status === "ready" ? (
                <TaskStatusIcon taskKey={task.taskId} status={task.status === "ready" ? "succeeded" : "running"} className="mt-0.5 text-primary" />
              ) : task.status === "failed" || task.status === "interrupted" ? (
                <XCircle aria-hidden="true" className="mt-0.5 size-4 text-destructive" />
              ) : null}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate text-sm font-medium">{voiceTaskTitle(task)}</h3>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                    {voiceTaskStatusLabel(task)}
                  </span>
                  {voiceTaskPlaybackLabel(task) ? (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                      {voiceTaskPlaybackLabel(task)}
                    </span>
                  ) : null}
                </div>
              </div>
            </summary>
            {open ? <>
            <div className="mt-3 space-y-3">
                <ManagedAgentControls taskId={task.taskId} active={voiceTaskIsActive(task)} />
                <VoiceTaskScene task={task} open={open} onRender={onRender} onExplain={onExplain} />
                {task.status === "waiting_approval" && task.approvalRequestPreview ? (
                  <p className="mt-2 text-xs text-muted-foreground">确认内容：{task.approvalRequestPreview}</p>
                ) : null}
                {task.playbackStatus &&
                ["interrupted", "failed"].includes(task.playbackStatus) &&
                onRetryPlayback ? (
                  <Button
                    className="mt-2"
                    disabled={busyTaskIds?.has(task.taskId)}
                    onClick={() => onRetryPlayback(task.taskId)}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    {busyTaskIds?.has(task.taskId) ? "处理中…" : "重试播报"}
                  </Button>
                ) : null}
                {task.progress !== undefined && voiceTaskIsActive(task) ? (
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemax={100} aria-valuemin={0} aria-valuenow={task.progress}>
                    <span className="block h-full rounded-full bg-primary transition-[width]" style={{ width: `${task.progress}%` }} />
                  </div>
                ) : null}
                {task.sources.length ? (
                  <div className="mt-3 text-xs text-muted-foreground">
                    来源：{task.sources.slice(0, 8).map((source, index) => <span key={source.id}>
                      {index ? "、" : ""}{source.url ? <a className="underline" href={source.url} target="_blank" rel="noreferrer">{voiceTaskSourceLabel(source)}</a> : voiceTaskSourceLabel(source)}
                    </span>)}
                  </div>
                ) : null}
                {task.evidenceRefs.length ? (
                  <div className="mt-2 text-xs text-muted-foreground">
                    Evidence：{task.evidenceRefs.slice(0, 4).join("、")}
                  </div>
                ) : null}
                {task.activities?.length ? (
                  <div
                    className="mt-4 max-h-56 overflow-y-auto rounded-lg border border-border/50 px-3 py-2"
                  >
                    <p className="text-xs font-medium text-muted-foreground">
                      执行过程（结构化进度）
                    </p>
                    <ol className="mt-2 space-y-1 border-l border-border/60 pl-3">
                      {task.activities.slice(-24).map((activity, index) => (
                        <li className="text-xs leading-snug text-muted-foreground" key={activity.eventId ?? `${task.taskId}-activity-${activity.eventSeq ?? activity.createdAt ?? index}-${activity.text}`}>
                          {activity.text}
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
            </div>
            <div className="mt-3 flex justify-end gap-2">
              {onControlExecution && ["queued", "running", "pausing", "paused"].includes(task.status) ? <Button
                type="button" size="sm" variant="outline" disabled={busyTaskIds?.has(task.taskId) || task.status === "pausing"}
                onClick={() => onControlExecution(task.taskId, task.status === "paused" ? "resume" : "pause")}>
                {task.status === "paused" ? "继续任务" : task.status === "pausing" ? "正在保存检查点…" : "暂停任务"}
              </Button> : null}
              {onRetryPlayback && task.resultAvailable && !["speaking", "enqueued", "claimed", "interrupted", "failed"].includes(task.playbackStatus ?? "") ? (
                <Button type="button" size="sm" variant="outline" disabled={busyTaskIds?.has(task.taskId) || task.status === "stale"} onClick={() => onRetryPlayback(task.taskId)}>听取结果</Button>
              ) : null}
              {onSetAutoDelivery ? <Button type="button" size="sm" variant="ghost" disabled={busyTaskIds?.has(task.taskId)} onClick={() => onSetAutoDelivery(task.taskId, task.autoDelivery === false)}>
                {task.autoDelivery === false ? "开启自动汇报" : "只更新页面"}
              </Button> : null}
              {task.status === "waiting_approval" && (onApprove || onReject) ? (
                <div className="flex shrink-0 flex-col gap-2">
                  {task.approvalExpiresAt &&
                  new Date(task.approvalExpiresAt).getTime() <= now &&
                  onRetryApproval ? (
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
                      disabled={
                        busyTaskIds?.has(task.taskId) ||
                        Boolean(
                          task.approvalExpiresAt &&
                            new Date(task.approvalExpiresAt).getTime() <= now,
                        )
                      }
                      onClick={() => onApprove(task.taskId)}
                      size="sm"
                      type="button"
                    >
                      {busyTaskIds?.has(task.taskId) ? "处理中…" : "确认执行"}
                    </Button>
                  ) : null}
                </div>
              ) : null}
              {(voiceTaskIsActive(task) || task.status === "paused") && onCancel ? (
                <Button onClick={() => onCancel(task.taskId)} size="sm" type="button" variant="outline">
                  停止
                </Button>
              ) : null}
            </div>
            </> : null}
          </details>
        ); })}
      </div>
    </section>
  );
}
