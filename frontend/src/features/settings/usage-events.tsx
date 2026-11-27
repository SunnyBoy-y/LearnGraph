import { useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Download, Info } from "lucide-react";

import type { UsageFilters } from "@/api/usage";
import { useActionConfirmation } from "@/components/shared/use-action-confirmation";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type { Provider } from "@/types/providers";
import type { BillingCurrency, UsageEvent } from "@/types/usage";
import {
  USAGE_CARD_CLASS,
  formatAmountSource,
  formatCompactTokens,
  formatCostStatus,
  formatDisplayMoney,
  formatExactMoney,
  formatFeatureLabel,
  resolveProviderInfo,
  stamp,
} from "./usage-formatting";
import {
  AdvancedFilterPopover,
  CurrencySelect,
  DateRangeControl,
  QuerySection,
} from "./usage-shared";

export type EventAnomalyMode = "all" | "unpriced" | "unknown_currency";

export function UsageEventsPanel({
  currency,
  onCurrencyChange,
  filters,
  onUpdateFilter,
  onUpdateDateRange,
  onResetAdvancedFilters,
  anomalyMode,
  onAnomalyModeChange,
  archived,
  onArchivedChange,
  page,
  pageSize,
  onPageChange,
  canManage,
  exporting,
  onExportEvents,
  archiving,
  onArchiveScope,
  eventsQuery,
  providers,
  historicalProviderNames,
  knownModels,
  knownFeatures,
  unpricedCount,
  unknownCurrencyCount,
  onOpenCatalog,
}: {
  currency: BillingCurrency;
  onCurrencyChange: (next: BillingCurrency) => void;
  filters: UsageFilters;
  onUpdateFilter: (name: "provider_id" | "model_id" | "feature", value: string) => void;
  onUpdateDateRange: (range: { start_at?: string; end_at?: string }) => void;
  onResetAdvancedFilters: () => void;
  anomalyMode: EventAnomalyMode;
  onAnomalyModeChange: (next: EventAnomalyMode) => void;
  archived: "exclude" | "include" | "only";
  onArchivedChange: (next: "exclude" | "include" | "only") => void;
  page: number;
  pageSize: number;
  onPageChange: (nextPage: number) => void;
  canManage: boolean;
  exporting: boolean;
  onExportEvents: () => void;
  archiving: boolean;
  onArchiveScope: () => void;
  eventsQuery: UseQueryResult<UsageEvent[], Error>;
  providers: readonly Provider[];
  historicalProviderNames: Readonly<Record<string, string>>;
  knownModels: readonly string[];
  knownFeatures: readonly string[];
  unpricedCount?: number;
  unknownCurrencyCount?: number;
  onOpenCatalog: () => void;
}) {
  const { confirm, dialog } = useActionConfirmation();
  const [detailEvent, setDetailEvent] = useState<UsageEvent | null>(null);

  const activeProvidersMap = useMemo(
    () => new Map(providers.map((p) => [p.id, p])),
    [providers],
  );

  const mergedHistoricalNames = useMemo(() => {
    const map: Record<string, string> = { ...historicalProviderNames };
    for (const row of eventsQuery.data ?? []) {
      if (row.provider_display_name) {
        map[row.provider_id] = row.provider_display_name;
      }
    }
    return map;
  }, [eventsQuery.data, historicalProviderNames]);

  return (
    <div className="space-y-3">
      {dialog}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <div
            aria-label="记录视图筛选"
            className="inline-flex max-w-full flex-nowrap items-center gap-1 rounded-lg bg-muted/60 p-1"
            role="tablist"
          >
            {(
              [
                ["all", "全部记录", undefined],
                ["unpriced", "待核实费用", unpricedCount],
                ["unknown_currency", "缺失币种", unknownCurrencyCount],
              ] as const
            ).map(([mode, label, count]) => {
              const active = anomalyMode === mode;
              return (
                <button
                  aria-selected={active}
                  className={cn(
                    "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-xs transition-colors",
                    active
                      ? "bg-background font-medium text-foreground shadow-xs"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                  key={mode}
                  onClick={() => onAnomalyModeChange(mode)}
                  role="tab"
                  type="button"
                >
                  <span>{label}</span>
                  {count !== undefined && count > 0 ? (
                    <span className="rounded-full bg-amber-500/15 px-1.5 text-[10px] font-medium tabular-nums text-amber-700 dark:text-amber-300">
                      {count}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>

          <select
            aria-label="归档筛选"
            className="h-8 rounded-md border bg-background px-2 text-xs"
            onChange={(event) =>
              onArchivedChange(event.target.value as "exclude" | "include" | "only")
            }
            value={archived}
          >
            <option value="exclude">未归档</option>
            <option value="include">包含归档</option>
            <option value="only">已归档</option>
          </select>

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

        <div className="flex flex-wrap items-center gap-2">
          <CurrencySelect currency={currency} onChange={onCurrencyChange} />
          <Button
            disabled={exporting}
            onClick={onExportEvents}
            size="sm"
            type="button"
            variant="outline"
          >
            <Download className="size-3.5" />
            {exporting ? "导出中…" : "导出明细"}
          </Button>
          {canManage && anomalyMode === "all" && archived !== "only" ? (
            <Button
              disabled={archiving}
              onClick={() =>
                confirm({
                  title: "归档用量明细？",
                  description: `归档当前 ${currency} 筛选范围的明细，保留账本费用和预算。`,
                  label: "归档",
                  run: onArchiveScope,
                })
              }
              size="sm"
              type="button"
              variant="outline"
            >
              归档当前范围
            </Button>
          ) : null}
        </div>
      </div>

      {anomalyMode !== "all" ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/30 bg-amber-500/8 px-3.5 py-2.5 text-xs text-amber-900 dark:text-amber-200">
          <div className="flex min-w-0 items-center gap-2">
            <Info className="size-3.5 shrink-0" />
            <span>
              {anomalyMode === "unpriced"
                ? "以下调用已结算，但因缺少单价配置、用量回报或调用中断而暂未得出费用；不计入「已记录费用」，已按预估金额计入「预算占用」。"
                : "以下记录缺少明确币种（多为历史兼容数据），无法自动并入 USD 或 CNY 统计；可点击「详情」查看历史账单快照。"}
            </span>
          </div>
          {anomalyMode === "unpriced" && canManage ? (
            <Button onClick={onOpenCatalog} size="xs" type="button" variant="outline">
              前往价格配置
            </Button>
          ) : null}
        </div>
      ) : null}

      <QuerySection loadingLabel="正在加载调用明细…" query={eventsQuery}>
        {(rows) => (
          <EventTable
            activeProvidersMap={activeProvidersMap}
            historicalNames={mergedHistoricalNames}
            onSelectDetail={setDetailEvent}
            rows={rows}
          />
        )}
      </QuerySection>

      <div className="flex items-center justify-end gap-3 text-xs">
        <Button
          disabled={!page}
          onClick={() => onPageChange(page - 1)}
          size="sm"
          type="button"
          variant="outline"
        >
          上一页
        </Button>
        <span className="tabular-nums text-muted-foreground">第 {page + 1} 页</span>
        <Button
          disabled={(eventsQuery.data?.length ?? 0) < pageSize}
          onClick={() => onPageChange(page + 1)}
          size="sm"
          type="button"
          variant="outline"
        >
          下一页
        </Button>
      </div>

      <EventDetailDialog
        activeProvidersMap={activeProvidersMap}
        event={detailEvent}
        historicalNames={mergedHistoricalNames}
        onClose={() => setDetailEvent(null)}
      />
    </div>
  );
}

function EventTable({
  rows,
  activeProvidersMap,
  historicalNames,
  onSelectDetail,
}: {
  rows: UsageEvent[];
  activeProvidersMap: ReadonlyMap<string, Provider>;
  historicalNames: Readonly<Record<string, string>>;
  onSelectDetail: (row: UsageEvent) => void;
}) {
  if (!rows.length) {
    return (
      <section className={cn(USAGE_CARD_CLASS, "p-8 text-center text-sm text-muted-foreground")}>
        当前筛选范围内暂无用量记录
      </section>
    );
  }

  return (
    <section className={cn(USAGE_CARD_CLASS, "overflow-x-auto")}>
      <table className="w-full min-w-[780px] text-left text-xs">
        <thead className="border-b bg-muted/35 text-muted-foreground">
          <tr>
            <th className="p-3 font-medium">时间</th>
            <th className="p-3 font-medium">服务连接 / 模型</th>
            <th className="p-3 font-medium">功能</th>
            <th className="p-3 text-right font-medium">Token 消耗</th>
            <th className="p-3 text-right font-medium">费用与状态</th>
            <th className="p-3 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((row) => {
            const providerInfo = resolveProviderInfo(
              row.provider_id,
              activeProvidersMap,
              historicalNames,
            );
            const featureLabel = formatFeatureLabel(row.feature);
            const isUnknownCost = row.cost_amount === null || row.currency === null;
            return (
              <tr className="align-top transition-colors hover:bg-muted/20" key={row.id}>
                <td className="whitespace-nowrap p-3">
                  <span title={row.created_at}>{stamp(row.created_at)}</span>
                  {row.archived_at ? (
                    <span className="mt-1 block w-fit rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      已归档
                    </span>
                  ) : null}
                </td>
                <td className="max-w-60 p-3">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span
                      className="font-medium text-foreground"
                      title={`连接 ID: ${row.provider_id}`}
                    >
                      {providerInfo.name}
                    </span>
                    {providerInfo.isDeleted ? (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        已删除
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">
                    {row.model_id}
                  </p>
                </td>
                <td className="p-3">
                  <span title={row.feature}>{featureLabel}</span>
                  {row.attempt > 1 ? (
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">
                      第 {row.attempt} 次尝试
                    </span>
                  ) : null}
                </td>
                <td className="whitespace-nowrap p-3 text-right tabular-nums">
                  <span title={`${row.total_tokens.toLocaleString("en-US")} Tokens`}>
                    {formatCompactTokens(row.total_tokens)}
                  </span>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    入 {formatCompactTokens(row.input_tokens)} / 出{" "}
                    {formatCompactTokens(row.output_tokens)}
                  </p>
                </td>
                <td className="whitespace-nowrap p-3 text-right">
                  {isUnknownCost ? (
                    <div className="inline-flex flex-col items-end">
                      <span className="rounded-full border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                        {row.currency === null ? "缺失币种" : "待核实"}
                      </span>
                      <span className="mt-0.5 text-[11px] text-muted-foreground">
                        {formatCostStatus(row.cost_status)}
                        {row.reserved_amount && row.currency
                          ? ` · 占用 ${formatDisplayMoney(row.reserved_amount, row.currency)}`
                          : ""}
                      </span>
                    </div>
                  ) : (
                    <div className="inline-flex flex-col items-end">
                      <span
                        className="font-mono font-medium tabular-nums"
                        title={formatExactMoney(row.cost_amount, row.currency)}
                      >
                        {formatDisplayMoney(row.cost_amount, row.currency, { showCode: true })}
                      </span>
                      <span className="mt-0.5 text-[11px] text-muted-foreground">
                        {formatCostStatus(row.cost_status)}
                      </span>
                    </div>
                  )}
                </td>
                <td className="whitespace-nowrap p-3 text-right">
                  <Button
                    onClick={() => onSelectDetail(row)}
                    size="xs"
                    type="button"
                    variant="ghost"
                  >
                    详情
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function EventDetailDialog({
  event,
  onClose,
  activeProvidersMap,
  historicalNames,
}: {
  event: UsageEvent | null;
  onClose: () => void;
  activeProvidersMap: ReadonlyMap<string, Provider>;
  historicalNames: Readonly<Record<string, string>>;
}) {
  if (!event) return null;
  const providerInfo = resolveProviderInfo(
    event.provider_id,
    activeProvidersMap,
    historicalNames,
  );
  const featureLabel = formatFeatureLabel(event.feature);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[86dvh] flex-col overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>调用与计费详情</DialogTitle>
          <DialogDescription>
            保留原始连接 UUID、内部功能标识、计价快照与核算依据，便于核账与排查。
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1 text-xs">
          <dl className="grid gap-3 rounded-lg border bg-muted/20 p-3.5 sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">服务连接</dt>
              <dd className="mt-0.5 font-medium">
                {providerInfo.name}
                {providerInfo.isDeleted ? "（已删除）" : ""}
              </dd>
              <dd className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">
                UUID: {event.provider_id}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">模型</dt>
              <dd className="mt-0.5 break-all font-mono font-medium">{event.model_id}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">功能</dt>
              <dd className="mt-0.5 font-medium">{featureLabel}</dd>
              <dd className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                标识: {event.feature}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">记录 ID / 时间</dt>
              <dd className="mt-0.5 break-all font-mono text-[11px]">{event.id}</dd>
              <dd className="mt-0.5 text-muted-foreground">
                {stamp(event.created_at)}（UTC: {event.created_at}）
              </dd>
            </div>
          </dl>

          <dl className="grid gap-3 rounded-lg border p-3.5 sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">实记费用（精确）</dt>
              <dd className="mt-0.5 font-mono font-medium">
                {event.cost_amount !== null && event.currency
                  ? formatExactMoney(event.cost_amount, event.currency)
                  : "待核实（未入账）"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">预算占用</dt>
              <dd className="mt-0.5 font-mono">
                {event.reserved_amount !== null && event.currency
                  ? formatExactMoney(event.reserved_amount, event.currency)
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">核算状态 / 来源</dt>
              <dd className="mt-0.5">
                {formatCostStatus(event.cost_status)}（{event.cost_status}）
              </dd>
              <dd className="text-muted-foreground">
                {formatAmountSource(event.amount_source)}（{event.amount_source}）
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">输入 / 缓存读 / 缓存写</dt>
              <dd className="mt-0.5 font-mono tabular-nums">
                {event.input_tokens.toLocaleString("en-US")} /{" "}
                {event.cached_input_tokens.toLocaleString("en-US")} /{" "}
                {event.cache_creation_input_tokens.toLocaleString("en-US")}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">输出 / 推理 / 总 Tokens</dt>
              <dd className="mt-0.5 font-mono tabular-nums">
                {event.output_tokens.toLocaleString("en-US")} /{" "}
                {event.reasoning_tokens.toLocaleString("en-US")} /{" "}
                {event.total_tokens.toLocaleString("en-US")}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">尝试 / 耗时 / 价目版本</dt>
              <dd className="mt-0.5 tabular-nums">
                第 {event.attempt} 次 · {event.latency_ms} ms
              </dd>
              <dd className="break-all font-mono text-[11px] text-muted-foreground">
                {event.price_version_id ?? "无价目版本"}
              </dd>
            </div>
          </dl>

          <div className="space-y-2">
            <p className="font-medium text-muted-foreground">计价单位 (usage_units)</p>
            <pre className="overflow-x-auto rounded-lg border bg-muted/25 p-3 font-mono text-[11px]">
              {JSON.stringify(event.usage_units ?? {}, null, 2)}
            </pre>
          </div>

          {Object.keys(event.pricing_snapshot ?? {}).length > 0 ? (
            <details className="rounded-lg border p-3">
              <summary className="cursor-pointer font-medium">定价快照 (pricing_snapshot)</summary>
              <pre className="mt-2 overflow-x-auto font-mono text-[11px] text-muted-foreground">
                {JSON.stringify(event.pricing_snapshot, null, 2)}
              </pre>
            </details>
          ) : null}

          {Object.keys(event.legacy_billing_snapshot ?? {}).length > 0 ? (
            <details className="rounded-lg border p-3">
              <summary className="cursor-pointer font-medium">
                历史账单证据 (legacy_billing_snapshot)
              </summary>
              <pre className="mt-2 overflow-x-auto font-mono text-[11px] text-muted-foreground">
                {JSON.stringify(event.legacy_billing_snapshot, null, 2)}
              </pre>
            </details>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
