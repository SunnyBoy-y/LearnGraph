import { useId, useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { ChevronRight, Download, Info, X } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { UsageFilters } from "@/api/usage";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useReducedMotionPreference } from "@/lib/motion-preferences";
import { cn } from "@/lib/utils";
import type { Provider } from "@/types/providers";
import type { BillingCurrency, UsageSummary } from "@/types/usage";
import {
  USAGE_CARD_CLASS,
  aggregateTrendSeries,
  compareDecimalStrings,
  currencySymbol,
  decimalToNumber,
  formatCompactCount,
  formatCompactTokens,
  formatDisplayMoney,
  formatExactMoney,
  formatFeatureLabel,
  resolveProviderInfo,
} from "./usage-formatting";
import {
  AdvancedFilterPopover,
  CurrencySelect,
  DateRangeControl,
  QuerySection,
} from "./usage-shared";

type BreakdownDimension = "model_id" | "provider_id" | "feature";
type BreakdownSort = "cost" | "attempts" | "tokens";
type TrendGranularity = "day" | "week" | "month";

export function UsageOverviewPanel({
  currency,
  onCurrencyChange,
  filters,
  onUpdateFilter,
  onUpdateDateRange,
  onResetAdvancedFilters,
  providers,
  historicalProviderNames,
  knownModels,
  knownFeatures,
  summaryQuery,
  onExportOverview,
  onOpenAnomalies,
  onDrillDownFilter,
}: {
  currency: BillingCurrency;
  onCurrencyChange: (next: BillingCurrency) => void;
  filters: UsageFilters;
  onUpdateFilter: (name: "provider_id" | "model_id" | "feature", value: string) => void;
  onUpdateDateRange: (range: { start_at?: string; end_at?: string }) => void;
  onResetAdvancedFilters: () => void;
  providers: readonly Provider[];
  historicalProviderNames: Readonly<Record<string, string>>;
  knownModels: readonly string[];
  knownFeatures: readonly string[];
  summaryQuery: UseQueryResult<UsageSummary, Error>;
  onExportOverview: (data: UsageSummary) => void;
  onOpenAnomalies: (mode: "unpriced" | "unknown_currency") => void;
  onDrillDownFilter: (name: "provider_id" | "model_id" | "feature", value: string) => void;
}) {
  const activeProvidersMap = useMemo(
    () => new Map(providers.map((p) => [p.id, p])),
    [providers],
  );
  const summaryData = summaryQuery.data;

  const mergedHistoricalNames = useMemo(
    () => ({ ...historicalProviderNames, ...(summaryData?.provider_names ?? {}) }),
    [historicalProviderNames, summaryData?.provider_names],
  );

  const activeAdvancedChips = useMemo(() => {
    const chips: Array<{
      key: "provider_id" | "model_id" | "feature";
      label: string;
    }> = [];
    if (filters.provider_id) {
      const resolved = resolveProviderInfo(
        filters.provider_id,
        activeProvidersMap,
        mergedHistoricalNames,
      );
      chips.push({ key: "provider_id", label: `连接：${resolved.name}` });
    }
    if (filters.model_id) {
      chips.push({ key: "model_id", label: `模型：${filters.model_id}` });
    }
    if (filters.feature) {
      chips.push({
        key: "feature",
        label: `功能：${formatFeatureLabel(filters.feature)}`,
      });
    }
    return chips;
  }, [activeProvidersMap, filters.feature, filters.model_id, filters.provider_id, mergedHistoricalNames]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <DateRangeControl
            endAt={filters.end_at}
            onChange={onUpdateDateRange}
            startAt={filters.start_at}
          />
          <AdvancedFilterPopover
            filters={filters}
            historicalProviderNames={mergedHistoricalNames}
            knownFeatures={knownFeatures}
            knownModels={knownModels}
            onChangeFilter={onUpdateFilter}
            onResetFilters={onResetAdvancedFilters}
            providers={providers}
          />
        </div>
        <div className="flex items-center gap-2">
          <CurrencySelect currency={currency} onChange={onCurrencyChange} />
          <Button
            aria-label="导出概览"
            className="size-8"
            disabled={!summaryData}
            onClick={() => {
              if (summaryData) onExportOverview(summaryData);
            }}
            size="icon-sm"
            title="导出概览 CSV"
            type="button"
            variant="outline"
          >
            <Download className="size-4" />
          </Button>
        </div>
      </div>

      {activeAdvancedChips.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {activeAdvancedChips.map((chip) => (
            <span
              className="inline-flex items-center gap-1 rounded-full border bg-muted/40 px-2.5 py-0.5 text-foreground"
              key={chip.key}
            >
              <span>{chip.label}</span>
              <button
                aria-label={`移除${chip.label}筛选`}
                className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => onUpdateFilter(chip.key, "")}
                type="button"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
          <button
            className="px-1.5 py-0.5 text-xs text-muted-foreground hover:text-foreground"
            onClick={onResetAdvancedFilters}
            type="button"
          >
            清除筛选
          </button>
        </div>
      ) : null}

      <QuerySection loadingLabel="正在加载用量概览…" query={summaryQuery}>
        {(data) => (
          <OverviewBody
            activeProvidersMap={activeProvidersMap}
            data={data}
            historicalNames={mergedHistoricalNames}
            onDrillDownFilter={onDrillDownFilter}
            onOpenAnomalies={onOpenAnomalies}
          />
        )}
      </QuerySection>
    </div>
  );
}

function OverviewBody({
  data,
  activeProvidersMap,
  historicalNames,
  onOpenAnomalies,
  onDrillDownFilter,
}: {
  data: UsageSummary;
  activeProvidersMap: ReadonlyMap<string, Provider>;
  historicalNames: Readonly<Record<string, string>>;
  onOpenAnomalies: (mode: "unpriced" | "unknown_currency") => void;
  onDrillDownFilter: (name: "provider_id" | "model_id" | "feature", value: string) => void;
}) {
  const hasAnomalies = data.unpriced_events > 0 || data.unknown_currency_events > 0;
  const allCostUnknown =
    data.attempts > 0 &&
    data.unpriced_events >= data.attempts &&
    compareDecimalStrings(data.cost_amount, "0") === 0;

  return (
    <div className="space-y-4">
      <section aria-label="核心指标" className={cn(USAGE_CARD_CLASS, "overflow-hidden")}>
        <div className="grid gap-x-8 gap-y-5 p-5 sm:grid-cols-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <span>已记录费用</span>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    aria-label="已记录费用口径说明"
                    className="inline-flex size-4 items-center justify-center rounded-full text-muted-foreground hover:text-foreground"
                    type="button"
                  >
                    <Info className="size-3.5" />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs leading-relaxed">
                  统计所选币种下已结算记录的已知费用之和（含归档记录）；未知费用记录不计入此金额，不能视为完整账单。供应商账户余额请至「服务连接」查看。
                </TooltipContent>
              </Tooltip>
            </div>
            <Tooltip>
              <TooltipTrigger asChild>
                <p className="mt-1.5 w-fit max-w-full truncate text-2xl font-semibold tracking-tight tabular-nums sm:text-[28px]">
                  {formatDisplayMoney(data.cost_amount, data.currency, {
                    allUnknown: allCostUnknown,
                    compact: true,
                  })}
                </p>
              </TooltipTrigger>
              <TooltipContent>
                {allCostUnknown
                  ? "所选范围内记录均待核实费用，不显示为零费用"
                  : formatExactMoney(data.cost_amount, data.currency)}
              </TooltipContent>
            </Tooltip>
            <p className="mt-1 text-xs text-muted-foreground">
              {hasAnomalies ? "不含未知费用" : "已完成全部费用核算"}
            </p>
          </div>

          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground">预算占用</p>
            <Tooltip>
              <TooltipTrigger asChild>
                <p className="mt-1.5 w-fit max-w-full truncate text-2xl font-semibold tracking-tight tabular-nums sm:text-[28px]">
                  {formatDisplayMoney(data.reserved_amount, data.currency, { compact: true })}
                </p>
              </TooltipTrigger>
              <TooltipContent>
                {formatExactMoney(data.reserved_amount, data.currency)}
              </TooltipContent>
            </Tooltip>
            <p className="mt-1 text-xs text-muted-foreground">含未核实及执行中调用的占用</p>
          </div>

          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground">模型调用</p>
            <Tooltip>
              <TooltipTrigger asChild>
                <p className="mt-1 w-fit text-lg font-semibold tracking-tight tabular-nums sm:text-xl">
                  {formatCompactCount(data.attempts)}
                  <span className="ml-1 text-xs font-normal text-muted-foreground">次</span>
                </p>
              </TooltipTrigger>
              <TooltipContent>{data.attempts.toLocaleString("en-US")} 次已结算调用</TooltipContent>
            </Tooltip>
          </div>

          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground">Token 消耗</p>
            <Tooltip>
              <TooltipTrigger asChild>
                <p className="mt-1 w-fit text-lg font-semibold tracking-tight tabular-nums sm:text-xl">
                  {formatCompactTokens(data.total_tokens)}
                </p>
              </TooltipTrigger>
              <TooltipContent className="space-y-0.5 text-left">
                <p className="font-medium">{data.total_tokens.toLocaleString("en-US")} Tokens</p>
                <p className="opacity-85">
                  输入 {data.input_tokens.toLocaleString("en-US")} · 输出{" "}
                  {data.output_tokens.toLocaleString("en-US")}
                </p>
                {data.cached_input_tokens > 0 ||
                data.cache_creation_input_tokens > 0 ||
                data.reasoning_tokens > 0 ? (
                  <p className="opacity-85">
                    缓存读 {data.cached_input_tokens.toLocaleString("en-US")} · 缓存写{" "}
                    {data.cache_creation_input_tokens.toLocaleString("en-US")} · 推理{" "}
                    {data.reasoning_tokens.toLocaleString("en-US")}
                  </p>
                ) : null}
              </TooltipContent>
            </Tooltip>
          </div>
        </div>

        {hasAnomalies ? (
          <div className="flex flex-wrap items-center justify-between gap-2 border-t bg-muted/20 px-5 py-2.5 text-xs">
            <div className="flex min-w-0 items-center gap-2 text-muted-foreground">
              <Info className="size-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
              <span>
                部分用量尚未完成费用核算
                <span className="ml-1.5 tabular-nums">
                  （
                  {[
                    data.unpriced_events > 0
                      ? `待核实记录 ${data.unpriced_events.toLocaleString("en-US")} 条`
                      : null,
                    data.unknown_currency_events > 0
                      ? `缺失币种 ${data.unknown_currency_events.toLocaleString("en-US")} 条`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  ）
                </span>
              </span>
            </div>
            <button
              className="inline-flex shrink-0 items-center gap-0.5 font-medium text-foreground transition-colors hover:text-primary"
              onClick={() =>
                onOpenAnomalies(data.unpriced_events > 0 ? "unpriced" : "unknown_currency")
              }
              type="button"
            >
              <span>查看异常记录</span>
              <ChevronRight className="size-3.5" />
            </button>
          </div>
        ) : null}
      </section>

      <ExpenseTrendCard data={data} />

      <ConsumptionDistributionCard
        activeProvidersMap={activeProvidersMap}
        data={data}
        historicalNames={historicalNames}
        onDrillDownFilter={onDrillDownFilter}
      />
    </div>
  );
}

function ExpenseTrendCard({ data }: { data: UsageSummary }) {
  const reducedMotion = useReducedMotionPreference();
  const gradientId = useId().replace(/:/g, "");
  const [granularity, setGranularity] = useState<TrendGranularity>("day");

  const series = useMemo(
    () => aggregateTrendSeries(data.breakdown.day ?? [], granularity),
    [data.breakdown.day, granularity],
  );
  const maxChartAmount = useMemo(
    () => series.reduce((max, item) => Math.max(max, item.chartAmount ?? 0), 0),
    [series],
  );
  const sym = currencySymbol(data.currency);

  return (
    <section aria-label="费用趋势" className={cn(USAGE_CARD_CLASS, "p-5")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">费用趋势</h3>
        <Select
          onValueChange={(value) => setGranularity(value as TrendGranularity)}
          value={granularity}
        >
          <SelectTrigger aria-label="趋势时间粒度" className="h-7 w-[88px] text-xs" size="sm">
            <SelectValue>
              {granularity === "day" ? "按日" : granularity === "week" ? "按周" : "按月"}
            </SelectValue>
          </SelectTrigger>
          <SelectContent align="end">
            <SelectItem value="day">按日</SelectItem>
            <SelectItem value="week">按周</SelectItem>
            <SelectItem value="month">按月</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {series.length > 0 ? (
        <div className="mt-4 h-56 w-full min-w-0">
          <ResponsiveContainer
            height="100%"
            initialDimension={{ width: 640, height: 224 }}
            minHeight={180}
            minWidth={0}
            width="100%"
          >
            <AreaChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.22} />
                  <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0.01} />
                </linearGradient>
              </defs>
              <CartesianGrid
                stroke="var(--border)"
                strokeDasharray="3 3"
                strokeOpacity={0.55}
                vertical={false}
              />
              <XAxis
                axisLine={false}
                dataKey="shortLabel"
                minTickGap={26}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                tickLine={false}
              />
              <YAxis
                axisLine={false}
                tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                tickFormatter={(val: number) => {
                  if (val === 0) return `${sym}0`;
                  if (maxChartAmount < 1) return `${sym}${val.toFixed(2)}`;
                  if (maxChartAmount < 10) return `${sym}${val.toFixed(1)}`;
                  return `${sym}${Math.round(val)}`;
                }}
                tickLine={false}
                width={50}
              />
              <RechartsTooltip
                filterNull={false}
                content={({ active, payload }) => {
                  const point = payload?.[0]?.payload as (typeof series)[number] | undefined;
                  if (!active || !point) return null;
                  const allPointUnknown =
                    point.unknownEvents >= point.attempts &&
                    compareDecimalStrings(point.costAmount, "0") === 0;
                  return (
                    <div className="rounded-lg border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
                      <p className="font-medium">{point.tooltipLabel}</p>
                      <p className="mt-1 font-semibold tabular-nums">
                        {formatDisplayMoney(point.costAmount, data.currency, {
                          allUnknown: allPointUnknown,
                          showCode: true,
                        })}
                      </p>
                      <p className="mt-0.5 text-muted-foreground tabular-nums">
                        {point.attempts.toLocaleString("en-US")} 次 ·{" "}
                        {formatCompactTokens(point.totalTokens)} Tokens
                      </p>
                      {point.unknownEvents > 0 ? (
                        <p className="mt-0.5 text-amber-600 dark:text-amber-400">
                          含 {point.unknownEvents} 条待核实记录
                        </p>
                      ) : null}
                    </div>
                  );
                }}
              />
              <Area
                activeDot={{ r: 4, strokeWidth: 0, fill: "var(--chart-1)" }}
                dataKey="chartAmount"
                fill={`url(#${gradientId})`}
                isAnimationActive={!reducedMotion}
                stroke="var(--chart-1)"
                strokeWidth={2}
                type="monotone"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="mt-4 flex h-56 items-center justify-center text-sm text-muted-foreground">
          当前筛选范围内暂无已结算费用记录
        </div>
      )}
    </section>
  );
}

interface EnrichedBreakdownRow {
  rawLabel: string;
  displayName: string;
  isDeleted: boolean;
  costAmount: string;
  costNumber: number;
  allUnknown: boolean;
  attempts: number;
  totalTokens: number;
  unknownEvents: number;
}

function ConsumptionDistributionCard({
  data,
  activeProvidersMap,
  historicalNames,
  onDrillDownFilter,
}: {
  data: UsageSummary;
  activeProvidersMap: ReadonlyMap<string, Provider>;
  historicalNames: Readonly<Record<string, string>>;
  onDrillDownFilter: (name: "provider_id" | "model_id" | "feature", value: string) => void;
}) {
  const [dimension, setDimension] = useState<BreakdownDimension>("model_id");
  const [sortBy, setSortBy] = useState<BreakdownSort>("cost");
  const [showAllOpen, setShowAllOpen] = useState(false);

  const enrichedRows = useMemo<EnrichedBreakdownRow[]>(() => {
    const rawRows = data.breakdown[dimension] ?? [];
    const mapped = rawRows.map((row) => {
      let displayName = row.label;
      let isDeleted = false;
      if (dimension === "provider_id") {
        const resolved = resolveProviderInfo(row.label, activeProvidersMap, historicalNames);
        displayName = resolved.name;
        isDeleted = resolved.isDeleted;
      } else if (dimension === "feature") {
        displayName = formatFeatureLabel(row.label);
      }
      const allUnknown =
        row.unknown_events > 0 &&
        row.unknown_events >= row.attempts &&
        compareDecimalStrings(row.cost_amount, "0") === 0;
      return {
        rawLabel: row.label,
        displayName,
        isDeleted,
        costAmount: row.cost_amount,
        costNumber: decimalToNumber(row.cost_amount),
        allUnknown,
        attempts: row.attempts,
        totalTokens: row.total_tokens,
        unknownEvents: row.unknown_events,
      };
    });

    return mapped.sort((a, b) => {
      if (sortBy === "attempts") {
        return (
          b.attempts - a.attempts ||
          compareDecimalStrings(b.costAmount, a.costAmount) ||
          b.totalTokens - a.totalTokens
        );
      }
      if (sortBy === "tokens") {
        return (
          b.totalTokens - a.totalTokens ||
          compareDecimalStrings(b.costAmount, a.costAmount) ||
          b.attempts - a.attempts
        );
      }
      // Sort by known cost descending; push all-unknown rows after priced rows.
      if (a.allUnknown !== b.allUnknown) return a.allUnknown ? 1 : -1;
      return (
        compareDecimalStrings(b.costAmount, a.costAmount) ||
        b.attempts - a.attempts ||
        b.totalTokens - a.totalTokens
      );
    });
  }, [activeProvidersMap, data.breakdown, dimension, historicalNames, sortBy]);

  const topFive = enrichedRows.slice(0, 5);
  const maxBarValue = useMemo(() => {
    if (!topFive.length) return 0;
    if (sortBy === "attempts") return Math.max(...topFive.map((r) => r.attempts), 0);
    if (sortBy === "tokens") return Math.max(...topFive.map((r) => r.totalTokens), 0);
    return Math.max(...topFive.map((r) => (r.allUnknown ? 0 : r.costNumber)), 0);
  }, [sortBy, topFive]);

  const dimensionTitle =
    dimension === "model_id" ? "模型" : dimension === "provider_id" ? "供应商" : "功能";

  return (
    <>
      <section aria-label="消耗分布" className={cn(USAGE_CARD_CLASS, "overflow-hidden")}>
        <div className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">消耗分布</h3>
            <Select value={sortBy} onValueChange={(value) => setSortBy(value as BreakdownSort)}>
              <SelectTrigger aria-label="消耗分布排序" className="h-7 w-auto min-w-[112px] text-xs" size="sm">
                <SelectValue>
                  {sortBy === "cost"
                    ? "按费用排序"
                    : sortBy === "attempts"
                      ? "按调用次数排序"
                      : "按 Token 排序"}
                </SelectValue>
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="cost">按费用排序</SelectItem>
                <SelectItem value="attempts">按调用次数排序</SelectItem>
                <SelectItem value="tokens">按 Token 排序</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div
            aria-label="消耗分布维度"
            className="mt-3 inline-flex max-w-full flex-nowrap items-center gap-1 rounded-lg bg-muted/60 p-1"
            role="tablist"
          >
            {(
              [
                ["model_id", "按模型"],
                ["provider_id", "按供应商"],
                ["feature", "按功能"],
              ] as const
            ).map(([key, label]) => {
              const active = dimension === key;
              return (
                <button
                  aria-selected={active}
                  className={cn(
                    "whitespace-nowrap rounded-md px-2.5 py-1 text-xs transition-colors",
                    active
                      ? "bg-background font-medium text-foreground shadow-xs"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  key={key}
                  onClick={() => setDimension(key)}
                  role="tab"
                  type="button"
                >
                  {label}
                </button>
              );
            })}
          </div>

          {topFive.length > 0 ? (
            <div className="mt-4 space-y-3.5">
              {topFive.map((row) => {
                const metricValue =
                  sortBy === "attempts"
                    ? row.attempts
                    : sortBy === "tokens"
                      ? row.totalTokens
                      : row.allUnknown
                        ? 0
                        : row.costNumber;
                const widthPercent =
                  maxBarValue > 0 && metricValue > 0
                    ? Math.max(2, Math.min(100, (metricValue / maxBarValue) * 100))
                    : 0;
                const moneyText = formatDisplayMoney(row.costAmount, data.currency, {
                  allUnknown: row.allUnknown,
                });

                return (
                  <div className="min-w-0" key={row.rawLabel}>
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="flex min-w-0 items-center gap-1.5">
                            <span className="truncate font-medium text-foreground">
                              {row.displayName}
                            </span>
                            {row.isDeleted ? (
                              <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                已删除
                              </span>
                            ) : null}
                          </div>
                        </TooltipTrigger>
                        <TooltipContent className="space-y-0.5 text-left">
                          <p className="font-medium">{row.displayName}</p>
                          {row.displayName !== row.rawLabel ? (
                            <p className="font-mono text-[11px] opacity-80">{row.rawLabel}</p>
                          ) : null}
                          <p className="opacity-90">
                            {row.allUnknown
                              ? "费用待核实"
                              : formatExactMoney(row.costAmount, data.currency)}{" "}
                            · {row.attempts.toLocaleString("en-US")} 次 ·{" "}
                            {row.totalTokens.toLocaleString("en-US")} Tokens
                          </p>
                          {row.unknownEvents > 0 ? (
                            <p className="text-amber-300">
                              含 {row.unknownEvents} 条待核实记录
                            </p>
                          ) : null}
                        </TooltipContent>
                      </Tooltip>

                      <div className="flex shrink-0 items-baseline gap-2.5 tabular-nums">
                        {sortBy === "cost" ? (
                          <>
                            <span className="text-xs text-muted-foreground">
                              {formatCompactCount(row.attempts)} 次
                            </span>
                            <span
                              className={cn(
                                "font-medium",
                                row.allUnknown
                                  ? "text-amber-600 dark:text-amber-400"
                                  : "text-foreground",
                              )}
                            >
                              {moneyText}
                            </span>
                          </>
                        ) : sortBy === "attempts" ? (
                          <>
                            <span className="text-xs text-muted-foreground">{moneyText}</span>
                            <span className="font-medium text-foreground">
                              {formatCompactCount(row.attempts)} 次
                            </span>
                          </>
                        ) : (
                          <>
                            <span className="text-xs text-muted-foreground">
                              {formatCompactCount(row.attempts)} 次 · {moneyText}
                            </span>
                            <span className="font-medium text-foreground">
                              {formatCompactTokens(row.totalTokens)}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-[var(--chart-1)] transition-[width] duration-300"
                        style={{ width: `${widthPercent}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              当前筛选范围内暂无消耗分布数据
            </p>
          )}
        </div>

        {enrichedRows.length > 0 ? (
          <div className="border-t">
            <button
              className="flex w-full items-center justify-center gap-1 px-5 py-2.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/30 hover:text-foreground"
              onClick={() => setShowAllOpen(true)}
              type="button"
            >
              <span>
                查看全部{dimensionTitle}
                {enrichedRows.length > 5 ? `（共 ${enrichedRows.length} 项）` : ""}
              </span>
              <ChevronRight className="size-3.5" />
            </button>
          </div>
        ) : null}
      </section>

      <DistributionDetailDialog
        currency={data.currency}
        dimension={dimension}
        dimensionTitle={dimensionTitle}
        onClose={() => setShowAllOpen(false)}
        onDrillDown={(rawLabel) => {
          setShowAllOpen(false);
          onDrillDownFilter(dimension, rawLabel);
        }}
        open={showAllOpen}
        rows={enrichedRows}
      />
    </>
  );
}

function DistributionDetailDialog({
  open,
  onClose,
  dimension,
  dimensionTitle,
  currency,
  rows,
  onDrillDown,
}: {
  open: boolean;
  onClose: () => void;
  dimension: BreakdownDimension;
  dimensionTitle: string;
  currency: BillingCurrency;
  rows: readonly EnrichedBreakdownRow[];
  onDrillDown: (rawLabel: string) => void;
}) {
  const [search, setSearch] = useState("");
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (row) =>
        row.displayName.toLowerCase().includes(q) ||
        row.rawLabel.toLowerCase().includes(q),
    );
  }, [rows, search]);

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className="flex max-h-[86dvh] flex-col overflow-hidden sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>全部{dimensionTitle}消耗明细 · {currency}</DialogTitle>
          <DialogDescription>
            展示当前筛选范围内的完整{dimensionTitle}统计，包含原始标识与待核实记录数。
          </DialogDescription>
        </DialogHeader>
        <ClearableSearchInput
          aria-label={`搜索${dimensionTitle}`}
          onChange={setSearch}
          placeholder={`搜索${dimensionTitle}名称或原始标识…`}
          value={search}
        />
        <div className="min-h-0 flex-1 overflow-auto rounded-lg border">
          <table className="w-full min-w-[600px] text-left text-xs">
            <thead className="sticky top-0 bg-muted/80 backdrop-blur-sm">
              <tr className="border-b text-muted-foreground">
                <th className="p-3 font-medium">{dimensionTitle}</th>
                <th className="p-3 text-right font-medium">已记录费用</th>
                <th className="p-3 text-right font-medium">调用次数</th>
                <th className="p-3 text-right font-medium">Token 消耗</th>
                <th className="p-3 text-right font-medium">待核实记录</th>
                <th className="p-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {visible.map((row) => (
                <tr className="hover:bg-muted/25" key={row.rawLabel}>
                  <td className="max-w-60 p-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-medium text-foreground">{row.displayName}</span>
                      {row.isDeleted ? (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          已删除
                        </span>
                      ) : null}
                    </div>
                    {row.displayName !== row.rawLabel || dimension === "provider_id" ? (
                      <p className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">
                        {row.rawLabel}
                      </p>
                    ) : null}
                  </td>
                  <td
                    className={cn(
                      "whitespace-nowrap p-3 text-right font-mono tabular-nums",
                      row.allUnknown && "text-amber-600 dark:text-amber-400",
                    )}
                    title={
                      row.allUnknown
                        ? "全部记录待核实费用"
                        : formatExactMoney(row.costAmount, currency)
                    }
                  >
                    {formatDisplayMoney(row.costAmount, currency, {
                      allUnknown: row.allUnknown,
                      showCode: true,
                    })}
                  </td>
                  <td className="whitespace-nowrap p-3 text-right tabular-nums">
                    {row.attempts.toLocaleString("en-US")} 次
                  </td>
                  <td
                    className="whitespace-nowrap p-3 text-right tabular-nums"
                    title={`${row.totalTokens.toLocaleString("en-US")} Tokens`}
                  >
                    {formatCompactTokens(row.totalTokens)}
                  </td>
                  <td className="whitespace-nowrap p-3 text-right tabular-nums text-muted-foreground">
                    {row.unknownEvents > 0 ? `${row.unknownEvents.toLocaleString("en-US")} 条` : "—"}
                  </td>
                  <td className="whitespace-nowrap p-3 text-right">
                    <Button
                      onClick={() => onDrillDown(row.rawLabel)}
                      size="xs"
                      type="button"
                      variant="ghost"
                    >
                      查看明细
                    </Button>
                  </td>
                </tr>
              ))}
              {visible.length === 0 ? (
                <tr>
                  <td className="p-8 text-center text-muted-foreground" colSpan={6}>
                    未找到匹配的{dimensionTitle}记录
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </DialogContent>
    </Dialog>
  );
}
