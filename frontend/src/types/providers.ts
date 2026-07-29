import type { IsoDateTime, UnknownRecord } from "./common";

export type ProviderRole =
  | "model"
  | "image_generation"
  | "vision"
  | "search"
  | "fetch"
  | "deep_research"
  | "memory"
  | "transcription"
  | "embedding";

export interface ProviderTypeCatalogItem {
  provider_type: string;
  role: ProviderRole;
  label: string;
  description: string;
  requires_base_url: boolean;
  requires_secret: boolean;
  supports_model_discovery: boolean;
  supports_probe: boolean;
  create_allowed: boolean;
  default_base_url: string | null;
  probe_notice: string | null;
  brand_id: string | null;
  brand_icon_url: string | null;
  documentation_url: string | null;
  key_management_url: string | null;
  supports_account_balance: boolean;
}

export interface ProviderCreateRequest {
  display_name: string;
  provider_type: string;
  base_url?: string | null;
  api_key?: string | null;
  capabilities?: UnknownRecord;
}

export interface ProviderUpdateRequest {
  enabled?: boolean;
  base_url?: string | null;
  default_model?: string | null;
  default_image_generation_model_id?: string | null;
  default_transcription_model_id?: string | null;
  default_vision_model_id?: string | null;
  /** Custom HTTP headers for proxy / relay stations. Credentials are never accepted here. */
  extra_headers?: Record<string, string> | null;
  /** Global template switch: on = template overrides every model, off = per-model configs apply. */
  model_defaults_enabled?: boolean;
}

export interface Provider {
  id: string;
  workspace_id: string;
  display_name: string;
  provider_type: string;
  base_url: string | null;
  api_key_masked: string | null;
  enabled: boolean;
  remote_capability: boolean;
  capabilities: UnknownRecord;
  status: string;
  secret_status: "active" | "revoked" | "missing";
  secret_version: number | null;
  secret_key_provider: "environment" | "keyring" | null;
  secret_key_version: number | null;
  created_at: IsoDateTime;
}

/**
 * Provider types whose role is "model" in the backend catalog
 * (`MODEL_PROVIDER_TYPES` in backend/app/providers/catalog.py).
 *
 * Every one of them implements the structured function-call loop that Agent
 * mode drives, so this list doubles as the Agent-capable set. Keep it as the
 * single source of truth: copying the literal per call site is how the `qwen`
 * type was silently left out of the Agent gate while it worked everywhere else.
 */
export const MODEL_PROVIDER_TYPES = [
  "openai_responses",
  "openai_compatible_chat",
  "qwen",
  "codex_chatgpt",
  "deepseek_chat",
  "anthropic_messages",
] as const;

export function isModelProviderType(providerType: string): boolean {
  return (MODEL_PROVIDER_TYPES as readonly string[]).includes(providerType);
}

/**
 * DeepSeek is OpenAI-compatible at the wire level. Official DeepSeek features
 * (balance lookup, native thinking stream) activate when the provider points at
 * api.deepseek.com, is a legacy deepseek_chat row, or declares model_family /
 * brand_id deepseek, or the selected model id looks like a DeepSeek model.
 */
export function isDeepSeekProvider(
  provider: Pick<Provider, "provider_type" | "base_url" | "capabilities">,
  modelId?: string | null,
): boolean {
  if (provider.provider_type === "deepseek_chat") return true;
  // DeepSeek models hosted by Qwen still use Qwen request parameters,
  // billing, cache rules, and balance semantics.
  if (provider.provider_type === "qwen") return false;
  const capabilities = provider.capabilities ?? {};
  if (
    capabilities.model_family === "deepseek" ||
    capabilities.brand_id === "deepseek"
  ) {
    return true;
  }
  const candidate = (modelId ?? capabilities.default_model ?? "").toString();
  if (candidate) {
    const lowered = candidate.toLowerCase();
    if (lowered.startsWith("deepseek") || lowered.includes("deepseek")) {
      return true;
    }
  }
  if (
    !["deepseek_chat", "openai_compatible_chat"].includes(provider.provider_type) ||
    !provider.base_url
  ) {
    return false;
  }
  try {
    const url = new URL(provider.base_url);
    return (
      url.protocol === "https:" &&
      url.hostname.toLowerCase() === "api.deepseek.com" &&
      !url.port &&
      !url.username &&
      !url.password &&
      url.pathname === "/" &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

export function isAnthropicProvider(
  provider: Pick<Provider, "provider_type">,
): boolean {
  return provider.provider_type === "anthropic_messages";
}

/** Official origins with a verified API-key balance endpoint (2026-07). */
const BALANCE_OFFICIAL_HOSTS = new Set([
  "api.deepseek.com",
  "api.moonshot.cn",
  "api.moonshot.ai",
  "api.siliconflow.cn",
  "api.siliconflow.com",
  "openrouter.ai",
]);

/** Official origins verified to NOT expose any key-based balance endpoint. */
const BALANCE_UNSUPPORTED_HOSTS = new Set([
  "api.openai.com",
  "dashscope.aliyuncs.com",
  "dashscope-intl.aliyuncs.com",
  "api.minimaxi.com",
  "api.minimax.io",
  "api.xiaomimimo.com",
  "generativelanguage.googleapis.com",
  "api.anthropic.com",
]);

/** Types whose relay stations may implement the one-api billing convention. */
const GATEWAY_BILLING_PROVIDER_TYPES = new Set([
  "openai_compatible_chat",
  "openai_responses",
  "qwen",
  "deepseek_chat",
  "anthropic_messages",
  "openai_images",
  "openai_compatible_vision",
  "openai_responses_vision",
  "openai_compatible_transcription",
]);

/** cc-switch style custom balance query configuration (per provider). */
export interface ProviderBalanceQueryConfig {
  enabled: boolean;
  template_id: string | null;
  script: string;
  timeout_seconds: number;
  auto_query_interval_minutes: number;
  variables: Record<string, string>;
}

/** The HTTP request part evaluated out of the config script. */
export interface ProviderBalanceQueryHttpRequest {
  url: string;
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  headers: Record<string, string>;
  body?: string | null;
}

export interface ProviderBalanceQueryExecuteResponse {
  provider_id: string;
  status_code: number;
  ok: boolean;
  payload: unknown;
  text: string | null;
  queried_at: IsoDateTime;
}

/** Extractor return value (cc-switch fields; single object or array). */
export interface BalanceExtractorResult {
  isValid?: boolean;
  invalidMessage?: string;
  remaining?: number;
  unit?: string;
  planName?: string;
  total?: number;
  used?: number;
  extra?: string;
}

export interface ProviderBalanceQueryLastResult {
  is_valid: boolean | null;
  invalid_message: string | null;
  remaining: number | null;
  unit: string | null;
  plan_name: string | null;
  total: number | null;
  used: number | null;
  extra: string | null;
  queried_at: IsoDateTime;
}

export function providerBalanceQueryConfig(
  provider: Pick<Provider, "capabilities">,
): ProviderBalanceQueryConfig | null {
  const bucket = provider.capabilities?.balance_query;
  if (!bucket || typeof bucket !== "object" || Array.isArray(bucket)) return null;
  const raw = (bucket as UnknownRecord).config;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const config = raw as UnknownRecord;
  if (typeof config.script !== "string" || !config.script.trim()) return null;
  return {
    enabled: Boolean(config.enabled),
    template_id:
      typeof config.template_id === "string" ? config.template_id : null,
    script: config.script,
    timeout_seconds:
      typeof config.timeout_seconds === "number" ? config.timeout_seconds : 10,
    auto_query_interval_minutes:
      typeof config.auto_query_interval_minutes === "number"
        ? config.auto_query_interval_minutes
        : 0,
    variables:
      config.variables &&
      typeof config.variables === "object" &&
      !Array.isArray(config.variables)
        ? Object.fromEntries(
            Object.entries(config.variables as UnknownRecord).map(
              ([key, value]) => [key, String(value ?? "")],
            ),
          )
        : {},
  };
}

export function providerBalanceQueryLastResult(
  provider: Pick<Provider, "capabilities">,
): ProviderBalanceQueryLastResult | null {
  const bucket = provider.capabilities?.balance_query;
  if (!bucket || typeof bucket !== "object" || Array.isArray(bucket)) return null;
  const raw = (bucket as UnknownRecord).last_result;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const result = raw as UnknownRecord;
  if (typeof result.queried_at !== "string") return null;
  const numeric = (value: unknown): number | null =>
    typeof value === "number" && Number.isFinite(value) ? value : null;
  const text = (value: unknown): string | null =>
    typeof value === "string" && value ? value : null;
  return {
    is_valid: typeof result.is_valid === "boolean" ? result.is_valid : null,
    invalid_message: text(result.invalid_message),
    remaining: numeric(result.remaining),
    unit: text(result.unit),
    plan_name: text(result.plan_name),
    total: numeric(result.total),
    used: numeric(result.used),
    extra: text(result.extra),
    queried_at: result.queried_at,
  };
}

/** Mirrors the backend dispatch in ManagementService.balance(). */
export function providerSupportsBalance(
  provider: Pick<Provider, "provider_type" | "base_url" | "capabilities">,
): boolean {
  // A custom balance query replaces the built-in official dispatch entirely.
  if (providerBalanceQueryConfig(provider)?.enabled) return true;
  // Codex reports rolling 5h / weekly plan usage instead of a credit balance.
  if (provider.provider_type === "codex_chatgpt") return true;
  let host = "";
  try {
    if (provider.base_url) {
      const url = new URL(provider.base_url);
      if (url.protocol === "https:") host = url.hostname.toLowerCase();
    }
  } catch {
    return false;
  }
  if (!host) return false;
  if (BALANCE_OFFICIAL_HOSTS.has(host)) return true;
  if (
    BALANCE_UNSUPPORTED_HOSTS.has(host) ||
    host.endsWith(".maas.aliyuncs.com")
  ) {
    return false;
  }
  return GATEWAY_BILLING_PROVIDER_TYPES.has(provider.provider_type);
}

/** Read sanitized custom request headers from Provider capabilities. */
export function providerExtraHeaders(
  provider: Pick<Provider, "capabilities">,
): Record<string, string> {
  const raw = provider.capabilities?.extra_headers;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const name = key.trim();
    const text = typeof value === "string" ? value.trim() : String(value ?? "").trim();
    if (!name || !text) continue;
    headers[name] = text;
  }
  return headers;
}

export interface ProviderSecretLifecycle {
  provider_id: string;
  api_key_masked: string | null;
  status: string;
  secret_version: number;
  key_version: number;
  rotated_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
}

export interface ProviderBalanceInfo {
  currency: "CNY" | "USD";
  total_balance: string;
  granted_balance: string | null;
  topped_up_balance: string | null;
}

export interface ProviderUsageWindow {
  label: string;
  used_percent: number;
  window_minutes: number | null;
  resets_at: IsoDateTime | null;
}

export interface ProviderBalance {
  provider_id: string;
  vendor: string;
  vendor_label: string;
  is_available: boolean;
  balance_infos: ProviderBalanceInfo[];
  usage_windows: ProviderUsageWindow[] | null;
  notice: string | null;
  queried_at: IsoDateTime;
}

export interface CodexDeviceLoginStart {
  device_auth_id: string;
  user_code: string;
  verification_url: string;
  interval_seconds: number;
}

export interface CodexDeviceLoginPoll {
  status: "pending" | "authorized";
  /** The Codex token set, returned once so it can be saved as the secret. */
  api_key: string | null;
  account_id: string | null;
  plan_type: string | null;
}

export interface SecretStoreStatus {
  provider: "environment" | "keyring";
  available: boolean;
  secure_backend: boolean;
  backend_name: string;
  active_key_version: number | null;
}

export interface MasterKeyRotation {
  provider: string;
  previous_key_version: number;
  active_key_version: number;
  reencrypted_secrets: number;
}

export interface ProviderModel {
  id: string;
  roles: string[];
  streaming: boolean;
  remote: boolean;
  enabled?: boolean;
  capabilities?: ProviderModelCapabilities;
  [key: string]: unknown;
}

export interface ProviderModelsResponse {
  provider_id: string;
  status: string;
  models: ProviderModel[];
  notice?: string;
}

export type ThinkingMode = "off" | "low" | "medium" | "high" | "xhigh";
export type ReasoningParameter =
  | "reasoning_effort"
  | "reasoning.effort"
  | "enable_thinking"
  | "thinking_budget"
  | "thinking";
export type SearchRoute =
  | "disabled"
  | "model_native"
  | "external"
  | "local"
  | "auto";
export type ImageInputMode = "native" | "external_vision" | "auto";
export type ModelCapabilitySource =
  | "user_declared"
  | "provider_probe"
  | "official_catalog"
  | "runtime_observation";

export interface ProviderModelCapabilities {
  reasoning_efforts: Exclude<ThinkingMode, "off">[];
  thinking_mapping: Partial<Record<ThinkingMode, string | number | boolean | null>>;
  default_thinking_mode: ThinkingMode;
  reasoning_parameter: ReasoningParameter;
  thinking_required?: boolean;
  hosted_web_search: boolean;
  hosted_web_fetch?: boolean;
  hosted_image_search?: boolean;
  supports_image_input: boolean;
  supports_video_input?: boolean;
  supports_structured_output?: boolean;
  supports_agent_tools?: boolean;
  /** How image attachments should reach this model. Default auto. */
  image_input_mode?: ImageInputMode;
  default_search_route: SearchRoute;
  capability_source: ModelCapabilitySource;
  /** Vendor-declared physical context window. */
  context_window_tokens: number;
  /** Workspace-selected cap; cannot exceed the physical window. */
  context_limit_tokens: number;
  max_output_tokens: number;
}

export type ProviderModelCapabilityUpdateRequest = ProviderModelCapabilities;

export interface ProviderModelCapabilityView {
  provider_id: string;
  model_id: string;
  capabilities: ProviderModelCapabilities;
}

export interface ProviderModelDefaultsView {
  model_id: string;
  provider_type: string | null;
  capabilities: ProviderModelCapabilities & UnknownRecord;
}

export interface ProviderModelCatalogSyncView {
  provider_id: string;
  models: ProviderModelCapabilityView[];
}

export interface ProviderModelStateView {
  provider_id: string;
  model_id: string;
  enabled: boolean;
  is_default: boolean;
}

export interface ProviderModelStatesView {
  provider_id: string;
  states: Record<string, boolean>;
  default_model: string | null;
}
