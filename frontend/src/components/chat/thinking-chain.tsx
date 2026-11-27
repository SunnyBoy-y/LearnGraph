"use client";

import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  LoaderCircle,
  XCircle,
} from "lucide-react";
import {
  Children,
  Fragment,
  isValidElement,
  useEffect,
  useMemo,
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
import { buildProcessStages } from "@/features/chat/process-stages";
import { cn } from "@/lib/utils";
import type { MessagePart } from "@/types/sessions";

import "./thinking-chain.css";

/**
 * Outer ChatGPT-style thinking collapsible with a two-level process timeline:
 * - Level 0 (collapsed): live status / completed duration + activity summary.
 * - Level 1 (outer open): lightweight vertical timeline of stage titles,
 *   call summaries, and execution statuses (finished stages stay collapsed).
 * - Level 2 (stage open): in-place expansion revealing commentary, reasoning
 *   summary rows, and tool calls (which retain Level-3 I/O expansion).
 *
 * Automatic vs manual priority:
 * - Streaming auto-expands the active stage and auto-collapses old stages when
 *   a new Commentary stage forms or when `answer.started` / completion fires.
 * - Manually expanded historical stages stay locked open; manually collapsed
 *   stages stay collapsed even across incremental streaming deltas.
 * - Failures keep their stage expanded so errors remain inspectable.
 */
export function ThinkingChain({
  chainParts,
  messageStatus,
  startedAt,
  completedDurationSec,
  finalAnswerStarted = false,
  thinkingDurationMs,
  toolCallCount: toolCalls = 0,
  defaultOpen = false,
  renderPart,
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
  /** Processing-phase default open state (user setting; default collapsed). */
  defaultOpen?: boolean;
  /** Lazy per-part renderer used when a stage expands at Level-2. */
  renderPart?: (part: MessagePart) => ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const isStreaming = messageStatus === "streaming";
  const isTerminal = !isStreaming;
  const isFailed = messageStatus === "failed" || messageStatus === "cancelled";
  const isFailureStatus = messageStatus === "failed";
  const phase = finalAnswerStarted || isTerminal ? "answer" : "thinking";
  const [open, setOpen] = useState(
    () => (finalAnswerStarted || isTerminal ? false : defaultOpen),
  );
  const userOpenedDuringStreamRef = useRef(false);
  const userCollapsedOuterRef = useRef(false);
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

  const stages = useMemo(
    () =>
      buildProcessStages(chainParts, {
        messageStatus,
        finalAnswerStarted,
      }),
    [chainParts, messageStatus, finalAnswerStarted],
  );
  const [manualStageStates, setManualStageStates] = useState<
    Record<string, "expanded" | "collapsed">
  >({});

  const childByPartId = useMemo(() => {
    if (renderPart || !children) return null;
    const map = new Map<string, ReactNode>();
    const array = Children.toArray(children);
    array.forEach((child, index) => {
      if (isValidElement(child) && child.key != null) {
        const rawKey = String(child.key).replace(/^\.\$/, "");
        map.set(rawKey, child);
        return;
      }
      const positionalPart = chainParts[index];
      if (positionalPart && !map.has(positionalPart.id)) {
        map.set(positionalPart.id, child);
      }
    });
    return map;
  }, [renderPart, children, chainParts]);

  const renderStagePart = (part: MessagePart): ReactNode => {
    if (renderPart) return renderPart(part);
    return childByPartId?.get(part.id) ?? null;
  };

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

  // Final-answer boundary: auto-collapse once unless the user manually expanded
  // during streaming. Re-expanding afterwards is also never undone by deltas.
  useEffect(() => {
    if (!finalAnswerStarted || hasAutoCollapsedAtFinalRef.current) return;
    hasAutoCollapsedAtFinalRef.current = true;
    if (userOpenedDuringStreamRef.current) return;
    setOpen(false);
  }, [finalAnswerStarted]);

  // Streaming → terminal: keep failure inspectable unless manually collapsed;
  // otherwise auto-collapse after 400ms unless the user locked it open.
  useEffect(() => {
    if (isStreaming) {
      wasStreamingRef.current = true;
      return;
    }
    if (!wasStreamingRef.current) return;
    wasStreamingRef.current = false;
    if (isFailureStatus) {
      if (!userCollapsedOuterRef.current) {
        setOpen(true);
      }
      return;
    }
    if (userOpenedDuringStreamRef.current) return;
    if (open) {
      const timer = window.setTimeout(() => {
        setOpen(false);
      }, 400);
      return () => window.clearTimeout(timer);
    }
  }, [isStreaming, isFailureStatus, open]);

  if (!chainParts.length) return null;

  const headerLabel =
    phase === "thinking" ? (
      <span className="text-[13px] font-medium text-muted-foreground">
        {`正在思考${open ? ` ${formatThinkingDuration(durationSec ?? 0)}` : ""}`}
      </span>
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
        if (next) {
          userCollapsedOuterRef.current = false;
          if (isStreaming) {
            // User manually expanded during generation — lock open across deltas/answer.
            userOpenedDuringStreamRef.current = true;
          }
        } else {
          userOpenedDuringStreamRef.current = false;
          userCollapsedOuterRef.current = true;
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
        {stages.length > 0 ? (
          <div className="thinking-chain__steps process-timeline" role="list">
            {stages.map((stage, index) => {
              const manual = manualStageStates[stage.id];
              const stageOpen =
                manual !== undefined ? manual === "expanded" : stage.autoOpen;
              const isLast = index === stages.length - 1;
              const showLine = !isLast || stageOpen;
              return (
                <div
                  className="process-stage"
                  data-stage-id={stage.id}
                  data-stage-open={stageOpen ? "true" : "false"}
                  data-stage-status={stage.status}
                  key={stage.id}
                  role="listitem"
                >
                  <div aria-hidden="true" className="process-stage__rail">
                    <span className="process-stage__icon">
                      {stage.status === "failed" ? (
                        <XCircle className="size-3.5 text-destructive" />
                      ) : stage.status === "running" ? (
                        stage.hasRunningPart ? (
                          <LoaderCircle className="size-3.5 animate-spin" />
                        ) : (
                          <CircleDot className="size-3.5" />
                        )
                      ) : (
                        <CheckCircle2 className="size-3.5" />
                      )}
                    </span>
                    {showLine ? <span className="process-stage__line" /> : null}
                  </div>
                  <div className="process-stage__content">
                    <button
                      aria-expanded={stageOpen}
                      className="process-stage__trigger"
                      onClick={() =>
                        setManualStageStates((prev) => ({
                          ...prev,
                          [stage.id]: stageOpen ? "collapsed" : "expanded",
                        }))
                      }
                      type="button"
                    >
                      <div className="process-stage__headline">
                        <span className="process-stage__title">
                          {stage.title}
                        </span>
                        {stage.status === "running" ? (
                          <span className="process-stage__status process-stage__status--running">
                            进行中
                          </span>
                        ) : stage.status === "failed" ? (
                          <span className="process-stage__status process-stage__status--failed">
                            失败
                          </span>
                        ) : (
                          <span className="sr-only">已完成</span>
                        )}
                        <ChevronRight
                          aria-hidden="true"
                          className={cn(
                            "process-stage__chevron size-3.5 shrink-0 text-muted-foreground transition-transform",
                            stageOpen && "rotate-90",
                          )}
                        />
                      </div>
                      {stage.summary ? (
                        <div className="process-stage__summary">
                          {stage.summary}
                        </div>
                      ) : null}
                    </button>
                    {stageOpen ? (
                      <div className="process-stage__body">
                        {stage.parts.map((part) => (
                          <Fragment key={part.id}>
                            {renderStagePart(part)}
                          </Fragment>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}
      </CollapsibleContent>
    </Collapsible>
  );
}
