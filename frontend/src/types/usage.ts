import type { IsoDateTime } from "./common";

export type BillingCurrency = "USD" | "CNY";
export type Money = string;

export interface UsageSummary {
  schema_version: 2; workspace_id: string; currency: BillingCurrency;
  input_tokens: number; cached_input_tokens: number; cache_creation_input_tokens: number;
  output_tokens: number; reasoning_tokens: number; total_tokens: number; attempts: number;
  cost_amount: Money; reserved_amount: Money; unpriced_events: number;
  unknown_currency_events: number; remote_usage_recorded: boolean;
  breakdown: Record<string, { label: string; cost_amount: Money; attempts: number; total_tokens: number; unknown_events: number }[]>;
}
export interface UsageEvent {
  id: string; workspace_id: string; provider_id: string; model_id: string; feature: string;
  currency: BillingCurrency | null; cost_amount: Money | null; reserved_amount: Money | null;
  input_tokens: number; cached_input_tokens: number; cache_creation_input_tokens: number;
  output_tokens: number; reasoning_tokens: number; total_tokens: number; attempt: number;
  cost_status: string; amount_source: string; record_stage: string;
  price_version_id: string | null; pricing_snapshot: Record<string, unknown>;
  legacy_billing_snapshot: Record<string, unknown>; usage_units: Record<string, unknown>;
  archived_at: IsoDateTime | null; latency_ms: number; created_at: IsoDateTime;
}
export interface ManualPriceUpsert {
  model_id: string; provider_id: string; feature?: string; currency: BillingCurrency;
  input_per_million: Money; cached_input_per_million?: Money | null;
  cache_write_per_million?: Money | null; output_per_million: Money; fixed_per_call?: Money;
  conditions?: Record<string, unknown>; rates_by_unit?: Record<string, Money>;
}
export interface ManualPrice extends ManualPriceUpsert {
  id: string; effective_at: IsoDateTime; version: number;
}
export interface PriceCatalogItem {
  catalog_id: string; provider_key: string; model_id: string; currency: BillingCurrency;
  native_input_per_million: Money; native_cached_input_per_million: Money | null;
  native_cache_write_per_million: Money | null; native_output_per_million: Money;
  native_fixed_per_call: Money; rates_by_unit: Record<string, Money>;
  conditions: Record<string, unknown>; source_url: string; as_of: string;
  source: "builtin" | "models_dev";
}
export interface ModelsDevStatus {
  source: string; origin: "bundled" | "network" | "network_cache" | "missing"; fetched_at: string | null;
  provider_count: number; model_count: number; priced_model_count: number;
  auto_refresh_enabled: boolean; refresh_interval_seconds: number; next_refresh_at: string | null; stale: boolean;
}
export interface AlertEmailConfig {
  enabled: boolean; smtp_host: string; smtp_port: number;
  smtp_security: "ssl" | "starttls" | "none"; smtp_username: string;
  has_password: boolean; from_address: string; to_addresses: string[];
}
export interface AlertEmailConfigUpdate {
  enabled: boolean; smtp_host: string; smtp_port: number;
  smtp_security: "ssl" | "starttls" | "none"; smtp_username: string;
  smtp_password?: string | null; from_address: string; to_addresses: string[];
}
export interface AlertEmailTestResult { ok: boolean; detail: string }
export interface BudgetPolicy {
  id: string; workspace_id: string; name: string; provider_id: string; model_id: string; feature: string;
  period: "calendar_day_utc" | "calendar_month_utc"; currency: BillingCurrency;
  soft_limit: Money | null; hard_limit: Money | null; enabled: boolean;
  created_at: IsoDateTime; updated_at: IsoDateTime;
}
export interface BudgetPolicyCreate {
  name: string; provider_id?: string; model_id?: string; feature?: string;
  period?: "calendar_day_utc" | "calendar_month_utc"; currency: BillingCurrency;
  soft_limit?: Money | null; hard_limit?: Money | null; enabled?: boolean;
}
export interface BudgetPolicyUpdate {
  name: string; soft_limit?: Money | null; hard_limit?: Money | null; enabled?: boolean;
  currency?: BillingCurrency;
}
export interface BudgetStatus {
  policy_id: string; name: string; provider_id: string; model_id: string; feature: string; period: string;
  currency: BillingCurrency; period_start: IsoDateTime; period_end: IsoDateTime;
  spent_amount: Money; reserved_amount: Money; soft_limit: Money | null; hard_limit: Money | null;
  remaining_amount: Money | null; unknown_events: number; status: string; usage_ratio: number | null;
  soft_exceeded: boolean; hard_exceeded: boolean; enabled: boolean;
}
export interface BudgetAlert {
  id: string; workspace_id: string; policy_id: string; level: string; status: string;
  provider_id: string; model_id: string; feature: string; currency: BillingCurrency | null;
  period_start: IsoDateTime; period_end: IsoDateTime; spent_amount: Money | null;
  reserved_amount: Money | null; projected_amount: Money | null; limit_amount: Money | null;
  legacy_billing_snapshot: Record<string, unknown>; acknowledged_at: IsoDateTime | null;
  created_at: IsoDateTime; updated_at: IsoDateTime;
}
