import { useEffect, useRef, useState } from "react";
import { AnswerParts } from "@/components/chat/answer-parts";
import { ChatStreamPartRenderer } from "@/components/chat/chat-stream-part-renderer";
import { Button } from "@/components/ui/button";
import { prefersReducedMotion } from "@/lib/motion-preferences";
import type { VoiceTask } from "./voice-tasks";
import { useManagedAgentAnswer } from "./managed-agent-answer";
import type { MessagePart } from "@/types/sessions";

export function VoiceTaskScene({ task, open, onRender, onExplain }: {
  task: VoiceTask;
  open: boolean;
  onRender?: (taskId: string, anchors: string[], status?: "rendered" | "failed") => void | Promise<void>;
  onExplain?: (taskId: string, anchor: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [displayState, setDisplayState] = useState<"waiting" | "visible" | "failed">("waiting");
  const activeAnchor = task.focusId ? task.focusAnchor : task.playbackStatus === "speaking"
    ? (task.visuals?.find(scene => scene.state !== "removed") ? `visual-${task.visuals.find(scene => scene.state !== "removed")!.block_id}` : "summary") : undefined;
  const childParts = useManagedAgentAnswer(task, open);
  const sceneKey = JSON.stringify([task.resultId, task.resultVersion, task.summary, task.findings, task.visuals, childParts]);
  useEffect(() => {
    if (!open || !onRender || !task.resultId) return;
    const element = root.current;
    if (!element) return;
    setDisplayState("waiting");
    let visible = typeof IntersectionObserver === "undefined";
    let pending = false;
    let disposed = false;
    let visibilityRevision = 0;
    let pendingAnchors = "";
    let refreshAfterPending = false;
    const unavailable = () => {
      visibilityRevision += 1;
      if (!disposed) setDisplayState("waiting");
      Promise.resolve(onRender(task.taskId, [], "failed")).catch(() => {});
    };
    const acknowledge = () => {
      if (disposed) return;
      if (!visible || document.visibilityState === "hidden") { unavailable(); return; }
      const anchors = [...element.querySelectorAll<HTMLElement>("[data-voice-anchor]")]
        .filter((node) => {
          const rect = node.getBoundingClientRect();
          const inViewport = typeof IntersectionObserver === "undefined" || (rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth);
          return inViewport && !node.querySelector(".stream-ui__invalid") && (!node.dataset.voiceAnchor?.startsWith("visual-") || Boolean(node.querySelector("[data-ui-component]")));
        })
        .map((node) => node.dataset.voiceAnchor!).slice(0, 100);
      if (!anchors.length) { unavailable(); return; }
      const anchorKey = anchors.join("\n");
      if (pending) {
        if (anchorKey !== pendingAnchors) {
          visibilityRevision += 1;
          refreshAfterPending = true;
          setDisplayState("waiting");
        }
        return;
      }
      pending = true;
      pendingAnchors = anchorKey;
      const revision = ++visibilityRevision;
      Promise.resolve(onRender(task.taskId, anchors)).then(() => {
        if (!disposed && revision === visibilityRevision && anchors.includes(activeAnchor || "summary")) setDisplayState("visible");
      }).catch(() => {
        if (!disposed && revision === visibilityRevision) setDisplayState("failed");
      }).finally(() => {
        pending = false;
        if (!disposed && refreshAfterPending) {
          refreshAfterPending = false;
          acknowledge();
        }
      });
    };
    const observer = typeof IntersectionObserver !== "undefined" ? new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      acknowledge();
    }) : null;
    observer?.observe(element);
    const mutations = new MutationObserver(acknowledge);
    mutations.observe(element, { childList: true, subtree: true });
    acknowledge();
    const timer = window.setInterval(acknowledge, 10_000);
    document.addEventListener("visibilitychange", acknowledge);
    window.addEventListener("scroll", acknowledge, true);
    window.addEventListener("resize", acknowledge);
    return () => {
      disposed = true;
      observer?.disconnect();
      mutations.disconnect();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", acknowledge);
      window.removeEventListener("scroll", acknowledge, true);
      window.removeEventListener("resize", acknowledge);
      unavailable();
    };
  }, [open, onRender, task.taskId, task.resultId, sceneKey, activeAnchor]);

  const lastFocused = useRef<string | undefined>(undefined);
  const childLoaded = Boolean(childParts?.length);
  useEffect(() => {
    if ((task.playbackStatus === "speaking" || task.focusId) && open) {
      const identity = `${task.focusId ?? task.speechId}:${activeAnchor || "summary"}:${childLoaded}`;
      const focus = () => {
      if (lastFocused.current === identity) return;
      const target = [...(root.current?.querySelectorAll<HTMLElement>("[data-voice-anchor]") ?? [])]
        .find((node) => node.dataset.voiceAnchor === (activeAnchor || "summary"));
      if (!target || (activeAnchor?.startsWith("visual-") && !target.querySelector("[data-ui-component]"))) return;
      lastFocused.current = identity;
      target.scrollIntoView?.({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
      };
      focus();
      const observer = new MutationObserver(focus);
      if (root.current) observer.observe(root.current, { childList: true, subtree: true });
      return () => observer.disconnect();
    }
    lastFocused.current = undefined;
  }, [task.playbackStatus, task.focusId, activeAnchor, task.speechId, childLoaded, open]);

  const isHighlighted = (anchor: string) => activeAnchor === anchor;
  const highlight = (anchor: string) => isHighlighted(anchor)
    ? "bg-primary/10 ring-1 ring-primary/40" : "";

  const fallbackParts: MessagePart[] = [
    ...(task.summary ? [{ id: `summary-${task.taskId}`, type: "text" as const, status: "completed" as const, content: task.summary }] : []),
    ...(task.visuals ?? []).filter(scene => scene.state !== "removed").map(scene => ({
      id: `voice-${task.taskId}-${scene.block_id}`, type: "ui" as const, status: "completed" as const, data: scene,
    })),
  ];
  const parts = childParts?.length ? childParts : fallbackParts;
  const renderedParts: MessagePart[] = parts.map(part => {
    // The voice scene may have a newer committed revision than a stored message.
    if (part.type !== "ui") return part;
    const scene = task.visuals?.find(scene => scene.block_id === part.data?.block_id);
    return scene && scene.revision >= Number(part.data?.revision ?? 0) ? { ...part, data: scene } : part;
  });
  const renderedBlocks = new Set(renderedParts.filter(part => part.type === "ui").map(part => part.data?.block_id));
  for (const part of fallbackParts.filter(part => part.type === "ui" && !renderedBlocks.has(part.data?.block_id))) renderedParts.push(part);
  for (const artifact of task.artifacts) {
    if (!artifact.fileId || renderedParts.some(part => part.data?.file_id === artifact.fileId)) continue;
    renderedParts.push({ id: `artifact-${task.taskId}-${artifact.id}`, type: "sandbox_artifact", status: "completed",
      data: { title: artifact.name, path: artifact.path, file_id: artifact.fileId, mime_type: artifact.mimeType,
        size_bytes: artifact.sizeBytes, sha256: artifact.sha256, task_id: task.taskId } });
  }

  return <div ref={root} className="space-y-3">
    {task.presentationMode === "display" ? <p role="status" className="text-xs text-muted-foreground">
      {displayState === "visible" ? "页面已显示，可直接操作" : displayState === "failed" ? "页面显示未确认，请重新展开" : "正在定位页面，暂不播报"}
    </p> : null}
    {renderedParts.length ? <div data-voice-anchor="summary" data-voice-focused={isHighlighted("summary") || undefined} className={`space-y-3 transition-colors ${highlight("summary")}`}>
      <AnswerParts parts={renderedParts} renderPart={part => {
        const anchor = part.type === "ui" && part.data?.block_id ? `visual-${part.data.block_id}` : undefined;
        return <div key={part.id} data-voice-anchor={anchor} data-voice-focused={anchor && isHighlighted(anchor) || undefined} className={anchor ? `rounded-xl scroll-mt-20 ${highlight(anchor)}` : undefined}>
          <ChatStreamPartRenderer part={part} siblingParts={renderedParts}
            interactive={task.status !== "stale"}
            onAction={anchor && onExplain ? () => onExplain(task.taskId, anchor) : undefined} />
          {anchor && onExplain && task.status !== "stale" ? <Button size="sm" variant="ghost" onClick={() => onExplain(task.taskId, anchor)}>讲解这个组件</Button> : null}
        </div>;
      }} />
      {onExplain && task.status !== "stale" ? <Button type="button" size="sm" variant="ghost" onClick={() => onExplain(task.taskId, "summary")}>讲解这项结果</Button> : null}
    </div> : null}
    {task.findings?.length ? <ol aria-label="研究结论" className="space-y-2">
      {task.findings.map((finding, index) => <li key={`finding-${index}`} data-voice-anchor={`finding-${index}`} data-voice-focused={isHighlighted(`finding-${index}`) || undefined} className={`rounded-lg border p-3 text-sm ${highlight(`finding-${index}`)}`}>
        <p>{finding}</p>
        {onExplain && task.status !== "stale" ? <Button type="button" size="sm" variant="ghost" onClick={() => onExplain(task.taskId, `finding-${index}`)}>解释第 {index + 1} 项</Button> : null}
      </li>)}
    </ol> : null}
    {task.limitations.length ? <div className="rounded-lg bg-muted/60 p-3 text-xs text-muted-foreground">
      <p className="font-medium">依据与限制</p>
      <ul className="mt-1 list-disc pl-4">{task.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>
    </div> : null}
    {task.artifacts.filter(artifact => !artifact.fileId).map(artifact => <p key={artifact.id} className="text-xs text-muted-foreground">
      {artifact.name}：文件仍在 Agent 工作区，尚未发布。
    </p>)}
  </div>;
}
