import type {
  FetchAuthorizationDecision,
  WebFetchRuntimeSettings,
} from "@/types/fetch-authorization";

import { apiClient } from "./client";

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
