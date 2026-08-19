export type FetchAuthorizationDecision =
  | "allow_once"
  | "allow_always"
  | "deny";

export type WebFetchChannel = "sandbox" | "remote" | "hosted";

export interface WebFetchRuntimeSettings {
  /** Workspace-level sandbox fetch switch (global env gate still applies). */
  sandbox_enabled: boolean;
  /** Fetch channel order of preference; resolver picks the first usable one. */
  priority: WebFetchChannel[];
  persisted: boolean;
  // --- effective status (read-only, computed by the backend) ---------------
  global_sandbox_gate: boolean;
  egress_enabled: boolean;
  allowlist_count: number;
  /** 统一白名单「不拦截全放行」模式。 */
  allow_all: boolean;
  image_available: boolean;
  sandbox_effective: boolean;
  remote_configured: boolean;
  hosted_configured: boolean;
  effective_channel: WebFetchChannel | null;
}

export interface FetchAuthorizationData {
  authorization_request_id?: string;
  tool_call_id?: string;
  tool_name?: string;
  tool_label?: string;
  requested_url?: string;
  hostname?: string;
  message_zh?: string;
  /** Server resumes this paused 极速/思考 turn after approval. */
  resume_mode?: "server";
  /** Terminal decision persisted on the message part (approved / denied). */
  decision?: FetchAuthorizationDecision;
  authorization_status?: "approved" | "denied" | "pending";
}

/** Durable web-fetch approval record (fetch_authorization_requests row). */
export interface FetchAuthorizationRequestView {
  id: string;
  workspace_id: string;
  chat_session_id: string;
  tool_call_id: string;
  tool_name: string;
  requested_url: string;
  hostname: string;
  status: string;
  decision: FetchAuthorizationDecision | null;
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface FetchAuthorizationListResponse {
  items: FetchAuthorizationRequestView[];
  total: number;
  offset: number;
  limit: number;
}

/** Current user's personal web-fetch whitelist (聊天内「以后都允许」). */
export interface FetchUserPolicy {
  allowed_domains: string[];
  allow_without_confirmation: boolean;
}
