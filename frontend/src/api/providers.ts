import type {
  ProviderBalance,
  Provider,
  ProviderCreateRequest,
  ProviderUpdateRequest,
  ProviderModelsResponse,
  ProviderModelCapabilityUpdateRequest,
  ProviderModelCapabilityView,
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

export function getProviderModelCapabilities(
  providerId: string,
  modelId: string,
): Promise<ProviderModelCapabilityView> {
  return apiClient.get<ProviderModelCapabilityView>(
    `/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}/capabilities`,
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
    `/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}/capabilities`,
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

export function updateProviderModelStates(
  providerId: string,
  states: Record<string, boolean>,
): Promise<ProviderModelStatesView> {
  return apiClient.patch<
    ProviderModelStatesView,
    { states: Record<string, boolean> }
  >(`/providers/${encodeURIComponent(providerId)}/models`, { states });
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
