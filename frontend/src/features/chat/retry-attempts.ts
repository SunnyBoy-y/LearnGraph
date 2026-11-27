export interface ProviderRetryAttempt {
  attemptNo: number;
  maxRetries?: number;
  maxAttempts?: number;
  errorCategory: string;
  backoffMs?: number;
  errorMessage?: string;
  phase: "scheduled" | "started" | "exhausted";
}

const MAX_RETRY_ATTEMPTS = 12;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asPositiveInt(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.round(value)
    : undefined;
}

export function normalizeRetryAttempt(
  raw: unknown,
  defaultPhase: ProviderRetryAttempt["phase"] = "scheduled",
): ProviderRetryAttempt | null {
  const record = asRecord(raw);
  if (!record) return null;
  const attemptNo = asPositiveInt(record.attempt_no ?? record.attemptNo);
  if (attemptNo === undefined || attemptNo <= 0) return null;
  const errorCategory =
    typeof record.error_category === "string" && record.error_category.trim()
      ? record.error_category.trim()
      : typeof record.errorCategory === "string" && record.errorCategory.trim()
        ? record.errorCategory.trim()
        : "transient_error";
  const phaseRaw = record.phase;
  const phase: ProviderRetryAttempt["phase"] =
    phaseRaw === "scheduled" || phaseRaw === "started" || phaseRaw === "exhausted"
      ? phaseRaw
      : defaultPhase;
  const errorMessage =
    typeof record.error_message === "string" && record.error_message.trim()
      ? record.error_message.trim()
      : typeof record.errorMessage === "string" && record.errorMessage.trim()
        ? record.errorMessage.trim()
        : undefined;
  return {
    attemptNo,
    maxRetries: asPositiveInt(record.max_retries ?? record.maxRetries),
    maxAttempts: asPositiveInt(record.max_attempts ?? record.maxAttempts),
    errorCategory,
    backoffMs: asPositiveInt(record.backoff_ms ?? record.backoffMs),
    errorMessage,
    phase,
  };
}

export function extractRetryAttemptsFromTrace(
  providerTrace: Record<string, unknown> | undefined | null,
): ProviderRetryAttempt[] {
  const trace = asRecord(providerTrace);
  if (!trace || !Array.isArray(trace.retry_attempts)) return [];
  const items: ProviderRetryAttempt[] = [];
  for (const entry of trace.retry_attempts) {
    const normalized = normalizeRetryAttempt(entry, "scheduled");
    if (normalized) items.push(normalized);
  }
  return items.slice(-MAX_RETRY_ATTEMPTS);
}

export function mergeRetryEventIntoTrace(
  providerTrace: Record<string, unknown> | undefined | null,
  eventType: string,
  data: Record<string, unknown>,
): Record<string, unknown> {
  const baseTrace = { ...(asRecord(providerTrace) ?? {}) };
  const payload = asRecord(data.payload) ?? data;
  const phase: ProviderRetryAttempt["phase"] =
    eventType === "provider.retry.exhausted"
      ? "exhausted"
      : eventType === "provider.retry.started"
        ? "started"
        : "scheduled";
  const nextAttempt = normalizeRetryAttempt(payload, phase);
  if (!nextAttempt) return baseTrace;

  const existing = extractRetryAttemptsFromTrace(baseTrace);
  const index = existing.findIndex((item) => item.attemptNo === nextAttempt.attemptNo);
  const merged = [...existing];
  if (index >= 0) {
    merged[index] = {
      ...merged[index],
      ...nextAttempt,
      errorCategory:
        nextAttempt.errorCategory === "transient_error" && merged[index].errorCategory
          ? merged[index].errorCategory
          : nextAttempt.errorCategory,
      backoffMs: nextAttempt.backoffMs ?? merged[index].backoffMs,
      errorMessage: nextAttempt.errorMessage ?? merged[index].errorMessage,
    };
  } else {
    merged.push(nextAttempt);
  }

  return {
    ...baseTrace,
    retry_attempts: merged.slice(-MAX_RETRY_ATTEMPTS).map((item) => ({
      attempt_no: item.attemptNo,
      ...(item.maxRetries !== undefined ? { max_retries: item.maxRetries } : {}),
      ...(item.maxAttempts !== undefined ? { max_attempts: item.maxAttempts } : {}),
      error_category: item.errorCategory,
      ...(item.backoffMs !== undefined ? { backoff_ms: item.backoffMs } : {}),
      ...(item.errorMessage ? { error_message: item.errorMessage } : {}),
      phase: item.phase,
    })),
  };
}

export function formatRetryCategory(category: string): string {
  switch (category) {
    case "timeout":
      return "请求超时";
    case "upstream_http":
      return "上游服务限流或波动";
    case "upstream_transport":
      return "网络连接中断";
    default:
      return "临时通信波动";
  }
}

export function formatBackoffDuration(backoffMs: number | undefined): string | null {
  if (backoffMs === undefined || backoffMs <= 0) return null;
  if (backoffMs < 1000) return `${backoffMs}ms`;
  const seconds = backoffMs / 1000;
  return `${seconds >= 10 ? Math.round(seconds) : Number(seconds.toFixed(1))}s`;
}
