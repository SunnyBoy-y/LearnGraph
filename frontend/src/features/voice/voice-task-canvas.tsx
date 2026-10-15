import { useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, XCircle } from "lucide-react";

import { SandboxFileArtifact } from "@/components/chat/sandbox-file-artifact";
import { SandboxImageArtifact } from "@/components/chat/sandbox-image-artifact";
import { Button } from "@/components/ui/button";
import type { MessagePart } from "@/types/sessions";

import {
  voiceTaskArtifactIsVerified,
  voiceTaskArtifactLabel,
  voiceTaskIsActive,
  voiceTaskPlaybackLabel,
  voiceTaskSourceLabel,
  voiceTaskStatusLabel,
  type VoiceTask,
} from "./voice-tasks";

export interface VoiceTaskCanvasProps {
  tasks: VoiceTask[];
  onCancel?: (taskId: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
  onRetryApproval?: (taskId: string) => void;
  onRetryPlayback?: (taskId: string) => void;
  busyTaskIds?: ReadonlySet<string>;
}

function artifactData(task: VoiceTask, artifact: VoiceTask["artifacts"][number]) {
  return {
    title: artifact.name,
    path: artifact.path,
    file_id: artifact.fileId,
    mime_type: artifact.mimeType,
    size_bytes: artifact.sizeBytes,
    sha256: artifact.sha256,
    task_id: task.taskId,
  };
}

function VoiceArtifact({
  task,
  artifact,
  index,
}: {
  task: VoiceTask;
  artifact: VoiceTask["artifacts"][number];
  index: number;
}) {
  const data = artifactData(task, artifact);
  const imageArtifact =
    artifact.fileId &&
    (artifact.mimeType?.toLowerCase().startsWith("image/") ||
      /\.(?:png|jpe?g|gif|webp|svg)$/i.test(artifact.path || artifact.name));
  if (imageArtifact) {
    const part: MessagePart = {
      id: `voice-agent-artifact-${task.taskId}-${index}`,
      type: "sandbox_artifact",
      status: "completed",
      data,
    };
    return <SandboxImageArtifact part={part} />;
  }
  if (artifact.fileId) {
    return <SandboxFileArtifact data={data} />;
  }
  return (
    <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
      {voiceTaskArtifactLabel(artifact)}
      <span className="ml-2">文件仍在 Agent 工作区，暂未发布到画布文件存储。</span>
    </div>
  );
}

export function VoiceTaskCanvas({
  tasks,
  onCancel,
  onApprove,
  onReject,
  onRetryApproval,
  onRetryPlayback,
  busyTaskIds,
}: VoiceTaskCanvasProps) {
  const [now, setNow] = useState(() => Date.now());
  const [openTaskIds, setOpenTaskIds] = useState<Set<string>>(
    () => new Set(tasks.filter(voiceTaskIsActive).map((task) => task.taskId)),
  );
  const orderRef = useRef<string[]>(tasks.map((task) => task.taskId));
  const activeByIdRef = useRef(new Map(tasks.map((task) => [task.taskId, voiceTaskIsActive(task)])));
  const userToggledIds = useRef(new Set<string>());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const present = new Set(tasks.map((task) => task.taskId));
    const autoOpen = tasks.filter((task) => voiceTaskIsActive(task)
      && !activeByIdRef.current.get(task.taskId) && !userToggledIds.current.has(task.taskId));
    activeByIdRef.current = new Map(tasks.map((task) => [task.taskId, voiceTaskIsActive(task)]));
    for (const id of userToggledIds.current) if (!present.has(id)) userToggledIds.current.delete(id);
    setOpenTaskIds((current) => {
      const next = new Set([...current].filter((id) => present.has(id)));
      for (const task of autoOpen) next.add(task.taskId);
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
    <section aria-label="后台 Agent 任务" className="max-h-[min(70dvh,44rem)] w-full overflow-y-auto rounded-2xl border border-border/70 bg-card/70 p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">后台 Agent</h2>
          <p className="text-xs text-muted-foreground">任务进度、来源和文件结果直接显示在当前画布。</p>
        </div>
        <span className="text-xs text-muted-foreground">{tasks.length} 个任务</span>
      </div>
      <div className="space-y-4">
        {orderedTasks.map((task) => (
          <details
            className="rounded-xl border border-border/60 bg-background/70 p-3"
            key={task.taskId}
            onToggle={(event) => {
              const open = event.currentTarget.open;
              if (openTaskIds.has(task.taskId) === open) return;
              userToggledIds.current.add(task.taskId);
              setOpenTaskIds((current) => {
                const next = new Set(current);
                if (open) next.add(task.taskId);
                else next.delete(task.taskId);
                return next;
              });
            }}
            open={openTaskIds.has(task.taskId)}
          >
            <summary className="flex cursor-pointer list-none items-start gap-3">
              {voiceTaskIsActive(task) ? (
                <LoaderCircle aria-hidden="true" className="mt-0.5 size-4 animate-spin text-primary" />
              ) : task.status === "failed" || task.status === "interrupted" ? (
                <XCircle aria-hidden="true" className="mt-0.5 size-4 text-destructive" />
              ) : null}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate text-sm font-medium">{task.title}</h3>
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
            <div className="mt-3 pl-7">
                {task.summary ? <p className="whitespace-pre-wrap text-sm leading-6">{task.summary}</p> : null}
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
                    来源：{task.sources.slice(0, 4).map(voiceTaskSourceLabel).join("、")}
                  </div>
                ) : null}
                {task.evidenceRefs.length ? (
                  <div className="mt-2 text-xs text-muted-foreground">
                    Evidence：{task.evidenceRefs.slice(0, 4).join("、")}
                  </div>
                ) : null}
                {task.artifacts.length ? (
                  <div className="mt-4 space-y-3">
                    <h4 className="text-xs font-medium text-muted-foreground">Agent 文件与产物</h4>
                    {task.artifacts.map((artifact, index) => (
                      <div key={artifact.id}>
                        <VoiceArtifact artifact={artifact} index={index} task={task} />
                        {!voiceTaskArtifactIsVerified(artifact) ? (
                          <p className="mt-1 text-[11px] text-muted-foreground">文件尚未完成独立校验。</p>
                        ) : null}
                      </div>
                    ))}
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
              {voiceTaskIsActive(task) && onCancel ? (
                <Button onClick={() => onCancel(task.taskId)} size="sm" type="button" variant="outline">
                  停止
                </Button>
              ) : null}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
