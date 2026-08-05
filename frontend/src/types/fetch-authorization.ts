export type FetchAuthorizationDecision =
  | "allow_once"
  | "allow_always"
  | "deny";

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
