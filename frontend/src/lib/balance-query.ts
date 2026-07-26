import {
  executeProviderBalanceQuery,
  saveProviderBalanceQueryResult,
} from "@/api";
import { openBalanceScript } from "@/lib/balance-script";
import type {
  BalanceExtractorResult,
  ProviderBalanceQueryConfig,
  ProviderBalanceQueryLastResult,
} from "@/types/providers";

/* ------------------------------------------------------------------ */
/* Preset templates (adapted from cc-switch)                           */
/* ------------------------------------------------------------------ */

export interface BalanceQueryPreset {
  id: string;
  label: string;
  note?: string;
  script: string;
}

export const BALANCE_QUERY_PRESETS: BalanceQueryPreset[] = [
  {
    id: "general",
    label: "通用模板",
    note: "适用于返回 { balance } 的 /user/balance 接口",
    script: `({
  request: {
    url: "{{baseUrl}}/user/balance",
    method: "GET",
    headers: {
      "Authorization": "Bearer {{apiKey}}",
      "User-Agent": "LearnGraph/1.0"
    }
  },
  extractor: function(response) {
    return {
      isValid: response.is_active !== false,
      remaining: response.balance,
      unit: "USD"
    };
  }
})`,
  },
  {
    id: "newapi",
    label: "NewAPI",
    note: "需要在下方「自定义变量」中填写 accessToken 与 userId（系统访问令牌，非 sk- 密钥）",
    script: `({
  request: {
    url: "{{baseUrl}}/api/user/self",
    method: "GET",
    headers: {
      "Content-Type": "application/json",
      "Authorization": "Bearer {{accessToken}}",
      "User-Agent": "LearnGraph/1.0",
      "New-Api-User": "{{userId}}"
    }
  },
  extractor: function (response) {
    if (response.success && response.data) {
      return {
        planName: response.data.group || "默认套餐",
        remaining: response.data.quota / 500000,
        used: response.data.used_quota / 500000,
        total: (response.data.quota + response.data.used_quota) / 500000,
        unit: "USD"
      };
    }
    return {
      isValid: false,
      invalidMessage: response.message || "查询失败"
    };
  }
})`,
  },
  {
    id: "sub2api",
    label: "Sub2API",
    note: "Sub2API 中转站官方余额接口（GET /v1/usage）；仅需 API Key，额度耗尽的 Key 也可查询",
    script: `({
  request: {
    url: "{{baseUrl}}/v1/usage",
    method: "GET",
    headers: {
      "Authorization": "Bearer {{apiKey}}"
    }
  },
  extractor: function (response) {
    var remaining = response.remaining;
    if (remaining === undefined || remaining === null) {
      remaining = response.quota ? response.quota.remaining : undefined;
    }
    if (remaining === undefined || remaining === null) {
      remaining = response.balance;
    }
    var unit = response.unit || (response.quota && response.quota.unit) || "USD";
    if (remaining === -1) {
      return {
        isValid: response.isValid !== false,
        planName: response.planName || "Sub2API",
        unit: unit,
        extra: "不限额度（remaining = -1）"
      };
    }
    var result = {
      isValid: response.isValid !== false,
      planName: response.planName || "Sub2API",
      remaining: remaining,
      unit: unit
    };
    if (response.quota && typeof response.quota.limit === "number") {
      result.total = response.quota.limit;
      result.used = response.quota.used;
    }
    return result;
  }
})`,
  },
  {
    id: "custom",
    label: "自定义",
    script: `({
  request: {
    url: "{{baseUrl}}/",
    method: "GET",
    headers: {
      "Authorization": "Bearer {{apiKey}}"
    }
  },
  extractor: function(response) {
    return {
      remaining: 0,
      unit: "USD"
    };
  }
})`,
  },
];

/* ------------------------------------------------------------------ */
/* Query flow                                                          */
/* ------------------------------------------------------------------ */

export interface CustomBalanceQueryOutcome {
  providerId: string;
  results: BalanceExtractorResult[];
  queriedAt: string;
}

/** Evaluate the script in the sandbox, run the request on the backend, and
 * feed the parsed JSON back into the sandboxed extractor. */
export async function runCustomBalanceQuery(
  providerId: string,
  config: Pick<
    ProviderBalanceQueryConfig,
    "script" | "timeout_seconds" | "variables"
  >,
): Promise<CustomBalanceQueryOutcome> {
  const runtime = await openBalanceScript(config.script);
  try {
    const response = await executeProviderBalanceQuery(providerId, {
      request: runtime.request,
      timeout_seconds: config.timeout_seconds,
      variables: config.variables,
    });
    if (!response.ok) {
      const snippet =
        response.text?.slice(0, 200) ??
        (response.payload !== null && response.payload !== undefined
          ? JSON.stringify(response.payload).slice(0, 200)
          : "");
      throw new Error(
        `HTTP ${response.status_code}${snippet ? `：${snippet}` : ""}`,
      );
    }
    if (response.payload === null || response.payload === undefined) {
      throw new Error("响应不是有效 JSON，无法交给 extractor 处理");
    }
    const results = await runtime.extract(response.payload);
    return {
      providerId,
      results,
      queriedAt: response.queried_at,
    };
  } finally {
    runtime.dispose();
  }
}

/** Cache the first extractor entry so the provider list can show it. */
export async function persistCustomBalanceResult(
  outcome: CustomBalanceQueryOutcome,
): Promise<void> {
  const first = outcome.results[0];
  await saveProviderBalanceQueryResult(outcome.providerId, {
    is_valid: first?.isValid ?? null,
    invalid_message: first?.invalidMessage ?? null,
    remaining: first?.remaining ?? null,
    unit: first?.unit ?? null,
    plan_name: first?.planName ?? null,
    total: first?.total ?? null,
    used: first?.used ?? null,
    extra: first?.extra ?? null,
  });
}

/* ------------------------------------------------------------------ */
/* Display / input helpers                                             */
/* ------------------------------------------------------------------ */

export function relativeTimeLabel(iso: string): string {
  const elapsedMs = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(elapsedMs) || elapsedMs < 0) return "刚刚";
  const minutes = Math.floor(elapsedMs / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

export function formatBalanceQuerySummary(
  last: ProviderBalanceQueryLastResult,
): string {
  if (last.is_valid === false) {
    return last.invalid_message ? `失效：${last.invalid_message}` : "套餐已失效";
  }
  if (last.remaining !== null) {
    const amount = formatBalanceAmount(last.remaining);
    return `剩余 ${amount}${last.unit ? ` ${last.unit}` : ""}`;
  }
  return "已查询";
}

export function formatBalanceAmount(value: number): string {
  return Math.abs(value) >= 10_000
    ? Math.round(value).toLocaleString("zh-CN")
    : value.toFixed(2);
}

export function parseVariablesInput(text: string): Record<string, string> {
  const variables: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf("=");
    if (separator <= 0) {
      throw new Error(`自定义变量需按「名称=值」填写：${trimmed}`);
    }
    const name = trimmed.slice(0, separator).trim();
    const value = trimmed.slice(separator + 1).trim();
    if (!name || !value) continue;
    if (name === "baseUrl" || name === "apiKey") {
      throw new Error("baseUrl 与 apiKey 为保留变量，由系统自动注入");
    }
    variables[name] = value;
  }
  return variables;
}

export function stringifyVariables(
  variables: Record<string, string>,
): string {
  return Object.entries(variables)
    .map(([name, value]) => `${name}=${value}`)
    .join("\n");
}

/** Bake the provider's actual base URL into a preset script so the user sees
 * the real target address. `{{apiKey}}` stays a placeholder — the plaintext
 * key lives only on the server and is injected there at request time. */
export function materializeScript(
  script: string,
  baseUrl: string | null | undefined,
): string {
  const cleaned = (baseUrl ?? "").trim().replace(/\/+$/, "");
  if (!cleaned) return script;
  return script.replaceAll("{{baseUrl}}", cleaned);
}
