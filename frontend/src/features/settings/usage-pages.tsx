import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";

import {
  clearUsageEvents,
  getUsageSummary,
  listProviders,
  listUsageEvents,
} from "@/api";
import { authStore } from "@/api/auth-store";
import type { UsageFilters, UsageScope } from "@/api/usage";
import { PageFrame } from "@/components/shared/page-elements";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAuth } from "@/features/auth/auth-context-value";
import { toast } from "@/lib/notifications";
import { billingQueryKey, workspaceQueryKey } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import type { BillingCurrency, UsageEvent, UsageSummary } from "@/types/usage";
import { BudgetPanel } from "./usage-budget";
import { CatalogPanel } from "./usage-catalog";
import { UsageEventsPanel, type EventAnomalyMode } from "./usage-events";
import { saveCsv } from "./usage-formatting";
import { UsageOverviewPanel } from "./usage-overview";

const PAGE_SIZE = 100;
type UsageView = "overview" | "budget" | "events" | "catalog";

function parseUsageView(value: string | null): UsageView {
  if (value === "budget" || value === "events" || value === "catalog") return value;
  return "overview";
}

export function UsagePage() {
  const auth = useAuth();
  const [params, setParams] = useSearchParams();
  const currency: BillingCurrency = params.get("currency") === "CNY" ? "CNY" : "USD";
  const view = parseUsageView(params.get("usage_view"));
  const client = useQueryClient();

  useEffect(() => {
    const legacy = new Set([
      "usage-summary",
      "usage-events",
      "usage",
      "price-catalog",
      "manual-prices",
      "budget-policies",
      "budget-status",
      "budget-alerts",
      "exchange-rate",
      "alert-email",
    ]);
    client.removeQueries({ predicate: (query) => legacy.has(String(query.queryKey[0])) });
  }, [client]);

  function setCurrency(nextCurrency: BillingCurrency) {
    const next = new URLSearchParams(params);
    next.set("currency", nextCurrency);
    setParams(next, { replace: true });
  }

  function setView(nextView: UsageView) {
    const next = new URLSearchParams(params);
    if (nextView === "overview") next.delete("usage_view");
    else next.set("usage_view", nextView);
    setParams(next, { replace: true });
  }

  return (
    <TooltipProvider delayDuration={200}>
      <PageFrame>
        <UsageWindow
          key={`${auth.userId}:${auth.workspaceId}`}
          onCurrencyChange={setCurrency}
          onViewChange={setView}
          scope={{
            workspaceId: auth.workspaceId,
            userId: auth.userId,
            currency,
          }}
          view={view}
        />
      </PageFrame>
    </TooltipProvider>
  );
}

function UsageWindow({
  scope,
  view,
  onViewChange,
  onCurrencyChange,
}: {
  scope: UsageScope;
  view: UsageView;
  onViewChange: (next: UsageView) => void;
  onCurrencyChange: (next: BillingCurrency) => void;
}) {
  const { effectivePermissions, isSystemAdmin } = useAuth();
  const canManage = effectivePermissions.includes("workspace.manage");
  const canRefresh = isSystemAdmin && effectivePermissions.includes("workspace.write");
  const client = useQueryClient();
  const key = (...parts: unknown[]) =>
    billingQueryKey(scope.workspaceId, scope.userId ?? "", scope.currency, ...parts);

  const [filters, setFilters] = useState<UsageFilters>({});
  const [page, setPage] = useState(0);
  const [anomalyMode, setAnomalyMode] = useState<EventAnomalyMode>("all");
  const [archived, setArchived] = useState<"exclude" | "include" | "only">("exclude");
  const [exporting, setExporting] = useState(false);
  const [knownModels, setKnownModels] = useState<string[]>([]);
  const [knownFeatures, setKnownFeatures] = useState<string[]>([]);
  const [knownProviderNames, setKnownProviderNames] = useState<Record<string, string>>({});
  const exportController = useRef<AbortController | null>(null);
  useEffect(() => () => exportController.current?.abort(), []);

  const providers = useQuery({
    queryKey: workspaceQueryKey(scope.workspaceId, "providers", scope.userId),
    queryFn: ({ signal }) =>
      listProviders({
        signal,
        headers: { "X-Workspace-ID": scope.workspaceId },
        workspace: false,
      }),
  });

  const summary = useQuery({
    queryKey: key("summary", filters),
    queryFn: ({ signal }) => getUsageSummary(scope, filters, signal),
    enabled: view === "overview" || view === "events",
  });

  const events = useQuery({
    queryKey: key("events", filters, page, archived, anomalyMode),
    queryFn: ({ signal }) =>
      listUsageEvents(
        scope,
        {
          ...filters,
          offset: page * PAGE_SIZE,
          limit: PAGE_SIZE,
          archived,
          unknown_currency: anomalyMode === "unknown_currency",
          unpriced_only: anomalyMode === "unpriced",
        },
        signal,
      ),
    enabled: view === "events",
  });

  // Accumulate observed filter options and historical provider names so
  // switching filters doesn't shrink the available option lists.
  useEffect(() => {
    const data = summary.data;
    if (!data) return;
    const models = (data.breakdown.model_id ?? []).map((row) => row.label);
    const features = (data.breakdown.feature ?? []).map((row) => row.label);
    if (models.length) {
      setKnownModels((prev) => {
        const merged = new Set([...prev, ...models]);
        return merged.size === prev.length ? prev : [...merged].sort();
      });
    }
    if (features.length) {
      setKnownFeatures((prev) => {
        const merged = new Set([...prev, ...features]);
        return merged.size === prev.length ? prev : [...merged];
      });
    }
    if (data.provider_names && Object.keys(data.provider_names).length > 0) {
      setKnownProviderNames((prev) => ({ ...prev, ...data.provider_names }));
    }
  }, [summary.data]);

  const invalidate = () =>
    client.invalidateQueries({
      queryKey: workspaceQueryKey(scope.workspaceId, "usage", "native-v2", scope.userId),
    });

  const activeScope = () => {
    const session = authStore.getSession();
    return (
      session?.workspaceId === scope.workspaceId &&
      (session.userId ?? session.username) === scope.userId
    );
  };

  const archive = useMutation({
    mutationFn: () => clearUsageEvents(scope, filters),
    onSuccess: async (result) => {
      await invalidate();
      if (activeScope()) {
        toast.success(`已归档 ${result.archived_count} 条，费用与预算记录保留`);
      }
    },
    onError: (error) => toast.error(error.message),
  });

  const filename = (kind: string) =>
    `usage-${scope.workspaceId}-${
      anomalyMode === "unknown_currency" && kind === "events"
        ? "legacy-unknown"
        : scope.currency
    }-${filters.start_at?.slice(0, 10) ?? "all"}-${filters.end_at?.slice(0, 10) ?? "now"}-${kind}.csv`;

  async function exportOverview(data: UsageSummary) {
    await saveCsv(
      [
        [
          "workspace",
          "currency",
          "start_inclusive",
          "end_exclusive",
          "amount",
          "unknown_events",
          "tokens",
          "attempts",
        ],
        [
          scope.workspaceId,
          scope.currency,
          filters.start_at,
          filters.end_at,
          data.cost_amount,
          data.unpriced_events,
          data.total_tokens,
          data.attempts,
        ],
        [
          "dimension",
          "label",
          "currency",
          "amount",
          "unknown_events",
          "tokens",
          "attempts",
        ],
        ...Object.entries(data.breakdown).flatMap(([dimension, rows]) =>
          rows.map((row) => [
            dimension,
            row.label,
            scope.currency,
            row.cost_amount,
            row.unknown_events,
            row.total_tokens,
            row.attempts,
          ]),
        ),
      ],
      filename("overview"),
    );
  }

  async function exportEvents() {
    const controller = new AbortController();
    exportController.current = controller;
    setExporting(true);
    try {
      const all: UsageEvent[] = [];
      for (let offset = 0; ; offset += 1000) {
        const rows = await listUsageEvents(
          scope,
          {
            ...filters,
            archived,
            unknown_currency: anomalyMode === "unknown_currency",
            unpriced_only: anomalyMode === "unpriced",
            limit: 1000,
            offset,
          },
          controller.signal,
        );
        all.push(...rows);
        if (rows.length < 1000) break;
      }
      if (!activeScope() || controller.signal.aborted) return;
      await saveCsv(
        [
          [
            "workspace",
            "currency",
            "id",
            "created_at_utc",
            "provider",
            "model",
            "feature",
            "amount",
            "status",
            "source",
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
            "reasoning_tokens",
            "attempt",
            "price_version",
            "units",
            "archived_at",
          ],
          ...all.map((row) => [
            row.workspace_id,
            row.currency,
            row.id,
            row.created_at,
            row.provider_id,
            row.model_id,
            row.feature,
            row.cost_amount,
            row.cost_status,
            row.amount_source,
            row.input_tokens,
            row.cached_input_tokens,
            row.cache_creation_input_tokens,
            row.output_tokens,
            row.reasoning_tokens,
            row.attempt,
            row.price_version_id,
            JSON.stringify(row.usage_units),
            row.archived_at,
          ]),
        ],
        filename("events"),
      );
    } catch (error) {
      if (!controller.signal.aborted) toast.error((error as Error).message);
    } finally {
      setExporting(false);
    }
  }

  function updateFilter(name: "provider_id" | "model_id" | "feature", value: string) {
    setFilters((current) => ({ ...current, [name]: value || undefined }));
    setPage(0);
  }

  function updateDateRange(range: { start_at?: string; end_at?: string }) {
    setFilters((current) => ({
      ...current,
      start_at: range.start_at,
      end_at: range.end_at,
    }));
    setPage(0);
  }

  function resetAdvancedFilters() {
    setFilters((current) => ({
      start_at: current.start_at,
      end_at: current.end_at,
    }));
    setPage(0);
  }

  function handleCurrencyChange(nextCurrency: BillingCurrency) {
    setPage(0);
    onCurrencyChange(nextCurrency);
  }

  const providerList = useMemo(() => providers.data ?? [], [providers.data]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-3">
        <nav
          aria-label="用量与预算子页面"
          className="flex flex-nowrap items-center gap-1"
          role="tablist"
        >
          {(
            [
              ["overview", "用量概览"],
              ["budget", "预算管理"],
              ["events", "用量明细"],
            ] as const
          ).map(([id, label]) => {
            const active = view === id;
            return (
              <button
                aria-selected={active}
                className={cn(
                  "whitespace-nowrap rounded-lg px-3 py-1.5 text-xs transition-colors sm:text-sm",
                  active
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
                key={id}
                onClick={() => onViewChange(id)}
                role="tab"
                type="button"
              >
                {label}
              </button>
            );
          })}
        </nav>

        <button
          aria-pressed={view === "catalog"}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors sm:text-sm",
            view === "catalog"
              ? "bg-muted font-medium text-foreground"
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
          )}
          onClick={() => onViewChange(view === "catalog" ? "overview" : "catalog")}
          type="button"
        >
          <SlidersHorizontal className="size-3.5" />
          <span>价格配置</span>
        </button>
      </div>

      {providers.isError ? (
        <p className="text-xs text-destructive" role="alert">
          服务连接列表读取失败：{providers.error.message}
        </p>
      ) : null}

      {view === "overview" ? (
        <UsageOverviewPanel
          currency={scope.currency}
          filters={filters}
          historicalProviderNames={knownProviderNames}
          knownFeatures={knownFeatures}
          knownModels={knownModels}
          onCurrencyChange={handleCurrencyChange}
          onDrillDownFilter={(dimension, rawValue) => {
            setFilters((current) => ({ ...current, [dimension]: rawValue }));
            setAnomalyMode("all");
            setPage(0);
            onViewChange("events");
          }}
          onExportOverview={(data) => void exportOverview(data)}
          onOpenAnomalies={(mode) => {
            setAnomalyMode(mode);
            setArchived("include");
            setPage(0);
            onViewChange("events");
          }}
          onResetAdvancedFilters={resetAdvancedFilters}
          onUpdateDateRange={updateDateRange}
          onUpdateFilter={updateFilter}
          providers={providerList}
          summaryQuery={summary}
        />
      ) : null}

      {view === "budget" ? (
        <BudgetPanel
          canManage={canManage}
          historicalProviderNames={knownProviderNames}
          onCurrencyChange={handleCurrencyChange}
          providers={providerList}
          scope={scope}
        />
      ) : null}

      {view === "events" ? (
        <UsageEventsPanel
          anomalyMode={anomalyMode}
          archived={archived}
          archiving={archive.isPending}
          canManage={canManage}
          currency={scope.currency}
          eventsQuery={events}
          exporting={exporting}
          filters={filters}
          historicalProviderNames={knownProviderNames}
          knownFeatures={knownFeatures}
          knownModels={knownModels}
          onAnomalyModeChange={(mode) => {
            setAnomalyMode(mode);
            setPage(0);
          }}
          onArchiveScope={() => archive.mutate()}
          onArchivedChange={(nextArchived) => {
            setArchived(nextArchived);
            setPage(0);
          }}
          onCurrencyChange={handleCurrencyChange}
          onExportEvents={() => void exportEvents()}
          onOpenCatalog={() => onViewChange("catalog")}
          onPageChange={setPage}
          onResetAdvancedFilters={resetAdvancedFilters}
          onUpdateDateRange={updateDateRange}
          onUpdateFilter={updateFilter}
          page={page}
          pageSize={PAGE_SIZE}
          providers={providerList}
          unknownCurrencyCount={summary.data?.unknown_currency_events}
          unpricedCount={summary.data?.unpriced_events}
        />
      ) : null}

      {view === "catalog" ? (
        <CatalogPanel
          canManage={canManage}
          canRefresh={canRefresh}
          historicalProviderNames={knownProviderNames}
          onBackToOverview={() => onViewChange("overview")}
          onCurrencyChange={handleCurrencyChange}
          providers={providerList}
          scope={scope}
        />
      ) : null}
    </div>
  );
}
