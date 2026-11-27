/**
 * Live/terminal stream metrics indicator and details popover:
 * 首字延迟 (first-token latency), token/s, 输出 Token, and 缓存命中率.
 *
 * During a response stream `StreamStatsBadge` renders the animated meteor/sparkle
 * indicator (and optional live numeric readout when `useShowStreamStats` is on).
 * Once the message reaches a terminal state, `StreamStatsAction` renders a
 * low-emphasis bar-chart button inside the message action bar whose hover/click
 * popover shows the terminal generation details in neutral grayscale.
 *
 * While the message is streaming it reads the client-side registry updated by
 * the stream consumers and refreshes on a short interval. Once the message is
 * terminal it prefers the backend's authoritative `provider_trace` numbers and
 * falls back to the client measurement for TTFT when no provider trace exists
 * (e.g. after a page reload the registry may be empty).
 */
import { useEffect, useRef, useState } from "react";
import { ChartNoAxesColumn, Sparkles } from "lucide-react";

import { MessageAction, MessageActions } from "@/components/ai-elements/message";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  estimateStreamTokens,
  formatMilliseconds,
  formatTokenCount,
  formatTokensPerSecond,
  lookupStreamStats,
  useShowCacheHit,
  useShowStreamStats,
} from "@/features/chat/stream-stats";
import type { UnknownRecord } from "@/types/common";
import "./stream-stats-badge.css";

const STREAMING_TICK_MS = 250;
const HOVER_OPEN_DELAY_MS = 150;
const HOVER_CLOSE_DELAY_MS = 180;

interface StreamStatsProps {
  messageId: string;
  status: string;
  providerTrace: UnknownRecord;
}

interface StreamStatsBadgeProps extends StreamStatsProps {
  /** When false, renders only while streaming; completed stats live in StreamStatsAction. */
  showCompleted?: boolean;
}

interface StreamStatsActionProps extends StreamStatsProps {
  /** Wrap the trigger inside a MessageActions row for surfaces without their own toolbar. */
  wrapInActions?: boolean;
}

interface ComputedStreamMetrics {
  streaming: boolean;
  pending: boolean;
  ttftMs: number | undefined;
  tokensPerSecond: number | undefined;
  totalTokens: number | undefined;
  cacheHitPercent: number | undefined;
  cachedReadTokens: number | undefined;
  cachedWriteTokens: number | undefined;
  inputTokensTotal: number | undefined;
  hasLiveMetrics: boolean;
  hasTerminalMetrics: boolean;
}

function numericTraceValue(
  providerTrace: UnknownRecord,
  key: string,
): number | undefined {
  const value = providerTrace[key];
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

function splitLatency(ms: number | undefined): { value: string; unit: string } {
  if (ms === undefined || !Number.isFinite(ms) || ms < 0) {
    return { value: "—", unit: "" };
  }
  if (ms < 1_000) {
    return { value: String(Math.round(ms)), unit: "ms" };
  }
  return { value: (ms / 1_000).toFixed(1), unit: "s" };
}

function splitSpeed(
  tokensPerSecond: number | undefined,
): { value: string; unit: string } {
  if (
    tokensPerSecond === undefined ||
    !Number.isFinite(tokensPerSecond) ||
    tokensPerSecond <= 0
  ) {
    return { value: "—", unit: "" };
  }
  return {
    value:
      tokensPerSecond >= 100
        ? String(Math.round(tokensPerSecond))
        : tokensPerSecond.toFixed(1),
    unit: "tok/s",
  };
}

function computeStreamMetrics({
  messageId,
  status,
  providerTrace,
  showCacheHit,
}: {
  messageId: string;
  status: string;
  providerTrace: UnknownRecord;
  showCacheHit: boolean;
}): ComputedStreamMetrics {
  const streaming = status === "streaming" || status === "submitted";
  const pending = status === "pending";
  const stats = lookupStreamStats(messageId);
  const now = Date.now();

  let ttftMs: number | undefined;
  let tokensPerSecond: number | undefined;
  let totalTokens: number | undefined;

  const backendFirstTokenMs = numericTraceValue(providerTrace, "first_token_ms");
  const backendOutputTokens = numericTraceValue(providerTrace, "output_tokens");
  const backendDurationMs = numericTraceValue(
    providerTrace,
    "generation_duration_ms",
  );

  if (streaming) {
    if (stats && stats.firstDeltaAt !== null) {
      ttftMs = stats.firstDeltaAt - stats.startedAt;
      const elapsed = now - stats.firstDeltaAt;
      if (elapsed >= 200) {
        tokensPerSecond = (estimateStreamTokens(stats) / elapsed) * 1_000;
      }
      totalTokens = estimateStreamTokens(stats);
    }
  } else if (
    !pending &&
    backendOutputTokens !== undefined &&
    backendDurationMs !== undefined
  ) {
    totalTokens = backendOutputTokens;
    const durationSeconds = backendDurationMs / 1_000;
    if (durationSeconds > 0) {
      tokensPerSecond = backendOutputTokens / durationSeconds;
    }
    // Prefer the client-measured TTFT (user-perceived); fall back to the
    // provider-measured value when the registry was cleared (page reload).
    ttftMs =
      stats && stats.firstDeltaAt !== null
        ? stats.firstDeltaAt - stats.startedAt
        : backendFirstTokenMs;
  } else if (!pending && stats && stats.firstDeltaAt !== null) {
    ttftMs = stats.firstDeltaAt - stats.startedAt;
    const elapsed = (stats.lastDeltaAt ?? now) - stats.firstDeltaAt;
    if (elapsed >= 200) {
      tokensPerSecond = (estimateStreamTokens(stats) / elapsed) * 1_000;
    }
    totalTokens = estimateStreamTokens(stats);
  }

  let cacheHitPercent: number | undefined;
  let cachedReadTokens: number | undefined;
  let cachedWriteTokens: number | undefined;
  let inputTokensTotal: number | undefined;

  if (!streaming && !pending && showCacheHit) {
    inputTokensTotal = numericTraceValue(providerTrace, "input_tokens");
    cachedReadTokens = numericTraceValue(providerTrace, "cached_input_tokens");
    cachedWriteTokens = numericTraceValue(
      providerTrace,
      "cache_creation_input_tokens",
    );
    const hasCacheActivity =
      (cachedReadTokens ?? 0) > 0 || (cachedWriteTokens ?? 0) > 0;
    if (
      inputTokensTotal !== undefined &&
      inputTokensTotal > 0 &&
      hasCacheActivity &&
      cachedReadTokens !== undefined
    ) {
      cacheHitPercent = Math.round((cachedReadTokens / inputTokensTotal) * 100);
    }
  }

  const hasLiveMetrics = Boolean(streaming && stats && stats.firstDeltaAt !== null);
  const hasTerminalMetrics =
    !streaming &&
    !pending &&
    (ttftMs !== undefined ||
      tokensPerSecond !== undefined ||
      totalTokens !== undefined ||
      cacheHitPercent !== undefined);

  return {
    streaming,
    pending,
    ttftMs,
    tokensPerSecond,
    totalTokens,
    cacheHitPercent,
    cachedReadTokens,
    cachedWriteTokens,
    inputTokensTotal,
    hasLiveMetrics,
    hasTerminalMetrics,
  };
}

/**
 * The selected preview treatment: a Lucide star that brightens in place while
 * three short meteor streaks pass behind it. Keep the visual in one component
 * so the same animation is used before the first delta and while later deltas
 * are still arriving.
 */
function MeteorSparkle() {
  return (
    <span
      aria-hidden="true"
      className="message-stream-stats__loading-visual"
      data-animation="meteor-flicker"
    >
      <span className="message-stream-stats__meteor-trail message-stream-stats__meteor-trail--a" />
      <span className="message-stream-stats__meteor-trail message-stream-stats__meteor-trail--b" />
      <span className="message-stream-stats__meteor-trail message-stream-stats__meteor-trail--c" />
      <Sparkles
        aria-hidden="true"
        className="message-stream-stats__loading-icon"
      />
    </span>
  );
}

export function StreamStatsAction({
  messageId,
  status,
  providerTrace,
  wrapInActions = false,
}: StreamStatsActionProps) {
  const [enabled] = useShowStreamStats();
  const [showCacheHit] = useShowCacheHit();
  const [open, setOpen] = useState(false);
  const pinnedRef = useRef(false);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  const cancelTimers = () => {
    clearTimeout(openTimerRef.current);
    clearTimeout(closeTimerRef.current);
  };

  useEffect(() => () => cancelTimers(), []);

  const metrics = computeStreamMetrics({
    messageId,
    status,
    providerTrace,
    showCacheHit,
  });

  if (!enabled || !metrics.hasTerminalMetrics) {
    return null;
  }

  const handleOpenChange = (next: boolean) => {
    cancelTimers();
    setOpen(next);
    if (!next) {
      pinnedRef.current = false;
    }
  };

  const scheduleOpen = () => {
    cancelTimers();
    openTimerRef.current = setTimeout(() => {
      setOpen(true);
    }, HOVER_OPEN_DELAY_MS);
  };

  const scheduleClose = () => {
    cancelTimers();
    if (pinnedRef.current) return;
    closeTimerRef.current = setTimeout(() => {
      if (!pinnedRef.current) {
        setOpen(false);
      }
    }, HOVER_CLOSE_DELAY_MS);
  };

  const latency = splitLatency(metrics.ttftMs);
  const speed = splitSpeed(metrics.tokensPerSecond);
  const hasSecondaryRows =
    metrics.totalTokens !== undefined || metrics.cacheHitPercent !== undefined;

  const control = (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <MessageAction
          aria-label="生成详情"
          className="data-[state=open]:bg-muted data-[state=open]:text-foreground"
          label="生成详情"
          onClick={(event) => {
            cancelTimers();
            if (open && !pinnedRef.current) {
              event.preventDefault();
              pinnedRef.current = true;
              return;
            }
            if (!open) {
              pinnedRef.current = true;
            }
          }}
          onFocus={(event) => {
            if (event.target.matches(":focus-visible")) {
              cancelTimers();
              setOpen(true);
            }
          }}
          onPointerEnter={(event) => {
            if (event.pointerType === "touch") return;
            if (!open) scheduleOpen();
            else cancelTimers();
          }}
          onPointerLeave={scheduleClose}
        >
          <ChartNoAxesColumn aria-hidden="true" className="size-3.5" />
        </MessageAction>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-label="生成详情"
        className="message-stream-popover"
        collisionPadding={12}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onOpenAutoFocus={(event) => event.preventDefault()}
        onPointerEnter={cancelTimers}
        onPointerLeave={scheduleClose}
        side="bottom"
        sideOffset={6}
      >
        <div className="message-stream-popover__header">
          <span className="message-stream-popover__title">生成详情</span>
          <span className="message-stream-popover__subtitle">本次回复</span>
        </div>
        <div className="message-stream-popover__hero">
          <div className="message-stream-popover__metric">
            <span className="message-stream-popover__metric-label">首字延迟</span>
            <div className="message-stream-popover__metric-value">
              <strong>{latency.value}</strong>
              {latency.unit ? <span>{latency.unit}</span> : null}
            </div>
          </div>
          <div className="message-stream-popover__metric">
            <span className="message-stream-popover__metric-label">生成速度</span>
            <div className="message-stream-popover__metric-value">
              <strong>{speed.value}</strong>
              {speed.unit ? <span>{speed.unit}</span> : null}
            </div>
          </div>
        </div>
        {hasSecondaryRows ? (
          <div className="message-stream-popover__rows">
            {metrics.totalTokens !== undefined ? (
              <div className="message-stream-popover__row">
                <span className="message-stream-popover__row-label">
                  输出 Token
                </span>
                <span className="message-stream-popover__row-value">
                  {Math.round(metrics.totalTokens).toLocaleString("en-US")}
                </span>
              </div>
            ) : null}
            {metrics.cacheHitPercent !== undefined ? (
              <div
                className="message-stream-popover__cache"
                title={`缓存读 ${formatTokenCount(metrics.cachedReadTokens ?? 0)} · 缓存写 ${formatTokenCount(metrics.cachedWriteTokens ?? 0)} · 总输入 ${formatTokenCount(metrics.inputTokensTotal ?? 0)}（命中率 = 缓存读 / 总输入）`}
              >
                <div className="message-stream-popover__row">
                  <span className="message-stream-popover__row-label">
                    缓存命中率
                  </span>
                  <span className="message-stream-popover__row-value">
                    {metrics.cacheHitPercent}%
                  </span>
                </div>
                <div aria-hidden="true" className="message-stream-popover__bar">
                  <div
                    className="message-stream-popover__bar-fill"
                    style={{
                      transform: `scaleX(${Math.max(0, Math.min(100, metrics.cacheHitPercent)) / 100})`,
                    }}
                  />
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );

  if (wrapInActions) {
    return (
      <MessageActions className="opacity-60 transition-opacity focus-within:opacity-100 hover:opacity-100 has-[[data-state=open]]:opacity-100">
        {control}
      </MessageActions>
    );
  }

  return control;
}

export function StreamStatsBadge({
  messageId,
  status,
  providerTrace,
  showCompleted = true,
}: StreamStatsBadgeProps) {
  const [enabled] = useShowStreamStats();
  const [, setTick] = useState(0);

  const streaming = status === "streaming" || status === "submitted";

  // Refresh while streaming even when the optional numeric metrics are off:
  // the selected star animation is visible for the whole response stream.
  useEffect(() => {
    if (!streaming) return;
    const timer = window.setInterval(
      () => setTick((value) => value + 1),
      STREAMING_TICK_MS,
    );
    return () => window.clearInterval(timer);
  }, [streaming]);

  if (!streaming) {
    if (!showCompleted) return null;
    return (
      <StreamStatsAction
        messageId={messageId}
        providerTrace={providerTrace}
        status={status}
        wrapInActions
      />
    );
  }

  const metrics = computeStreamMetrics({
    messageId,
    status,
    providerTrace,
    showCacheHit: false,
  });

  if (!enabled || !metrics.hasLiveMetrics) {
    return (
      <div
        aria-busy="true"
        aria-label="正在生成回答"
        aria-live="polite"
        className="message-stream-stats message-stream-stats--loading"
        role="status"
      >
        <MeteorSparkle />
      </div>
    );
  }

  const segments = [
    metrics.ttftMs !== undefined
      ? `首字 ${formatMilliseconds(metrics.ttftMs)}`
      : null,
    metrics.tokensPerSecond !== undefined
      ? formatTokensPerSecond(metrics.tokensPerSecond)
      : null,
    metrics.totalTokens !== undefined
      ? `共 ${formatTokenCount(metrics.totalTokens)}`
      : null,
  ].filter((segment): segment is string => segment !== null);

  return (
    <div
      aria-busy="true"
      aria-live="polite"
      className="message-stream-stats"
      role="status"
    >
      <MeteorSparkle />
      {segments.map((segment) => (
        <span className="message-stream-stats__item" key={segment}>
          {segment}
        </span>
      ))}
    </div>
  );
}
