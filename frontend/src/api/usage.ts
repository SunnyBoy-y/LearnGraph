import type {
  BudgetAlert,
  BudgetPolicy,
  BudgetPolicyCreate,
  BudgetPolicyUpdate,
  BudgetStatus,
  ExchangeRateVersion,
  PriceVersion,
  PriceVersionCreate,
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

export function listPriceVersions(): Promise<PriceVersion[]> {
  return apiClient.get<PriceVersion[]>("/usage/prices");
}

export function listPriceCatalog(): Promise<PriceCatalogItem[]> {
  return apiClient.get<PriceCatalogItem[]>("/usage/price-catalog");
}

export function createPriceVersion(
  payload: PriceVersionCreate,
): Promise<PriceVersion> {
  return apiClient.post<PriceVersion, PriceVersionCreate>(
    "/usage/prices",
    payload,
  );
}

export function listExchangeRates(): Promise<ExchangeRateVersion[]> {
  return apiClient.get<ExchangeRateVersion[]>("/usage/exchange-rates");
}

export function createExchangeRate(rate: number): Promise<ExchangeRateVersion> {
  return apiClient.post<ExchangeRateVersion, { rate: number; source: string }>(
    "/usage/exchange-rates",
    { rate, source: "workspace_manual" },
  );
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

export function retirePriceVersion(priceId: string): Promise<PriceVersion> {
  return apiClient.post<PriceVersion, { retired_at?: string }>(
    `/usage/prices/${encodeURIComponent(priceId)}/retire`,
    {},
  );
}

export function retireExchangeRate(
  rateId: string,
): Promise<ExchangeRateVersion> {
  return apiClient.post<ExchangeRateVersion, { retired_at?: string }>(
    `/usage/exchange-rates/${encodeURIComponent(rateId)}/retire`,
    {},
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
