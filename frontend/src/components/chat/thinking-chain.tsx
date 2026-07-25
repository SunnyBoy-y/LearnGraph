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
  currentActivityLabel,
  formatThinkingDuration,
} from "@/features/chat/chat-message-parts";
import { cn } from "@/lib/utils";
import type { MessagePart } from "@/types/sessions";

import { Shimmer } from "@/components/ai-elements/shimmer";

/**
 * Outer ChatGPT-style thinking collapsible.
 *
 * - Default closed ("正在思考" / "思考了 …").
 * - User may expand during streaming; the fold stays open while the turn is
 *   still generating (even as new tool/reasoning steps append).
 * - When streaming ends (final answer body is out), auto-collapse so the main
 *   answer sits cleanly below a collapsed thinking header.
 */
export function ThinkingChain({
  chainParts,
  messageStatus,
  startedAt,
  completedDurationSec,
  children,
  className,
}: {
  chainParts: MessagePart[];
  messageStatus: string;
  startedAt?: string;
  completedDurationSec?: number;
  children: ReactNode;
  className?: string;
}) {
  const isStreaming = messageStatus === "streaming";
  const [open, setOpen] = useState(false);
  const userOpenedDuringStreamRef = useRef(false);
  const wasStreamingRef = useRef(isStreaming);
  const parsedStartedAt = startedAt ? Date.parse(startedAt) : Number.NaN;
  const startTimeRef = useRef<number>(
    Number.isFinite(parsedStartedAt) ? parsedStartedAt : Date.now(),
  );
  const [durationSec, setDurationSec] = useState<number | undefined>(undefined);
  const activity = currentActivityLabel(messageStatus, chainParts);

  // Rebase optimistic client time onto the durable server start time once known.
  useEffect(() => {
    if (Number.isFinite(parsedStartedAt)) {
      startTimeRef.current = parsedStartedAt;
    }
  }, [parsedStartedAt]);

  // Only run the visible stopwatch while expanded. Each tick derives from wall
  // clock time, so background-tab throttling cannot make the counter drift.
  useEffect(() => {
    if (!isStreaming || !open) return;
    const updateDuration = () => {
      setDurationSec(
        Math.max(0, Math.floor((Date.now() - startTimeRef.current) / 1_000)),
      );
    };
    updateDuration();
    const timer = window.setInterval(updateDuration, 1_000);
    return () => window.clearInterval(timer);
  }, [isStreaming, open, parsedStartedAt]);

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

  // Streaming → completed: auto-collapse once. Manual expand mid-stream is
  // intentionally kept open until this transition fires.
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

  const headerLabel = isStreaming ? (
    <Shimmer duration={1} className="text-[13px] font-medium">
      {`正在思考${open ? ` ${formatThinkingDuration(durationSec ?? 0)}` : ""}`}
    </Shimmer>
  ) : (
    <span className="text-[13px] font-medium text-muted-foreground">
      思考了 {formatThinkingDuration(durationSec)}
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
        <ChevronDown
          className={cn(
            "thinking-chain__chevron size-3.5 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </CollapsibleTrigger>
      {isStreaming && !open && activity && activity !== "正在思考" ? (
        <div className="thinking-chain__live" role="status" aria-live="polite">
          <span className="thinking-chain__live-dot" />
          <span>{activity}</span>
        </div>
      ) : null}
      <CollapsibleContent className="thinking-chain__content">
        <div className="thinking-chain__steps">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  );
}
