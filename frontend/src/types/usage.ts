import type { IsoDateTime } from "./common";

export interface UsageSummary {
  workspace_id: string;
  input_tokens: number;
  output_tokens: number;
  attempts: number;
  cost_usd: number;
  cost_cny: number;
  unpriced_events: number;
  remote_usage_recorded: boolean;
}

export interface UsageEvent {
  id: string;
  workspace_id: string;
  provider_id: string;
  model_id: string;
  feature: string;
  input_tokens: number;
  output_tokens: number;
  attempt: number;
  cost_usd: number;
  cost_cny: number;
  cost_status: string;
  price_version_id: string | null;
  exchange_rate_version_id: string | null;
  input_usd_per_million: number;
  cached_input_usd_per_million: number;
  price_multiplier: number;
  output_usd_per_million: number;
  fixed_usd_per_call: number;
  usd_cny_rate: number;
  latency_ms: number;
  created_at: IsoDateTime;
}

export interface PriceVersion {
  id: string;
  workspace_id: string;
  provider_id: string;
  model_id: string;
  feature: string;
  version: number;
  input_usd_per_million: number;
  cached_input_usd_per_million: number | null;
  cache_write_usd_per_million: number | null;
  output_usd_per_million: number;
  fixed_usd_per_call: number;
  effective_at: IsoDateTime;
  retired_at: IsoDateTime | null;
  source: string;
  conditions: Record<string, unknown>;
  created_at: IsoDateTime;
}

export interface PriceVersionCreate {
  provider_id?: string;
  model_id?: string;
  feature?: string;
  input_usd_per_million?: number;
  cached_input_usd_per_million?: number | null;
  cache_write_usd_per_million?: number | null;
  output_usd_per_million?: number;
  fixed_usd_per_call?: number;
  currency?: "USD" | "CNY";
  input_cny_per_million?: number | null;
  cached_input_cny_per_million?: number | null;
  cache_write_cny_per_million?: number | null;
  output_cny_per_million?: number | null;
  fixed_cny_per_call?: number | null;
  source?: string;
  conditions?: Record<string, unknown>;
}

export interface PriceCatalogItem {
  catalog_id: string;
  provider_key: string;
  model_id: string;
  currency: "USD" | "CNY";
  native_input_per_million: number;
  native_cached_input_per_million: number | null;
  native_cache_write_per_million: number | null;
  native_output_per_million: number;
  input_usd_per_million: number;
  cached_input_usd_per_million: number | null;
  cache_write_usd_per_million: number | null;
  output_usd_per_million: number;
  conditions: Record<string, unknown>;
  source_url: string;
  as_of: string;
}

export interface PriceCatalogApply {
  catalog_id: string;
  provider_id?: string;
  feature?: string;
  input_usd_per_million?: number;
  cached_input_usd_per_million?: number | null;
  cache_write_usd_per_million?: number | null;
  output_usd_per_million?: number;
}

export interface ExchangeRateVersion {
  id: string;
  workspace_id: string;
  base_currency: string;
  quote_currency: string;
  version: number;
  rate: number;
  effective_at: IsoDateTime;
  retired_at: IsoDateTime | null;
  source: string;
  created_at: IsoDateTime;
}

export interface BudgetPolicy {
  id: string;
  workspace_id: string;
  name: string;
  provider_id: string;
  model_id: string;
  feature: string;
  period: "calendar_day_utc" | "calendar_month_utc";
  soft_limit_cny: number | null;
  hard_limit_cny: number | null;
  enabled: boolean;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface BudgetPolicyCreate {
  name: string;
  provider_id?: string;
  model_id?: string;
  feature?: string;
  period?: "calendar_day_utc" | "calendar_month_utc";
  soft_limit_cny?: number | null;
  hard_limit_cny?: number | null;
  enabled?: boolean;
}

export interface BudgetPolicyUpdate {
  name: string;
  soft_limit_cny?: number | null;
  hard_limit_cny?: number | null;
  enabled?: boolean;
}

export interface BudgetStatus {
  policy_id: string;
  name: string;
  provider_id: string;
  model_id: string;
  feature: string;
  period: string;
  period_start: IsoDateTime;
  period_end: IsoDateTime;
  spent_cny: number;
  soft_limit_cny: number | null;
  hard_limit_cny: number | null;
  soft_exceeded: boolean;
  hard_exceeded: boolean;
  enabled: boolean;
}

export interface BudgetAlert {
  id: string;
  workspace_id: string;
  policy_id: string;
  level: string;
  status: string;
  provider_id: string;
  model_id: string;
  feature: string;
  period_start: IsoDateTime;
  period_end: IsoDateTime;
  spent_cny: number;
  projected_cost_cny: number;
  limit_cny: number;
  acknowledged_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}
