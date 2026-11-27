import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import {
  acknowledgeBudgetAlert,
  createBudgetPolicy,
  deleteBudgetPolicy,
  getAlertEmailConfig,
  listBudgetAlerts,
  listBudgetPolicies,
  listBudgetStatuses,
  sendTestAlertEmail,
  updateAlertEmailConfig,
  updateBudgetPolicy,
} from "@/api";
import type { UsageScope } from "@/api/usage";
import { StatePill } from "@/components/shared/page-elements";
import { useActionConfirmation } from "@/components/shared/use-action-confirmation";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { billingQueryKey, workspaceQueryKey } from "@/lib/query-keys";
import { toast } from "@/lib/notifications";
import { cn } from "@/lib/utils";
import type { Provider } from "@/types/providers";
import type {
  AlertEmailConfig,
  BillingCurrency,
  BudgetPolicy,
  BudgetStatus,
} from "@/types/usage";
import {
  USAGE_CARD_CLASS,
  field,
  formatDisplayMoney,
  formatExactMoney,
  formatFeatureLabel,
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

export function BudgetPanel({
  scope,
  canManage,
  providers,
  historicalProviderNames,
  onCurrencyChange,
}: {
  scope: UsageScope;
  canManage: boolean;
  providers: Provider[];
  historicalProviderNames: Readonly<Record<string, string>>;
  onCurrencyChange: (next: BillingCurrency) => void;
}) {
  const client = useQueryClient();
  const { confirm, dialog } = useActionConfirmation();
  const key = (...parts: unknown[]) =>
    billingQueryKey(scope.workspaceId, scope.userId ?? "", scope.currency, ...parts);
  const policies = useQuery({
    queryKey: key("policies"),
    queryFn: ({ signal }) => listBudgetPolicies(scope, signal),
  });
  const statuses = useQuery({
    queryKey: key("statuses"),
    queryFn: ({ signal }) => listBudgetStatuses(scope, signal),
  });
  const alerts = useQuery({
    queryKey: key("alerts"),
    queryFn: ({ signal }) => listBudgetAlerts(scope, signal),
  });
  const [editing, setEditing] = useState<BudgetPolicy | "new" | null>(null);
  const invalidate = () =>
    client.invalidateQueries({
      queryKey: workspaceQueryKey(scope.workspaceId, "usage", "native-v2", scope.userId),
    });

  const save = useMutation({
    mutationFn: async (form: FormData) => {
      const payload = {
        name: field(form, "name"),
        soft_limit: optionalMoney(form, "soft_limit"),
        hard_limit: optionalMoney(form, "hard_limit"),
        enabled: form.get("enabled") === "on",
        currency: scope.currency,
      };
      if (editing && editing !== "new") {
        return updateBudgetPolicy(editing.id, payload, scope);
      }
      return createBudgetPolicy(
        {
          ...payload,
          provider_id: field(form, "provider_id"),
          model_id: field(form, "model_id"),
          feature: field(form, "feature"),
          period: field(form, "period") as BudgetPolicy["period"],
        },
        scope,
      );
    },
    onSuccess: async () => {
      setEditing(null);
      await invalidate();
    },
    onError: (error) => toast.error(error.message),
  });

  const disable = useMutation({
    mutationFn: (id: string) => deleteBudgetPolicy(id, scope),
    onSuccess: invalidate,
    onError: (error) => toast.error(error.message),
  });

  const ack = useMutation({
    mutationFn: (id: string) => acknowledgeBudgetAlert(id, scope),
    onSuccess: invalidate,
    onError: (error) => toast.error(error.message),
  });

  const activeProvidersMap = useMemo(
    () => new Map(providers.map((p) => [p.id, p])),
    [providers],
  );
  const statusByPolicyId = useMemo(
    () => new Map<string, BudgetStatus>((statuses.data ?? []).map((s) => [s.policy_id, s])),
    [statuses.data],
  );
  const policy = editing && editing !== "new" ? editing : null;

  return (
    <div className="space-y-4">
      {dialog}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{scope.currency} 预算管理</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            同币种检查已用金额与执行占用；周期按 UTC 重置。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CurrencySelect currency={scope.currency} onChange={onCurrencyChange} />
          {canManage ? (
            <Button onClick={() => setEditing("new")} size="sm" type="button">
              <Plus className="size-3.5" />
              新建预算
            </Button>
          ) : null}
        </div>
      </div>

      <QuerySection loadingLabel="正在加载预算策略…" query={policies}>
        {(items) =>
          items.length ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {items.map((item) => {
                const status = statusByPolicyId.get(item.id);
                const providerInfo = resolveProviderInfo(
                  item.provider_id,
                  activeProvidersMap,
                  historicalProviderNames,
                );
                const modelLabel = item.model_id === "*" ? "全部模型" : item.model_id;
                const featureLabel = formatFeatureLabel(item.feature);
                const periodLabel =
                  item.period === "calendar_day_utc" ? "自然日 UTC" : "自然月 UTC";

                return (
                  <section
                    className={cn(USAGE_CARD_CLASS, "min-w-0 space-y-3 p-4")}
                    key={item.id}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h4 className="truncate font-medium text-foreground">{item.name}</h4>
                          <span className="rounded-md border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            {periodLabel}
                          </span>
                        </div>
                        <p
                          className="mt-1 truncate text-xs text-muted-foreground"
                          title={`${item.provider_id} / ${item.model_id} / ${item.feature}`}
                        >
                          {providerInfo.name} · {modelLabel} · {featureLabel}
                        </p>
                      </div>
                      <StatePill status={item.enabled ? "enabled" : "disabled"} />
                    </div>

                    {status ? (
                      <div className="space-y-2 rounded-lg bg-muted/30 p-3 text-xs">
                        <div className="flex flex-wrap items-baseline justify-between gap-2 tabular-nums">
                          <span>
                            已用{" "}
                            <strong
                              className="font-semibold text-foreground"
                              title={formatExactMoney(status.spent_amount, status.currency)}
                            >
                              {formatDisplayMoney(status.spent_amount, status.currency)}
                            </strong>
                            {" · "}占用{" "}
                            <span title={formatExactMoney(status.reserved_amount, status.currency)}>
                              {formatDisplayMoney(status.reserved_amount, status.currency)}
                            </span>
                          </span>
                          <span className="text-muted-foreground">
                            {status.hard_limit === null
                              ? "未设硬限额"
                              : status.remaining_amount === null
                                ? "剩余待核实"
                                : `剩余 ${formatDisplayMoney(status.remaining_amount, status.currency)}`}
                          </span>
                        </div>
                        <Progress value={(status.usage_ratio ?? 0) * 100} />
                        {status.unknown_events > 0 ? (
                          <p className="text-amber-600 dark:text-amber-400">
                            有 {status.unknown_events} 条记录金额待核实，硬预算暂停授权新调用。
                          </p>
                        ) : null}
                        <p className="text-[11px] text-muted-foreground">
                          重置时间：{stamp(status.period_end)}（本地时间）
                        </p>
                      </div>
                    ) : statuses.isError ? (
                      <p className="text-xs text-destructive">{statuses.error.message}</p>
                    ) : null}

                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span className="tabular-nums">
                        软限额{" "}
                        {item.soft_limit === null
                          ? "未设置"
                          : formatDisplayMoney(item.soft_limit, item.currency)}
                        {" · "}硬限额{" "}
                        {item.hard_limit === null
                          ? "未设置"
                          : formatDisplayMoney(item.hard_limit, item.currency)}
                      </span>
                      {canManage ? (
                        <div className="flex gap-2">
                          <Button
                            onClick={() => setEditing(item)}
                            size="xs"
                            type="button"
                            variant="outline"
                          >
                            编辑
                          </Button>
                          <Button
                            disabled={!item.enabled || disable.isPending}
                            onClick={() =>
                              confirm({
                                title: `停用预算「${item.name}」？`,
                                description: "停用后该预算不再限制或拦截新调用。",
                                label: "确认停用",
                                destructive: true,
                                run: () => disable.mutate(item.id),
                              })
                            }
                            size="xs"
                            type="button"
                            variant="outline"
                          >
                            停用
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  </section>
                );
              })}
            </div>
          ) : (
            <section className={cn(USAGE_CARD_CLASS, "p-8 text-center text-sm text-muted-foreground")}>
              暂未配置 {scope.currency} 预算规则。{canManage ? "可点击右上角「新建预算」设置软/硬限额。" : ""}
            </section>
          )
        }
      </QuerySection>

      <section aria-label="预算告警记录" className={cn(USAGE_CARD_CLASS, "space-y-3 p-4")}>
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-sm font-semibold">本币种告警记录</h4>
          <span className="text-xs text-muted-foreground">{scope.currency}</span>
        </div>
        <QuerySection loadingLabel="正在加载告警…" query={alerts}>
          {(items) =>
            items.length ? (
              <div className="divide-y">
                {items.map((item) => {
                  const providerInfo = resolveProviderInfo(
                    item.provider_id,
                    activeProvidersMap,
                    historicalProviderNames,
                  );
                  return (
                    <div
                      className="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-1 last:pb-0"
                      key={item.id}
                    >
                      <div className="min-w-0 space-y-1 text-xs">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-foreground">
                            {item.level === "hard" ? "硬限额阻断" : "软限额提醒"}
                          </span>
                          <StatePill
                            label={item.status === "acknowledged" ? "已确认" : "待确认"}
                            status={item.status === "acknowledged" ? "completed" : "pending"}
                          />
                          <span
                            className="text-muted-foreground"
                            title={`${item.provider_id} / ${item.model_id} / ${item.feature}`}
                          >
                            {providerInfo.name} · {item.model_id} · {formatFeatureLabel(item.feature)}
                          </span>
                        </div>
                        <p className="text-muted-foreground tabular-nums">
                          已用 {formatDisplayMoney(item.spent_amount, item.currency)} · 占用{" "}
                          {formatDisplayMoney(item.reserved_amount, item.currency)} · 本次{" "}
                          {formatDisplayMoney(item.projected_amount, item.currency)} · 限额{" "}
                          {formatDisplayMoney(item.limit_amount, item.currency)}
                        </p>
                        <p className="text-[11px] text-muted-foreground">
                          {stamp(item.created_at)}
                        </p>
                      </div>
                      {canManage && item.status !== "acknowledged" ? (
                        <Button
                          disabled={ack.isPending}
                          onClick={() => ack.mutate(item.id)}
                          size="xs"
                          type="button"
                          variant="outline"
                        >
                          确认告警
                        </Button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">暂无预算告警记录</p>
            )
          }
        </QuerySection>
      </section>

      {canManage ? (
        <EmailPanel scope={scope} />
      ) : (
        <p className="text-xs text-muted-foreground">
          预算和价格配置由拥有工作区管理权限的用户维护。
        </p>
      )}

      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {policy ? "编辑预算" : "新建预算"} · {scope.currency}
            </DialogTitle>
            <DialogDescription>
              币种创建后固定。USD 与 CNY 各自设置限额。
            </DialogDescription>
          </DialogHeader>
          <form
            className="grid gap-3"
            key={policy?.id ?? "new"}
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate(new FormData(event.currentTarget));
            }}
          >
            <label className="grid gap-1 text-sm">
              名称
              <Input defaultValue={policy?.name ?? ""} name="name" required />
            </label>
            {!policy ? (
              <>
                <ScopeFields
                  historicalProviderNames={historicalProviderNames}
                  providers={providers}
                />
                <label className="grid gap-1 text-sm">
                  周期
                  <select
                    className="h-9 rounded-md border bg-background px-2"
                    name="period"
                  >
                    <option value="calendar_month_utc">自然月 UTC</option>
                    <option value="calendar_day_utc">自然日 UTC</option>
                  </select>
                </label>
              </>
            ) : null}
            <MoneyInput
              label={`软限额 ${scope.currency}`}
              name="soft_limit"
              value={policy?.soft_limit}
            />
            <MoneyInput
              label={`硬限额 ${scope.currency}`}
              name="hard_limit"
              value={policy?.hard_limit}
            />
            <label className="flex items-center gap-2 text-sm">
              <input
                defaultChecked={policy?.enabled ?? true}
                name="enabled"
                type="checkbox"
              />
              启用
            </label>
            <Button disabled={save.isPending} type="submit">
              保存预算
            </Button>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function EmailPanel({ scope }: { scope: UsageScope }) {
  const client = useQueryClient();
  const key = workspaceQueryKey(
    scope.workspaceId,
    "usage",
    "native-v2",
    scope.userId,
    "alert-email-settings",
  );
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => getAlertEmailConfig(scope, signal),
    retry: false,
  });
  const save = useMutation({
    mutationFn: (form: FormData) =>
      updateAlertEmailConfig(
        {
          enabled: form.get("enabled") === "on",
          smtp_host: field(form, "host"),
          smtp_port: Number(field(form, "port")),
          smtp_security: field(form, "security") as AlertEmailConfig["smtp_security"],
          smtp_username: field(form, "username"),
          smtp_password:
            form.get("clear_password") === "on" ? "" : field(form, "password") || null,
          from_address: field(form, "from"),
          to_addresses: field(form, "to").split(/[,;\s]+/).filter(Boolean),
        },
        scope,
      ),
    onSuccess: () => {
      toast.success("告警邮件配置已保存");
      return client.invalidateQueries({ queryKey: key });
    },
    onError: (error) => toast.error(error.message),
  });
  const test = useMutation({
    mutationFn: () => sendTestAlertEmail(scope),
    onSuccess: (result) =>
      result.ok ? toast.success(result.detail) : toast.error(result.detail),
    onError: (error) => toast.error(error.message),
  });

  return (
    <section aria-label="预算邮件通知" className={cn(USAGE_CARD_CLASS, "space-y-3 p-4")}>
      <div>
        <h4 className="text-sm font-semibold">告警通知规则（邮件）</h4>
        <p className="mt-0.5 text-xs text-muted-foreground">
          工作区共用邮件配置；触发预算告警时按原币种发送提醒。
        </p>
      </div>
      <QuerySection loadingLabel="正在读取邮件配置…" query={query}>
        {(config) => (
          <form
            className="grid gap-3 sm:grid-cols-2"
            key={JSON.stringify(config)}
            onSubmit={(event: FormEvent<HTMLFormElement>) => {
              event.preventDefault();
              save.mutate(new FormData(event.currentTarget));
            }}
          >
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <input defaultChecked={config.enabled} name="enabled" type="checkbox" />
              启用邮件通知
            </label>
            {(
              [
                ["host", "SMTP 主机", config.smtp_host],
                ["port", "端口", String(config.smtp_port)],
                ["username", "用户名", config.smtp_username],
                ["from", "发件地址", config.from_address],
                ["to", "收件地址（逗号分隔）", config.to_addresses.join(",")],
              ] as const
            ).map(([name, label, value]) => (
              <label className="grid gap-1 text-sm" key={name}>
                {label}
                <Input defaultValue={value} name={name} />
              </label>
            ))}
            <label className="grid gap-1 text-sm">
              安全连接
              <select
                className="h-9 rounded-md border bg-background px-2"
                defaultValue={config.smtp_security}
                name="security"
              >
                <option value="ssl">SSL</option>
                <option value="starttls">STARTTLS</option>
                <option value="none">无</option>
              </select>
            </label>
            <label className="grid gap-1 text-sm">
              SMTP 密码
              <Input
                autoComplete="new-password"
                name="password"
                placeholder={config.has_password ? "已保存，留空保留" : "未配置"}
                type="password"
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input name="clear_password" type="checkbox" />
              清除已存密码
            </label>
            <div className="flex flex-wrap gap-2 sm:col-span-2">
              <Button disabled={save.isPending} size="sm" type="submit">
                保存邮件配置
              </Button>
              <Button
                disabled={test.isPending}
                onClick={() => test.mutate()}
                size="sm"
                type="button"
                variant="outline"
              >
                发送测试邮件
              </Button>
            </div>
          </form>
        )}
      </QuerySection>
    </section>
  );
}
