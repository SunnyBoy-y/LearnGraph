/**
 * Live/terminal stream metrics badge: 首字延迟 (first-token latency) + token/s.
 *
 * While the message is streaming it reads the client-side registry updated by
 * the stream consumers and refreshes on a short interval. Once the message is
 * terminal it prefers the backend's authoritative `provider_trace` numbers and
 * falls back to the client measurement for TTFT when no provider trace exists
 * (e.g. after a page reload the registry may be empty).
 */
import { useEffect, useState } from "react";

import {
  estimateStreamTokens,
  formatMilliseconds,
  formatTokenCount,
  formatTokensPerSecond,
  lookupStreamStats,
  useShowCacheHit,
  useShowStreamStats,
} from "@/features/chat/stream-stats";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { UnknownRecord } from "@/types/common";

const STREAMING_TICK_MS = 250;

interface StreamStatsBadgeProps {
  messageId: string;
  status: string;
  providerTrace: UnknownRecord;
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

export function StreamStatsBadge({
  messageId,
  status,
  providerTrace,
}: StreamStatsBadgeProps) {
  const [enabled] = useShowStreamStats();
  const [showCacheHit] = useShowCacheHit();
  const [, setTick] = useState(0);

  const streaming = status === "streaming" || status === "submitted";

  // Live refresh while streaming and enabled.
  useEffect(() => {
    if (!enabled || !streaming) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), STREAMING_TICK_MS);
    return () => window.clearInterval(timer);
  }, [enabled, streaming]);

  if (!enabled) return null;

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
    if (!stats || !stats.firstDeltaAt) {
      const waiting = stats ? now - stats.startedAt : undefined;
      return (
        <div className="message-stream-stats" role="status" aria-live="polite">
          <span className="message-stream-stats__item">
            {waiting !== undefined ? `等待首字… ${formatMilliseconds(waiting)}` : "等待首字…"}
          </span>
        </div>
      );
    }
    ttftMs = stats.firstDeltaAt - stats.startedAt;
    const elapsed = now - stats.firstDeltaAt;
    if (elapsed >= 200) {
      tokensPerSecond = (estimateStreamTokens(stats) / elapsed) * 1_000;
    }
  } else if (backendOutputTokens !== undefined && backendDurationMs !== undefined) {
    // Terminal: backend-accurate totals.
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
  } else {
    // Terminal without a provider trace: keep whatever client stats exist.
    if (!stats || !stats.firstDeltaAt) return null;
    ttftMs = stats.firstDeltaAt - stats.startedAt;
    const elapsed = (stats.lastDeltaAt ?? now) - stats.firstDeltaAt;
    if (elapsed >= 200) {
      tokensPerSecond = (estimateStreamTokens(stats) / elapsed) * 1_000;
    }
    totalTokens = estimateStreamTokens(stats);
  }

  // Cache-hit share of billed prompt input (terminal only: the cache fields
  // ride provider_trace, present on the completed event and persisted rows,
  // never mid-stream). Mirrors the usage-page formula: cachedRead over the
  // full input_tokens total, which already includes cache reads and writes.
  let cacheHitPercent: number | undefined;
  let cachedReadTokens: number | undefined;
  let cachedWriteTokens: number | undefined;
  let inputTokensTotal: number | undefined;
  if (!streaming && showCacheHit) {
    inputTokensTotal = numericTraceValue(providerTrace, "input_tokens");
    cachedReadTokens = numericTraceValue(providerTrace, "cached_input_tokens");
    cachedWriteTokens = numericTraceValue(
      providerTrace,
      "cache_creation_input_tokens",
    );
    const hasCacheActivity = (cachedReadTokens ?? 0) > 0 || (cachedWriteTokens ?? 0) > 0;
    if (
      inputTokensTotal !== undefined &&
      inputTokensTotal > 0 &&
      hasCacheActivity &&
      cachedReadTokens !== undefined
    ) {
      cacheHitPercent = Math.round((cachedReadTokens / inputTokensTotal) * 100);
    }
  }

  const segments = [
    ttftMs !== undefined ? `首字 ${formatMilliseconds(ttftMs)}` : null,
    tokensPerSecond !== undefined
      ? formatTokensPerSecond(tokensPerSecond)
      : null,
    totalTokens !== undefined ? `共 ${formatTokenCount(totalTokens)}` : null,
  ].filter((segment): segment is string => segment !== null);

  const cacheBadge =
    cacheHitPercent !== undefined ? (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="message-stream-stats__badge" tabIndex={0}>
            缓存命中 {cacheHitPercent}%
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          <p className="text-xs font-medium">Prompt 缓存命中</p>
          <p className="mt-1 text-xs leading-5">
            缓存读 {formatTokenCount(cachedReadTokens ?? 0)} · 缓存写{" "}
            {formatTokenCount(cachedWriteTokens ?? 0)} · 总输入{" "}
            {formatTokenCount(inputTokensTotal ?? 0)}
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            命中率 = 缓存读 / 总输入
          </p>
        </TooltipContent>
      </Tooltip>
    ) : null;

  if (!segments.length && !cacheBadge) return null;

  return (
    <div className="message-stream-stats" role="status" aria-live="polite">
      {segments.map((segment) => (
        <span className="message-stream-stats__item" key={segment}>
          {segment}
        </span>
      ))}
      {cacheBadge}
    </div>
  );
}
