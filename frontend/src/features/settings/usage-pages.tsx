import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";
import {
  acknowledgeBudgetAlert, clearUsageEvents, createBudgetPolicy, deleteBudgetPolicy, getAlertEmailConfig,
  getModelsDevStatus, getUsageSummary, listBudgetAlerts, listBudgetPolicies, listBudgetStatuses, listManualPrices,
  listPriceCatalog, listProviders, listUsageEvents, refreshModelsDevSnapshot, removeManualPrice,
  sendTestAlertEmail, updateAlertEmailConfig, updateBudgetPolicy, upsertManualPrice, updateProvider,
} from "@/api";
import { authStore } from "@/api/auth-store";
import type { UsageFilters, UsageScope } from "@/api/usage";
import { useAuth } from "@/features/auth/auth-context-value";
import { ErrorState, LoadingState, PageFrame, PageIntro, SectionHeading, StatePill, Surface } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { billingQueryKey, workspaceQueryKey } from "@/lib/query-keys";
import { nativeMoney, validMoney } from "@/lib/money";
import { saveBlobViaNative } from "@/lib/native-download";
import type { Provider } from "@/types/providers";
import type { AlertEmailConfig, BillingCurrency, BudgetPolicy, ManualPriceUpsert, PriceCatalogItem, UsageEvent, UsageSummary } from "@/types/usage";

const PAGE_SIZE = 100;
function stamp(value: string) { return new Date(value).toLocaleString(); }
function field(form: FormData, key: string) { return String(form.get(key) ?? "").trim(); }
function optionalMoney(form: FormData, key: string) {
  const value = field(form, key);
  if (!value) return null;
  if (!validMoney(value)) throw new Error("请输入非负十进制金额，最多 25 位整数和 24 位小数");
  return value;
}
function csvCell(value: unknown) {
  const text = value === null || value === undefined ? "" : String(value);
  return '"' + (/^[=+@-]/.test(text) ? "'" + text : text).replaceAll('"', '""') + '"';
}
async function saveCsv(rows: unknown[][], filename: string) {
  const blob = new Blob(["\ufeff" + rows.map((row) => row.map(csvCell).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  if (await saveBlobViaNative(blob, filename)) return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function QuerySection<T>({ query, children }: { query: UseQueryResult<T, Error>; children: (data: T) => ReactNode }) {
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  if (query.isPending) return <LoadingState />;
  return <>{query.isFetching ? <p className="text-xs text-muted-foreground" role="status">正在更新…</p> : null}{children(query.data)}</>;
}
function MoneyInput({ name, label, value, required = false }: { name: string; label: string; value?: string | null; required?: boolean }) {
  return <label className="grid gap-1 text-sm">{label}<Input name={name} defaultValue={value ?? ""} inputMode="decimal" required={required} placeholder="0.00" /></label>;
}
function ScopeFields({ providers, providerId = "*", modelId = "*", feature = "*", requiredProvider = false }: { providers: Provider[]; providerId?: string; modelId?: string; feature?: string; requiredProvider?: boolean }) {
  return <>
    <label className="grid gap-1 text-sm">供应商实例<select name="provider_id" className="h-9 w-full rounded-md border bg-background px-2" defaultValue={providerId} required>
      {!requiredProvider ? <option value="*">全部供应商</option> : <option value="*">选择供应商实例</option>}
      {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.display_name}</option>)}
    </select></label>
    <label className="grid gap-1 text-sm">模型<Input name="model_id" defaultValue={modelId} required /></label>
    <label className="grid gap-1 text-sm">功能范围<Input name="feature" defaultValue={feature} required placeholder="* 表示该供应商的所有功能" /></label>
  </>;
}

export function UsagePage() {
  const auth = useAuth();
  const [params, setParams] = useSearchParams();
  const currency: BillingCurrency = params.get("currency") === "CNY" ? "CNY" : "USD";
  const client = useQueryClient();
  useEffect(() => {
    const legacy = new Set(["usage-summary", "usage-events", "usage", "price-catalog", "manual-prices", "budget-policies", "budget-status", "budget-alerts", "exchange-rate", "alert-email"]);
    client.removeQueries({ predicate: (query) => legacy.has(String(query.queryKey[0])) });
  }, [client]);
  return <PageFrame>
    <PageIntro title="用量与预算" description="USD 与人民币 CNY 分别计费、分别统计。价格和已发生费用保持供应商公布的币种。" />
    <div className="flex gap-2" role="group" aria-label="计费币种">
      {(["USD", "CNY"] as const).map((code) => <Button key={code} variant={code === currency ? "default" : "outline"} aria-pressed={code === currency} onClick={() => {
        const next = new URLSearchParams(params); next.set("currency", code); setParams(next);
      }}>{code === "USD" ? "美元 USD" : "人民币 CNY"}</Button>)}
    </div>
    <UsageWindow key={auth.userId + ":" + auth.workspaceId + ":" + currency}
      scope={{ workspaceId: auth.workspaceId, userId: auth.userId, currency }} />
  </PageFrame>;
}

function UsageWindow({ scope }: { scope: UsageScope }) {
  const { effectivePermissions, isSystemAdmin } = useAuth();
  const canManage = effectivePermissions.includes("workspace.manage");
  const client = useQueryClient();
  const key = (...parts: unknown[]) => billingQueryKey(scope.workspaceId, scope.userId ?? "", scope.currency, ...parts);
  const [filters, setFilters] = useState<UsageFilters>({});
  const [page, setPage] = useState(0);
  const [legacy, setLegacy] = useState(false);
  const [archived, setArchived] = useState<"exclude" | "include" | "only">("exclude");
  const [exporting, setExporting] = useState(false);
  const exportController = useRef<AbortController | null>(null);
  useEffect(() => () => exportController.current?.abort(), []);
  const providers = useQuery({
    queryKey: workspaceQueryKey(scope.workspaceId, "providers", scope.userId),
    queryFn: ({ signal }) => listProviders({ signal, headers: { "X-Workspace-ID": scope.workspaceId }, workspace: false }),
  });
  const summary = useQuery({ queryKey: key("summary", filters), queryFn: ({ signal }) => getUsageSummary(scope, filters, signal) });
  const events = useQuery({ queryKey: key("events", filters, page, archived, legacy),
    queryFn: ({ signal }) => listUsageEvents(scope, { ...filters, offset: page * PAGE_SIZE, limit: PAGE_SIZE, archived, unknown_currency: legacy }, signal) });
  const invalidate = () => client.invalidateQueries({ queryKey: workspaceQueryKey(scope.workspaceId, "usage", "native-v2", scope.userId) });
  const activeScope = () => {
    const session = authStore.getSession();
    return session?.workspaceId === scope.workspaceId && (session.userId ?? session.username) === scope.userId;
  };
  const archive = useMutation({ mutationFn: () => clearUsageEvents(scope, filters), onSuccess: async (result) => {
    await invalidate(); if (activeScope()) toast.success("已归档 " + result.archived_count + " 条，费用与预算记录保留");
  }, onError: (error) => toast.error(error.message) });
  const filename = (kind: string) => "usage-" + scope.workspaceId + "-" + (legacy && kind === "events" ? "legacy-unknown" : scope.currency) + "-" +
    (filters.start_at?.slice(0, 10) ?? "all") + "-" + (filters.end_at?.slice(0, 10) ?? "now") + "-" + kind + ".csv";
  async function exportEvents() {
    const controller = new AbortController(); exportController.current = controller; setExporting(true);
    try {
      const all: UsageEvent[] = [];
      for (let offset = 0; ; offset += 1000) {
        const rows = await listUsageEvents(scope, { ...filters, archived, unknown_currency: legacy, limit: 1000, offset }, controller.signal);
        all.push(...rows); if (rows.length < 1000) break;
      }
      if (!activeScope() || controller.signal.aborted) return;
      await saveCsv([
        ["workspace", "currency", "id", "created_at_utc", "provider", "model", "feature", "amount", "status", "source", "input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens", "attempt", "price_version", "units", "archived_at"],
        ...all.map((row) => [row.workspace_id, row.currency, row.id, row.created_at, row.provider_id, row.model_id, row.feature,
          row.cost_amount, row.cost_status, row.amount_source, row.input_tokens, row.cached_input_tokens, row.cache_creation_input_tokens,
          row.output_tokens, row.reasoning_tokens, row.attempt, row.price_version_id, JSON.stringify(row.usage_units), row.archived_at]),
      ], filename("events"));
    } catch (error) { if (!controller.signal.aborted) toast.error((error as Error).message); }
    finally { setExporting(false); }
  }
  function updateFilter(name: keyof UsageFilters, value: string) { setFilters((current) => ({ ...current, [name]: value || undefined })); setPage(0); }
  return <>
    <Surface className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3" aria-label="用量筛选">
      <label className="grid min-w-0 gap-1 text-xs">供应商<select className="h-9 min-w-0 w-full rounded-md border bg-background px-2" value={filters.provider_id ?? ""} onChange={(event) => updateFilter("provider_id", event.target.value)}>
        <option value="">全部</option>{(providers.data ?? []).map((provider) => <option key={provider.id} value={provider.id}>{provider.display_name}</option>)}
      </select></label>
      <label className="grid gap-1 text-xs">模型<Input value={filters.model_id ?? ""} onChange={(event) => updateFilter("model_id", event.target.value)} placeholder="模型 ID" /></label>
      <label className="grid gap-1 text-xs">功能<Input value={filters.feature ?? ""} onChange={(event) => updateFilter("feature", event.target.value)} placeholder="全部功能" /></label>
      <label className="grid gap-1 text-xs">开始日期 UTC（含）<Input type="date" value={filters.start_at?.slice(0, 10) ?? ""} onChange={(event) => updateFilter("start_at", event.target.value ? event.target.value + "T00:00:00Z" : "")} /></label>
      <label className="grid gap-1 text-xs">结束日期 UTC（不含）<Input type="date" value={filters.end_at?.slice(0, 10) ?? ""} onChange={(event) => updateFilter("end_at", event.target.value ? event.target.value + "T00:00:00Z" : "")} /></label>
    </Surface>
    {providers.isError ? <p role="alert" className="text-sm text-destructive">供应商列表：{providers.error.message}</p> : null}
    <QuerySection query={summary}>{(data) => <>
      <div className="grid gap-3 sm:grid-cols-2">
        {[["已记录费用", nativeMoney(data.cost_amount, data.currency), "包含归档记录；未知金额另列"],
          ["预算占用", nativeMoney(data.reserved_amount, data.currency), "执行中的调用及未核实金额的占用"],
          ["实际调用次数", String(data.attempts), data.total_tokens.toLocaleString() + " Tokens"],
          ["待核实金额", String(data.unpriced_events), "另有 " + data.unknown_currency_events + " 条记录缺少明确币种"]].map(([label, value, hint]) =>
          <Surface key={label} className="min-w-0 p-4"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-2 break-all text-xl font-semibold tabular-nums">{value}</p><p className="mt-1 text-xs text-muted-foreground">{hint}</p></Surface>)}
      </div>
      <p className="text-xs text-muted-foreground">{data.unpriced_events || data.unknown_currency_events ? "已记录费用为已知金额之和，不能视为完整账单。" : "费用均为原币金额。"} 供应商账户余额、订阅额度与这里的费用和预算分别展示。</p>
    </>}</QuerySection>
    <Tabs defaultValue="overview">
      <TabsList className="flex h-auto flex-wrap">
        <TabsTrigger value="overview">统计概览</TabsTrigger><TabsTrigger value="budget">预算与告警</TabsTrigger>
        <TabsTrigger value="catalog">价格目录</TabsTrigger><TabsTrigger value="events">用量明细</TabsTrigger>
      </TabsList>
      <TabsContent value="overview"><QuerySection query={summary}>{(data) => <Overview data={data} onExport={() => void saveCsv([
        ["workspace", "currency", "start_inclusive", "end_exclusive", "amount", "unknown_events", "tokens", "attempts"],
        [scope.workspaceId, scope.currency, filters.start_at, filters.end_at, data.cost_amount, data.unpriced_events, data.total_tokens, data.attempts],
        ["dimension", "label", "currency", "amount", "unknown_events", "tokens", "attempts"],
        ...Object.entries(data.breakdown).flatMap(([dimension, rows]) => rows.map((row) => [dimension, row.label, scope.currency, row.cost_amount, row.unknown_events, row.total_tokens, row.attempts])),
      ], filename("overview"))} />}</QuerySection></TabsContent>
      <TabsContent value="budget"><BudgetPanel scope={scope} canManage={canManage} providers={providers.data ?? []} /></TabsContent>
      <TabsContent value="catalog"><CatalogPanel scope={scope} canManage={canManage} canRefresh={isSystemAdmin && effectivePermissions.includes("workspace.write")} providers={providers.data ?? []} /></TabsContent>
      <TabsContent value="events" className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-sm"><input type="checkbox" checked={legacy} onChange={(event) => { setLegacy(event.target.checked); setPage(0); }} /> 查看币种待核实的记录</label>
          <select aria-label="归档筛选" className="h-9 rounded-md border bg-background px-2 text-sm" value={archived} onChange={(event) => { setArchived(event.target.value as typeof archived); setPage(0); }}>
            <option value="exclude">未归档</option><option value="include">包含归档</option><option value="only">已归档</option>
          </select>
          <Button variant="outline" disabled={exporting} onClick={() => void exportEvents()}>{exporting ? "导出中…" : "导出全部筛选明细"}</Button>
          {canManage && !legacy ? <Button variant="outline" disabled={archive.isPending} onClick={() => {
            if (window.confirm("归档当前 " + scope.currency + " 筛选范围的明细？账本费用和预算不会重置。")) archive.mutate();
          }}>归档当前范围</Button> : null}
        </div>
        <QuerySection query={events}>{(rows) => <EventTable rows={rows} />}</QuerySection>
        <div className="flex items-center justify-end gap-3 text-sm"><Button size="sm" variant="outline" disabled={!page} onClick={() => setPage(page - 1)}>上一页</Button>
          <span>第 {page + 1} 页</span><Button size="sm" variant="outline" disabled={(events.data?.length ?? 0) < PAGE_SIZE} onClick={() => setPage(page + 1)}>下一页</Button></div>
      </TabsContent>
    </Tabs>
  </>;
}
function Overview({ data, onExport }: { data: UsageSummary; onExport: () => void }) {
  const days = (data.breakdown.day ?? []).map((row) => ({ ...row, chart_amount: Number(row.cost_amount) }));
  return <div className="space-y-4">
    <Surface className="p-4"><SectionHeading title={data.currency + " 每日费用"} description="统计包含全部筛选记录与归档记录，日期按 UTC。" action={<Button variant="outline" onClick={onExport}>导出概览</Button>} />
      {days.length ? <div className="mt-4 h-64"><ResponsiveContainer width="100%" height="100%"><AreaChart data={days}>
        <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis />
        <Tooltip content={({ active, payload }) => active && payload?.[0] ? <div className="rounded border bg-background p-2 text-sm">{payload[0].payload.label}<br />{nativeMoney(payload[0].payload.cost_amount, data.currency)}</div> : null} />
        <Area dataKey="chart_amount" stroke="var(--color-primary)" fill="var(--color-primary)" fillOpacity={0.12} />
      </AreaChart></ResponsiveContainer></div> : <p className="py-8 text-center text-sm text-muted-foreground">当前范围暂无已结算用量</p>}
    </Surface>
    <div className="grid gap-4 lg:grid-cols-3">{([["provider_id", "供应商统计"], ["model_id", "模型统计"], ["feature", "功能统计"]] as const).map(([dimension, title]) =>
      <Surface className="min-w-0 p-4" key={dimension}><SectionHeading title={title} /><div className="mt-3 space-y-3">
        {(data.breakdown[dimension] ?? []).map((row) => <div key={row.label} className="border-b pb-2 text-sm"><p className="break-all">{row.label}</p>
          <p className="mt-1 break-all font-mono">{nativeMoney(row.cost_amount, data.currency)}</p><p className="text-xs text-muted-foreground">{row.attempts} 次 · {row.total_tokens.toLocaleString()} Tokens{row.unknown_events ? " · " + row.unknown_events + " 条待核实" : ""}</p></div>)}
      </div></Surface>)}</div>
  </div>;
}
function EventTable({ rows }: { rows: UsageEvent[] }) {
  if (!rows.length) return <Surface className="p-8 text-center text-sm text-muted-foreground">当前范围暂无用量记录</Surface>;
  return <Surface className="overflow-x-auto"><table className="w-full min-w-[950px] text-left text-xs"><thead><tr>
    {["时间", "供应商 / 模型", "功能", "原币费用", "状态 / 来源", "输入 / 缓存 / 写入", "输出 / 推理", "版本 / 单位"].map((label) => <th className="p-3" key={label}>{label}</th>)}
  </tr></thead><tbody>{rows.map((row) => <tr className="border-t align-top" key={row.id}>
    <td className="p-3">{stamp(row.created_at)}{row.archived_at ? <p>已归档</p> : null}</td><td className="max-w-52 break-all p-3">{row.provider_id}<br />{row.model_id}</td>
    <td className="p-3">{row.feature}<p>第 {row.attempt} 次尝试</p></td><td className="whitespace-nowrap p-3 font-mono">{nativeMoney(row.cost_amount, row.currency)}</td>
    <td className="max-w-48 break-all p-3">{row.cost_status}<p className="text-muted-foreground">{row.amount_source}</p></td>
    <td className="p-3">{row.input_tokens} / {row.cached_input_tokens} / {row.cache_creation_input_tokens}</td><td className="p-3">{row.output_tokens} / {row.reasoning_tokens}</td>
    <td className="max-w-40 break-all p-3">{row.price_version_id ?? "—"}<p>{JSON.stringify(row.usage_units)}</p>
      {Object.keys(row.legacy_billing_snapshot).length ? <details><summary>历史证据</summary><pre className="max-w-64 whitespace-pre-wrap">{JSON.stringify(row.legacy_billing_snapshot, null, 2)}</pre></details> : null}</td>
  </tr>)}</tbody></table></Surface>;
}
function BudgetPanel({ scope, canManage, providers }: { scope: UsageScope; canManage: boolean; providers: Provider[] }) {
  const client = useQueryClient();
  const key = (...parts: unknown[]) => billingQueryKey(scope.workspaceId, scope.userId ?? "", scope.currency, ...parts);
  const policies = useQuery({ queryKey: key("policies"), queryFn: ({ signal }) => listBudgetPolicies(scope, signal) });
  const statuses = useQuery({ queryKey: key("statuses"), queryFn: ({ signal }) => listBudgetStatuses(scope, signal) });
  const alerts = useQuery({ queryKey: key("alerts"), queryFn: ({ signal }) => listBudgetAlerts(scope, signal) });
  const [editing, setEditing] = useState<BudgetPolicy | "new" | null>(null);
  const invalidate = () => client.invalidateQueries({ queryKey: workspaceQueryKey(scope.workspaceId, "usage", "native-v2", scope.userId) });
  const save = useMutation({ mutationFn: async (form: FormData) => {
    const payload = { name: field(form, "name"), soft_limit: optionalMoney(form, "soft_limit"), hard_limit: optionalMoney(form, "hard_limit"), enabled: form.get("enabled") === "on", currency: scope.currency };
    if (editing && editing !== "new") return updateBudgetPolicy(editing.id, payload, scope);
    return createBudgetPolicy({ ...payload, provider_id: field(form, "provider_id"), model_id: field(form, "model_id"),
      feature: field(form, "feature"), period: field(form, "period") as BudgetPolicy["period"] }, scope);
  }, onSuccess: async () => { setEditing(null); await invalidate(); }, onError: (error) => toast.error(error.message) });
  const disable = useMutation({ mutationFn: (id: string) => deleteBudgetPolicy(id, scope), onSuccess: invalidate, onError: (error) => toast.error(error.message) });
  const ack = useMutation({ mutationFn: (id: string) => acknowledgeBudgetAlert(id, scope), onSuccess: invalidate, onError: (error) => toast.error(error.message) });
  const policy = editing && editing !== "new" ? editing : null;
  return <div className="space-y-4">
    <SectionHeading title={scope.currency + " 预算"} description="同币种检查已用金额与执行占用。周期按 UTC 重置。" action={canManage ? <Button onClick={() => setEditing("new")}>新建预算</Button> : null} />
    <QuerySection query={policies}>{(items) => <div className="grid gap-3 sm:grid-cols-2">{items.map((item) => <Surface className="min-w-0 p-4" key={item.id}>
      <div className="flex items-center justify-between gap-2"><h3 className="font-medium">{item.name}</h3><StatePill status={item.enabled ? "enabled" : "disabled"} /></div>
      <p className="mt-1 break-all text-xs text-muted-foreground">{item.provider_id} / {item.model_id} / {item.feature}</p>
      <p className="mt-3 break-all text-sm">软限额 {item.soft_limit === null ? "未设置" : nativeMoney(item.soft_limit, item.currency)} · 硬限额 {item.hard_limit === null ? "未设置" : nativeMoney(item.hard_limit, item.currency)}</p>
      {canManage ? <div className="mt-3 flex gap-2"><Button size="sm" variant="outline" onClick={() => setEditing(item)}>编辑</Button>
        <Button size="sm" variant="outline" disabled={!item.enabled || disable.isPending} onClick={() => disable.mutate(item.id)}>停用</Button></div> : null}
    </Surface>)}</div>}</QuerySection>
    <QuerySection query={statuses}>{(items) => <div className="grid gap-3 sm:grid-cols-2">{items.map((item) => <Surface className="min-w-0 p-4" key={item.policy_id}>
      <h3 className="font-medium">{item.name} · 本周期</h3><p className="mt-1 break-all text-sm">已用 {nativeMoney(item.spent_amount, item.currency)} / 占用 {nativeMoney(item.reserved_amount, item.currency)}</p>
      <p className="mt-1 break-all text-sm">剩余 {item.hard_limit === null ? "未设置硬限额" : nativeMoney(item.remaining_amount, item.currency)}</p>
      <Progress className="mt-3" value={(item.usage_ratio ?? 0) * 100} />
      {item.unknown_events ? <p className="mt-2 text-sm text-amber-600">有 {item.unknown_events} 条金额待核实，硬预算暂停授权新的调用。</p> : null}
      <p className="mt-2 text-xs text-muted-foreground">重置时间：{stamp(item.period_end)}（本地时间）</p>
    </Surface>)}</div>}</QuerySection>
    <Surface className="space-y-3 p-4"><SectionHeading title="本币种告警" /><QuerySection query={alerts}>{(items) => items.length ? items.map((item) =>
      <div className="flex flex-wrap items-start justify-between gap-2 border-t pt-3" key={item.id}><div className="min-w-0 text-sm">
        <p>{item.level === "hard" ? "硬限额阻断" : "软限额提醒"} · {item.status}</p><p className="break-all">已用 {nativeMoney(item.spent_amount, item.currency)}，占用 {nativeMoney(item.reserved_amount, item.currency)}，本次 {nativeMoney(item.projected_amount, item.currency)}，限额 {nativeMoney(item.limit_amount, item.currency)}</p>
        <p className="text-xs text-muted-foreground">{stamp(item.created_at)}</p></div>{canManage && item.status !== "acknowledged" ? <Button size="sm" variant="outline" disabled={ack.isPending} onClick={() => ack.mutate(item.id)}>确认告警</Button> : null}
      </div>) : <p className="text-sm text-muted-foreground">暂无告警</p>}</QuerySection></Surface>
    {canManage ? <EmailPanel scope={scope} /> : <p className="text-xs text-muted-foreground">预算和价格配置由拥有工作区管理权限的用户维护。</p>}
    <Dialog open={editing !== null} onOpenChange={(open) => { if (!open) setEditing(null); }}><DialogContent className="max-h-[85vh] overflow-y-auto"><DialogHeader>
      <DialogTitle>{policy ? "编辑预算" : "新建预算"} · {scope.currency}</DialogTitle><DialogDescription>币种创建后固定。USD 与 CNY 各自设置限额。</DialogDescription></DialogHeader>
      <form key={policy?.id ?? "new"} className="grid gap-3" onSubmit={(event) => { event.preventDefault(); save.mutate(new FormData(event.currentTarget)); }}>
        <label className="grid gap-1 text-sm">名称<Input name="name" defaultValue={policy?.name ?? ""} required /></label>
        {!policy ? <><ScopeFields providers={providers} /><label className="grid gap-1 text-sm">周期<select name="period" className="h-9 rounded-md border bg-background px-2"><option value="calendar_month_utc">自然月 UTC</option><option value="calendar_day_utc">自然日 UTC</option></select></label></> : null}
        <MoneyInput name="soft_limit" label={"软限额 " + scope.currency} value={policy?.soft_limit} /><MoneyInput name="hard_limit" label={"硬限额 " + scope.currency} value={policy?.hard_limit} />
        <label className="text-sm"><input name="enabled" type="checkbox" defaultChecked={policy?.enabled ?? true} /> 启用</label>
        <Button disabled={save.isPending}>保存预算</Button>
      </form>
    </DialogContent></Dialog>
  </div>;
}
function CatalogPanel({ scope, canManage, canRefresh, providers }: { scope: UsageScope; canManage: boolean; canRefresh: boolean; providers: Provider[] }) {
  const client = useQueryClient();
  const key = (...parts: unknown[]) => billingQueryKey(scope.workspaceId, scope.userId ?? "", scope.currency, ...parts);
  const catalog = useQuery({ queryKey: key("catalog"), queryFn: ({ signal }) => listPriceCatalog(scope, signal) });
  const manual = useQuery({ queryKey: key("manual"), queryFn: ({ signal }) => listManualPrices(scope, signal) });
  const status = useQuery({ queryKey: key("models-dev"), queryFn: ({ signal }) => getModelsDevStatus(scope, signal) });
  const [search, setSearch] = useState(""); const [page, setPage] = useState(0);
  const [draft, setDraft] = useState<ManualPriceUpsert | null>(null);
  const binding = useMutation({ mutationFn: (provider: Provider) => updateProvider(provider.id,
    { billing_currency: scope.currency },
    { workspace: false, headers: { "X-Workspace-ID": scope.workspaceId } }),
    onSuccess: () => client.invalidateQueries({ queryKey: workspaceQueryKey(scope.workspaceId) }),
    onError: (error) => toast.error(error.message) });
  const invalidate = () => client.invalidateQueries({ queryKey: workspaceQueryKey(scope.workspaceId, "usage", "native-v2", scope.userId) });
  const refresh = useMutation({ mutationFn: () => refreshModelsDevSnapshot(scope), onSuccess: invalidate, onError: (error) => toast.error(error.message) });
  const remove = useMutation({ mutationFn: (id: string) => removeManualPrice(id, scope), onSuccess: invalidate, onError: (error) => toast.error(error.message) });
  const save = useMutation({ mutationFn: (form: FormData) => {
    const providerId = field(form, "provider_id"); if (providerId === "*") throw new Error("请选择具体供应商实例");
    let conditions: Record<string, unknown>, units: Record<string, string>;
    try { conditions = JSON.parse(field(form, "conditions") || "{}"); units = JSON.parse(field(form, "rates_by_unit") || "{}"); } catch { throw new Error("计价条件和单位价格必须是 JSON 对象"); }
    if (!conditions || Array.isArray(conditions) || typeof conditions !== "object" || !units || Array.isArray(units) || typeof units !== "object") throw new Error("请输入 JSON 对象");
    if (Object.values(units).some((value) => typeof value !== "string" || !validMoney(value))) throw new Error('单位价格使用非负金额字符串，例如 {"seconds":"0.02"}');
    return upsertManualPrice({ provider_id: providerId, model_id: field(form, "model_id"), feature: field(form, "feature"), currency: scope.currency,
      input_per_million: optionalMoney(form, "input") ?? "0", cached_input_per_million: optionalMoney(form, "cached"),
      cache_write_per_million: optionalMoney(form, "write"), output_per_million: optionalMoney(form, "output") ?? "0",
      fixed_per_call: optionalMoney(form, "fixed") ?? "0", conditions, rates_by_unit: units }, scope);
  }, onSuccess: async () => { setDraft(null); await invalidate(); }, onError: (error) => toast.error(error.message) });
  const rows = useMemo(() => (catalog.data ?? []).filter((item) => (item.provider_key + " " + item.model_id).toLowerCase().includes(search.toLowerCase())), [catalog.data, search]);
  function copy(item?: PriceCatalogItem) {
    setDraft({ provider_id: "*", model_id: item?.model_id ?? "", feature: "*", currency: scope.currency,
      input_per_million: item?.native_input_per_million ?? "0", cached_input_per_million: item?.native_cached_input_per_million,
      cache_write_per_million: item?.native_cache_write_per_million, output_per_million: item?.native_output_per_million ?? "0",
      fixed_per_call: item?.native_fixed_per_call ?? "0", conditions: item?.conditions ?? {}, rates_by_unit: item?.rates_by_unit ?? {} });
  }
  return <div className="space-y-4">
    <Surface className="space-y-3 p-4"><SectionHeading title="价格来源" description="只展示该币种的原价；按实际供应商实例、模型和计价条件匹配。" action={canRefresh ? <Button variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}>刷新 models.dev</Button> : null} />
      <QuerySection query={status}>{(value) => <p className="text-sm">共享来源快照：{value.origin} · 全部币种共 {value.priced_model_count} 条价格记录 · {value.fetched_at ? stamp(value.fetched_at) : "随项目提供的快照"}</p>}</QuerySection>
    </Surface>
    <Surface className="space-y-3 p-4"><SectionHeading title="供应商结算币种" description="有两种原币报价时，按供应商实际结算币种选择；已有账单保留调用时的币种。" />
      {providers.filter((provider) => provider.remote_capability).map((provider) => <div className="flex flex-wrap items-center justify-between gap-2 text-sm" key={provider.id}>
        <span>{provider.display_name} · {String(provider.capabilities.billing_currency ?? "按模型原价")}</span>
        {canManage && provider.capabilities.billing_currency !== scope.currency ? <Button size="sm" variant="outline" disabled={binding.isPending} onClick={() => binding.mutate(provider)}>设置为 {scope.currency}</Button> : null}
      </div>)}
    </Surface>
    <SectionHeading title={scope.currency + " 手动价格"} description="手动价格仅覆盖所选供应商、币种、功能和计价条件。删除按价格版本执行。" action={canManage ? <Button onClick={() => copy()}>添加手动价格</Button> : null} />
    <QuerySection query={manual}>{(items) => <div className="grid gap-3 sm:grid-cols-2">{items.map((item) => <Surface className="min-w-0 space-y-2 p-4" key={item.id}>
      {item.provider_id === "*" ? <p className="text-sm text-amber-600">该历史价格尚未绑定供应商实例，暂不用于新调用。请编辑并选择具体供应商。</p> : null}
      <h3 className="break-all font-medium">{item.model_id}</h3><p className="break-all text-xs">{item.provider_id} / {item.feature} · v{item.version}</p>
      <p className="break-all text-sm">输入 {nativeMoney(item.input_per_million, item.currency)} / 输出 {nativeMoney(item.output_per_million, item.currency)} 每百万 Tokens</p>
      <p className="break-all text-xs">缓存读取 {nativeMoney(item.cached_input_per_million ?? item.input_per_million, item.currency)} / 写入 {nativeMoney(item.cache_write_per_million ?? item.input_per_million, item.currency)} / 单次 {nativeMoney(item.fixed_per_call, item.currency)}</p>
      {item.cached_input_per_million === null || item.cache_write_per_million === null ? <p className="text-xs text-muted-foreground">未单列的缓存价格沿用输入价。</p> : null}
      {Object.entries(item.rates_by_unit ?? {}).map(([unit, rate]) => <p className="text-xs" key={unit}>每 {unit}：{nativeMoney(rate, item.currency)}</p>)}
      <p className="break-all text-xs text-muted-foreground">{JSON.stringify(item.conditions)}</p>
      {canManage ? <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setDraft({ ...item, conditions: Object.fromEntries(Object.entries(item.conditions ?? {}).filter(([name]) => name !== "native_schema")) })}>编辑</Button><Button size="sm" variant="outline" disabled={remove.isPending} onClick={() => remove.mutate(item.id)}>删除此价格</Button></div> : null}
    </Surface>)}</div>}</QuerySection>
    <Input aria-label="搜索模型价格" value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="搜索供应商或模型" />
    <QuerySection query={catalog}>{() => <Surface className="overflow-x-auto"><table className="w-full min-w-[900px] text-left text-xs"><thead><tr>
      {["来源 / 供应商", "模型", "输入 / 输出（每百万）", "缓存读取 / 写入（每百万）", "固定费用 / 单位价", "计价条件", ""].map((label, index) => <th key={index} className="p-3">{label}</th>)}
    </tr></thead><tbody>{rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((item) => <tr key={item.catalog_id} className="border-t align-top">
      <td className="p-3">{item.source}<br />{item.provider_key}<br /><a className="underline" href={item.source_url} target="_blank" rel="noreferrer">原始来源</a></td>
      <td className="max-w-56 break-all p-3">{item.model_id}</td><td className="whitespace-nowrap p-3">{nativeMoney(item.native_input_per_million, item.currency)}<br />{nativeMoney(item.native_output_per_million, item.currency)}</td>
      <td className="whitespace-nowrap p-3">{nativeMoney(item.native_cached_input_per_million ?? item.native_input_per_million, item.currency)}{item.native_cached_input_per_million === null ? <span className="block text-muted-foreground">未单列，沿用输入价</span> : null}<br />{nativeMoney(item.native_cache_write_per_million ?? item.native_input_per_million, item.currency)}{item.native_cache_write_per_million === null ? <span className="block text-muted-foreground">未单列，沿用输入价</span> : null}</td>
      <td className="whitespace-nowrap p-3">每次 {nativeMoney(item.native_fixed_per_call, item.currency)}{Object.entries(item.rates_by_unit).map(([unit, rate]) => <p key={unit}>每 {unit}：{nativeMoney(rate, item.currency)}</p>)}</td>
      <td className="max-w-60 break-all p-3">{JSON.stringify(item.conditions)}</td><td className="p-3">{canManage ? <Button size="sm" variant="outline" onClick={() => copy(item)}>填入手动价格</Button> : null}</td>
    </tr>)}</tbody></table></Surface>}</QuerySection>
    <div className="flex justify-end gap-3 text-sm"><Button variant="outline" size="sm" disabled={!page} onClick={() => setPage(page - 1)}>上一页</Button><span>{rows.length} 条 · 第 {page + 1} 页</span><Button variant="outline" size="sm" disabled={(page + 1) * PAGE_SIZE >= rows.length} onClick={() => setPage(page + 1)}>下一页</Button></div>
    <Dialog open={draft !== null} onOpenChange={(open) => { if (!open) setDraft(null); }}><DialogContent className="max-h-[85vh] overflow-y-auto"><DialogHeader><DialogTitle>原币手动价格 · {scope.currency}</DialogTitle><DialogDescription>币种由当前窗口固定。请输入该供应商实际公布的价格。</DialogDescription></DialogHeader>
      {draft ? <form key={JSON.stringify(draft)} className="grid gap-3" onSubmit={(event) => { event.preventDefault(); save.mutate(new FormData(event.currentTarget)); }}>
        <ScopeFields providers={providers} requiredProvider providerId={draft.provider_id} modelId={draft.model_id} feature={draft.feature} />
        <div className="grid gap-3 sm:grid-cols-2"><MoneyInput name="input" label="输入 / 百万 Tokens" value={draft.input_per_million} required /><MoneyInput name="output" label="输出 / 百万 Tokens" value={draft.output_per_million} required />
          <MoneyInput name="cached" label="缓存读取 / 百万（空白沿用输入价）" value={draft.cached_input_per_million} /><MoneyInput name="write" label="缓存写入 / 百万（空白沿用输入价）" value={draft.cache_write_per_million} /><MoneyInput name="fixed" label="单次固定费用" value={draft.fixed_per_call} /></div>
        <label className="grid gap-1 text-sm">计价条件<textarea className="rounded-md border bg-background p-2 text-xs" name="conditions" defaultValue={JSON.stringify(draft.conditions ?? {}, null, 2)} rows={3} /></label>
        <label className="grid gap-1 text-sm">非 Token 单位价格<textarea className="rounded-md border bg-background p-2 text-xs" name="rates_by_unit" defaultValue={JSON.stringify(draft.rates_by_unit ?? {}, null, 2)} rows={2} /></label>
        <Button disabled={save.isPending}>保存此价格</Button>
      </form> : null}
    </DialogContent></Dialog>
  </div>;
}
function EmailPanel({ scope }: { scope: UsageScope }) {
  const client = useQueryClient();
  const key = workspaceQueryKey(scope.workspaceId, "usage", "native-v2", scope.userId, "alert-email-settings");
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => getAlertEmailConfig(scope, signal), retry: false });
  const save = useMutation({ mutationFn: (form: FormData) => updateAlertEmailConfig({
    enabled: form.get("enabled") === "on", smtp_host: field(form, "host"), smtp_port: Number(field(form, "port")),
    smtp_security: field(form, "security") as AlertEmailConfig["smtp_security"], smtp_username: field(form, "username"),
    smtp_password: form.get("clear_password") === "on" ? "" : field(form, "password") || null,
    from_address: field(form, "from"), to_addresses: field(form, "to").split(/[,;\s]+/).filter(Boolean),
  }, scope), onSuccess: () => client.invalidateQueries({ queryKey: key }), onError: (error) => toast.error(error.message) });
  const test = useMutation({ mutationFn: () => sendTestAlertEmail(scope), onSuccess: (result) => result.ok ? toast.success(result.detail) : toast.error(result.detail), onError: (error) => toast.error(error.message) });
  return <Surface className="space-y-3 p-4"><SectionHeading title="预算邮件通知" description="工作区共用邮件配置；告警邮件注明原币金额。" />
    <QuerySection query={query}>{(config) => <form key={JSON.stringify(config)} className="grid gap-3 sm:grid-cols-2" onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); save.mutate(new FormData(event.currentTarget)); }}>
      <label className="text-sm sm:col-span-2"><input type="checkbox" name="enabled" defaultChecked={config.enabled} /> 启用邮件通知</label>
      {[["host", "SMTP 主机", config.smtp_host], ["port", "端口", String(config.smtp_port)], ["username", "用户名", config.smtp_username], ["from", "发件地址", config.from_address], ["to", "收件地址（逗号分隔）", config.to_addresses.join(",")]].map(([name, label, value]) =>
        <label className="grid gap-1 text-sm" key={name}>{label}<Input name={name} defaultValue={value} /></label>)}
      <label className="grid gap-1 text-sm">安全连接<select name="security" className="h-9 rounded-md border bg-background px-2" defaultValue={config.smtp_security}><option value="ssl">SSL</option><option value="starttls">STARTTLS</option><option value="none">无</option></select></label>
      <label className="grid gap-1 text-sm">SMTP 密码<Input type="password" name="password" autoComplete="new-password" placeholder={config.has_password ? "已保存，留空保留" : "未配置"} /></label>
      <label className="text-sm"><input type="checkbox" name="clear_password" /> 清除已存密码</label>
      <div className="flex gap-2 sm:col-span-2"><Button disabled={save.isPending}>保存邮件配置</Button><Button type="button" variant="outline" disabled={test.isPending} onClick={() => test.mutate()}>发送测试邮件</Button></div>
    </form>}</QuerySection>
  </Surface>;
}
