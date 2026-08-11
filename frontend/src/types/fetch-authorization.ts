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
