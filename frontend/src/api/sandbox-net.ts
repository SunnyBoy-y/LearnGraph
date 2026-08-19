import { apiClient } from "./client";

export interface SandboxNetAuditEntry {
  id: string;
  created_at: string;
  action: string;
  outcome: string;
  details: Record<string, unknown>;
}

export interface SandboxNetAuditListResponse {
  items: SandboxNetAuditEntry[];
}

/** Read-only audit trail for frontend-sandbox network relays (免审批直连记录). */
export function listSandboxNetAudit(options: { limit?: number } = {}): Promise<SandboxNetAuditListResponse> {
  return apiClient.get<SandboxNetAuditListResponse>("/sandbox-net/audit", {
    query: options.limit ? { limit: options.limit } : undefined,
  });
}
