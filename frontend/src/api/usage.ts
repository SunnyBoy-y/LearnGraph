import type {
  AlertEmailConfig, AlertEmailConfigUpdate, AlertEmailTestResult, BudgetAlert,
  BudgetPolicy, BudgetPolicyCreate, BudgetPolicyUpdate, BudgetStatus, BillingCurrency,
  ManualPrice, ManualPriceUpsert, ModelsDevStatus, PriceCatalogItem, UsageEvent, UsageSummary,
} from "@/types/usage";
import { apiClient, type ApiRequestOptions } from "./client";
import { authStore } from "./auth-store";

export interface UsageScope { workspaceId: string; userId?: string; currency: BillingCurrency }
export interface UsageFilters {
  provider_id?: string; model_id?: string; feature?: string; start_at?: string; end_at?: string;
  archived?: "exclude" | "include" | "only"; offset?: number; limit?: number; unknown_currency?: boolean;
}
function currentScope(): UsageScope {
  const session = authStore.getSession();
  return { workspaceId: session?.workspaceId ?? "", userId: session?.userId ?? session?.username, currency: "USD" };
}
function assertAccount(scope: UsageScope) {
  const session = authStore.getSession();
  if (!session || (scope.userId && scope.userId !== (session.userId ?? session.username))) throw new Error("登录账号已变化，请重新读取");
  return session;
}
function options(scope: UsageScope, signal?: AbortSignal, filters: UsageFilters = {}): ApiRequestOptions {
  const session = assertAccount(scope);
  return { signal, workspace: false, headers: { "X-Workspace-ID": scope.workspaceId,
    Authorization: "Bearer " + session.accessToken }, query: { ...filters, currency: scope.currency } };
}
export async function getUsageSummary(scope = currentScope(), filters: UsageFilters = {}, signal?: AbortSignal): Promise<UsageSummary> {
  const result = await apiClient.get<UsageSummary>("/usage/summary", options(scope, signal, filters));
  assertAccount(scope);
  if (result.schema_version !== 2 || result.currency !== scope.currency || result.workspace_id !== scope.workspaceId || typeof result.cost_amount !== "string") throw new Error("计费数据格式已变化，请刷新页面");
  return result;
}
export async function listUsageEvents(scope = currentScope(), filters: UsageFilters = {}, signal?: AbortSignal): Promise<UsageEvent[]> {
  const records = await apiClient.get<UsageEvent[]>("/usage/events", options(scope, signal, filters));
  assertAccount(scope);
  if (records.some((item) => item.workspace_id !== scope.workspaceId || (filters.unknown_currency ? item.currency !== null : item.currency !== scope.currency) ||
      (item.cost_amount !== null && typeof item.cost_amount !== "string"))) throw new Error("用量数据范围或格式不匹配");
  return records;
}
export function clearUsageEvents(scope: UsageScope, filters: UsageFilters = {}): Promise<{ archived_count: number }> {
  const { archived: _archived, offset: _offset, limit: _limit, unknown_currency: _unknown, ...criteria } = filters;
  return apiClient.delete("/usage/events", options(scope, undefined, criteria));
}
export function listPriceCatalog(scope = currentScope(), signal?: AbortSignal): Promise<PriceCatalogItem[]> {
  return apiClient.get("/usage/price-catalog", options(scope, signal));
}
export function getModelsDevStatus(scope = currentScope(), signal?: AbortSignal): Promise<ModelsDevStatus> {
  return apiClient.get("/usage/models-dev", options(scope, signal));
}
export function refreshModelsDevSnapshot(scope = currentScope()): Promise<ModelsDevStatus> {
  return apiClient.post("/usage/models-dev/refresh", undefined, options(scope));
}
export function listManualPrices(scope = currentScope(), signal?: AbortSignal): Promise<ManualPrice[]> {
  return apiClient.get("/usage/manual-prices", options(scope, signal));
}
export function upsertManualPrice(payload: ManualPriceUpsert, scope = currentScope()): Promise<ManualPrice> {
  return apiClient.put("/usage/manual-prices", payload, options(scope));
}
export function removeManualPrice(priceId: string, scope = currentScope()): Promise<{ removed_count: number }> {
  return apiClient.delete("/usage/manual-prices", { ...options(scope), query: { price_id: priceId } });
}
export function getAlertEmailConfig(scope = currentScope(), signal?: AbortSignal): Promise<AlertEmailConfig> {
  return apiClient.get("/usage/alert-email", options(scope, signal));
}
export function updateAlertEmailConfig(payload: AlertEmailConfigUpdate, scope = currentScope()): Promise<AlertEmailConfig> {
  return apiClient.put("/usage/alert-email", payload, options(scope));
}
export function sendTestAlertEmail(scope = currentScope()): Promise<AlertEmailTestResult> {
  return apiClient.post("/usage/alert-email/test", undefined, options(scope));
}
export function listBudgetPolicies(scope = currentScope(), signal?: AbortSignal): Promise<BudgetPolicy[]> {
  return apiClient.get("/usage/budget-policies", options(scope, signal));
}
export function createBudgetPolicy(payload: BudgetPolicyCreate, scope = currentScope()): Promise<BudgetPolicy> {
  return apiClient.post("/usage/budget-policies", payload, options(scope));
}
export function updateBudgetPolicy(policyId: string, payload: BudgetPolicyUpdate, scope = currentScope()): Promise<BudgetPolicy> {
  return apiClient.put("/usage/budget-policies/" + encodeURIComponent(policyId), payload, options(scope));
}
export function deleteBudgetPolicy(policyId: string, scope = currentScope()): Promise<void> {
  return apiClient.delete("/usage/budget-policies/" + encodeURIComponent(policyId), options(scope));
}
export function listBudgetStatuses(scope = currentScope(), signal?: AbortSignal): Promise<BudgetStatus[]> {
  return apiClient.get("/usage/budget-status", options(scope, signal));
}
export function listBudgetAlerts(scope = currentScope(), signal?: AbortSignal): Promise<BudgetAlert[]> {
  return apiClient.get("/usage/budget-alerts", options(scope, signal));
}
export function acknowledgeBudgetAlert(alertId: string, scope = currentScope()): Promise<BudgetAlert> {
  return apiClient.post("/usage/budget-alerts/" + encodeURIComponent(alertId) + "/acknowledge", undefined, options(scope));
}
