import type {
  AlertEmailConfig,
  AlertEmailConfigUpdate,
  AlertEmailTestResult,
  BudgetAlert,
  BudgetPolicy,
  BudgetPolicyCreate,
  BudgetPolicyUpdate,
  BudgetStatus,
  ExchangeRateInfo,
  ManualPrice,
  ManualPriceUpsert,
  ModelsDevStatus,
  PriceCatalogItem,
  UsageEvent,
  UsageSummary,
} from "@/types/usage";

import { apiClient } from "./client";

export function getUsageSummary(): Promise<UsageSummary> {
  return apiClient.get<UsageSummary>("/usage/summary");
}

export function listUsageEvents(): Promise<UsageEvent[]> {
  return apiClient.get<UsageEvent[]>("/usage/events");
}

export function clearUsageEvents(): Promise<{ deleted_count: number }> {
  return apiClient.delete<{ deleted_count: number }>("/usage/events");
}

export function listPriceCatalog(): Promise<PriceCatalogItem[]> {
  return apiClient.get<PriceCatalogItem[]>("/usage/price-catalog");
}

export function getModelsDevStatus(): Promise<ModelsDevStatus> {
  return apiClient.get<ModelsDevStatus>("/usage/models-dev");
}

export function refreshModelsDevSnapshot(): Promise<ModelsDevStatus> {
  return apiClient.post<ModelsDevStatus>("/usage/models-dev/refresh");
}

export function listManualPrices(): Promise<ManualPrice[]> {
  return apiClient.get<ManualPrice[]>("/usage/manual-prices");
}

export function upsertManualPrice(
  payload: ManualPriceUpsert,
): Promise<ManualPrice> {
  return apiClient.put<ManualPrice, ManualPriceUpsert>(
    "/usage/manual-prices",
    payload,
  );
}

export function removeManualPrice(
  modelId: string,
): Promise<{ removed_count: number }> {
  return apiClient.delete<{ removed_count: number }>("/usage/manual-prices", {
    query: { model_id: modelId },
  });
}

export function getExchangeRate(): Promise<ExchangeRateInfo> {
  return apiClient.get<ExchangeRateInfo>("/usage/exchange-rate");
}

export function setExchangeRate(rate: number): Promise<ExchangeRateInfo> {
  return apiClient.put<ExchangeRateInfo, { rate: number }>(
    "/usage/exchange-rate",
    { rate },
  );
}

export function refreshExchangeRate(): Promise<ExchangeRateInfo> {
  return apiClient.post<ExchangeRateInfo>("/usage/exchange-rate/refresh");
}

export function getAlertEmailConfig(): Promise<AlertEmailConfig> {
  return apiClient.get<AlertEmailConfig>("/usage/alert-email");
}

export function updateAlertEmailConfig(
  payload: AlertEmailConfigUpdate,
): Promise<AlertEmailConfig> {
  return apiClient.put<AlertEmailConfig, AlertEmailConfigUpdate>(
    "/usage/alert-email",
    payload,
  );
}

export function sendTestAlertEmail(): Promise<AlertEmailTestResult> {
  return apiClient.post<AlertEmailTestResult>("/usage/alert-email/test");
}

export function listBudgetPolicies(): Promise<BudgetPolicy[]> {
  return apiClient.get<BudgetPolicy[]>("/usage/budget-policies");
}

export function createBudgetPolicy(
  payload: BudgetPolicyCreate,
): Promise<BudgetPolicy> {
  return apiClient.post<BudgetPolicy, BudgetPolicyCreate>(
    "/usage/budget-policies",
    payload,
  );
}

export function updateBudgetPolicy(
  policyId: string,
  payload: BudgetPolicyUpdate,
): Promise<BudgetPolicy> {
  return apiClient.put<BudgetPolicy, BudgetPolicyUpdate>(
    `/usage/budget-policies/${encodeURIComponent(policyId)}`,
    payload,
  );
}

export function deleteBudgetPolicy(policyId: string): Promise<void> {
  return apiClient.delete<void>(
    `/usage/budget-policies/${encodeURIComponent(policyId)}`,
  );
}

export function listBudgetStatuses(): Promise<BudgetStatus[]> {
  return apiClient.get<BudgetStatus[]>("/usage/budget-status");
}

export function listBudgetAlerts(): Promise<BudgetAlert[]> {
  return apiClient.get<BudgetAlert[]>("/usage/budget-alerts");
}

export function acknowledgeBudgetAlert(alertId: string): Promise<BudgetAlert> {
  return apiClient.post<BudgetAlert>(
    `/usage/budget-alerts/${encodeURIComponent(alertId)}/acknowledge`,
  );
}
