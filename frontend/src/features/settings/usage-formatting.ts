import { saveBlobViaNative } from "@/lib/native-download";
import { sumMoney, validMoney } from "@/lib/money";
import type { Provider } from "@/types/providers";
import type { BillingCurrency } from "@/types/usage";

export const USAGE_CARD_CLASS =
  "rounded-xl border border-border/80 bg-card text-card-foreground dark:border-[var(--border-subtle)] dark:bg-[var(--bg-surface)]";

export function stamp(value: string): string {
  return new Date(value).toLocaleString();
}

export function field(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

export function optionalMoney(form: FormData, key: string): string | null {
  const value = field(form, key);
  if (!value) return null;
  if (!validMoney(value)) throw new Error("请输入非负十进制金额，最多 25 位整数和 24 位小数");
  return value;
}

export const FEATURE_LABELS: Record<string, string> = {
  "*": "全部功能",
  audio_transcription: "语音转写",
  chat: "对话",
  chat_retry: "对话重试",
  chat_fast_preface: "快速响应",
  chat_goal_verification: "目标验证",
  chat_session_activity_summary: "会话摘要",
  chat_session_auto_title: "自动生成标题",
  chat_dictation_cleanup: "语音转写整理",
  chat_vision_describe: "图片理解",
  chat_graph_proposal: "对话图谱更新",
  chat_suggested_prompts: "追问建议",
  chat_opening_prompts: "开场引导提示",
  chat_demo: "对话演示",
  vision: "识图与视觉",
  image_generation: "图片生成",
  web_search: "网络搜索",
  schedule_suggestions: "定时任务建议",
  exercise_generate: "练习出题",
  exercise_grade: "练习判分",
  learning_package: "教学包生成",
  learning_illustration: "教学配图",
  graph_cover_image: "图谱封面图片",
  graph_cover_svg: "图谱封面矢量图",
  graph_cover_prompt: "图谱封面构思",
  learngraph_goal_plan: "学习目标规划",
  learngraph_graph_draft: "知识图谱初稿",
  learngraph_graph_root: "图谱根节点生成",
  learngraph_graph_chunk: "图谱主干生成",
  learngraph_node_patch: "知识节点改写",
  learngraph_node_merge_classification: "节点合并判定",
  learngraph_multi_node_study: "多节点联合学习",
  memory_embedding: "记忆向量索引",
  memory_extraction: "记忆整理",
  context_summarization: "长对话自动压缩",
  memory_profile_summary: "记忆画像汇总",
  memory_profile_verification: "记忆画像校验",
  memory_profile_intent: "记忆检索意图",
  memory_legacy_migration: "历史记忆迁移",
  sticker_vision: "表情包视觉分析",
  sticker_embedding: "表情包语义索引",
};

export const COST_STATUS_LABELS: Record<string, string> = {
  priced: "按价目表核算",
  provider_reported: "服务商回报",
  non_billable: "本地免计费",
  reserved: "执行中占用",
  unpriced: "未配置价格",
  usage_missing: "缺少用量数据",
  unpriced_units: "缺少计价单位",
  interrupted_usage_missing: "调用中断未结算",
  legacy_unknown: "历史待核实",
  not_executed: "未执行已释放",
};

export function formatFeatureLabel(feature: string): string {
  return FEATURE_LABELS[feature] ?? feature;
}

export function formatCostStatus(status: string): string {
  return COST_STATUS_LABELS[status] ?? status;
}

export function formatAmountSource(source: string): string {
  if (source === "workspace_manual") return "手动价格";
  if (source === "provider_reported") return "服务商回报";
  if (source === "non_billable") return "本地免计费";
  if (source === "provider_estimate") return "预估金额";
  if (source === "official_catalog") return "内置价格目录";
  if (source === "unknown" || source === "legacy_unknown") return "未知";
  if (source.startsWith("models_dev")) return "models.dev 目录";
  return source;
}

export function currencySymbol(currency: BillingCurrency): string {
  return currency === "USD" ? "$" : "¥";
}

export function compareDecimalStrings(a: string, b: string): number {
  const norm = (val: string) => {
    const clean = val.trim();
    if (!/^\d+(?:\.\d+)?$/.test(clean)) return { whole: 0n, frac: "" };
    const [w, f = ""] = clean.split(".");
    return { whole: BigInt(w || "0"), frac: f.replace(/0+$/, "") };
  };
  const pa = norm(a);
  const pb = norm(b);
  if (pa.whole !== pb.whole) return pa.whole < pb.whole ? -1 : 1;
  const maxLen = Math.max(pa.frac.length, pb.frac.length);
  const fa = pa.frac.padEnd(maxLen, "0");
  const fb = pb.frac.padEnd(maxLen, "0");
  if (fa === fb) return 0;
  return fa < fb ? -1 : 1;
}

export function decimalToNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : 0;
}

export function formatCompactTokens(tokens: number): string {
  const safe = Math.max(0, Math.round(tokens));
  if (safe < 1_000) return safe.toLocaleString("en-US");
  if (safe < 1_000_000) return `${(safe / 1_000).toFixed(2).replace(/\.?0+$/, "")}K`;
  if (safe < 1_000_000_000) return `${(safe / 1_000_000).toFixed(2)}M`;
  return `${(safe / 1_000_000_000).toFixed(2)}B`;
}

export function formatCompactCount(count: number): string {
  const safe = Math.max(0, Math.round(count));
  if (safe < 1_000_000) return safe.toLocaleString("en-US");
  if (safe < 1_000_000_000) return `${(safe / 1_000_000).toFixed(2)}M`;
  return `${(safe / 1_000_000_000).toFixed(2)}B`;
}

export function formatDisplayMoney(
  amount: string | number | null | undefined,
  currency: BillingCurrency | null | undefined,
  options: { showCode?: boolean; compact?: boolean; allUnknown?: boolean } = {},
): string {
  if (options.allUnknown) return "待核实";
  if (amount === null || amount === undefined || !currency) {
    return currency ? `金额待核实 (${currency})` : "金额待核实";
  }
  const value = String(amount).trim();
  if (!/^\d+(?:\.\d+)?$/.test(value)) return "金额待核实";
  const sym = currencySymbol(currency);
  const suffix = options.showCode ? ` ${currency}` : "";
  const [whole, fraction = ""] = value.split(".");
  const normalizedFrac = fraction.replace(/0+$/, "");
  const isZero = /^0+$/.test(whole) && normalizedFrac === "";
  if (isZero) return `${sym}0.00${suffix}`;

  if (options.compact && Number(whole) >= 1_000_000) {
    const millions = Number(`${whole}.${fraction.slice(0, 6)}`) / 1_000_000;
    return `${sym}${millions.toFixed(2)}M${suffix}`;
  }

  // Avoid displaying small non-zero costs as "$0.00".
  if (/^0+$/.test(whole) && compareDecimalStrings(value, "0.01") < 0) {
    if (compareDecimalStrings(value, "0.0001") < 0) {
      return `<${sym}0.0001${suffix}`;
    }
    return `${sym}0.${fraction.slice(0, 4).replace(/0+$/, "")}${suffix}`;
  }

  const cents = (
    BigInt(whole + fraction.padEnd(2, "0").slice(0, 2)) +
    ((fraction[2] ?? "0") >= "5" ? 1n : 0n)
  )
    .toString()
    .padStart(3, "0");
  const grouped = cents.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sym}${grouped}.${cents.slice(-2)}${suffix}`;
}

export function formatExactMoney(
  amount: string | number | null | undefined,
  currency: BillingCurrency | null | undefined,
): string {
  if (amount === null || amount === undefined || !currency) {
    return currency ? `金额待核实 ${currency}` : "缺失币种";
  }
  const value = String(amount).trim();
  if (!/^\d+(?:\.\d+)?$/.test(value)) return "金额待核实";
  const sym = currencySymbol(currency);
  const [whole, fraction = ""] = value.split(".");
  const grouped = (whole || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const trimmedFrac = fraction.replace(/0+$/, "");
  if (!trimmedFrac) return `${sym}${grouped}.00 ${currency}`;
  const displayFrac = trimmedFrac.length === 1 ? `${trimmedFrac}0` : trimmedFrac;
  return `${sym}${grouped}.${displayFrac} ${currency}`;
}

export function formatRateMoney(
  amount: string | number | null | undefined,
  currency: BillingCurrency,
): string {
  if (amount === null || amount === undefined) return "—";
  const value = String(amount).trim();
  if (!/^\d+(?:\.\d+)?$/.test(value)) return "—";
  const sym = currencySymbol(currency);
  const [whole, fraction = ""] = value.split(".");
  const grouped = (whole || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const trimmedFrac = fraction.replace(/0+$/, "");
  if (!trimmedFrac) return `${sym}${grouped}.00`;
  const displayFrac = trimmedFrac.length === 1 ? `${trimmedFrac}0` : trimmedFrac;
  return `${sym}${grouped}.${displayFrac}`;
}

export interface ResolvedProvider {
  name: string;
  rawId: string;
  isDeleted: boolean;
  isWildcard: boolean;
}

export function resolveProviderInfo(
  providerId: string,
  activeProviders: ReadonlyMap<string, Provider>,
  historicalNames: Readonly<Record<string, string>>,
): ResolvedProvider {
  if (providerId === "*") {
    return { name: "全部服务连接", rawId: "*", isDeleted: false, isWildcard: true };
  }
  const active = activeProviders.get(providerId);
  if (active) {
    return { name: active.display_name, rawId: providerId, isDeleted: false, isWildcard: false };
  }
  if (providerId === "local_mock") {
    return { name: "本地模拟服务", rawId: providerId, isDeleted: false, isWildcard: false };
  }
  const historical = historicalNames[providerId];
  if (historical) {
    return { name: historical, rawId: providerId, isDeleted: true, isWildcard: false };
  }
  return { name: "已删除的连接", rawId: providerId, isDeleted: true, isWildcard: false };
}

export type DatePreset = "all" | "today" | "last7d" | "last30d" | "month" | "custom";

function formatUtcIsoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function addUtcDays(date: Date, days: number): Date {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() + days));
}

export function presetToUtcRange(
  preset: Exclude<DatePreset, "custom">,
  now: Date = new Date(),
): { start_at?: string; end_at?: string } {
  if (preset === "all") return {};
  const todayStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const tomorrowStart = addUtcDays(todayStart, 1);
  if (preset === "today") {
    return {
      start_at: `${formatUtcIsoDate(todayStart)}T00:00:00Z`,
      end_at: `${formatUtcIsoDate(tomorrowStart)}T00:00:00Z`,
    };
  }
  if (preset === "last7d") {
    const start = addUtcDays(todayStart, -6);
    return {
      start_at: `${formatUtcIsoDate(start)}T00:00:00Z`,
      end_at: `${formatUtcIsoDate(tomorrowStart)}T00:00:00Z`,
    };
  }
  if (preset === "last30d") {
    const start = addUtcDays(todayStart, -29);
    return {
      start_at: `${formatUtcIsoDate(start)}T00:00:00Z`,
      end_at: `${formatUtcIsoDate(tomorrowStart)}T00:00:00Z`,
    };
  }
  const monthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const nextMonthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return {
    start_at: `${formatUtcIsoDate(monthStart)}T00:00:00Z`,
    end_at: `${formatUtcIsoDate(nextMonthStart)}T00:00:00Z`,
  };
}

export function detectDatePreset(
  startAt?: string,
  endAt?: string,
  now: Date = new Date(),
): DatePreset {
  if (!startAt && !endAt) return "all";
  for (const preset of ["today", "last7d", "last30d", "month"] as const) {
    const range = presetToUtcRange(preset, now);
    if (range.start_at === startAt && range.end_at === endAt) return preset;
  }
  return "custom";
}

export function formatDateRangeLabel(
  startAt?: string,
  endAt?: string,
  now: Date = new Date(),
): string {
  const preset = detectDatePreset(startAt, endAt, now);
  if (preset === "all") return "全部时间";
  if (preset === "today") return "今天";
  if (preset === "last7d") return "近 7 天";
  if (preset === "last30d") return "近 30 天";
  if (preset === "month") return "本月";
  const s = startAt?.slice(5, 10).replace("-", "/");
  const e = endAt?.slice(5, 10).replace("-", "/");
  if (s && e) return `${s} – ${e}`;
  if (s) return `${s} 起`;
  if (e) return `至 ${e}`;
  return "自定义时间";
}

export interface TrendPoint {
  key: string;
  shortLabel: string;
  tooltipLabel: string;
  costAmount: string;
  chartAmount: number | null;
  attempts: number;
  totalTokens: number;
  unknownEvents: number;
}

function isoWeekStartUtc(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  const day = dt.getUTCDay();
  const diff = day === 0 ? -6 : 1 - day;
  dt.setUTCDate(dt.getUTCDate() + diff);
  return dt.toISOString().slice(0, 10);
}

export function aggregateTrendSeries(
  days: readonly {
    label: string;
    cost_amount: string;
    attempts: number;
    total_tokens: number;
    unknown_events: number;
  }[],
  granularity: "day" | "week" | "month",
): TrendPoint[] {
  if (!days.length) return [];
  const buckets = new Map<
    string,
    { costs: string[]; attempts: number; totalTokens: number; unknownEvents: number }
  >();
  for (const row of days) {
    const key =
      granularity === "day"
        ? row.label
        : granularity === "week"
          ? isoWeekStartUtc(row.label)
          : row.label.slice(0, 7);
    const existing = buckets.get(key) ?? {
      costs: [],
      attempts: 0,
      totalTokens: 0,
      unknownEvents: 0,
    };
    existing.costs.push(row.cost_amount);
    existing.attempts += row.attempts;
    existing.totalTokens += row.total_tokens;
    existing.unknownEvents += row.unknown_events;
    buckets.set(key, existing);
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, item]) => {
      const costAmount = sumMoney(item.costs);
      const shortLabel =
        granularity === "month"
          ? key.slice(2).replace("-", "/")
          : key.slice(5).replace("-", "/");
      const tooltipLabel =
        granularity === "week"
          ? `${key} 起（当周 UTC）`
          : granularity === "month"
            ? `${key}（UTC）`
            : `${key}（UTC）`;
      return {
        key,
        shortLabel,
        tooltipLabel,
        costAmount,
        chartAmount: item.unknownEvents >= item.attempts && compareDecimalStrings(costAmount, "0") === 0
          ? null
          : decimalToNumber(costAmount),
        attempts: item.attempts,
        totalTokens: item.totalTokens,
        unknownEvents: item.unknownEvents,
      };
    });
}

export function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  return '"' + (/^[=+@-]/.test(text) ? "'" + text : text).replaceAll('"', '""') + '"';
}

export async function saveCsv(rows: unknown[][], filename: string): Promise<void> {
  const blob = new Blob(
    ["\ufeff" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n")],
    { type: "text/csv;charset=utf-8" },
  );
  if (await saveBlobViaNative(blob, filename)) return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
