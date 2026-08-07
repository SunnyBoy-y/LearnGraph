import type {
  CodexDeviceLoginPoll,
  CodexDeviceLoginStart,
  CopilotDeviceLoginPoll,
  CopilotDeviceLoginStart,
  ProviderBalance,
  ProviderBalanceQueryConfig,
  ProviderBalanceQueryExecuteResponse,
  ProviderBalanceQueryHttpRequest,
  ProviderBalanceQueryLastResult,
  Provider,
  ProviderCreateRequest,
  ProviderUpdateRequest,
  ProviderModelsResponse,
  ProviderModelCapabilityUpdateRequest,
  ProviderModelCapabilityView,
  ProviderModelCatalogSyncView,
  ProviderModelDeleteView,
  ProviderModelDefaultsView,
  ProviderModelStateView,
  ProviderModelStatesView,
  ProviderSecretLifecycle,
  ProviderTypeCatalogItem,
  SecretStoreStatus,
  MasterKeyRotation,
} from "@/types/providers";

import { apiClient } from "./client";

export function listProviders(): Promise<Provider[]> {
  return apiClient.get<Provider[]>("/providers");
}

export function listProviderCatalog(): Promise<ProviderTypeCatalogItem[]> {
  return apiClient.get<ProviderTypeCatalogItem[]>("/providers/catalog");
}

export function createProvider(
  payload: ProviderCreateRequest,
): Promise<Provider> {
  return apiClient.post<Provider, ProviderCreateRequest>("/providers", payload);
}

export function discoverProviderModels(
  providerId: string,
): Promise<ProviderModelsResponse> {
  return apiClient.get<ProviderModelsResponse>(
    `/providers/${encodeURIComponent(providerId)}/models`,
  );
}

export function getProviderBalance(providerId: string): Promise<ProviderBalance> {
  return apiClient.get<ProviderBalance>(
    `/providers/${encodeURIComponent(providerId)}/balance`,
  );
}

export function getProviderBalanceQueryConfig(
  providerId: string,
): Promise<{ provider_id: string; config: ProviderBalanceQueryConfig | null }> {
  return apiClient.get(
    `/providers/${encodeURIComponent(providerId)}/balance-query`,
  );
}

export function updateProviderBalanceQueryConfig(
  providerId: string,
  config: ProviderBalanceQueryConfig | null,
): Promise<{ provider_id: string; config: ProviderBalanceQueryConfig | null }> {
  return apiClient.put(
    `/providers/${encodeURIComponent(providerId)}/balance-query`,
    { config },
  );
}

export function executeProviderBalanceQuery(
  providerId: string,
  payload: {
    request: ProviderBalanceQueryHttpRequest;
    timeout_seconds?: number | null;
    variables?: Record<string, string> | null;
  },
): Promise<ProviderBalanceQueryExecuteResponse> {
  return apiClient.post(
    `/providers/${encodeURIComponent(providerId)}/balance-query/execute`,
    payload,
  );
}

export function saveProviderBalanceQueryResult(
  providerId: string,
  payload: {
    is_valid?: boolean | null;
    invalid_message?: string | null;
    remaining?: number | null;
    unit?: string | null;
    plan_name?: string | null;
    total?: number | null;
    used?: number | null;
    extra?: string | null;
  },
): Promise<ProviderBalanceQueryLastResult & { provider_id: string }> {
  return apiClient.put(
    `/providers/${encodeURIComponent(providerId)}/balance-query/result`,
    payload,
  );
}

export function startCopilotDeviceLogin(): Promise<CopilotDeviceLoginStart> {
  return apiClient.post<CopilotDeviceLoginStart, Record<string, never>>(
    "/providers/copilot/device-login",
    {},
  );
}

export function pollCopilotDeviceLogin(payload: {
  device_auth_id: string;
  user_code: string;
}): Promise<CopilotDeviceLoginPoll> {
  return apiClient.post<CopilotDeviceLoginPoll, typeof payload>(
    "/providers/copilot/device-login/poll",
    payload,
  );
}

export function startCodexDeviceLogin(): Promise<CodexDeviceLoginStart> {
  return apiClient.post<CodexDeviceLoginStart, Record<string, never>>(
    "/providers/codex/device-login",
    {},
  );
}

export function pollCodexDeviceLogin(payload: {
  device_auth_id: string;
  user_code: string;
}): Promise<CodexDeviceLoginPoll> {
  return apiClient.post<CodexDeviceLoginPoll, typeof payload>(
    "/providers/codex/device-login/poll",
    payload,
  );
}

export function getProviderModelCapabilities(
  providerId: string,
  modelId: string,
): Promise<ProviderModelCapabilityView> {
  return apiClient.get<ProviderModelCapabilityView>(
    `/providers/${encodeURIComponent(providerId)}/model-capabilities?model_id=${encodeURIComponent(modelId)}`,
  );
}

export function getProviderModelDefaults(
  modelId: string,
  providerType?: string,
): Promise<ProviderModelDefaultsView> {
  const query = providerType
    ? `?provider_type=${encodeURIComponent(providerType)}`
    : "";
  return apiClient.get<ProviderModelDefaultsView>(
    `/providers/model-defaults/${encodeURIComponent(modelId)}${query}`,
  );
}

export function updateProviderModelCapabilities(
  providerId: string,
  modelId: string,
  payload: ProviderModelCapabilityUpdateRequest,
): Promise<ProviderModelCapabilityView> {
  return apiClient.put<
    ProviderModelCapabilityView,
    ProviderModelCapabilityUpdateRequest
  >(
    `/providers/${encodeURIComponent(providerId)}/model-capabilities?model_id=${encodeURIComponent(modelId)}`,
    payload,
  );
}

export function updateProviderModelGroupCapabilities(
  providerId: string,
  payload: ProviderModelCapabilityUpdateRequest,
): Promise<ProviderModelCapabilityView> {
  return apiClient.put<
    ProviderModelCapabilityView,
    ProviderModelCapabilityUpdateRequest
  >(`/providers/${encodeURIComponent(providerId)}/models/capabilities`, payload);
}

export function syncProviderModelCatalogDefaults(
  providerId: string,
  modelIds: string[],
): Promise<ProviderModelCatalogSyncView> {
  return apiClient.post<
    ProviderModelCatalogSyncView,
    { model_ids: string[] }
  >(
    `/providers/${encodeURIComponent(providerId)}/models/sync-catalog-defaults`,
    { model_ids: modelIds },
  );
}

export function updateProviderModelStates(
  providerId: string,
  states: Record<string, boolean>,
): Promise<ProviderModelStatesView> {
  return apiClient.patch<
    ProviderModelStatesView,
    { states: Record<string, boolean> }
  >(`/providers/${encodeURIComponent(providerId)}/models`, { states });
}

export function deleteProviderModel(
  providerId: string,
  modelId: string,
): Promise<ProviderModelDeleteView> {
  return apiClient.delete<ProviderModelDeleteView>(
    `/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`,
  );
}

export function updateProviderModelState(
  providerId: string,
  modelId: string,
  enabled: boolean,
): Promise<ProviderModelStateView> {
  return apiClient.patch<ProviderModelStateView, { enabled: boolean }>(
    `/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`,
    { enabled },
  );
}

export function updateProvider(
  providerId: string,
  payload: ProviderUpdateRequest,
): Promise<Provider> {
  return apiClient.patch<Provider, ProviderUpdateRequest>(
    `/providers/${encodeURIComponent(providerId)}`,
    payload,
  );
}

export function deleteProvider(providerId: string): Promise<{ status: string; resource_id: string }> {
  return apiClient.delete<{ status: string; resource_id: string }>(
    `/providers/${encodeURIComponent(providerId)}`,
  )
}

export function probeProvider(providerId: string): Promise<Provider> {
  return apiClient.post<Provider>(
    `/providers/${encodeURIComponent(providerId)}/probe`,
  );
}

export function getSecretStoreStatus(): Promise<SecretStoreStatus> {
  return apiClient.get<SecretStoreStatus>("/providers/secret-store/status");
}

export function rotateProviderSecret(
  providerId: string,
  apiKey: string,
): Promise<ProviderSecretLifecycle> {
  return apiClient.post<ProviderSecretLifecycle, { api_key: string }>(
    `/providers/${encodeURIComponent(providerId)}/rotate-secret`,
    { api_key: apiKey },
  );
}

export function rotateProviderMasterKey(): Promise<MasterKeyRotation> {
  return apiClient.post<MasterKeyRotation>(
    "/providers/secret-store/rotate-master-key",
  );
}
