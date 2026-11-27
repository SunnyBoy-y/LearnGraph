import { useId, useState, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Calendar, Check, ChevronDown, Filter, RotateCcw } from "lucide-react";

import type { UsageFilters } from "@/api/usage";
import { ErrorState, LoadingState } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import type { Provider } from "@/types/providers";
import type { BillingCurrency } from "@/types/usage";
import {
  FEATURE_LABELS,
  detectDatePreset,
  formatDateRangeLabel,
  formatFeatureLabel,
  presetToUtcRange,
  type DatePreset,
} from "./usage-formatting";

export function QuerySection<T>({
  query,
  children,
  loadingLabel,
}: {
  query: UseQueryResult<T, Error>;
  children: (data: T) => ReactNode;
  loadingLabel?: string;
}) {
  if (query.isError && query.data === undefined) {
    return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  }
  if (query.isPending || query.data === undefined) {
    return <LoadingState label={loadingLabel} />;
  }
  return (
    <div className="space-y-2">
      {query.isError ? (
        <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />
      ) : null}
      {children(query.data)}
    </div>
  );
}

export function CurrencySelect({
  currency,
  onChange,
  className,
}: {
  currency: BillingCurrency;
  onChange: (next: BillingCurrency) => void;
  className?: string;
}) {
  return (
    <select
      aria-label="计费币种"
      className={cn("h-8 w-[84px] rounded-lg border border-input bg-background px-2 text-xs font-medium", className)}
      onChange={(event) => onChange(event.target.value === "CNY" ? "CNY" : "USD")}
      value={currency}
    >
      <option value="USD">USD</option>
      <option value="CNY">CNY</option>
    </select>
  );
}

const DATE_PRESETS: ReadonlyArray<{ id: Exclude<DatePreset, "custom">; label: string }> = [
  { id: "all", label: "全部时间" },
  { id: "today", label: "今天 (UTC)" },
  { id: "last7d", label: "近 7 天 (UTC)" },
  { id: "last30d", label: "近 30 天 (UTC)" },
  { id: "month", label: "本月 (UTC)" },
];

export function DateRangeControl({
  startAt,
  endAt,
  onChange,
}: {
  startAt?: string;
  endAt?: string;
  onChange: (range: { start_at?: string; end_at?: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draftStart, setDraftStart] = useState(() => startAt?.slice(0, 10) ?? "");
  const [draftEnd, setDraftEnd] = useState(() => endAt?.slice(0, 10) ?? "");

  const activePreset = detectDatePreset(startAt, endAt);
  const label = formatDateRangeLabel(startAt, endAt);
  const invalidCustom = Boolean(draftStart && draftEnd && draftStart >= draftEnd);

  function handleOpenChange(nextOpen: boolean) {
    if (nextOpen) {
      setDraftStart(startAt?.slice(0, 10) ?? "");
      setDraftEnd(endAt?.slice(0, 10) ?? "");
    }
    setOpen(nextOpen);
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          aria-label="时间范围"
          className="h-8 gap-1.5 px-2.5 text-xs font-normal"
          size="sm"
          type="button"
          variant={activePreset === "all" ? "outline" : "secondary"}
        >
          <Calendar className="size-3.5 text-muted-foreground" />
          <span>{label}</span>
          <ChevronDown className="size-3 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 space-y-3 p-3">
        <div className="grid gap-0.5">
          {DATE_PRESETS.map((preset) => {
            const selected = activePreset === preset.id;
            return (
              <button
                className={cn(
                  "flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted",
                  selected && "bg-muted font-medium text-foreground",
                )}
                key={preset.id}
                onClick={() => {
                  onChange(presetToUtcRange(preset.id));
                  setOpen(false);
                }}
                type="button"
              >
                <span>{preset.label}</span>
                {selected ? <Check className="size-3.5 text-primary" /> : null}
              </button>
            );
          })}
        </div>
        <div className="space-y-2 border-t pt-2.5">
          <p className="text-[11px] font-medium text-muted-foreground">自定义日期范围 (UTC)</p>
          <div className="grid grid-cols-2 gap-2">
            <label className="grid gap-1 text-[11px] text-muted-foreground">
              开始日期（含）
              <Input
                aria-label="开始日期 UTC"
                className="h-8 text-xs"
                onChange={(event) => setDraftStart(event.target.value)}
                type="date"
                value={draftStart}
              />
            </label>
            <label className="grid gap-1 text-[11px] text-muted-foreground">
              结束日期（不含）
              <Input
                aria-label="结束日期 UTC"
                className="h-8 text-xs"
                onChange={(event) => setDraftEnd(event.target.value)}
                type="date"
                value={draftEnd}
              />
            </label>
          </div>
          {invalidCustom ? (
            <p className="text-[11px] text-destructive" role="alert">
              结束日期需晚于开始日期
            </p>
          ) : null}
          <div className="flex items-center justify-between gap-2 pt-1">
            <Button
              disabled={!startAt && !endAt && !draftStart && !draftEnd}
              onClick={() => {
                setDraftStart("");
                setDraftEnd("");
                onChange({});
                setOpen(false);
              }}
              size="xs"
              type="button"
              variant="ghost"
            >
              重置
            </Button>
            <Button
              disabled={invalidCustom}
              onClick={() => {
                onChange({
                  start_at: draftStart ? `${draftStart}T00:00:00Z` : undefined,
                  end_at: draftEnd ? `${draftEnd}T00:00:00Z` : undefined,
                });
                setOpen(false);
              }}
              size="xs"
              type="button"
            >
              应用日期
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function AdvancedFilterPopover({
  filters,
  providers,
  historicalProviderNames,
  knownModels,
  knownFeatures,
  onChangeFilter,
  onResetFilters,
}: {
  filters: UsageFilters;
  providers: readonly Provider[];
  historicalProviderNames: Readonly<Record<string, string>>;
  knownModels: readonly string[];
  knownFeatures: readonly string[];
  onChangeFilter: (name: "provider_id" | "model_id" | "feature", value: string) => void;
  onResetFilters: () => void;
}) {
  const [open, setOpen] = useState(false);
  const modelListId = useId();
  const activeCount = [filters.provider_id, filters.model_id, filters.feature].filter(Boolean).length;

  const providerOptions: Array<{ id: string; label: string }> = providers.map((p) => ({
    id: p.id,
    label: p.display_name,
  }));
  const seenProviderIds = new Set(providerOptions.map((p) => p.id));
  for (const [id, name] of Object.entries(historicalProviderNames)) {
    if (!seenProviderIds.has(id) && id !== "*") {
      providerOptions.push({ id, label: `${name}（历史）` });
      seenProviderIds.add(id);
    }
  }
  if (filters.provider_id && !seenProviderIds.has(filters.provider_id)) {
    providerOptions.push({ id: filters.provider_id, label: "已删除的连接" });
  }

  const featureSet = new Set<string>([
    ...knownFeatures,
    ...Object.keys(FEATURE_LABELS).filter((k) => k !== "*"),
    ...(filters.feature ? [filters.feature] : []),
  ]);
  const featureOptions = [...featureSet].sort((a, b) =>
    formatFeatureLabel(a).localeCompare(formatFeatureLabel(b), "zh-CN"),
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          aria-label="筛选"
          className="h-8 gap-1.5 px-2.5 text-xs font-normal"
          size="sm"
          type="button"
          variant={activeCount > 0 ? "secondary" : "outline"}
        >
          <Filter className="size-3.5 text-muted-foreground" />
          <span>筛选</span>
          {activeCount > 0 ? (
            <span className="rounded-full bg-foreground/10 px-1.5 text-[11px] font-medium tabular-nums">
              {activeCount}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 space-y-3 p-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold">高级筛选</p>
          {activeCount > 0 ? (
            <Button
              className="h-6 gap-1 px-1.5 text-[11px] text-muted-foreground"
              onClick={onResetFilters}
              size="xs"
              type="button"
              variant="ghost"
            >
              <RotateCcw className="size-3" />
              重置
            </Button>
          ) : null}
        </div>
        <div className="space-y-3">
          <label className="grid gap-1 text-xs text-muted-foreground">
            服务连接
            <select
              aria-label="筛选服务连接"
              className="h-8 w-full rounded-md border bg-background px-2 text-xs text-foreground"
              onChange={(event) => onChangeFilter("provider_id", event.target.value)}
              value={filters.provider_id ?? ""}
            >
              <option value="">全部连接</option>
              {providerOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            模型
            <Input
              aria-label="筛选模型"
              className="h-8 text-xs"
              list={modelListId}
              onChange={(event) => onChangeFilter("model_id", event.target.value)}
              placeholder="全部模型或输入模型 ID"
              value={filters.model_id ?? ""}
            />
            <datalist id={modelListId}>
              {knownModels.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
          </label>
          <label className="grid gap-1 text-xs text-muted-foreground">
            功能
            <select
              aria-label="筛选功能"
              className="h-8 w-full rounded-md border bg-background px-2 text-xs text-foreground"
              onChange={(event) => onChangeFilter("feature", event.target.value)}
              value={filters.feature ?? ""}
            >
              <option value="">全部功能</option>
              {featureOptions.map((feature) => (
                <option key={feature} title={feature} value={feature}>
                  {formatFeatureLabel(feature)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="flex justify-end border-t pt-2.5">
          <Button onClick={() => setOpen(false)} size="xs" type="button">
            完成
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function MoneyInput({
  name,
  label,
  value,
  required = false,
}: {
  name: string;
  label: string;
  value?: string | null;
  required?: boolean;
}) {
  return (
    <label className="grid gap-1 text-sm">
      {label}
      <Input
        defaultValue={value ?? ""}
        inputMode="decimal"
        name={name}
        placeholder="0.00"
        required={required}
      />
    </label>
  );
}

export function ScopeFields({
  providers,
  providerId = "*",
  modelId = "*",
  feature = "*",
  requiredProvider = false,
  historicalProviderNames = {},
}: {
  providers: Provider[];
  providerId?: string;
  modelId?: string;
  feature?: string;
  requiredProvider?: boolean;
  historicalProviderNames?: Readonly<Record<string, string>>;
}) {
  const featureListId = useId();
  const hasProvider = providerId === "*" || providers.some((p) => p.id === providerId);
  return (
    <>
      <label className="grid gap-1 text-sm">
        服务连接
        <select
          className="h-9 w-full rounded-md border bg-background px-2"
          defaultValue={providerId}
          name="provider_id"
          required
        >
          {!requiredProvider ? (
            <option value="*">全部服务连接</option>
          ) : (
            <option value="*">选择服务连接</option>
          )}
          {!hasProvider ? (
            <option value={providerId}>
              {historicalProviderNames[providerId]
                ? `${historicalProviderNames[providerId]}（已删除）`
                : "已删除的连接"}
            </option>
          ) : null}
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.display_name}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1 text-sm">
        模型
        <Input defaultValue={modelId} name="model_id" required />
      </label>
      <label className="grid gap-1 text-sm">
        功能范围
        <Input
          defaultValue={feature}
          list={featureListId}
          name="feature"
          placeholder="* 表示该连接的所有功能"
          required
        />
        <datalist id={featureListId}>
          <option value="*">全部功能</option>
          {Object.entries(FEATURE_LABELS)
            .filter(([key]) => key !== "*")
            .map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
        </datalist>
      </label>
    </>
  );
}
