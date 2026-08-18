"use client";

import { ChevronDown } from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  currentActivityChips,
  formatThinkingDuration,
} from "@/features/chat/chat-message-parts";
import { cn } from "@/lib/utils";
import type { MessagePart } from "@/types/sessions";

import { Shimmer } from "@/components/ai-elements/shimmer";

/**
 * Outer ChatGPT-style thinking collapsible — one fold for the WHOLE process
 * chain (reasoning + plan narration + tool calls in raw order).
 *
 * Phases:
 * - thinking: message is streaming and the final-answer boundary has not been
 *   reached yet. Defaults OPEN during processing (user setting), so the user
 *   watches the process unfold in real time.
 * - final_answer: the backend emitted `answer.started` — freeze the thinking
 *   duration and auto-collapse ONCE. Re-expanding afterwards is never undone
 *   by later deltas.
 * - completed/failed/cancelled: terminal header ("思考了 X" / "处理失败 · 用时 X").
 *
 * History / replay messages always start collapsed.
 */
export function ThinkingChain({
  chainParts,
  messageStatus,
  startedAt,
  completedDurationSec,
  finalAnswerStarted = false,
  thinkingDurationMs,
  toolCallCount: toolCalls = 0,
  defaultOpen = true,
  children,
  className,
}: {
  chainParts: MessagePart[];
  messageStatus: string;
  startedAt?: string;
  completedDurationSec?: number;
  /** Whether the backend has emitted `answer.started` (final answer is out). */
  finalAnswerStarted?: boolean;
  /** Frozen thinking duration (ms) from `answer.started` / provider_trace. */
  thinkingDurationMs?: number;
  /** Settled tool-call count shown in the collapsed final header. */
  toolCallCount?: number;
  /** Processing-phase default open state (user setting; default open). */
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const isStreaming = messageStatus === "streaming";
  const isTerminal = !isStreaming;
  const isFailed = messageStatus === "failed" || messageStatus === "cancelled";
  const phase = finalAnswerStarted || isTerminal ? "answer" : "thinking";
  const [open, setOpen] = useState(
    () => (finalAnswerStarted || isTerminal ? false : defaultOpen),
  );
  const userOpenedDuringStreamRef = useRef(false);
  const wasStreamingRef = useRef(isStreaming);
  const hasAutoCollapsedAtFinalRef = useRef(false);
  const parsedStartedAt = startedAt ? Date.parse(startedAt) : Number.NaN;
  const startTimeRef = useRef<number>(
    Number.isFinite(parsedStartedAt) ? parsedStartedAt : Date.now(),
  );
  const [durationSec, setDurationSec] = useState<number | undefined>(undefined);
  const frozenSeconds =
    typeof thinkingDurationMs === "number" &&
    Number.isFinite(thinkingDurationMs) &&
    thinkingDurationMs >= 0
      ? Math.floor(thinkingDurationMs / 1_000)
      : undefined;
  const chips = currentActivityChips(messageStatus, chainParts);

  // Rebase optimistic client time onto the durable server start time once known.
  useEffect(() => {
    if (Number.isFinite(parsedStartedAt)) {
      startTimeRef.current = parsedStartedAt;
    }
  }, [parsedStartedAt]);

  // Only run the visible stopwatch while expanded. Each tick derives from wall
  // clock time, so background-tab throttling cannot make the counter drift.
  useEffect(() => {
    if (!isStreaming || phase !== "thinking" || !open) return;
    const updateDuration = () => {
      setDurationSec(
        Math.max(0, Math.floor((Date.now() - startTimeRef.current) / 1_000)),
      );
    };
    updateDuration();
    const timer = window.setInterval(updateDuration, 1_000);
    return () => window.clearInterval(timer);
  }, [isStreaming, phase, open, parsedStartedAt]);

  // Freeze at the final-answer boundary: the backend already measured
  // thinking_duration_ms (reasoning + tool rounds, excluding the answer).
  useEffect(() => {
    if (!finalAnswerStarted) return;
    if (frozenSeconds !== undefined) {
      setDurationSec(frozenSeconds);
    } else if (phase === "answer") {
      setDurationSec(
        Math.max(0, Math.floor((Date.now() - startTimeRef.current) / 1_000)),
      );
    }
  }, [finalAnswerStarted, frozenSeconds, phase]);

  // Freeze to the persisted server duration at the terminal event. The local
  // elapsed value is only a fallback for legacy responses without timing data.
  useEffect(() => {
    if (isStreaming) return;
    if (completedDurationSec !== undefined) {
      setDurationSec(completedDurationSec);
      return;
    }
    if (wasStreamingRef.current) {
      setDurationSec(
        Math.max(0, Math.round((Date.now() - startTimeRef.current) / 1_000)),
      );
    }
  }, [isStreaming, completedDurationSec]);

  // Final-answer boundary: auto-collapse exactly once. Re-expanding afterwards
  // is respected — later text deltas never force another collapse.
  useEffect(() => {
    if (!finalAnswerStarted || hasAutoCollapsedAtFinalRef.current) return;
    hasAutoCollapsedAtFinalRef.current = true;
    setOpen(false);
  }, [finalAnswerStarted]);

  // Streaming → completed (legacy messages without answer.started): collapse
  // once the terminal event arrives, as before.
  useEffect(() => {
    if (isStreaming) {
      wasStreamingRef.current = true;
      return;
    }
    if (!wasStreamingRef.current) return;
    wasStreamingRef.current = false;
    if (open || userOpenedDuringStreamRef.current) {
      const timer = window.setTimeout(() => {
        setOpen(false);
        userOpenedDuringStreamRef.current = false;
      }, 400);
      return () => window.clearTimeout(timer);
    }
  }, [isStreaming, open]);

  if (!chainParts.length) return null;

  const headerLabel =
    phase === "thinking" ? (
      <Shimmer duration={1} className="text-[13px] font-medium">
        {`正在思考${open ? ` ${formatThinkingDuration(durationSec ?? 0)}` : ""}`}
      </Shimmer>
    ) : isFailed ? (
      <span className="text-[13px] font-medium text-muted-foreground">
        {messageStatus === "cancelled"
          ? `已取消 · 用时 ${formatThinkingDuration(durationSec)}`
          : `处理失败 · 用时 ${formatThinkingDuration(durationSec)}`}
      </span>
    ) : (
      <span className="text-[13px] font-medium text-muted-foreground">
        思考了 {formatThinkingDuration(durationSec ?? frozenSeconds)}
      </span>
    );

  return (
    <Collapsible
      className={cn("thinking-chain", className)}
      onOpenChange={(next) => {
        setOpen(next);
        if (isStreaming && next) {
          // User manually expanded during generation — keep open until stream ends.
          userOpenedDuringStreamRef.current = true;
        }
        if (!next) {
          userOpenedDuringStreamRef.current = false;
        }
      }}
      open={open}
    >
      <CollapsibleTrigger
        aria-label={open ? "收起思维链条" : "展开思维链条"}
        className="thinking-chain__trigger"
      >
        {headerLabel}
        {phase === "thinking" && !open && chips.length > 0 ? (
          <span
            className="thinking-chain__chips"
            role="status"
            aria-live="polite"
          >
            {chips.map((chip) => (
              <span className="thinking-chain__chip" key={chip}>
                {chip}
              </span>
            ))}
          </span>
        ) : null}
        {phase === "answer" && toolCalls > 0 ? (
          <span className="thinking-chain__chip">{`${toolCalls} 次工具调用`}</span>
        ) : null}
        <ChevronDown
          className={cn(
            "thinking-chain__chevron size-3.5 text-muted-foreground transition-transform",
            !open && "rotate-[-90deg]",
          )}
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="thinking-chain__content">
        <div className="thinking-chain__steps">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  );
}
