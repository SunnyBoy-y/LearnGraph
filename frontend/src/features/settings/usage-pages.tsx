import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  Download,
  Globe,
  Mail,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import {
  acknowledgeBudgetAlert,
  clearUsageEvents,
  createBudgetPolicy,
  deleteBudgetPolicy,
  getAlertEmailConfig,
  getExchangeRate,
  getModelsDevStatus,
  getUsageSummary,
  listBudgetAlerts,
  listBudgetPolicies,
  listBudgetStatuses,
  listManualPrices,
  listPriceCatalog,
  listProviders,
  listSettings,
  listUsageEvents,
  refreshExchangeRate,
  refreshModelsDevSnapshot,
  removeManualPrice,
  sendTestAlertEmail,
  setExchangeRate,
  updateAlertEmailConfig,
  updateBudgetPolicy,
  updateSetting,
  upsertManualPrice,
} from "@/api";
import {
  ErrorState,
  LoadingState,
  MetricStrip,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Provider } from "@/types/providers";
import type {
  AlertEmailConfig,
  BudgetPolicy,
  BudgetPolicyCreate,
  BudgetPolicyUpdate,
  BudgetStatus,
  ManualPrice,
  ModelsDevStatus,
  PriceCatalogItem,
  UsageEvent,
} from "@/types/usage";

const CATALOG_PAGE_SIZE = 100;

function shortId(value: string): string {
  return value.length > 18 ? `${value.slice(0, 8)}…` : value;
}

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString();
}

function formatMoney(value: number, currency: "USD" | "CNY"): string {
  return `${currency === "CNY" ? "¥" : "$"}${value.toFixed(4)}`;
}

function formatPerMillion(value: number | null, currency: string): string {
  if (value === null) return "—";
  return `${currency === "USD" ? "$" : "¥"}${value}`;
}

const MODELS_DEV_ORIGIN_LABEL: Record<ModelsDevStatus["origin"], string> = {
  bundled: "内置快照",
  network: "联网快照",
  network_cache: "本地缓存（此前联网获取）",
  missing: "快照缺失",
};

/* ------------------------------------------------------------------ */
/* 概览：时间跨度、费用趋势、模型消费比例、调用次数                     */
/* ------------------------------------------------------------------ */

type RangeKey = "1h" | "24h" | "7d" | "30d" | "all" | "custom";

const RANGE_OPTIONS: { key: RangeKey; label: string }[] = [
  { key: "1h", label: "近 1 小时" },
  { key: "24h", label: "近 24 小时" },
  { key: "7d", label: "近 7 天" },
  { key: "30d", label: "近 30 天" },
  { key: "all", label: "全部" },
  { key: "custom", label: "自定义" },
];

const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;

const PIE_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

function rangeBounds(
  range: RangeKey,
  customStart: string,
  customEnd: string,
): { start: number | null; end: number | null } {
  const now = Date.now();
  if (range === "1h") return { start: now - HOUR_MS, end: now };
  if (range === "24h") return { start: now - DAY_MS, end: now };
  if (range === "7d") return { start: now - 7 * DAY_MS, end: now };
  if (range === "30d") return { start: now - 30 * DAY_MS, end: now };
  if (range === "custom") {
    const start = customStart ? new Date(customStart).getTime() : Number.NaN;
    const end = customEnd ? new Date(customEnd).getTime() : Number.NaN;
    return {
      start: Number.isFinite(start) ? start : null,
      end: Number.isFinite(end) ? end : null,
    };
  }
  return { start: null, end: null };
}

function bucketSizeFor(spanMs: number): number {
  if (spanMs <= 2 * HOUR_MS) return 5 * 60_000;
  if (spanMs <= 48 * HOUR_MS) return HOUR_MS;
  return DAY_MS;
}

function floorBucket(timestamp: number, bucket: number): number {
  if (bucket >= DAY_MS) {
    const date = new Date(timestamp);
    date.setHours(0, 0, 0, 0);
    return date.getTime();
  }
  return Math.floor(timestamp / bucket) * bucket;
}

function bucketLabel(timestamp: number, bucket: number): string {
  const date = new Date(timestamp);
  if (bucket >= DAY_MS)
    return date.toLocaleDateString([], { month: "2-digit", day: "2-digit" });
  return date.toLocaleString([], {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface TrendBucket {
  time: string;
  cost: number;
  tokens: number;
  calls: number;
  cachedRead: number;
  cachedWrite: number;
  uncachedInput: number;
  hitRate: number | null;
}

function buildBuckets(
  events: UsageEvent[],
  start: number | null,
  end: number | null,
  displayCurrency: "USD" | "CNY",
): TrendBucket[] {
  if (!events.length) return [];
  const times = events.map((event) => new Date(event.created_at).getTime());
  const spanStart = start ?? Math.min(...times);
  const spanEnd = end ?? Math.max(...times);
  const bucket = bucketSizeFor(Math.max(1, spanEnd - spanStart));
  const groups = new Map<number, TrendBucket>();
  for (const event of events) {
    const key = floorBucket(new Date(event.created_at).getTime(), bucket);
    const current = groups.get(key) ?? {
      time: bucketLabel(key, bucket),
      cost: 0,
      tokens: 0,
      calls: 0,
      cachedRead: 0,
      cachedWrite: 0,
      uncachedInput: 0,
      hitRate: null,
    };
    const cachedRead = event.cached_input_tokens ?? 0;
    const cachedWrite = event.cache_creation_input_tokens ?? 0;
    current.cost +=
      displayCurrency === "CNY" ? event.cost_cny : event.cost_usd;
    current.tokens += event.input_tokens + event.output_tokens;
    current.calls += 1;
    current.cachedRead += cachedRead;
    current.cachedWrite += cachedWrite;
    // 缓存读写均包含在 input_tokens 总量中，不重复累加；普通输入为扣除缓存部分后的余量。
    current.uncachedInput += Math.max(0, event.input_tokens - cachedRead - cachedWrite);
    groups.set(key, current);
  }
  // 补零桶让趋势的空白时间段可见；桶数过多时跳过以免拖慢渲染。
  const first = floorBucket(spanStart, bucket);
  if ((spanEnd - first) / bucket <= 400) {
    for (let key = first; key <= spanEnd; key += bucket) {
      const aligned = floorBucket(key, bucket);
      if (!groups.has(aligned)) {
        groups.set(aligned, {
          time: bucketLabel(aligned, bucket),
          cost: 0,
          tokens: 0,
          calls: 0,
          cachedRead: 0,
          cachedWrite: 0,
          uncachedInput: 0,
          hitRate: null,
        });
      }
    }
  }
  return [...groups.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([, value]) => {
      const totalInput = value.cachedRead + value.cachedWrite + value.uncachedInput;
      return {
        ...value,
        hitRate: totalInput > 0 ? (value.cachedRead / totalInput) * 100 : null,
      };
    });
}

/** 子序列式模糊匹配："cld5" 可命中 "claude-fable-5"。 */
function fuzzyMatches(text: string, query: string): boolean {
  let index = 0;
  for (const char of text) {
    if (index < query.length && char === query[index]) index += 1;
  }
  return index === query.length;
}

function FilterCombobox({
  ariaLabel,
  className,
  emptyText,
  onValueChange,
  options,
  searchPlaceholder,
  value,
}: {
  ariaLabel: string;
  className?: string;
  emptyText: string;
  onValueChange: (value: string) => void;
  options: { label: string; value: string }[];
  searchPlaceholder: string;
  value: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const selected = options.find((option) => option.value === value);
  const query = search.trim().toLowerCase();
  const visibleOptions = query
    ? options
        .map((option) => {
          const label = option.label.toLowerCase();
          const id = option.value.toLowerCase();
          const score =
            label.includes(query) || id.includes(query)
              ? 0
              : fuzzyMatches(label, query) || fuzzyMatches(id, query)
                ? 1
                : -1;
          return { option, score };
        })
        .filter((entry) => entry.score >= 0)
        .sort((left, right) => left.score - right.score)
        .map((entry) => entry.option)
    : options;
  return (
    <Popover
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        setSearch("");
      }}
      open={open}
    >
      <PopoverTrigger asChild>
        <button
          aria-expanded={open}
          aria-label={ariaLabel}
          className={`flex h-8 items-center justify-between gap-2 rounded-lg border border-input bg-transparent px-2.5 text-left text-xs whitespace-nowrap transition-colors outline-none hover:bg-muted/50 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 ${className ?? ""}`}
          type="button"
        >
          <span className="truncate">{selected?.label ?? value}</span>
          <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-0">
        {/* 过滤与排序由上面自行完成；cmdk 内建排序会移动 DOM 节点，与 ScrollArea 的包裹结构冲突 */}
        <Command shouldFilter={false}>
          <CommandInput
            onValueChange={setSearch}
            placeholder={searchPlaceholder}
            value={search}
          />
          {/* CommandList 自带 no-scrollbar，改由 ScrollArea 承担滚动并常驻显示滚动条 */}
          <CommandList className="max-h-none">
            <ScrollArea
              className="[&>[data-slot=scroll-area-viewport]]:max-h-64"
              type="always"
            >
              <CommandEmpty>{emptyText}</CommandEmpty>
              {visibleOptions.map((option) => (
                <CommandItem
                  className="pr-4"
                  data-checked={option.value === value}
                  key={option.value}
                  onSelect={() => {
                    onValueChange(option.value);
                    setOpen(false);
                  }}
                  value={option.value}
                >
                  <span className="truncate">{option.label}</span>
                </CommandItem>
              ))}
            </ScrollArea>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function OverviewSection({
  displayCurrency,
  events,
  providers,
}: {
  displayCurrency: "USD" | "CNY";
  events: UsageEvent[];
  providers: Provider[];
}) {
  const [range, setRange] = useState<RangeKey>("30d");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [providerFilter, setProviderFilter] = useState("all");
  const [modelFilter, setModelFilter] = useState("all");

  const providerName = (id: string) =>
    providers.find((provider) => provider.id === id)?.display_name ?? shortId(id);

  const providerOptions = useMemo(
    () => [...new Set(events.map((event) => event.provider_id))],
    [events],
  );
  const modelOptions = useMemo(
    () => [...new Set(events.map((event) => event.model_id))].sort(),
    [events],
  );

  // 颜色跟随模型本身（按全量费用排名分配），筛选变化不重新洗牌。
  const modelColorRank = useMemo(() => {
    const totals = new Map<string, number>();
    for (const event of events) {
      totals.set(
        event.model_id,
        (totals.get(event.model_id) ?? 0) +
          (displayCurrency === "CNY" ? event.cost_cny : event.cost_usd),
      );
    }
    return new Map(
      [...totals.entries()]
        .sort((left, right) => right[1] - left[1])
        .map(([model], index) => [model, index]),
    );
  }, [events, displayCurrency]);

  const { start, end } = rangeBounds(range, customStart, customEnd);

  const filtered = useMemo(
    () =>
      events.filter((event) => {
        const time = new Date(event.created_at).getTime();
        if (start !== null && time < start) return false;
        if (end !== null && time > end) return false;
        if (providerFilter !== "all" && event.provider_id !== providerFilter)
          return false;
        if (modelFilter !== "all" && event.model_id !== modelFilter)
          return false;
        return true;
      }),
    [events, start, end, providerFilter, modelFilter],
  );

  const buckets = useMemo(
    () => buildBuckets(filtered, start, end, displayCurrency),
    [filtered, start, end, displayCurrency],
  );

  const pieData = useMemo(() => {
    const totals = new Map<string, number>();
    for (const event of filtered) {
      totals.set(
        event.model_id,
        (totals.get(event.model_id) ?? 0) +
          (displayCurrency === "CNY" ? event.cost_cny : event.cost_usd),
      );
    }
    const sorted = [...totals.entries()]
      .filter(([, value]) => value > 0)
      .sort((left, right) => right[1] - left[1]);
    const top = sorted.slice(0, 5).map(([model, value]) => ({
      name: model,
      value,
      color:
        PIE_COLORS[(modelColorRank.get(model) ?? 0) % PIE_COLORS.length],
      isOther: false,
    }));
    const rest = sorted.slice(5).reduce((total, [, value]) => total + value, 0);
    if (rest > 0)
      top.push({
        name: "其他",
        value: rest,
        color: "var(--muted-foreground)",
        isOther: true,
      });
    return top;
  }, [filtered, displayCurrency, modelColorRank]);

  const pieTotal = pieData.reduce((total, item) => total + item.value, 0);
  const totalCalls = filtered.length;

  function exportBill() {
    const escape = (value: string | number) =>
      `"${String(value).replaceAll('"', '""')}"`;
    const groups = new Map<
      string,
      {
        provider: string;
        model: string;
        calls: number;
        inputTokens: number;
        cachedInputTokens: number;
        cacheCreationInputTokens: number;
        outputTokens: number;
        costUsd: number;
        costCny: number;
      }
    >();
    for (const event of filtered) {
      const key = `${event.provider_id}::${event.model_id}`;
      const current = groups.get(key) ?? {
        provider: providerName(event.provider_id),
        model: event.model_id,
        calls: 0,
        inputTokens: 0,
        cachedInputTokens: 0,
        cacheCreationInputTokens: 0,
        outputTokens: 0,
        costUsd: 0,
        costCny: 0,
      };
      current.calls += 1;
      current.inputTokens += event.input_tokens;
      current.cachedInputTokens += event.cached_input_tokens ?? 0;
      current.cacheCreationInputTokens += event.cache_creation_input_tokens ?? 0;
      current.outputTokens += event.output_tokens;
      current.costUsd += event.cost_usd;
      current.costCny += event.cost_cny;
      groups.set(key, current);
    }
    const items = [...groups.values()].sort(
      (left, right) => right.costCny - left.costCny,
    );
    const rows: (string | number)[][] = [
      [
        "provider",
        "model",
        "calls",
        "input_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
        "cost_cny",
      ],
      ...items.map((item) => [
        item.provider,
        item.model,
        item.calls,
        item.inputTokens,
        item.cachedInputTokens,
        item.cacheCreationInputTokens,
        item.outputTokens,
        item.inputTokens + item.outputTokens,
        item.costUsd.toFixed(6),
        item.costCny.toFixed(6),
      ]),
      [
        "合计",
        "",
        items.reduce((total, item) => total + item.calls, 0),
        items.reduce((total, item) => total + item.inputTokens, 0),
        items.reduce((total, item) => total + item.cachedInputTokens, 0),
        items.reduce((total, item) => total + item.cacheCreationInputTokens, 0),
        items.reduce((total, item) => total + item.outputTokens, 0),
        items.reduce(
          (total, item) => total + item.inputTokens + item.outputTokens,
          0,
        ),
        items.reduce((total, item) => total + item.costUsd, 0).toFixed(6),
        items.reduce((total, item) => total + item.costCny, 0).toFixed(6),
      ],
    ];
    const blob = new Blob(
      [`\uFEFF${rows.map((row) => row.map(escape).join(",")).join("\n")}`],
      { type: "text/csv;charset=utf-8" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `learngraph-bill-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

      const chartTooltipStyle = {
        background: "var(--card)",
        borderColor: "var(--border)",
        borderRadius: 10,
      } as const;

  const cacheTotals = useMemo(() => {
    let cachedRead = 0;
    let cachedWrite = 0;
    let uncachedInput = 0;
    for (const event of filtered) {
      cachedRead += event.cached_input_tokens ?? 0;
      cachedWrite += event.cache_creation_input_tokens ?? 0;
      uncachedInput += Math.max(
        0,
        event.input_tokens - (event.cached_input_tokens ?? 0) - (event.cache_creation_input_tokens ?? 0),
      );
    }
    const totalInput = cachedRead + cachedWrite + uncachedInput;
    const hitRate = totalInput > 0 ? (cachedRead / totalInput) * 100 : null;
    return { cachedRead, cachedWrite, uncachedInput, totalInput, hitRate };
  }, [filtered]);

  return (
    <>
      <Surface className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="no-scrollbar flex items-center gap-1 overflow-x-auto rounded-lg bg-muted p-[3px]">
            {RANGE_OPTIONS.map((option) => (
              <button
                className={`rounded-md px-2.5 py-1 text-xs font-medium whitespace-nowrap transition-colors ${
                  range === option.key
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
                key={option.key}
                onClick={() => setRange(option.key)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
          {range === "custom" ? (
            <div className="flex items-center gap-1.5">
              <Input
                aria-label="开始时间"
                className="h-8 w-44 text-xs"
                onChange={(event) => setCustomStart(event.currentTarget.value)}
                type="datetime-local"
                value={customStart}
              />
              <span className="text-xs text-muted-foreground">至</span>
              <Input
                aria-label="结束时间"
                className="h-8 w-44 text-xs"
                onChange={(event) => setCustomEnd(event.currentTarget.value)}
                type="datetime-local"
                value={customEnd}
              />
            </div>
          ) : null}
          <FilterCombobox
            ariaLabel="按供应商筛选"
            className="w-40"
            emptyText="没有匹配的供应商"
            onValueChange={setProviderFilter}
            options={[
              { label: "全部供应商", value: "all" },
              ...providerOptions.map((id) => ({
                label: providerName(id),
                value: id,
              })),
            ]}
            searchPlaceholder="搜索供应商…"
            value={providerFilter}
          />
          <FilterCombobox
            ariaLabel="按模型筛选"
            className="w-48"
            emptyText="没有匹配的模型"
            onValueChange={setModelFilter}
            options={[
              { label: "全部模型", value: "all" },
              ...modelOptions.map((model) => ({ label: model, value: model })),
            ]}
            searchPlaceholder="搜索模型…"
            value={modelFilter}
          />
          <Button
            className="ml-auto"
            disabled={!filtered.length}
            onClick={exportBill}
            size="sm"
            variant="outline"
          >
            <Download className="size-4" />
            导出账单
          </Button>
        </div>
      </Surface>

      <div className="grid gap-5 lg:grid-cols-[1.15fr_.85fr]">
        <Surface className="p-5">
          <SectionHeading
            description="当前筛选范围内按时间聚合的真实费用"
            title="费用趋势"
          />
          {buckets.length ? (
            <div className="mt-4 h-72">
              <ResponsiveContainer height="100%" width="100%">
                <AreaChart data={buckets}>
                  <defs>
                    <linearGradient id="usage-area" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="5%" stopColor="var(--primary)" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="var(--primary)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    stroke="var(--border)"
                    strokeDasharray="3 3"
                    vertical={false}
                  />
                  <XAxis
                    axisLine={false}
                    dataKey="time"
                    fontSize={11}
                    minTickGap={32}
                    tickLine={false}
                  />
                  <YAxis axisLine={false} fontSize={11} tickLine={false} width={48} />
                  <Tooltip
                    contentStyle={chartTooltipStyle}
                    formatter={(value, name) => [
                      name === "cost"
                        ? formatMoney(Number(value ?? 0), displayCurrency)
                        : Number(value ?? 0).toLocaleString(),
                      name === "cost" ? `费用 ${displayCurrency}` : "Token",
                    ]}
                  />
                  <Area
                    dataKey="cost"
                    fill="url(#usage-area)"
                    stroke="var(--primary)"
                    strokeWidth={2}
                    type="monotone"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="grid h-72 place-items-center text-sm text-muted-foreground">
              当前筛选范围内暂无用量事件
            </p>
          )}
        </Surface>
        <Surface className="p-5">
          <SectionHeading
            description={`按模型聚合的费用占比 · 共 ${formatMoney(pieTotal, displayCurrency)}`}
            title="模型消费比例"
          />
          {pieData.length ? (
            <div className="mt-2 flex flex-col items-center">
              <div className="h-48 w-full">
                <ResponsiveContainer height="100%" width="100%">
                  <PieChart>
                    <Pie
                      data={pieData}
                      dataKey="value"
                      innerRadius={52}
                      nameKey="name"
                      outerRadius={82}
                      paddingAngle={1}
                      stroke="var(--card)"
                      strokeWidth={2}
                    >
                      {pieData.map((item) => (
                        <Cell
                          fill={item.color}
                          fillOpacity={item.isOther ? 0.35 : 1}
                          key={item.name}
                        />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={chartTooltipStyle}
                      formatter={(value, name) => [
                        `${formatMoney(Number(value ?? 0), displayCurrency)} · ${
                          pieTotal
                            ? Math.round((Number(value ?? 0) / pieTotal) * 100)
                            : 0
                        }%`,
                        String(name),
                      ]}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <ul className="mt-3 w-full space-y-1.5 text-xs">
                {pieData.map((item) => (
                  <li className="flex items-center gap-2" key={item.name}>
                    <span
                      aria-hidden
                      className="size-2.5 shrink-0 rounded-full"
                      style={{
                        background: item.color,
                        opacity: item.isOther ? 0.35 : 1,
                      }}
                    />
                    <span className="min-w-0 flex-1 truncate font-mono" title={item.name}>
                      {item.name}
                    </span>
                    <span className="font-mono tabular-nums text-muted-foreground">
                      {formatMoney(item.value, displayCurrency)} ·{" "}
                      {pieTotal ? Math.round((item.value / pieTotal) * 100) : 0}%
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="grid h-64 place-items-center text-sm text-muted-foreground">
              当前筛选范围内暂无计费事件
            </p>
          )}
        </Surface>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Surface className="p-5">
          <SectionHeading
            description={`当前筛选范围内的实际 HTTP Attempt 次数 · 共 ${totalCalls.toLocaleString()} 次`}
            title="调用次数统计"
          />
          {buckets.length ? (
            <div className="mt-4 h-56">
              <ResponsiveContainer height="100%" width="100%">
                <BarChart data={buckets}>
                  <CartesianGrid
                    stroke="var(--border)"
                    strokeDasharray="3 3"
                    vertical={false}
                  />
                  <XAxis
                    axisLine={false}
                    dataKey="time"
                    fontSize={11}
                    minTickGap={32}
                    tickLine={false}
                  />
                  <YAxis
                    allowDecimals={false}
                    axisLine={false}
                    fontSize={11}
                    tickLine={false}
                    width={40}
                  />
                  <Tooltip
                    contentStyle={chartTooltipStyle}
                    cursor={{ fill: "var(--muted)", opacity: 0.5 }}
                    formatter={(value) => [
                      `${Number(value ?? 0).toLocaleString()} 次`,
                      "调用",
                    ]}
                  />
                  <Bar
                    dataKey="calls"
                    fill="var(--primary)"
                    maxBarSize={28}
                    radius={[4, 4, 0, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="grid h-56 place-items-center text-sm text-muted-foreground">
              当前筛选范围内暂无调用
            </p>
          )}
        </Surface>

        <Surface className="p-5">
          <SectionHeading
            description={
              cacheTotals.hitRate !== null
                ? `输入 Token 缓存命中率随时间变化 · 区间均值 ${cacheTotals.hitRate.toFixed(1)}%`
                : "输入 Token 缓存命中率随时间变化"
            }
            title="缓存命中"
          />
          {buckets.some((bucket) => bucket.hitRate !== null) ? (
            <div className="mt-4 h-56">
              <ResponsiveContainer height="100%" width="100%">
                <LineChart data={buckets}>
                  <CartesianGrid
                    stroke="var(--border)"
                    strokeDasharray="3 3"
                    vertical={false}
                  />
                  <XAxis
                    axisLine={false}
                    dataKey="time"
                    fontSize={11}
                    minTickGap={32}
                    tickLine={false}
                  />
                  <YAxis
                    axisLine={false}
                    domain={[0, 100]}
                    fontSize={11}
                    tickFormatter={(value) => `${value}%`}
                    tickLine={false}
                    width={44}
                  />
                  <Tooltip
                    contentStyle={chartTooltipStyle}
                    formatter={(value) => [
                      value === null || value === undefined
                        ? "—"
                        : `${Number(value).toFixed(1)}%`,
                      "缓存命中率",
                    ]}
                  />
                  <Line
                    connectNulls
                    dataKey="hitRate"
                    dot={false}
                    stroke="var(--chart-1)"
                    strokeWidth={2}
                    type="monotone"
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="grid h-56 place-items-center text-sm text-muted-foreground">
              当前筛选范围内暂无输入 Token
            </p>
          )}
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
            <li className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="size-2.5 rounded-full"
                style={{ background: "var(--chart-1)" }}
              />
              缓存读 {cacheTotals.cachedRead.toLocaleString()}
            </li>
            <li className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="size-2.5 rounded-full"
                style={{ background: "var(--chart-3)" }}
              />
              缓存写 {cacheTotals.cachedWrite.toLocaleString()}
            </li>
            <li className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="size-2.5 rounded-full"
                style={{ background: "var(--muted-foreground)", opacity: 0.35 }}
              />
              未命中 {cacheTotals.uncachedInput.toLocaleString()}
            </li>
          </ul>
        </Surface>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* 价格目录：models.dev、汇率、手动定价                                 */
/* ------------------------------------------------------------------ */

function ModelsDevCard() {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["models-dev-status"],
    queryFn: getModelsDevStatus,
  });
  const refresh = useMutation({
    mutationFn: refreshModelsDevSnapshot,
    onSuccess: (result) => {
      toast.success(
        `已从 models.dev 更新 ${result.model_count.toLocaleString()} 个模型（${result.priced_model_count.toLocaleString()} 个含资费）`,
      );
      void queryClient.invalidateQueries({ queryKey: ["models-dev-status"] });
      void queryClient.invalidateQueries({ queryKey: ["usage-price-catalog"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const data = status.data;
  return (
    <Surface className="p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <SectionHeading
            description="按模型名匹配：无论哪个供应商，只要模型名对得上，默认上下文窗口、思考模式与图片/视频等多模态能力都以 models.dev 为准；资费计算优先使用该快照的美元牌价（人民币原生目录除外）。你手动保存的模型价格始终优先。"
            title="models.dev 资费与能力快照"
          />
          {data ? (
            <p className="mt-3 font-mono text-xs text-muted-foreground">
              {MODELS_DEV_ORIGIN_LABEL[data.origin]} ·{" "}
              {data.model_count.toLocaleString()} 个模型 ·{" "}
              {data.priced_model_count.toLocaleString()} 个含资费
              {data.fetched_at
                ? ` · 数据时间 ${formatTimestamp(data.fetched_at)}`
                : ""}
            </p>
          ) : status.isError ? (
            <p className="mt-3 text-xs text-destructive">{status.error.message}</p>
          ) : (
            <p className="mt-3 text-xs text-muted-foreground">正在读取快照状态…</p>
          )}
        </div>
        <Button
          className="shrink-0"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
          size="sm"
        >
          <Globe className={`size-4 ${refresh.isPending ? "animate-spin" : ""}`} />
          {refresh.isPending ? "正在联网更新…" : "联网更新资费"}
        </Button>
      </div>
    </Surface>
  );
}

function ExchangeRateCard({ displayCurrency }: { displayCurrency: "USD" | "CNY" }) {
  const queryClient = useQueryClient();
  const rate = useQuery({
    queryKey: ["usage-exchange-rate"],
    queryFn: getExchangeRate,
  });
  const [manualRate, setManualRate] = useState("");
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["usage-exchange-rate"] });
  };
  const saveManual = useMutation({
    mutationFn: (value: number) => setExchangeRate(value),
    onSuccess: (result) => {
      toast.success(`汇率已更新为 1 USD = ${result.rate} CNY`);
      setManualRate("");
      invalidate();
    },
    onError: (error) => toast.error(error.message),
  });
  const refreshLive = useMutation({
    mutationFn: refreshExchangeRate,
    onSuccess: (result) => {
      toast.success(`已获取实时汇率：1 USD = ${result.rate} CNY`);
      invalidate();
    },
    onError: (error) => toast.error(error.message),
  });
  const data = rate.data;
  return (
    <Surface className="p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <SectionHeading
            description={`人民币计费模型按人民币计价，美元计费模型按美元计价；汇总显示为 ${displayCurrency} 时按此汇率折算。历史事件保留调用时的汇率快照。`}
            title="USD/CNY 汇率"
          />
          {data ? (
            <p className="mt-3 font-mono text-sm tabular-nums">
              1 USD = {data.rate} CNY
              <span className="ml-2 text-xs text-muted-foreground">
                {data.source.startsWith("live:")
                  ? `实时来源 ${data.source.slice(5)}`
                  : data.source === "workspace_manual"
                    ? "手动设置"
                    : data.source}{" "}
                · 生效 {formatTimestamp(data.effective_at)}
              </span>
            </p>
          ) : rate.isError ? (
            <p className="mt-3 text-xs text-destructive">{rate.error.message}</p>
          ) : (
            <p className="mt-3 text-xs text-muted-foreground">正在读取汇率…</p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Input
            aria-label="手动汇率"
            className="h-9 w-28"
            min="0.0001"
            onChange={(event) => setManualRate(event.currentTarget.value)}
            placeholder="手动汇率"
            step="0.0001"
            type="number"
            value={manualRate}
          />
          <Button
            disabled={saveManual.isPending || Number(manualRate) <= 0}
            onClick={() => saveManual.mutate(Number(manualRate))}
            size="sm"
            variant="outline"
          >
            保存
          </Button>
          <Button
            disabled={refreshLive.isPending}
            onClick={() => refreshLive.mutate()}
            size="sm"
          >
            <RefreshCw
              className={`size-4 ${refreshLive.isPending ? "animate-spin" : ""}`}
            />
            {refreshLive.isPending ? "获取中…" : "使用实时汇率"}
          </Button>
        </div>
      </div>
    </Surface>
  );
}

interface ManualPriceDraft {
  model_id: string;
  currency: "USD" | "CNY";
  input: string;
  cachedInput: string;
  output: string;
  fixed: string;
}

function draftFromCatalogItem(item: PriceCatalogItem): ManualPriceDraft {
  return {
    model_id: item.model_id,
    currency: item.currency,
    input: String(item.native_input_per_million),
    cachedInput:
      item.native_cached_input_per_million === null
        ? ""
        : String(item.native_cached_input_per_million),
    output: String(item.native_output_per_million),
    fixed: "0",
  };
}

function draftFromManualPrice(item: ManualPrice): ManualPriceDraft {
  return {
    model_id: item.model_id,
    currency: item.currency,
    input: String(item.input_per_million),
    cachedInput:
      item.cached_input_per_million === null
        ? ""
        : String(item.cached_input_per_million),
    output: String(item.output_per_million),
    fixed: String(item.fixed_per_call),
  };
}

function ManualPriceDialog({
  draft,
  onOpenChange,
  open,
}: {
  draft: ManualPriceDraft | null;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  const queryClient = useQueryClient();
  const [currency, setCurrency] = useState<"USD" | "CNY">("USD");
  const [input, setInput] = useState("0");
  const [cachedInput, setCachedInput] = useState("");
  const [output, setOutput] = useState("0");
  const [fixed, setFixed] = useState("0");
  const [initializedFor, setInitializedFor] = useState<ManualPriceDraft | null>(
    null,
  );
  if (draft && draft !== initializedFor) {
    setInitializedFor(draft);
    setCurrency(draft.currency);
    setInput(draft.input);
    setCachedInput(draft.cachedInput);
    setOutput(draft.output);
    setFixed(draft.fixed);
  }
  const save = useMutation({
    mutationFn: upsertManualPrice,
    onSuccess: (result) => {
      toast.success(`已保存 ${result.model_id} 的手动定价`);
      void queryClient.invalidateQueries({ queryKey: ["usage-manual-prices"] });
      onOpenChange(false);
    },
    onError: (error) => toast.error(error.message),
  });
  const valid =
    Number(input) >= 0 &&
    Number(output) >= 0 &&
    (cachedInput === "" || Number(cachedInput) >= 0) &&
    Number(fixed) >= 0;
  if (!draft) return null;
  const symbol = currency === "CNY" ? "¥" : "$";
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>手动定价</DialogTitle>
          <DialogDescription>
            为模型设置工作区级牌价，覆盖官方目录与 models.dev 快照；对所有供应商实例生效，历史账本不受影响。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-3">
          <div className="rounded-xl border bg-muted/25 p-3 font-mono text-xs">
            {draft.model_id}
          </div>
          <div className="space-y-2">
            <Label>计价币种</Label>
            <Select
              onValueChange={(value) => setCurrency(value as "USD" | "CNY")}
              value={currency}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="USD">USD（美元计费）</SelectItem>
                <SelectItem value="CNY">CNY（人民币计费）</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div className="space-y-1.5">
              <Label htmlFor="manual-price-input">输入 {symbol}/1M</Label>
              <Input
                id="manual-price-input"
                min="0"
                onChange={(event) => setInput(event.currentTarget.value)}
                type="number"
                value={input}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="manual-price-cached">缓存输入 {symbol}/1M</Label>
              <Input
                id="manual-price-cached"
                min="0"
                onChange={(event) => setCachedInput(event.currentTarget.value)}
                placeholder="同普通输入"
                type="number"
                value={cachedInput}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="manual-price-output">输出 {symbol}/1M</Label>
              <Input
                id="manual-price-output"
                min="0"
                onChange={(event) => setOutput(event.currentTarget.value)}
                type="number"
                value={output}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="manual-price-fixed">每次调用固定费用 {symbol}</Label>
            <Input
              id="manual-price-fixed"
              min="0"
              onChange={(event) => setFixed(event.currentTarget.value)}
              type="number"
              value={fixed}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            disabled={save.isPending || !valid}
            onClick={() =>
              save.mutate({
                model_id: draft.model_id,
                provider_id: "*",
                currency,
                input_per_million: Number(input) || 0,
                cached_input_per_million:
                  cachedInput === "" ? null : Number(cachedInput),
                output_per_million: Number(output) || 0,
                fixed_per_call: Number(fixed) || 0,
              })
            }
          >
            {save.isPending ? "保存中…" : "保存定价"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ManualPricesCard({
  manualPrices,
  onEdit,
}: {
  manualPrices: ManualPrice[];
  onEdit: (draft: ManualPriceDraft) => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: removeManualPrice,
    onSuccess: () => {
      toast.success("手动定价已删除，恢复目录默认价格");
      void queryClient.invalidateQueries({ queryKey: ["usage-manual-prices"] });
    },
    onError: (error) => toast.error(error.message),
  });
  if (!manualPrices.length) return null;
  return (
    <Surface className="p-5">
      <SectionHeading
        description="手动保存的模型牌价优先于官方目录与 models.dev；删除后恢复自动匹配。"
        title={`手动定价 · ${manualPrices.length}`}
      />
      <div className="mt-4 space-y-2">
        {manualPrices.map((item) => (
          <div
            className="flex flex-col gap-2 rounded-xl border p-3 text-xs sm:flex-row sm:items-center"
            key={item.id}
          >
            <div className="min-w-0 flex-1">
              <p className="break-all font-mono font-medium">{item.model_id}</p>
              <p className="mt-1 font-mono tabular-nums text-muted-foreground">
                in {formatPerMillion(item.input_per_million, item.currency)}/M
                {item.cached_input_per_million !== null
                  ? ` · cache ${formatPerMillion(item.cached_input_per_million, item.currency)}/M`
                  : ""}{" "}
                · out {formatPerMillion(item.output_per_million, item.currency)}/M
                {item.fixed_per_call
                  ? ` · call ${formatPerMillion(item.fixed_per_call, item.currency)}`
                  : ""}{" "}
                · {item.currency === "CNY" ? "人民币计费" : "美元计费"}
              </p>
              <p className="mt-1 text-[10px] text-muted-foreground">
                生效 {formatTimestamp(item.effective_at)}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                onClick={() => onEdit(draftFromManualPrice(item))}
                size="xs"
                variant="outline"
              >
                <Pencil className="size-3" />
                编辑
              </Button>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    aria-label={`删除 ${item.model_id} 的手动定价`}
                    disabled={remove.isPending}
                    size="icon-xs"
                    variant="ghost"
                  >
                    <Trash2 className="size-3.5 text-destructive" />
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogMedia className="bg-destructive/10 text-destructive">
                      <Trash2 />
                    </AlertDialogMedia>
                    <AlertDialogTitle>删除手动定价？</AlertDialogTitle>
                    <AlertDialogDescription>
                      删除后 {item.model_id} 恢复按官方目录或 models.dev
                      自动定价；历史账本不受影响。
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>取消</AlertDialogCancel>
                    <AlertDialogAction
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(item.model_id)}
                      variant="destructive"
                    >
                      确认删除
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>
        ))}
      </div>
    </Surface>
  );
}

function PriceCatalogTable({
  items,
  manualModels,
  onEdit,
}: {
  items: PriceCatalogItem[];
  manualModels: Set<string>;
  onEdit: (draft: ManualPriceDraft) => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [sourceFilter, setSourceFilter] = useState<"all" | "builtin" | "models_dev">("all");
  const [page, setPage] = useState(1);
  const filtered = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return items.filter((item) => {
      if (sourceFilter !== "all" && item.source !== sourceFilter) return false;
      if (!needle) return true;
      return (
        item.model_id.toLowerCase().includes(needle) ||
        item.provider_key.toLowerCase().includes(needle)
      );
    });
  }, [items, keyword, sourceFilter]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / CATALOG_PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visible = filtered.slice(
    (safePage - 1) * CATALOG_PAGE_SIZE,
    safePage * CATALOG_PAGE_SIZE,
  );
  return (
    <Surface className="p-5">
      <SectionHeading
        description="首次真实调用前自动加载匹配的官方价格快照；人民币原生目录按人民币计算，不会先按参考汇率改写。点击行内“编辑”可为该模型保存手动牌价。"
        title="模型价格映射目录"
      />
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索模型或渠道"
            className="h-9 w-64 pl-8"
            onChange={(event) => {
              setKeyword(event.currentTarget.value);
              setPage(1);
            }}
            placeholder="搜索模型或渠道…"
            value={keyword}
          />
        </div>
        <Select
          onValueChange={(value) => {
            setSourceFilter(value as "all" | "builtin" | "models_dev");
            setPage(1);
          }}
          value={sourceFilter}
        >
          <SelectTrigger aria-label="目录来源" className="h-9 w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部来源</SelectItem>
            <SelectItem value="builtin">内置目录</SelectItem>
            <SelectItem value="models_dev">models.dev</SelectItem>
          </SelectContent>
        </Select>
        <p className="ml-auto text-xs text-muted-foreground">
          共 {filtered.length.toLocaleString()} 条
          {totalPages > 1 ? ` · 第 ${safePage} / ${totalPages} 页` : ""}
        </p>
      </div>
      <div className="mt-3 max-h-[34rem] overflow-auto rounded-xl border">
        <table className="w-full min-w-[960px] text-left text-xs">
          <thead className="sticky top-0 z-10 bg-muted text-muted-foreground">
            <tr>
              <th className="px-3 py-2">渠道 / 模型</th>
              <th className="px-3 py-2">缓存输入</th>
              <th className="px-3 py-2">普通输入</th>
              <th className="px-3 py-2">输出</th>
              <th className="px-3 py-2">条件</th>
              <th className="px-3 py-2">来源</th>
              <th className="px-3 py-2 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((item) => (
              <tr className="border-t" key={item.catalog_id}>
                <td className="px-3 py-2">
                  <p className="font-medium">{item.provider_key}</p>
                  <p className="break-all font-mono text-muted-foreground">{item.model_id}</p>
                </td>
                <td className="px-3 py-2 font-mono tabular-nums">
                  {formatPerMillion(item.native_cached_input_per_million, item.currency)}
                </td>
                <td className="px-3 py-2 font-mono tabular-nums">
                  {formatPerMillion(item.native_input_per_million, item.currency)}
                </td>
                <td className="px-3 py-2 font-mono tabular-nums">
                  {formatPerMillion(item.native_output_per_million, item.currency)}
                </td>
                <td className="max-w-72 px-3 py-2 font-mono text-[10px] text-muted-foreground">
                  {Object.keys(item.conditions).length
                    ? JSON.stringify(item.conditions)
                    : "默认"}
                </td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-1.5">
                    <StatePill
                      label={item.source === "models_dev" ? "models.dev" : "内置"}
                      status={item.source === "models_dev" ? "local_mock" : "approved"}
                    />
                    {manualModels.has(item.model_id) ? (
                      <StatePill label="已手动定价" status="warning" />
                    ) : null}
                  </div>
                </td>
                <td className="px-3 py-2 text-right">
                  <Button
                    aria-label={`编辑 ${item.model_id} 定价`}
                    onClick={() => onEdit(draftFromCatalogItem(item))}
                    size="xs"
                    variant="outline"
                  >
                    <Pencil className="size-3" />
                    编辑
                  </Button>
                </td>
              </tr>
            ))}
            {!visible.length ? (
              <tr>
                <td className="px-3 py-8 text-center text-muted-foreground" colSpan={7}>
                  没有匹配的目录条目
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {totalPages > 1 ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            第 {safePage} / {totalPages} 页 · 每页 {CATALOG_PAGE_SIZE} 条
          </p>
          <div className="flex items-center gap-1">
            <Button
              disabled={safePage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              size="sm"
              variant="outline"
            >
              <ChevronLeft className="size-4" />
              上一页
            </Button>
            <Button
              disabled={safePage >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              size="sm"
              variant="outline"
            >
              下一页
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      ) : null}
      <p className="mt-3 text-xs text-muted-foreground">
        DeepSeek 目录包含 Asia/Shanghai 09:00–12:00、14:00–18:00 的 2 倍峰值规则；实际 UsageEvent 固化调用时倍率。促销和长上下文阶梯保存在条件字段中。
      </p>
    </Surface>
  );
}

/* ------------------------------------------------------------------ */
/* 预算与告警：额度策略、告警、邮件通知                                 */
/* ------------------------------------------------------------------ */

type PolicyScope = "workspace" | "provider" | "model";
type PolicyMode = "alert" | "block";

function CreateBudgetPolicyDialog({
  busy,
  models,
  onCreate,
  providers,
}: {
  busy: boolean;
  models: string[];
  onCreate: (payload: BudgetPolicyCreate) => Promise<void>;
  providers: Provider[];
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<PolicyScope>("workspace");
  const [providerId, setProviderId] = useState("");
  const [modelId, setModelId] = useState("");
  const [period, setPeriod] = useState<"calendar_month_utc" | "calendar_day_utc">(
    "calendar_month_utc",
  );
  const [mode, setMode] = useState<PolicyMode>("alert");
  const [currency, setCurrency] = useState<"USD" | "CNY">("CNY");
  const [amount, setAmount] = useState("");
  const [warnAmount, setWarnAmount] = useState("");

  const amountValue = Number(amount);
  const warnValue = warnAmount ? Number(warnAmount) : null;
  const scopeValid =
    scope === "workspace" ||
    (scope === "provider" && Boolean(providerId)) ||
    (scope === "model" && Boolean(modelId.trim()));
  const amountsValid =
    Number.isFinite(amountValue) &&
    amountValue > 0 &&
    (warnValue === null ||
      (Number.isFinite(warnValue) && warnValue >= 0 && warnValue <= amountValue));

  function reset() {
    setName("");
    setScope("workspace");
    setProviderId("");
    setModelId("");
    setPeriod("calendar_month_utc");
    setMode("alert");
    setCurrency("CNY");
    setAmount("");
    setWarnAmount("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!scopeValid || !amountsValid) return;
    const payload: BudgetPolicyCreate = {
      name:
        name.trim() ||
        (scope === "workspace"
          ? "工作区总额度"
          : scope === "provider"
            ? `供应商额度 · ${
                providers.find((provider) => provider.id === providerId)
                  ?.display_name ?? shortId(providerId)
              }`
            : `模型额度 · ${modelId.trim()}`),
      provider_id: scope === "provider" ? providerId : "*",
      model_id: scope === "model" ? modelId.trim() : "*",
      feature: "*",
      period,
      limit_currency: currency,
      soft_limit_cny: mode === "alert" ? amountValue : warnValue,
      hard_limit_cny: mode === "block" ? amountValue : null,
      enabled: true,
    };
    try {
      await onCreate(payload);
      setOpen(false);
      reset();
    } catch {
      // 服务端错误在页面级 mutation 中提示，不关闭表单。
    }
  }

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="size-4" />
          新建额度策略
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>新建额度策略</DialogTitle>
            <DialogDescription>
              支持工作区总额度、单个供应商额度和单个模型额度；可选择达到额度自动停用（阻断调用），或仅告警不停用。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-5">
            <div className="space-y-2">
              <Label htmlFor="budget-create-name">名称（可留空自动生成）</Label>
              <Input
                id="budget-create-name"
                maxLength={160}
                onChange={(event) => setName(event.currentTarget.value)}
                value={name}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>额度范围</Label>
                <Select
                  onValueChange={(value) => setScope(value as PolicyScope)}
                  value={scope}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="workspace">整个工作区（总额度）</SelectItem>
                    <SelectItem value="provider">指定供应商</SelectItem>
                    <SelectItem value="model">指定模型</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>统计周期</Label>
                <Select
                  onValueChange={(value) =>
                    setPeriod(value as "calendar_month_utc" | "calendar_day_utc")
                  }
                  value={period}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="calendar_month_utc">每自然月（UTC）</SelectItem>
                    <SelectItem value="calendar_day_utc">每自然日（UTC）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            {scope === "provider" ? (
              <div className="space-y-2">
                <Label>供应商</Label>
                <Select onValueChange={setProviderId} value={providerId}>
                  <SelectTrigger>
                    <SelectValue placeholder="选择供应商实例" />
                  </SelectTrigger>
                  <SelectContent>
                    {providers.map((provider) => (
                      <SelectItem key={provider.id} value={provider.id}>
                        {provider.display_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}
            {scope === "model" ? (
              <div className="space-y-2">
                <Label htmlFor="budget-create-model">模型 ID</Label>
                <Input
                  id="budget-create-model"
                  list="budget-model-options"
                  onChange={(event) => setModelId(event.currentTarget.value)}
                  placeholder="如 deepseek-v4-flash"
                  value={modelId}
                />
                <datalist id="budget-model-options">
                  {models.map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              </div>
            ) : null}
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>达到额度后</Label>
                <Select
                  onValueChange={(value) => setMode(value as PolicyMode)}
                  value={mode}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="alert">仅告警，不停用</SelectItem>
                    <SelectItem value="block">自动停用（阻断后续调用）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>额度币种</Label>
                <Select
                  onValueChange={(value) => setCurrency(value as "USD" | "CNY")}
                  value={currency}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CNY">CNY（人民币）</SelectItem>
                    <SelectItem value="USD">USD（美元，按当前汇率折算）</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="budget-create-amount">
                  额度金额 {currency === "CNY" ? "CNY ¥" : "USD $"}
                </Label>
                <Input
                  id="budget-create-amount"
                  min="0"
                  onChange={(event) => setAmount(event.currentTarget.value)}
                  step="0.01"
                  type="number"
                  value={amount}
                />
              </div>
              {mode === "block" ? (
                <div className="space-y-2">
                  <Label htmlFor="budget-create-warn">
                    提前告警金额 {currency === "CNY" ? "CNY ¥" : "USD $"}（可选）
                  </Label>
                  <Input
                    id="budget-create-warn"
                    min="0"
                    onChange={(event) => setWarnAmount(event.currentTarget.value)}
                    step="0.01"
                    type="number"
                    value={warnAmount}
                  />
                </div>
              ) : null}
            </div>
            {currency === "USD" ? (
              <p className="text-xs text-muted-foreground">
                美元额度在调用前按当前 USD/CNY 汇率折算成人民币门槛；汇率变化后门槛随之浮动。
              </p>
            ) : null}
            {!amountsValid && amount !== "" ? (
              <p className="text-xs text-destructive">
                额度必须大于 0；提前告警金额不能高于额度金额。
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button disabled={busy || !scopeValid || !amountsValid} type="submit">
              {busy ? "创建中…" : "创建策略"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function parseOptionalLimit(value: string): number | null | undefined {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
}

function EditBudgetPolicyDialog({
  busy,
  onUpdate,
  policy,
}: {
  busy: boolean;
  onUpdate: (payload: BudgetPolicyUpdate) => Promise<void>;
  policy: BudgetPolicy;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(policy.name);
  const [currency, setCurrency] = useState<"USD" | "CNY">(
    policy.limit_currency ?? "CNY",
  );
  const [softLimit, setSoftLimit] = useState(
    policy.soft_limit_cny === null ? "" : String(policy.soft_limit_cny),
  );
  const [hardLimit, setHardLimit] = useState(
    policy.hard_limit_cny === null ? "" : String(policy.hard_limit_cny),
  );
  const [enabled, setEnabled] = useState(policy.enabled);
  const soft = parseOptionalLimit(softLimit);
  const hard = parseOptionalLimit(hardLimit);
  const limitsValid =
    soft !== undefined &&
    hard !== undefined &&
    (soft !== null || hard !== null) &&
    !(soft !== null && hard !== null && soft > hard);

  function resetFromPolicy() {
    setName(policy.name);
    setCurrency(policy.limit_currency ?? "CNY");
    setSoftLimit(policy.soft_limit_cny === null ? "" : String(policy.soft_limit_cny));
    setHardLimit(policy.hard_limit_cny === null ? "" : String(policy.hard_limit_cny));
    setEnabled(policy.enabled);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!limitsValid || soft === undefined || hard === undefined) return;
    try {
      await onUpdate({
        name: name.trim(),
        limit_currency: currency,
        soft_limit_cny: soft,
        hard_limit_cny: hard,
        enabled,
      });
      setOpen(false);
    } catch {
      // The page-level mutation presents the server error without closing the form.
    }
  }

  return (
    <Dialog
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (nextOpen) resetFromPolicy();
      }}
      open={open}
    >
      <DialogTrigger asChild>
        <Button disabled={busy} size="xs" variant="outline">
          <Pencil className="size-3" />编辑
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>编辑额度策略</DialogTitle>
            <DialogDescription>
              范围和周期保持不可变；若要改变匹配范围，请删除后重新创建策略。软告警金额=仅提醒，硬停用金额=达到后阻断调用。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-5">
            <div className="rounded-xl border bg-muted/25 p-3 text-xs text-muted-foreground">
              {policy.provider_id} / {policy.model_id} / {policy.feature} · {policy.period}
            </div>
            <div className="space-y-2">
              <Label htmlFor={`budget-policy-name-${policy.id}`}>名称</Label>
              <Input
                id={`budget-policy-name-${policy.id}`}
                maxLength={160}
                onChange={(event) => setName(event.currentTarget.value)}
                value={name}
              />
            </div>
            <div className="space-y-2">
              <Label>额度币种</Label>
              <Select
                onValueChange={(value) => setCurrency(value as "USD" | "CNY")}
                value={currency}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="CNY">CNY（人民币）</SelectItem>
                  <SelectItem value="USD">USD（美元，按当前汇率折算）</SelectItem>
                </SelectContent>
              </Select>
              {currency === "USD" ? (
                <p className="text-xs text-muted-foreground">
                  美元额度在调用前按当前 USD/CNY 汇率折算成人民币门槛；汇率变化后门槛随之浮动。
                </p>
              ) : null}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor={`budget-policy-soft-${policy.id}`}>
                  软告警 {currency === "CNY" ? "CNY ¥" : "USD $"}
                </Label>
                <Input
                  id={`budget-policy-soft-${policy.id}`}
                  min="0"
                  onChange={(event) => setSoftLimit(event.currentTarget.value)}
                  type="number"
                  value={softLimit}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`budget-policy-hard-${policy.id}`}>
                  硬停用 {currency === "CNY" ? "CNY ¥" : "USD $"}
                </Label>
                <Input
                  id={`budget-policy-hard-${policy.id}`}
                  min="0"
                  onChange={(event) => setHardLimit(event.currentTarget.value)}
                  type="number"
                  value={hardLimit}
                />
              </div>
            </div>
            <div className="flex items-center justify-between rounded-xl border p-3">
              <div>
                <Label htmlFor={`budget-policy-enabled-${policy.id}`}>启用策略</Label>
                <p className="mt-1 text-xs text-muted-foreground">关闭后保留历史策略与告警，但不再参与后续预算匹配。</p>
              </div>
              <Switch
                checked={enabled}
                id={`budget-policy-enabled-${policy.id}`}
                onCheckedChange={setEnabled}
              />
            </div>
            {!limitsValid ? <p className="text-xs text-destructive">至少填写一个非负门槛，且软告警不能高于硬停用金额。</p> : null}
          </div>
          <DialogFooter>
            <Button disabled={busy || !name.trim() || !limitsValid} type="submit">
              {busy ? "保存中…" : "保存策略"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteBudgetPolicyButton({
  busy,
  onDelete,
  policy,
}: {
  busy: boolean;
  onDelete: () => void;
  policy: BudgetPolicy;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button aria-label={`删除额度策略 ${policy.name}`} disabled={busy} size="icon-xs" variant="ghost">
          <Trash2 className="size-3.5 text-destructive" />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogMedia className="bg-destructive/10 text-destructive"><Trash2 /></AlertDialogMedia>
          <AlertDialogTitle>删除“{policy.name}”？</AlertDialogTitle>
          <AlertDialogDescription>
            该策略及其预算告警将被删除，之后新的调用不会再匹配这组范围。用量事件不会被删除。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction disabled={busy} onClick={onDelete} variant="destructive">确认删除策略</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function AlertEmailForm({ config }: { config: AlertEmailConfig }) {
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = useState(config.enabled);
  const [host, setHost] = useState(config.smtp_host);
  const [port, setPort] = useState(String(config.smtp_port));
  const [security, setSecurity] = useState<AlertEmailConfig["smtp_security"]>(
    config.smtp_security,
  );
  const [username, setUsername] = useState(config.smtp_username);
  const [password, setPassword] = useState("");
  const [fromAddress, setFromAddress] = useState(config.from_address);
  const [toAddresses, setToAddresses] = useState(config.to_addresses.join(", "));

  const save = useMutation({
    mutationFn: updateAlertEmailConfig,
    onSuccess: () => {
      toast.success("邮件通知配置已保存");
      setPassword("");
      void queryClient.invalidateQueries({ queryKey: ["usage-alert-email"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const test = useMutation({
    mutationFn: sendTestAlertEmail,
    onSuccess: (result) => {
      if (result.ok) toast.success(result.detail);
      else toast.error(result.detail);
    },
    onError: (error) => toast.error(error.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate({
      enabled,
      smtp_host: host.trim(),
      smtp_port: Number(port) || 465,
      smtp_security: security,
      smtp_username: username.trim(),
      smtp_password: password === "" ? null : password,
      from_address: fromAddress.trim(),
      to_addresses: toAddresses
        .split(/[,，;；\s]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    });
  }

  return (
    <form className="space-y-3" onSubmit={submit}>
      <div className="flex items-center justify-between rounded-xl border p-3">
        <div>
          <Label htmlFor="alert-email-enabled">启用邮件告警</Label>
          <p className="mt-1 text-xs text-muted-foreground">
            触发软告警或硬停用时自动发送邮件到配置的收件人。
          </p>
        </div>
        <Switch
          checked={enabled}
          id="alert-email-enabled"
          onCheckedChange={setEnabled}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-[1fr_7rem]">
        <div className="space-y-1.5">
          <Label htmlFor="alert-email-host">SMTP 服务器</Label>
          <Input
            id="alert-email-host"
            onChange={(event) => setHost(event.currentTarget.value)}
            placeholder="smtp.example.com"
            value={host}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="alert-email-port">端口</Label>
          <Input
            id="alert-email-port"
            min="1"
            max="65535"
            onChange={(event) => setPort(event.currentTarget.value)}
            type="number"
            value={port}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label>加密方式</Label>
        <Select
          onValueChange={(value) =>
            setSecurity(value as AlertEmailConfig["smtp_security"])
          }
          value={security}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ssl">SSL（通常 465 端口）</SelectItem>
            <SelectItem value="starttls">STARTTLS（通常 587 端口）</SelectItem>
            <SelectItem value="none">不加密（不推荐）</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="alert-email-username">SMTP 用户名</Label>
          <Input
            autoComplete="off"
            id="alert-email-username"
            onChange={(event) => setUsername(event.currentTarget.value)}
            value={username}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="alert-email-password">SMTP 密码 / 授权码</Label>
          <Input
            autoComplete="new-password"
            id="alert-email-password"
            onChange={(event) => setPassword(event.currentTarget.value)}
            placeholder={config.has_password ? "已保存（留空保持不变）" : ""}
            type="password"
            value={password}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="alert-email-from">发件人地址（留空使用用户名）</Label>
        <Input
          id="alert-email-from"
          onChange={(event) => setFromAddress(event.currentTarget.value)}
          placeholder="alerts@example.com"
          value={fromAddress}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="alert-email-to">收件人（逗号分隔，可多个）</Label>
        <Input
          id="alert-email-to"
          onChange={(event) => setToAddresses(event.currentTarget.value)}
          placeholder="you@example.com, ops@example.com"
          value={toAddresses}
        />
      </div>
      <div className="flex items-center gap-2 pt-1">
        <Button disabled={save.isPending} size="sm" type="submit">
          {save.isPending ? "保存中…" : "保存配置"}
        </Button>
        <Button
          disabled={test.isPending}
          onClick={() => test.mutate()}
          size="sm"
          type="button"
          variant="outline"
        >
          <Mail className="size-4" />
          {test.isPending ? "发送中…" : "发送测试邮件"}
        </Button>
      </div>
    </form>
  );
}

function AlertEmailCard() {
  const config = useQuery({
    queryKey: ["usage-alert-email"],
    queryFn: getAlertEmailConfig,
  });
  return (
    <Surface className="p-5">
      <SectionHeading
        description="配置 SMTP 服务器后，额度告警会自动发送邮件；密码加密存储，仅用于服务端发信。"
        title="邮件通知"
      />
      <div className="mt-4">
        {config.data ? (
          <AlertEmailForm config={config.data} />
        ) : config.isError ? (
          <p className="text-xs text-destructive">{config.error.message}</p>
        ) : (
          <p className="text-xs text-muted-foreground">正在读取配置…</p>
        )}
      </div>
    </Surface>
  );
}

/* ------------------------------------------------------------------ */
/* 用量事件表（分页 + 滚动）                                            */
/* ------------------------------------------------------------------ */

function UsageEventsTable({
  displayCurrency,
  events,
}: {
  displayCurrency: "USD" | "CNY";
  events: UsageEvent[];
}) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(20);
  const pageCount = Math.max(1, Math.ceil(events.length / pageSize));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageEvents = events.slice(
    clampedPage * pageSize,
    clampedPage * pageSize + pageSize,
  );
  return (
    <Surface className="overflow-hidden">
      <div className="border-b p-5">
        <SectionHeading
          description="每条记录固化调用时的计费快照"
          title="用量事件"
        />
      </div>
      <div className="max-h-[36rem] overflow-auto">
        <table className="w-full min-w-[780px] text-left text-xs">
          <thead className="sticky top-0 z-10 bg-muted text-muted-foreground">
            <tr>
              <th className="px-5 py-3">时间</th>
              <th className="px-5 py-3">Provider / Model</th>
              <th className="px-5 py-3">功能</th>
              <th className="px-5 py-3">Token</th>
              <th className="px-5 py-3">Attempt</th>
              <th className="px-5 py-3">延迟</th>
              <th className="px-5 py-3">费用</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {pageEvents.map((event) => (
              <tr key={event.id}>
                <td className="px-5 py-3 font-mono">
                  {formatTimestamp(event.created_at)}
                </td>
                <td className="px-5 py-3">
                  <p className="font-mono" title={event.provider_id}>
                    {shortId(event.provider_id)}
                  </p>
                  <p className="mt-0.5 break-all text-muted-foreground">
                    {event.model_id}
                  </p>
                </td>
                <td className="px-5 py-3">{event.feature}</td>
                <td className="px-5 py-3 font-mono tabular-nums">
                  <p>
                    {(event.total_tokens ?? event.input_tokens + event.output_tokens).toLocaleString()}
                  </p>
                  <p className="mt-0.5 text-[10px] text-muted-foreground">
                    输入 {event.input_tokens.toLocaleString()} · 缓存读{" "}
                    {(event.cached_input_tokens ?? 0).toLocaleString()} · 缓存写{" "}
                    {(event.cache_creation_input_tokens ?? 0).toLocaleString()} · 输出{" "}
                    {event.output_tokens.toLocaleString()}
                  </p>
                </td>
                <td className="px-5 py-3 tabular-nums">{event.attempt}</td>
                <td className="px-5 py-3 font-mono tabular-nums">
                  {event.latency_ms ? `${event.latency_ms}ms` : "—"}
                </td>
                <td className="px-5 py-3 font-mono tabular-nums">
                  <p>
                    {formatMoney(
                      displayCurrency === "CNY" ? event.cost_cny : event.cost_usd,
                      displayCurrency,
                    )}
                  </p>
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    {formatMoney(
                      displayCurrency === "CNY" ? event.cost_usd : event.cost_cny,
                      displayCurrency === "CNY" ? "USD" : "CNY",
                    )}{" "}
                    · {event.cost_status}
                  </p>
                </td>
              </tr>
            ))}
            {!pageEvents.length ? (
              <tr>
                <td className="px-5 py-10 text-center text-muted-foreground" colSpan={7}>
                  暂无真实用量事件
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-3 border-t p-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>每页</span>
          <Select
            onValueChange={(value) => {
              setPageSize(Number(value));
              setPage(0);
            }}
            value={String(pageSize)}
          >
            <SelectTrigger aria-label="每页条数" className="h-8 w-20 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="20">20</SelectItem>
              <SelectItem value="50">50</SelectItem>
              <SelectItem value="100">100</SelectItem>
            </SelectContent>
          </Select>
          <span>条 · 共 {events.length.toLocaleString()} 条</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button
            aria-label="上一页"
            disabled={clampedPage === 0}
            onClick={() => setPage(clampedPage - 1)}
            size="icon-xs"
            variant="outline"
          >
            <ChevronLeft className="size-3.5" />
          </Button>
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {clampedPage + 1} / {pageCount}
          </span>
          <Button
            aria-label="下一页"
            disabled={clampedPage >= pageCount - 1}
            onClick={() => setPage(clampedPage + 1)}
            size="icon-xs"
            variant="outline"
          >
            <ChevronRight className="size-3.5" />
          </Button>
        </div>
      </div>
    </Surface>
  );
}

/* ------------------------------------------------------------------ */
/* 页面                                                                */
/* ------------------------------------------------------------------ */

export function UsagePage() {
  const queryClient = useQueryClient();
  const usageDisplaySetting = useQuery({
    queryKey: ["settings"],
    queryFn: listSettings,
  });
  const summary = useQuery({
    queryKey: ["usage-summary"],
    queryFn: getUsageSummary,
  });
  const events = useQuery({
    queryKey: ["usage-events"],
    queryFn: listUsageEvents,
  });
  const priceCatalog = useQuery({
    queryKey: ["usage-price-catalog"],
    queryFn: listPriceCatalog,
  });
  const manualPrices = useQuery({
    queryKey: ["usage-manual-prices"],
    queryFn: listManualPrices,
  });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
  });
  const policies = useQuery({
    queryKey: ["usage-budget-policies"],
    queryFn: listBudgetPolicies,
  });
  const budgetStatuses = useQuery({
    queryKey: ["usage-budget-status"],
    queryFn: listBudgetStatuses,
  });
  const alerts = useQuery({
    queryKey: ["usage-budget-alerts"],
    queryFn: listBudgetAlerts,
  });
  const refreshBudgets = () => {
    void queryClient.invalidateQueries({ queryKey: ["usage-budget-policies"] });
    void queryClient.invalidateQueries({ queryKey: ["usage-budget-status"] });
    void queryClient.invalidateQueries({ queryKey: ["usage-budget-alerts"] });
  };
  const createBudget = useMutation({
    mutationFn: createBudgetPolicy,
    onSuccess: () => {
      toast.success("额度策略已创建");
      refreshBudgets();
    },
    onError: (error) => toast.error(error.message),
  });
  const updateBudget = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: BudgetPolicyUpdate;
    }) => updateBudgetPolicy(id, payload),
    onSuccess: () => {
      toast.success("额度策略已更新");
      refreshBudgets();
    },
    onError: (error) => toast.error(error.message),
  });
  const removeBudget = useMutation({
    mutationFn: deleteBudgetPolicy,
    onSuccess: () => {
      toast.success("额度策略及其告警已删除");
      refreshBudgets();
    },
    onError: (error) => toast.error(error.message),
  });
  const acknowledge = useMutation({
    mutationFn: acknowledgeBudgetAlert,
    onSuccess: () => {
      toast.success("预算告警已确认");
      refreshBudgets();
    },
    onError: (error) => toast.error(error.message),
  });
  const clearEvents = useMutation({
    mutationFn: clearUsageEvents,
    onSuccess: (result) => {
      toast.success(
        result.deleted_count
          ? `已清空 ${result.deleted_count} 条用量事件`
          : "当前没有可清空的用量事件",
      );
      void queryClient.invalidateQueries({ queryKey: ["usage-summary"] });
      void queryClient.invalidateQueries({ queryKey: ["usage-events"] });
      void queryClient.invalidateQueries({ queryKey: ["usage-budget-status"] });
      void queryClient.invalidateQueries({ queryKey: ["usage-budget-alerts"] });
    },
    onError: (error) => toast.error(error.message),
  });
  const updateUsageDisplayCurrency = useMutation({
    mutationFn: (currency: "USD" | "CNY") =>
      updateSetting("usage.display_currency", currency),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const [manualPriceDraft, setManualPriceDraft] =
    useState<ManualPriceDraft | null>(null);
  const [manualPriceOpen, setManualPriceOpen] = useState(false);

  const allQueries = [
    summary,
    events,
    priceCatalog,
    manualPrices,
    providers,
    policies,
    budgetStatuses,
    alerts,
    usageDisplaySetting,
  ];
  if (allQueries.some((query) => query.isPending))
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  const queryError = allQueries.find((query) => query.isError)?.error;
  if (queryError)
    return (
      <PageFrame>
        <ErrorState message={queryError.message} />
      </PageFrame>
    );
  const usageEvents = events.data ?? [];
  const configuredDisplayCurrency = usageDisplaySetting.data?.find(
    (setting) => setting.key === "usage.display_currency",
  )?.value;
  const displayCurrency: "USD" | "CNY" =
    configuredDisplayCurrency === "USD" ? "USD" : "CNY";
  const usageSummary = summary.data!;
  const catalogItems = priceCatalog.data ?? [];
  const manualPriceItems = manualPrices.data ?? [];
  const providerItems = providers.data ?? [];
  const policyItems = policies.data ?? [];
  const statusItems = budgetStatuses.data ?? [];
  const alertItems = alerts.data ?? [];
  const statusByPolicy = new Map<string, BudgetStatus>(
    statusItems.map((status) => [status.policy_id, status]),
  );
  const openAlertCount = alertItems.filter(
    (alert) => alert.status !== "acknowledged",
  ).length;
  const eventModels = [...new Set(usageEvents.map((event) => event.model_id))].sort();
  const manualModels = new Set(manualPriceItems.map((item) => item.model_id));

  function openManualPriceEditor(draft: ManualPriceDraft) {
    setManualPriceDraft(draft);
    setManualPriceOpen(true);
  }

  function exportCsv() {
    const escape = (value: string | number) =>
      `"${String(value).replaceAll('"', '""')}"`;
    const rows = [
      [
        "created_at",
        "provider_id",
        "model_id",
        "feature",
        "input_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
        "attempt",
        "cost_usd",
        "cost_cny",
        "cost_status",
        "usd_cny_rate",
        "latency_ms",
      ],
      ...usageEvents.map((event) => [
        event.created_at,
        event.provider_id,
        event.model_id,
        event.feature,
        event.input_tokens,
        event.cached_input_tokens ?? 0,
        event.cache_creation_input_tokens ?? 0,
        event.output_tokens,
        event.attempt,
        event.cost_usd,
        event.cost_cny,
        event.cost_status,
        event.usd_cny_rate,
        event.latency_ms,
      ]),
    ];
    const blob = new Blob(
      [`\uFEFF${rows.map((row) => row.map(escape).join(",")).join("\n")}`],
      { type: "text/csv;charset=utf-8" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `learngraph-usage-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <PageFrame>
      <PageIntro
        actions={
          <div className="flex flex-wrap gap-2">
            <Select
              disabled={updateUsageDisplayCurrency.isPending}
              onValueChange={(value) =>
                updateUsageDisplayCurrency.mutate(value as "USD" | "CNY")
              }
              value={displayCurrency}
            >
              <SelectTrigger aria-label="费用显示币种" className="h-9 w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="CNY">显示 CNY</SelectItem>
                <SelectItem value="USD">显示 USD</SelectItem>
              </SelectContent>
            </Select>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  disabled={!usageEvents.length || clearEvents.isPending}
                  size="sm"
                  variant="outline"
                >
                  <Trash2 className="size-4" />
                  清空用量
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogMedia className="bg-destructive/10 text-destructive">
                    <Trash2 />
                  </AlertDialogMedia>
                  <AlertDialogTitle>清空当前工作区的用量计费记录？</AlertDialogTitle>
                  <AlertDialogDescription>
                    将永久删除当前工作区全部 {usageEvents.length} 条 UsageEvent。价格配置与额度策略会保留；此操作不可撤销。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                {clearEvents.isError ? (
                  <p className="text-sm text-destructive" role="alert">
                    {clearEvents.error.message}
                  </p>
                ) : null}
                <AlertDialogFooter>
                  <AlertDialogCancel disabled={clearEvents.isPending}>取消</AlertDialogCancel>
                  <AlertDialogAction
                    disabled={clearEvents.isPending}
                    onClick={(event) => {
                      event.preventDefault();
                      clearEvents.mutate();
                    }}
                    variant="destructive"
                  >
                    {clearEvents.isPending ? "正在清空…" : "确认清空"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <Button
              disabled={!usageEvents.length}
              onClick={exportCsv}
              size="sm"
              variant="outline"
            >
              <Download className="size-4" />
              导出 CSV
            </Button>
          </div>
        }
        description="每次实际 HTTP Attempt 追加一条用量事件，失败重试也单独计入；缓存读写 Token 均包含在输入 Token 总量中，不重复累加；人民币计费模型按人民币计价，美元计费模型按美元计价，汇总按当前汇率折算，历史账本保留调用时快照。"
        eyebrow="Usage ledger"
        title="用量计费与预算"
      />
      <MetricStrip
        items={[
          {
            label: "输入 Token",
            value: usageSummary.input_tokens.toLocaleString(),
            hint: `缓存读 ${(usageSummary.cached_input_tokens ?? 0).toLocaleString()} · 缓存写 ${(usageSummary.cache_creation_input_tokens ?? 0).toLocaleString()}`,
          },
          {
            label: "输出 Token",
            value: usageSummary.output_tokens.toLocaleString(),
            hint: "当前工作区",
            tone: "info",
          },
          {
            label: "实际尝试",
            value: usageSummary.attempts,
            hint: `${usageSummary.unpriced_events} 条未定价`,
            tone: "warning",
          },
          {
            label: `费用 ${displayCurrency}`,
            value:
              displayCurrency === "CNY"
                ? `¥${usageSummary.cost_cny.toFixed(4)}`
                : `$${usageSummary.cost_usd.toFixed(4)}`,
            hint: usageSummary.remote_usage_recorded
              ? displayCurrency === "CNY"
                ? `$${usageSummary.cost_usd.toFixed(4)} · 远程用量`
                : `¥${usageSummary.cost_cny.toFixed(4)} · 远程用量`
              : "暂无远程用量",
            tone: "positive",
          },
        ]}
      />
      <Tabs className="gap-5" defaultValue="overview">
        <TabsList className="no-scrollbar w-full justify-start overflow-x-auto sm:w-fit">
          <TabsTrigger className="px-3" value="overview">
            概览
          </TabsTrigger>
          <TabsTrigger className="px-3" value="budgets">
            预算与告警
            {openAlertCount ? (
              <span className="rounded-full bg-amber-500/15 px-1.5 font-mono text-[10px] font-semibold text-amber-600 dark:text-amber-400">
                {openAlertCount}
              </span>
            ) : null}
          </TabsTrigger>
          <TabsTrigger className="px-3" value="catalog">
            价格目录
          </TabsTrigger>
          <TabsTrigger className="px-3" value="events">
            用量事件
            <span className="font-mono text-[10px] text-muted-foreground">
              {usageEvents.length}
            </span>
          </TabsTrigger>
        </TabsList>

        <TabsContent className="flex flex-col gap-5" value="overview">
          <OverviewSection
            displayCurrency={displayCurrency}
            events={usageEvents}
            providers={providerItems}
          />
        </TabsContent>

        <TabsContent className="flex flex-col gap-5" value="budgets">
          <div className="grid gap-5 lg:grid-cols-2">
            <Surface className="p-5">
              <div className="flex items-start justify-between gap-3">
                <SectionHeading
                  description="硬停用金额在调用前阻断，软告警金额只提醒不停用；支持总额度、供应商额度和模型额度。"
                  title={`额度策略 · ${policyItems.length}`}
                />
                <CreateBudgetPolicyDialog
                  busy={createBudget.isPending}
                  models={eventModels}
                  onCreate={(payload) =>
                    createBudget.mutateAsync(payload).then(() => undefined)
                  }
                  providers={providerItems}
                />
              </div>
              <div className="mt-4 space-y-3">
                {policyItems.map((policy) => {
                  const status = statusByPolicy.get(policy.id);
                  const currency = policy.limit_currency ?? "CNY";
                  const nativeSymbol = currency === "CNY" ? "¥" : "$";
                  const effectiveLimit =
                    status?.hard_limit_cny_effective ??
                    status?.soft_limit_cny_effective ??
                    null;
                  const nativeLimit =
                    policy.hard_limit_cny ?? policy.soft_limit_cny ?? null;
                  // Percent uses the effective CNY limit vs. CNY spend, so USD
                  // limits float with the current exchange rate.
                  const percent =
                    status && effectiveLimit
                      ? Math.min(100, Math.round((status.spent_cny / effectiveLimit) * 100))
                      : null;
                  const limitLabel = (value: number | null, ccy: "USD" | "CNY") =>
                    value === null
                      ? "未设置"
                      : `${ccy === "CNY" ? "¥" : "$"}${value.toFixed(2)}`;
                  return (
                    <div className="rounded-xl border p-4" key={policy.id}>
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                        <div className="min-w-0 flex-1 text-xs">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="font-medium">{policy.name}</p>
                            <StatePill
                              label={policy.hard_limit_cny !== null ? "自动停用" : "仅告警"}
                              status={policy.hard_limit_cny !== null ? "warning" : "approved"}
                            />
                            <StatePill
                              label={currency === "USD" ? "USD 额度" : "CNY 额度"}
                              status={currency === "USD" ? "local_mock" : "approved"}
                            />
                            <StatePill label={policy.enabled ? "已启用" : "已停用"} status={policy.enabled ? "approved" : "archived"} />
                            {status?.hard_exceeded ? (
                              <StatePill label="已阻断" status="failed" />
                            ) : status?.soft_exceeded ? (
                              <StatePill label="已预警" status="warning" />
                            ) : null}
                          </div>
                          <p className="mt-1 break-all font-mono text-muted-foreground">
                            <span title={policy.provider_id}>{shortId(policy.provider_id)}</span> / {policy.model_id} / {policy.feature} · {policy.period === "calendar_day_utc" ? "每日" : "每月"}
                          </p>
                          <p className="mt-1 text-muted-foreground">
                            软告警 {limitLabel(policy.soft_limit_cny, currency)} · 硬停用 {limitLabel(policy.hard_limit_cny, currency)}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <EditBudgetPolicyDialog
                            busy={updateBudget.isPending}
                            onUpdate={(payload) =>
                              updateBudget
                                .mutateAsync({ id: policy.id, payload })
                                .then(() => undefined)
                            }
                            policy={policy}
                          />
                          <DeleteBudgetPolicyButton
                            busy={removeBudget.isPending}
                            onDelete={() => removeBudget.mutate(policy.id)}
                            policy={policy}
                          />
                        </div>
                      </div>
                      {status && percent !== null ? (
                        <div className="mt-3 flex items-center gap-2">
                          <Progress value={percent} />
                          <span className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground">
                            ¥{status.spent_cny.toFixed(4)} / ¥{Number(effectiveLimit).toFixed(2)}
                            {currency === "USD" && nativeLimit !== null
                              ? `（${nativeSymbol}${nativeLimit.toFixed(2)} 按汇率）`
                              : ""}{" "}
                            · {percent}%
                          </span>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
                {!policyItems.length ? (
                  <p className="rounded-xl border border-dashed py-8 text-center text-sm text-muted-foreground">
                    尚未配置额度策略；真实用量仍会记录，但不会执行预算阻断。
                  </p>
                ) : null}
              </div>
            </Surface>
            <div className="flex flex-col gap-5">
              <Surface className="p-5">
                <SectionHeading
                  description={`${openAlertCount} 条待确认`}
                  title={`预算告警 · ${alertItems.length}`}
                />
                <div className="mt-4 space-y-3">
                  {alertItems.slice(0, 8).map((alert) => (
                    <div
                      className="flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center"
                      key={alert.id}
                    >
                      <AlertTriangle className="size-4 shrink-0 text-amber-500" />
                      <div className="min-w-0 flex-1 text-xs">
                        <p className="font-medium">
                          {alert.level === "hard" ? "硬停用" : "软告警"} · {alert.feature || "全部功能"}
                        </p>
                        <p className="mt-1 font-mono tabular-nums text-muted-foreground">
                          ¥{alert.projected_cost_cny.toFixed(4)} / ¥
                          {alert.limit_cny.toFixed(4)} · {formatTimestamp(alert.created_at)}
                        </p>
                      </div>
                      {alert.status === "acknowledged" ? (
                        <StatePill label="已确认" status="approved" />
                      ) : (
                        <Button
                          disabled={acknowledge.isPending}
                          onClick={() => acknowledge.mutate(alert.id)}
                          size="xs"
                          variant="outline"
                        >
                          确认告警
                        </Button>
                      )}
                    </div>
                  ))}
                  {!alertItems.length ? (
                    <p className="rounded-xl border border-dashed py-8 text-center text-sm text-muted-foreground">
                      当前没有预算告警
                    </p>
                  ) : null}
                </div>
              </Surface>
              <AlertEmailCard />
            </div>
          </div>
        </TabsContent>

        <TabsContent className="flex flex-col gap-5" value="catalog">
          <ExchangeRateCard displayCurrency={displayCurrency} />
          <ManualPricesCard
            manualPrices={manualPriceItems}
            onEdit={openManualPriceEditor}
          />
          <ModelsDevCard />
          <PriceCatalogTable
            items={catalogItems}
            manualModels={manualModels}
            onEdit={openManualPriceEditor}
          />
        </TabsContent>

        <TabsContent className="flex flex-col gap-5" value="events">
          <UsageEventsTable displayCurrency={displayCurrency} events={usageEvents} />
        </TabsContent>
      </Tabs>
      <ManualPriceDialog
        draft={manualPriceDraft}
        onOpenChange={setManualPriceOpen}
        open={manualPriceOpen}
      />
    </PageFrame>
  );
}
