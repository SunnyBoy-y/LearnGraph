import type { IsoDateTime, UnknownRecord } from "./common";

export type ProviderRole =
  | "model"
  | "image_generation"
  | "vision"
  | "search"
  | "fetch"
  | "deep_research"
  | "memory"
  | "transcription";

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
  granted_balance: string;
  topped_up_balance: string;
}

export interface ProviderBalance {
  provider_id: string;
  is_available: boolean;
  balance_infos: ProviderBalanceInfo[];
  queried_at: IsoDateTime;
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
export type ReasoningParameter = "reasoning_effort" | "reasoning.effort";
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
  thinking_mapping: Partial<Record<ThinkingMode, string | null>>;
  default_thinking_mode: ThinkingMode;
  reasoning_parameter: ReasoningParameter;
  hosted_web_search: boolean;
  supports_image_input: boolean;
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
