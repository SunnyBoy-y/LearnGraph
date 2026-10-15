import { apiClient, type ApiRequestOptions } from './client';
import type { ProviderBalance } from '@/types/providers';

export interface BalanceStatus {
  provider_id: string;
  state: string;
  method_id: string | null;
  last_result: ProviderBalance | null;
  last_attempt_at: string | null;
  next_query_at: string | null;
  last_error: string | null;
  auto_refresh_enabled: boolean;
  request_count: number;
}

export function getBalanceStatus(id: string, options?: ApiRequestOptions) {
  return apiClient.get<BalanceStatus>(`/providers/${encodeURIComponent(id)}/balance-status`, options);
}
export function rediscoverBalance(id: string, options?: ApiRequestOptions) {
  return apiClient.post<BalanceStatus, Record<string, never>>(`/providers/${encodeURIComponent(id)}/balance-discovery`, {}, options);
}

export interface ConfigSource { id: string; label: string; kind: string; status: string; message: string }
export interface ConfigCandidate {
  id: string; name: string; source_id: string; source_label: string;
  provider_type: string; base_url: string; has_key: boolean; model_ids: string[];
  duplicate: boolean; duplicate_provider_id?: string | null;
}
export interface ConfigPreview { preview_id: string; candidates: ConfigCandidate[]; warnings: string[] }
export interface ConfigImportResult {
  created: { name: string; provider_id: string; enabled: boolean; models: string[] }[];
  skipped: { name: string; reason: string; provider_id?: string }[];
  failed: { name: string; reason: string }[];
}
export function getConfigSources() {
  return apiClient.get<{ sources: ConfigSource[] }>('/providers/config-import/sources');
}
export function previewConfigSources(sourceIds: string[]) {
  return apiClient.post<ConfigPreview, { source_ids: string[] }>('/providers/config-import/preview', { source_ids: sourceIds });
}
export function commitConfigPreview(previewId: string, candidateIds: string[]) {
  return apiClient.post<ConfigImportResult, { preview_id: string; candidate_ids: string[] }>('/providers/config-import/commit', { preview_id: previewId, candidate_ids: candidateIds });
}
