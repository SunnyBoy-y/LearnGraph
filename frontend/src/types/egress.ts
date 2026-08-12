/**
 * Generic Agent egress approval channel (D2.1).
 *
 * Mirrors `backend/app/domain/schemas/egress_authorization.py`. The only
 * authorization resource is a canonical exact hostname — decisions never
 * bypass the sandbox egress proxy classifier (private/loopback/metadata
 * targets stay denied).
 */

export type EgressAuthorizationDecision = "allow_once" | "allow_always" | "deny";

/** Data carried by an ``egress_authorization`` message part (D2.1 card). */
export interface EgressAuthorizationCardData {
  authorization_request_id?: string;
  tool_call_id?: string;
  tool_name?: string;
  tool_label?: string;
  hostname?: string;
  requested_url?: string;
  request_spec_sha256?: string;
  resource_summary?: string;
  destination_path?: string;
  message_zh?: string;
  /** Terminal decision persisted on the message part (approved / denied). */
  decision?: EgressAuthorizationDecision;
  authorization_status?: "approved" | "denied" | "pending";
  /** Server resumes the suspended Agent turn after approval. */
  resume_mode?: "server";
}

export type EgressAuthorizationStatus =
  | "pending"
  | "approved"
  | "claimed"
  | "denied"
  | "expired"
  | "consumed";

export type EgressAuthorizationRequest = {
  id: string;
  workspace_id: string;
  hostname: string;
  capability: string;
  requested_by: string;
  chat_session_id: string | null;
  status: EgressAuthorizationStatus;
  decision: EgressAuthorizationDecision | null;
  allow_always: boolean;
  decided_by: string | null;
  decided_at: string | null;
  expires_at: string;
  ttl_seconds: number;
  request_context: Record<string, unknown> | null;
  consumed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type EgressAuthorizationListResponse = {
  items: EgressAuthorizationRequest[];
  total: number;
  offset: number;
  limit: number;
};

export type EgressAuthorizationCreateRequest = {
  hostname: string;
  chat_session_id?: string | null;
  purpose?: string | null;
  request_context?: Record<string, unknown> | null;
  ttl_seconds?: number;
};
