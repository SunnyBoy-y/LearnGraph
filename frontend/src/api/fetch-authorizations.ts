import type { FetchAuthorizationDecision } from "@/types/fetch-authorization";

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
