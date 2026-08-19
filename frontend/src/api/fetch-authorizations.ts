import type {
  FetchAuthorizationDecision,
  FetchAuthorizationListResponse,
  FetchUserPolicy,
  WebFetchRuntimeSettings,
} from "@/types/fetch-authorization";

import { apiClient } from "./client";

export function listFetchAuthorizations(options: {
  status?: string;
  offset?: number;
  limit?: number;
} = {}): Promise<FetchAuthorizationListResponse> {
  const query: Record<string, string | number> = {};
  if (options.status) query.status = options.status;
  if (options.offset !== undefined) query.offset = options.offset;
  if (options.limit !== undefined) query.limit = options.limit;
  return apiClient.get<FetchAuthorizationListResponse>("/fetch-authorizations", {
    query,
  });
}

export function getFetchUserPolicy(): Promise<FetchUserPolicy> {
  return apiClient.get<FetchUserPolicy>("/fetch-authorizations/user-policy");
}

export function updateFetchUserPolicy(
  payload: FetchUserPolicy,
): Promise<FetchUserPolicy> {
  return apiClient.put<FetchUserPolicy, FetchUserPolicy>(
    "/fetch-authorizations/user-policy",
    payload,
  );
}

export function decideFetchAuthorization(
  requestId: string,
  decision: FetchAuthorizationDecision,
): Promise<void> {
  return apiClient.post<void, { decision: FetchAuthorizationDecision }>(
    `/fetch-authorizations/${encodeURIComponent(requestId)}/decision`,
    { decision },
  );
}

export function resumeFetchAuthorization(
  requestId: string,
): Promise<{ status: string; assistant_message_id: string }> {
  return apiClient.post<{ status: string; assistant_message_id: string }, Record<string, never>>(
    `/fetch-authorizations/${encodeURIComponent(requestId)}/resume`,
    {},
  );
}

export function getWebFetchSettings(): Promise<WebFetchRuntimeSettings> {
  return apiClient.get<WebFetchRuntimeSettings>("/fetch-authorizations/settings");
}

export function updateWebFetchSettings(
  payload: Pick<WebFetchRuntimeSettings, "sandbox_enabled" | "priority">,
): Promise<WebFetchRuntimeSettings> {
  return apiClient.put<WebFetchRuntimeSettings, Pick<WebFetchRuntimeSettings, "sandbox_enabled" | "priority">>(
    "/fetch-authorizations/settings",
    payload,
  );
}
