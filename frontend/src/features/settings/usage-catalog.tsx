import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, RefreshCw } from "lucide-react";

import {
  getModelsDevStatus,
  listManualPrices,
  listPriceCatalog,
  refreshModelsDevSnapshot,
  removeManualPrice,
  updateProvider,
  upsertManualPrice,
} from "@/api";
import type { UsageScope } from "@/api/usage";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import { Pagination } from "@/components/shared/pagination";
import { useActionConfirmation } from "@/components/shared/use-action-confirmation";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { adaptivePollInterval } from "@/lib/network-quality";
import { validMoney } from "@/lib/money";
import { toast } from "@/lib/notifications";
import { billingQueryKey, workspaceQueryKey } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import type { Provider } from "@/types/providers";
import type {
  BillingCurrency,
  ManualPriceUpsert,
  PriceCatalogItem,
} from "@/types/usage";
import {
  USAGE_CARD_CLASS,
  field,
  formatFeatureLabel,
  formatRateMoney,
  optionalMoney,
  resolveProviderInfo,
  stamp,
} from "./usage-formatting";
import {
  CurrencySelect,
  MoneyInput,
  QuerySection,
  ScopeFields,
} from "./usage-shared";

const CATALOG_PAGE_SIZE = 100;

export function CatalogPanel({
  scope,
  canManage,
  canRefresh,
  providers,
  historicalProviderNames,
  onCurrencyChange,
  onBackToOverview,
}: {
  scope: UsageScope;
  canManage: boolean;
  canRefresh: boolean;
  providers: Provider[];
  historicalProviderNames: Readonly<Record<string, string>>;
  onCurrencyChange: (next: BillingCurrency) => void;
  onBackToOverview: () => void;
}) {
  const client = useQueryClient();
  const { confirm, dialog } = useActionConfirmation();
  const key = (...parts: unknown[]) =>
    billingQueryKey(scope.workspaceId, scope.userId ?? "", scope.currency, ...parts);
  const status = useQuery({
    queryKey: key("models-dev"),
    queryFn: ({ signal }) => getModelsDevStatus(scope, signal),
    refetchInterval: () => adaptivePollInterval(60_000),
  });
  const catalog = useQuery({
    queryKey: key("catalog", status.data?.fetched_at),
    queryFn: ({ signal }) => listPriceCatalog(scope, signal),
  });
  const manual = useQuery({
    queryKey: key("manual"),
    queryFn: ({ signal }) => listManualPrices(scope, signal),
  });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [draft, setDraft] = useState<ManualPriceUpsert | null>(null);

  const binding = useMutation({
    mutationFn: (provider: Provider) =>
      updateProvider(
        provider.id,
        { billing_currency: scope.currency },
        { workspace: false, headers: { "X-Workspace-ID": scope.workspaceId } },
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: workspaceQueryKey(scope.workspaceId) }),
    onError: (error) => toast.error(error.message),
  });
  const invalidate = () =>
    client.invalidateQueries({
      queryKey: workspaceQueryKey(scope.workspaceId, "usage", "native-v2", scope.userId),
    });
  const refresh = useMutation({
    mutationFn: () => refreshModelsDevSnapshot(scope),
    onSuccess: invalidate,
    onError: (error) => toast.error(error.message),
  });
  const remove = useMutation({
    mutationFn: (id: string) => removeManualPrice(id, scope),
    onSuccess: invalidate,
    onError: (error) => toast.error(error.message),
  });
  const save = useMutation({
    mutationFn: (form: FormData) => {
      const providerId = field(form, "provider_id");
      if (providerId === "*") throw new Error("请选择具体服务连接");
      let conditions: Record<string, unknown>;
      let units: Record<string, string>;
      try {
        conditions = JSON.parse(field(form, "conditions") || "{}");
        units = JSON.parse(field(form, "rates_by_unit") || "{}");
      } catch {
        throw new Error("计价条件和单位价格必须是 JSON 对象");
      }
      if (
        !conditions ||
        Array.isArray(conditions) ||
        typeof conditions !== "object" ||
        !units ||
        Array.isArray(units) ||
        typeof units !== "object"
      ) {
        throw new Error("请输入 JSON 对象");
      }
      if (Object.values(units).some((value) => typeof value !== "string" || !validMoney(value))) {
        throw new Error('单位价格使用非负金额字符串，例如 {"seconds":"0.02"}');
      }
      return upsertManualPrice(
        {
          provider_id: providerId,
          model_id: field(form, "model_id"),
          feature: field(form, "feature"),
          currency: scope.currency,
          input_per_million: optionalMoney(form, "input") ?? "0",
          cached_input_per_million: optionalMoney(form, "cached"),
          cache_write_per_million: optionalMoney(form, "write"),
          output_per_million: optionalMoney(form, "output") ?? "0",
          fixed_per_call: optionalMoney(form, "fixed") ?? "0",
          conditions,
          rates_by_unit: units,
        },
        scope,
      );
    },
    onSuccess: async () => {
      setDraft(null);
      await invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  const activeProvidersMap = useMemo(
    () => new Map(providers.map((p) => [p.id, p])),
    [providers],
  );
  const rows = useMemo(
    () =>
      (catalog.data ?? []).filter((item) =>
        `${item.provider_key} ${item.model_id}`.toLowerCase().includes(search.toLowerCase()),
      ),
    [catalog.data, search],
  );

  function copy(item?: PriceCatalogItem) {
    setDraft({
      provider_id: "*",
      model_id: item?.model_id ?? "",
      feature: "*",
      currency: scope.currency,
      input_per_million: item?.native_input_per_million ?? "0",
      cached_input_per_million: item?.native_cached_input_per_million,
      cache_write_per_million: item?.native_cache_write_per_million,
      output_per_million: item?.native_output_per_million ?? "0",
      fixed_per_call: item?.native_fixed_per_call ?? "0",
      conditions: item?.conditions ?? {},
      rates_by_unit: item?.rates_by_unit ?? {},
    });
  }

  return (
    <div className="space-y-4">
      {dialog}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">价格配置 · {scope.currency}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            管理原币单价目录、连接结算币种与手动覆盖价格；不与概览统计混排。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CurrencySelect currency={scope.currency} onChange={onCurrencyChange} />
          <Button onClick={onBackToOverview} size="sm" type="button" variant="outline">
            <ArrowLeft className="size-3.5" />
            返回概览
          </Button>
        </div>
      </div>

      <section className={cn(USAGE_CARD_CLASS, "space-y-2.5 p-4")}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h4 className="text-sm font-semibold">价格来源：models.dev</h4>
            <p className="mt-0.5 text-xs text-muted-foreground">
              每日自动更新原币价格；用户手动价格始终优先，自动同步不会覆盖。
            </p>
          </div>
          {canRefresh ? (
            <Button
              disabled={refresh.isPending}
              onClick={() => refresh.mutate()}
              size="sm"
              type="button"
              variant="outline"
            >
              <RefreshCw className={cn("size-3.5", refresh.isPending && "animate-spin")} />
              刷新 models.dev
            </Button>
          ) : null}
        </div>
        <QuerySection loadingLabel="正在加载目录状态…" query={status}>
          {(value) => (
            <div className="space-y-1 text-xs">
              <p>
                <a
                  className="break-all underline underline-offset-2"
                  href={value.source}
                  rel="noreferrer"
                  target="_blank"
                >
                  models.dev 实时价格目录
                </a>{" "}
                · 全部币种共 {value.priced_model_count} 条模型价格
              </p>
              <p className="text-muted-foreground">
                上次成功同步：
                {value.origin === "bundled" || !value.fetched_at
                  ? "尚未联网同步"
                  : stamp(value.fetched_at)}{" "}
                ·{" "}
                {value.auto_refresh_enabled
                  ? value.stale
                    ? "等待自动同步"
                    : `下次更新：${value.next_refresh_at ? stamp(value.next_refresh_at) : "待安排"}`
                  : "自动更新已关闭"}
              </p>
              {value.stale ? (
                <p className="text-amber-600 dark:text-amber-400">
                  {value.origin === "network" || value.origin === "network_cache"
                    ? "价格缓存待更新，暂时沿用最近一次成功同步的价格。"
                    : "价格目录等待首次同步；未收录的模型可设置手动价格。"}
                </p>
              ) : null}
            </div>
          )}
        </QuerySection>
      </section>

      <section className={cn(USAGE_CARD_CLASS, "space-y-3 p-4")}>
        <div>
          <h4 className="text-sm font-semibold">服务连接结算币种</h4>
          <p className="mt-0.5 text-xs text-muted-foreground">
            有两种原币报价时，按服务商实际结算币种选择；已有账单保留调用时的币种。
          </p>
        </div>
        <div className="divide-y">
          {providers
            .filter((provider) => provider.remote_capability)
            .map((provider) => (
              <div
                className="flex flex-wrap items-center justify-between gap-2 py-2 text-xs first:pt-0 last:pb-0"
                key={provider.id}
              >
                <span>
                  <span className="font-medium text-foreground">{provider.display_name}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    · {String(provider.capabilities.billing_currency ?? "按模型原价")}
                  </span>
                </span>
                {canManage && provider.capabilities.billing_currency !== scope.currency ? (
                  <Button
                    disabled={binding.isPending}
                    onClick={() => binding.mutate(provider)}
                    size="xs"
                    type="button"
                    variant="outline"
                  >
                    设置为 {scope.currency}
                  </Button>
                ) : null}
              </div>
            ))}
        </div>
      </section>

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 className="text-sm font-semibold">{scope.currency} 手动价格</h4>
            <p className="mt-0.5 text-xs text-muted-foreground">
              手动价格仅覆盖所选服务连接、币种、功能和计价条件，每日同步会保留。
            </p>
          </div>
          {canManage ? (
            <Button onClick={() => copy()} size="sm" type="button">
              <Plus className="size-3.5" />
              添加手动价格
            </Button>
          ) : null}
        </div>

        <QuerySection loadingLabel="正在加载手动价格…" query={manual}>
          {(items) =>
            items.length ? (
              <div className="grid gap-3 sm:grid-cols-2">
                {items.map((item) => {
                  const providerInfo = resolveProviderInfo(
                    item.provider_id,
                    activeProvidersMap,
                    historicalProviderNames,
                  );
                  return (
                    <section
                      className={cn(USAGE_CARD_CLASS, "min-w-0 space-y-2 p-4")}
                      key={item.id}
                    >
                      {item.provider_id === "*" ? (
                        <p className="text-xs text-amber-600 dark:text-amber-400">
                          该历史价格尚未绑定服务连接，暂不用于新调用。请编辑并选择具体连接。
                        </p>
                      ) : null}
                      <h5 className="break-all font-medium text-sm">{item.model_id}</h5>
                      <p
                        className="text-xs text-muted-foreground"
                        title={`${item.provider_id} / ${item.feature ?? "*"}`}
                      >
                        {providerInfo.name} · {formatFeatureLabel(item.feature ?? "*")} · v{item.version}
                      </p>
                      <p className="text-xs tabular-nums">
                        输入 {formatRateMoney(item.input_per_million, item.currency)} / 输出{" "}
                        {formatRateMoney(item.output_per_million, item.currency)}（每百万 Tokens）
                      </p>
                      <p className="text-xs text-muted-foreground tabular-nums">
                        缓存读取{" "}
                        {formatRateMoney(
                          item.cached_input_per_million ?? item.input_per_million,
                          item.currency,
                        )}{" "}
                        / 写入{" "}
                        {formatRateMoney(
                          item.cache_write_per_million ?? item.input_per_million,
                          item.currency,
                        )}{" "}
                        / 单次 {formatRateMoney(item.fixed_per_call, item.currency)}
                      </p>
                      {item.cached_input_per_million === null ||
                      item.cache_write_per_million === null ? (
                        <p className="text-[11px] text-muted-foreground">
                          未单列的缓存价格沿用输入价。
                        </p>
                      ) : null}
                      {Object.entries(item.rates_by_unit ?? {}).map(([unit, rate]) => (
                        <p className="text-xs" key={unit}>
                          每 {unit}：{formatRateMoney(rate, item.currency)}
                        </p>
                      ))}
                      {Object.keys(item.conditions ?? {}).length > 0 ? (
                        <p className="break-all font-mono text-[11px] text-muted-foreground">
                          {JSON.stringify(item.conditions)}
                        </p>
                      ) : null}
                      {canManage ? (
                        <div className="flex gap-2 pt-1">
                          <Button
                            onClick={() =>
                              setDraft({
                                ...item,
                                conditions: Object.fromEntries(
                                  Object.entries(item.conditions ?? {}).filter(
                                    ([name]) => name !== "native_schema",
                                  ),
                                ),
                              })
                            }
                            size="xs"
                            type="button"
                            variant="outline"
                          >
                            编辑
                          </Button>
                          <Button
                            disabled={remove.isPending}
                            onClick={() =>
                              confirm({
                                title: `删除手动价格「${item.model_id}」？`,
                                description: "删除后该模型将回退到价格目录或未定价状态。",
                                label: "确认删除",
                                destructive: true,
                                run: () => remove.mutate(item.id),
                              })
                            }
                            size="xs"
                            type="button"
                            variant="outline"
                          >
                            删除此价格
                          </Button>
                        </div>
                      ) : null}
                    </section>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                暂无 {scope.currency} 手动覆盖价格，默认按价格目录计价。
              </p>
            )
          }
        </QuerySection>
      </div>

      <div className="space-y-3">
        <ClearableSearchInput
          aria-label="搜索模型价格"
          onChange={(value) => {
            setSearch(value);
            setPage(0);
          }}
          placeholder="搜索供应商或模型"
          value={search}
        />
        <QuerySection loadingLabel="正在加载价格目录…" query={catalog}>
          {() => (
            <section className={cn(USAGE_CARD_CLASS, "overflow-x-auto")}>
              <table className="w-full min-w-[860px] text-left text-xs">
                <thead className="border-b bg-muted/35 text-muted-foreground">
                  <tr>
                    {[
                      "来源 / 供应商",
                      "模型",
                      "输入 / 输出（每百万）",
                      "缓存读取 / 写入（每百万）",
                      "固定费用 / 单位价",
                      "计价条件",
                      "",
                    ].map((label, index) => (
                      <th className="p-3 font-medium" key={index}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {rows
                    .slice(page * CATALOG_PAGE_SIZE, (page + 1) * CATALOG_PAGE_SIZE)
                    .map((item) => (
                      <tr className="align-top hover:bg-muted/20" key={item.catalog_id}>
                        <td className="p-3">
                          {item.source}
                          <br />
                          {item.provider_key}
                          <br />
                          <a
                            className="underline underline-offset-2"
                            href={item.source_url}
                            rel="noreferrer"
                            target="_blank"
                          >
                            原始来源
                          </a>
                        </td>
                        <td className="max-w-56 break-all p-3 font-mono">{item.model_id}</td>
                        <td className="whitespace-nowrap p-3 font-mono tabular-nums">
                          {formatRateMoney(item.native_input_per_million, item.currency)}
                          <br />
                          {formatRateMoney(item.native_output_per_million, item.currency)}
                        </td>
                        <td className="whitespace-nowrap p-3 font-mono tabular-nums">
                          {formatRateMoney(
                            item.native_cached_input_per_million ?? item.native_input_per_million,
                            item.currency,
                          )}
                          {item.native_cached_input_per_million === null ? (
                            <span className="block font-sans text-[11px] text-muted-foreground">
                              沿用输入价
                            </span>
                          ) : null}
                          <br />
                          {formatRateMoney(
                            item.native_cache_write_per_million ?? item.native_input_per_million,
                            item.currency,
                          )}
                          {item.native_cache_write_per_million === null ? (
                            <span className="block font-sans text-[11px] text-muted-foreground">
                              沿用输入价
                            </span>
                          ) : null}
                        </td>
                        <td className="whitespace-nowrap p-3 tabular-nums">
                          每次 {formatRateMoney(item.native_fixed_per_call, item.currency)}
                          {Object.entries(item.rates_by_unit).map(([unit, rate]) => (
                            <p key={unit}>
                              每 {unit}：{formatRateMoney(rate, item.currency)}
                            </p>
                          ))}
                        </td>
                        <td className="max-w-60 break-all p-3 font-mono text-[11px] text-muted-foreground">
                          {JSON.stringify(item.conditions)}
                        </td>
                        <td className="p-3">
                          {canManage ? (
                            <Button
                              onClick={() => copy(item)}
                              size="xs"
                              type="button"
                              variant="outline"
                            >
                              填入手动价格
                            </Button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </section>
          )}
        </QuerySection>
        <Pagination
          onPageChange={(nextPage) => setPage(nextPage - 1)}
          page={page + 1}
          pageSize={CATALOG_PAGE_SIZE}
          total={rows.length}
        />
      </div>

      <Dialog open={draft !== null} onOpenChange={(open) => !open && setDraft(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>原币手动价格 · {scope.currency}</DialogTitle>
            <DialogDescription>
              币种由当前窗口固定。请输入该供应商实际公布的价格。
            </DialogDescription>
          </DialogHeader>
          {draft ? (
            <form
              className="grid gap-3"
              key={JSON.stringify(draft)}
              onSubmit={(event) => {
                event.preventDefault();
                save.mutate(new FormData(event.currentTarget));
              }}
            >
              <ScopeFields
                feature={draft.feature}
                historicalProviderNames={historicalProviderNames}
                modelId={draft.model_id}
                providerId={draft.provider_id}
                providers={providers}
                requiredProvider
              />
              <div className="grid gap-3 sm:grid-cols-2">
                <MoneyInput
                  label="输入 / 百万 Tokens"
                  name="input"
                  required
                  value={draft.input_per_million}
                />
                <MoneyInput
                  label="输出 / 百万 Tokens"
                  name="output"
                  required
                  value={draft.output_per_million}
                />
                <MoneyInput
                  label="缓存读取 / 百万（空白沿用输入价）"
                  name="cached"
                  value={draft.cached_input_per_million}
                />
                <MoneyInput
                  label="缓存写入 / 百万（空白沿用输入价）"
                  name="write"
                  value={draft.cache_write_per_million}
                />
                <MoneyInput
                  label="单次固定费用"
                  name="fixed"
                  value={draft.fixed_per_call}
                />
              </div>
              <label className="grid gap-1 text-sm">
                计价条件
                <textarea
                  className="rounded-md border bg-background p-2 font-mono text-xs"
                  defaultValue={JSON.stringify(draft.conditions ?? {}, null, 2)}
                  name="conditions"
                  rows={3}
                />
              </label>
              <label className="grid gap-1 text-sm">
                非 Token 单位价格
                <textarea
                  className="rounded-md border bg-background p-2 font-mono text-xs"
                  defaultValue={JSON.stringify(draft.rates_by_unit ?? {}, null, 2)}
                  name="rates_by_unit"
                  rows={2}
                />
              </label>
              <Button disabled={save.isPending} type="submit">
                保存此价格
              </Button>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
