import type {
  EgressAuthorizationCreateRequest,
  EgressAuthorizationDecision,
  EgressAuthorizationListResponse,
  EgressAuthorizationRequest,
} from "@/types/egress";

import { apiClient } from "./client";

export function listEgressApprovals(options: {
  status?: string;
  requestedBy?: string;
  offset?: number;
  limit?: number;
} = {}): Promise<EgressAuthorizationListResponse> {
  const query: Record<string, string | number> = {};
  if (options.status) query.status = options.status;
  if (options.requestedBy) query.requested_by = options.requestedBy;
  if (options.offset !== undefined) query.offset = options.offset;
  if (options.limit !== undefined) query.limit = options.limit;
  return apiClient.get<EgressAuthorizationListResponse>("/egress-approvals", {
    query,
  });
}

export function createEgressApproval(
  payload: EgressAuthorizationCreateRequest,
): Promise<EgressAuthorizationRequest> {
  return apiClient.post<EgressAuthorizationRequest, EgressAuthorizationCreateRequest>(
    "/egress-approvals",
    payload,
  );
}

export function decideEgressApproval(
  requestId: string,
  decision: EgressAuthorizationDecision,
): Promise<EgressAuthorizationRequest> {
  return apiClient.post<
    EgressAuthorizationRequest,
    { decision: EgressAuthorizationDecision }
  >(`/egress-approvals/${encodeURIComponent(requestId)}/decision`, { decision });
}

export function resumeEgressApproval(
  requestId: string,
): Promise<{ status: string; assistant_message_id: string }> {
  return apiClient.post<{ status: string; assistant_message_id: string }, Record<string, never>>(
    `/egress-approvals/${encodeURIComponent(requestId)}/resume`,
    {},
  );
}
