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

export interface ManualPrice {
  id: string;
  model_id: string;
  provider_id: string;
  currency: "USD" | "CNY";
  input_per_million: number;
  cached_input_per_million: number | null;
  output_per_million: number;
  fixed_per_call: number;
  effective_at: IsoDateTime;
}

export interface ManualPriceUpsert {
  model_id: string;
  provider_id?: string;
  currency: "USD" | "CNY";
  input_per_million: number;
  cached_input_per_million?: number | null;
  output_per_million: number;
  fixed_per_call?: number;
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
  source: "builtin" | "models_dev";
}

export interface ModelsDevStatus {
  source: string;
  origin: "bundled" | "network" | "network_cache" | "missing";
  fetched_at: string | null;
  provider_count: number;
  model_count: number;
  priced_model_count: number;
}

export interface ExchangeRateInfo {
  rate: number;
  source: string;
  effective_at: IsoDateTime;
}

export interface AlertEmailConfig {
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_security: "ssl" | "starttls" | "none";
  smtp_username: string;
  has_password: boolean;
  from_address: string;
  to_addresses: string[];
}

export interface AlertEmailConfigUpdate {
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_security: "ssl" | "starttls" | "none";
  smtp_username: string;
  /** null 保留已存密码；空字符串清除 */
  smtp_password?: string | null;
  from_address: string;
  to_addresses: string[];
}

export interface AlertEmailTestResult {
  ok: boolean;
  detail: string;
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
