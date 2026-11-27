export type TurnErrorKind =
  | "auth"
  | "rate_limit"
  | "context_limit"
  | "timeout"
  | "server"
  | "connection"
  | "bad_request"
  | "unknown";

export interface StructuredTurnErrorInput {
  kind?: string | null;
  code?: string | null;
  message?: string | null;
  statusCode?: number | null;
  status?: number | null;
  retryable?: boolean | null;
}

export interface ClassifiedTurnError {
  kind: TurnErrorKind;
  title: string;
  guidance: string;
  rawDetail: string;
  statusCode: number | null;
  code: string | null;
  retryable: boolean;
  primaryAction: "open_provider_settings" | "compact_and_retry" | "retry";
}

const VALID_KINDS: ReadonlySet<TurnErrorKind> = new Set([
  "auth",
  "rate_limit",
  "context_limit",
  "timeout",
  "server",
  "connection",
  "bad_request",
  "unknown",
]);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function extractStructuredErrorFromEvent(
  data: Record<string, unknown>,
): StructuredTurnErrorInput | null {
  const payload = asRecord(data.payload);
  const error = asRecord(payload?.error) ?? asRecord(data.error);
  if (!error) return null;
  const statusCode =
    typeof error.status_code === "number"
      ? error.status_code
      : typeof error.status === "number"
        ? error.status
        : null;
  return {
    kind: typeof error.kind === "string" ? error.kind : null,
    code: typeof error.code === "string" ? error.code : null,
    message: typeof error.message === "string" ? error.message : null,
    statusCode,
    retryable: typeof error.retryable === "boolean" ? error.retryable : null,
  };
}

export function extractLastErrorFromTrace(
  providerTrace: Record<string, unknown> | undefined | null,
): StructuredTurnErrorInput | null {
  const trace = asRecord(providerTrace);
  const error = asRecord(trace?.last_error);
  if (!error) return null;
  const statusCode =
    typeof error.status_code === "number"
      ? error.status_code
      : typeof error.status === "number"
        ? error.status
        : null;
  return {
    kind: typeof error.kind === "string" ? error.kind : null,
    code: typeof error.code === "string" ? error.code : null,
    message: typeof error.message === "string" ? error.message : null,
    statusCode,
    retryable: typeof error.retryable === "boolean" ? error.retryable : null,
  };
}

function inferKind(
  input: StructuredTurnErrorInput,
  combinedText: string,
  statusCode: number | null,
): TurnErrorKind {
  if (input.kind && VALID_KINDS.has(input.kind as TurnErrorKind)) {
    return input.kind as TurnErrorKind;
  }
  const lower = combinedText.toLowerCase();
  if (
    statusCode === 401 ||
    statusCode === 403 ||
    /(unauthorized|forbidden|invalid[_ -]?api[_ -]?key|authentication|incorrect api key|invalid token|expired token|未授权|密钥无效|认证失败)/i.test(
      lower,
    )
  ) {
    return "auth";
  }
  if (
    statusCode === 429 ||
    statusCode === 529 ||
    /(rate[_ -]?limit|too many requests|quota exceeded|insufficient_quota|overloaded|限流|配额不足)/i.test(
      lower,
    )
  ) {
    return "rate_limit";
  }
  if (
    statusCode === 413 ||
    /(context[_ -]?length|maximum context|token limit|prompt is too long|input is too long|too many tokens|exceeds the context|上下文过长|超出上下文)/i.test(
      lower,
    )
  ) {
    return "context_limit";
  }
  if (
    statusCode === 408 ||
    statusCode === 504 ||
    /(timed?\s*out|deadline exceeded|provider_timeout|超时)/i.test(lower)
  ) {
    return "timeout";
  }
  if (statusCode !== null && statusCode >= 500) {
    return "server";
  }
  if (
    /(econnrefused|econnreset|enotfound|connection reset|connection refused|network error|failed to fetch|socket hang up|upstream_transport|连接中断|网络异常)/i.test(
      lower,
    )
  ) {
    return "connection";
  }
  if (
    (statusCode !== null && statusCode >= 400 && statusCode < 500) ||
    /(invalid_request|bad_request|provider_invalid_response|model.*not found)/i.test(lower)
  ) {
    return "bad_request";
  }
  return "unknown";
}

function extractStatusFromString(raw: string): number | null {
  const match = /\bHTTP\s*(\d{3})\b/i.exec(raw);
  if (!match) return null;
  const parsed = Number(match[1]);
  return Number.isFinite(parsed) ? parsed : null;
}

export function classifyTurnError(
  rawInput: StructuredTurnErrorInput | string | null | undefined,
): ClassifiedTurnError {
  const input: StructuredTurnErrorInput =
    typeof rawInput === "string" ? { message: rawInput } : (rawInput ?? {});
  const code = input.code?.trim() || null;
  const message = input.message?.trim() || "";
  const statusCode =
    typeof input.statusCode === "number"
      ? input.statusCode
      : typeof input.status === "number"
        ? input.status
        : extractStatusFromString(message);
  const rawDetail = [
    statusCode ? `HTTP ${statusCode}` : null,
    code,
    message || "模型请求未能完成",
  ]
    .filter(Boolean)
    .join(" · ");

  const kind = inferKind(input, `${code ?? ""} ${message}`, statusCode);

  switch (kind) {
    case "auth":
      return {
        kind,
        title: "模型服务鉴权失败",
        guidance: "API 密钥无效、已过期或无当前模型访问权限，请检查模型配置后重试。",
        rawDetail,
        statusCode,
        code,
        retryable: false,
        primaryAction: "open_provider_settings",
      };
    case "rate_limit":
      return {
        kind,
        title: "模型服务触发限流或配额不足",
        guidance: "当前提供商请求频率达到上限或额度不足，建议稍后重试或切换其他模型。",
        rawDetail,
        statusCode,
        code,
        retryable: true,
        primaryAction: "retry",
      };
    case "context_limit":
      return {
        kind,
        title: "上下文长度超出模型上限",
        guidance: "当前对话历史与工具输出已超过所选模型的最大上下文窗口，可先压缩上下文再重试。",
        rawDetail,
        statusCode,
        code,
        retryable: false,
        primaryAction: "compact_and_retry",
      };
    case "timeout":
      return {
        kind,
        title: "模型服务响应超时",
        guidance: "上游推理或工具调用耗时过长导致连接超时，可直接重试或切换更快模型。",
        rawDetail,
        statusCode,
        code,
        retryable: true,
        primaryAction: "retry",
      };
    case "server":
      return {
        kind,
        title: "上游模型服务暂时异常",
        guidance: "模型提供商服务端返回 5xx 错误，通常是短暂波动，稍后重试即可恢复。",
        rawDetail,
        statusCode,
        code,
        retryable: true,
        primaryAction: "retry",
      };
    case "connection":
      return {
        kind,
        title: "网络连接中断",
        guidance: "与服务端或模型提供商的流式连接意外断开，请确认网络畅通后重试。",
        rawDetail,
        statusCode,
        code,
        retryable: true,
        primaryAction: "retry",
      };
    case "bad_request":
      return {
        kind,
        title: "请求参数被模型服务拒绝",
        guidance: "所选模型可能不支持当前参数、附件类型或配置项，可切换模型或在设置中核对接口。",
        rawDetail,
        statusCode,
        code,
        retryable: false,
        primaryAction: "retry",
      };
    default:
      return {
        kind: "unknown",
        title: "本次回答未能完成",
        guidance: "生成过程中发生未分类错误，可展开技术详情排查或直接重试。",
        rawDetail,
        statusCode,
        code,
        retryable: input.retryable ?? true,
        primaryAction: "retry",
      };
  }
}
