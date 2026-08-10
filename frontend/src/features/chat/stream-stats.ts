/**
 * Client-side live stream metrics: first-token latency (TTFT) and token/s.
 *
 * The backend reports authoritative `provider_trace` numbers (`first_token_ms`,
 * `output_tokens`, `generation_duration_ms`) only on the terminal
 * `message.completed` event. For a *live* readout during streaming we measure
 * on the client: the wall-clock time from request start to the first
 * `part.delta`, plus a lightweight character-based token estimate updated on
 * every delta. When the terminal event arrives the badge switches to the
 * backend's accurate output-token count.
 *
 * State is kept in a module-level registry keyed by the local message id so it
 * survives React re-renders and animation-frame batching. Entries are tiny and
 * overwritten on the next stream for the same message.
 */
import { useCallback, useState } from "react";

export interface StreamStats {
  /** Wall-clock ms when the stream request was dispatched (client-side). */
  startedAt: number;
  /** Wall-clock ms when the first text delta was applied (client-side TTFT). */
  firstDeltaAt: number | null;
  /** Last wall-clock ms a delta was applied (for the live rate window). */
  lastDeltaAt: number | null;
  /** Cumulative character counts used by the token estimator. */
  cjkChars: number;
  asciiChars: number;
}

const STREAM_STATS_KEY = "learngraph.showStreamStats";
const STREAM_STATS_MAX_ENTRIES = 200;

const registry = new Map<string, StreamStats>();

// CJK ideographs and their extension blocks dominate LearnGraph chat output.
const CJK_RE = /[\u2e80-\u9fff\uf900-\ufaff\u3400-\u4dbf]/;

export function beginStreamStats(messageId: string, startedAt: number): void {
  // Bound memory: evict oldest entries before inserting a new stream.
  if (!registry.has(messageId) && registry.size >= STREAM_STATS_MAX_ENTRIES) {
    const oldest = registry.keys().next().value;
    if (oldest !== undefined) registry.delete(oldest);
  }
  registry.set(messageId, {
    startedAt,
    firstDeltaAt: null,
    lastDeltaAt: null,
    cjkChars: 0,
    asciiChars: 0,
  });
}

export function recordStreamDelta(messageId: string, delta: string): void {
  const stats = registry.get(messageId);
  if (!stats) return;
  const now = Date.now();
  if (stats.firstDeltaAt === null) stats.firstDeltaAt = now;
  stats.lastDeltaAt = now;
  for (const character of delta) {
    if (CJK_RE.test(character)) stats.cjkChars += 1;
    else stats.asciiChars += 1;
  }
}

export function lookupStreamStats(messageId: string): StreamStats | undefined {
  return registry.get(messageId);
}

/**
 * Extract the text delta from a raw SSE event data object (structural check,
 * mirroring `isMessagePart` without importing chat UI helpers). Returns an
 * empty string for non-delta events so callers can record unconditionally.
 */
export function deltaTextOf(data: Record<string, unknown>): string {
  const part = data.part;
  if (typeof part !== "object" || part === null) return "";
  const delta = (part as Record<string, unknown>).content_delta;
  return typeof delta === "string" ? delta : "";
}

/**
 * Rough live token estimate: CJK characters are denser (~0.8 token/char) while
 * ASCII text averages roughly 4 chars/token. Only used for the *live* readout;
 * the completed badge uses the backend's real `output_tokens`.
 */
export function estimateStreamTokens(stats: StreamStats): number {
  return Math.round(stats.cjkChars * 0.8 + stats.asciiChars / 4);
}

export function formatMilliseconds(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "–";
  if (ms < 1_000) return `${Math.round(ms)}ms`;
  return `${(ms / 1_000).toFixed(1)}s`;
}

export function formatTokensPerSecond(tokensPerSecond: number): string {
  if (!Number.isFinite(tokensPerSecond) || tokensPerSecond <= 0) return "–";
  if (tokensPerSecond >= 100) return `${Math.round(tokensPerSecond)} tok/s`;
  return `${tokensPerSecond.toFixed(1)} tok/s`;
}

export function formatTokenCount(tokens: number): string {
  if (!Number.isFinite(tokens) || tokens <= 0) return "0 tok";
  return `${Math.round(tokens).toLocaleString("en-US")} tok`;
}

/** Persisted UI toggle; default off. */
export function useShowStreamStats(): [boolean, () => void] {
  const [enabled, setEnabled] = useState<boolean>(() => {
    try {
      return localStorage.getItem(STREAM_STATS_KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggle = useCallback(() => {
    setEnabled((current) => {
      const next = !current;
      try {
        localStorage.setItem(STREAM_STATS_KEY, next ? "1" : "0");
      } catch {
        // localStorage unavailable (private mode): keep the in-memory state.
      }
      return next;
    });
  }, []);
  return [enabled, toggle];
}
