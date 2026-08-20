import { apiClient } from "./client";

export type AgentTaskStatus =
  | "QUEUED"
  | "RUNNING"
  | "FINALIZING"
  | "SUCCEEDED"
  | "PARTIAL"
  | "FAILED"
  | "TIMED_OUT"
  | "CANCELLED"
  | "INTERRUPTED";

export interface AgentTaskEvent {
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at?: string | null;
}

export interface AgentTaskAttempt {
  attempt: number;
  status?: string;
  rounds?: number;
  tool_calls?: number;
  token_usage?: number;
  cost_usd?: number;
  started_at?: number;
  finished_at?: number;
  error_class?: string | null;
  error_message?: string | null;
}

export interface AgentTaskDeliverableFile {
  path: string;
  change?: string;
  sha256?: string | null;
  file_id?: string | null;
  exists?: boolean;
}

export interface AgentTaskDeliverables {
  handoff_parse?: boolean;
  summary?: string;
  artifacts?: AgentTaskDeliverableFile[];
  evidence?: Array<Record<string, unknown>>;
  acceptance?: Array<Record<string, unknown>>;
  risks?: unknown[];
  unresolved?: unknown[];
  recommended_next_action?: string;
  confidence?: number;
  default_output_root?: string;
}

export interface AgentTask {
  id: string;
  task_id: string;
  plan_id?: string | null;
  chat_session_id: string;
  title: string;
  role_key: string;
  status: string;
  status_reason?: string | null;
  spec_json: Record<string, unknown>;
  latest_job_id?: string | null;
  attempts: AgentTaskAttempt[];
  deliverables?: AgentTaskDeliverables | null;
  result_text?: string | null;
  event_seq: number;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export function listAgentTasks(options: {
  chatSessionId?: string;
  planId?: string;
  status?: string;
  limit?: number;
} = {}): Promise<{ tasks: AgentTask[] }> {
  const query: Record<string, string | number> = {};
  if (options.chatSessionId) query.chat_session_id = options.chatSessionId;
  if (options.planId) query.plan_id = options.planId;
  if (options.status) query.status = options.status;
  if (options.limit !== undefined) query.limit = options.limit;
  return apiClient.get<{ tasks: AgentTask[] }>("/sandbox/agent-tasks", { query });
}

export function getAgentTask(taskId: string): Promise<AgentTask> {
  return apiClient.get<AgentTask>(
    `/sandbox/agent-tasks/${encodeURIComponent(taskId)}`,
  );
}

export function listAgentTaskEvents(
  taskId: string,
  afterSeq = 0,
  limit = 50,
): Promise<AgentTaskEvent[]> {
  return apiClient.get<AgentTaskEvent[]>(
    `/sandbox/agent-tasks/${encodeURIComponent(taskId)}/events`,
    { query: { after_seq: afterSeq, limit } },
  );
}

export function cancelAgentTask(taskId: string): Promise<AgentTask> {
  return apiClient.post<AgentTask, Record<string, never>>(
    `/sandbox/agent-tasks/${encodeURIComponent(taskId)}/cancel`,
    {},
  );
}

export function retryAgentTask(
  taskId: string,
  payload: {
    scope: "same" | "scoped";
    note?: string;
    prompt_override?: string;
  },
): Promise<AgentTask> {
  return apiClient.post<AgentTask, typeof payload>(
    `/sandbox/agent-tasks/${encodeURIComponent(taskId)}/retry`,
    payload,
  );
}
