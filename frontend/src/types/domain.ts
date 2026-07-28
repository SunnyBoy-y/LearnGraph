export type AuthSession = {
  accessToken: string
  userId: string
  username: string
  workspaceId: string
}

export type Workspace = {
  id: string
  tenant_id: string
  owner_user_id: string
  name: string
  description: string
  created_at: string
}

export type DashboardMetric = {
  key: string
  label: string
  value: string | number
  status: string
}

export type Dashboard = {
  workspace_id: string
  metrics: DashboardMetric[]
  next_actions: Array<{ id: string; title: string; description: string; status: string; source: string; action_type: string; project_id: string | null; goal_id: string | null; graph_id: string | null; node_id: string | null; due_at: string | null; priority: number }>
  system_status: Record<string, string>
}

export type Goal = {
  id: string
  workspace_id: string
  title: string
  raw_prompt: string
  status: string
  intent: string
  time_limit: string
  desired_outcome: string
  constraints: Record<string, unknown>
  assumptions: Array<Record<string, unknown>>
  target_weight: number
  deadline_at: string | null
  availability: { minutes_per_day: number; days_per_week: number }
  preferences: { preferred_action_types: string[]; session_minutes: number }
  created_at: string
  updated_at: string
}

export type ClarificationQuestion = {
  key: string
  prompt: string
  options: string[]
  required: boolean
}

export type ClarifyResponse = {
  goal: Goal
  questions: ClarificationQuestion[]
  provider: string
  remote_model_used: boolean
}

export type GraphNode = {
  id: string
  graph_id: string
  workspace_id: string
  label: string
  description: string
  node_type: string
  external_concept_id: string | null
  target_weight: number
  mastery_stars: number
  retrieval_state: string
  evidence_state: string
  attention_state: string
}

export type GraphEdge = {
  id: string
  graph_id: string
  workspace_id: string
  source_node_id: string
  target_node_id: string
  relation: string
}

export type GraphSummary = {
  id: string
  goal_id: string
  workspace_id: string
  title: string
  status: string
  revision: number
  published_at: string | null
}

export type Graph = GraphSummary & {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export type MessagePartType =
  | 'acknowledgement'
  | 'text'
  | 'reasoning_summary'
  | 'reasoning_content'
  | 'agent_step'
  | 'tool_call'
  | 'source_list'
  | 'attachment'
  | 'document_selection'
  | 'selection_quote'
  | 'image'
  | 'graph_context'
  | 'quiz'
  | 'chart'
  | 'sandbox'
  | 'sandbox_artifact'
  | 'sandbox_status'
  | 'component'
  | 'user_confirmation'
  | 'error'

export type MessagePart = {
  id: string
  type: MessagePartType
  status: 'pending' | 'streaming' | 'completed' | 'failed'
  content?: string | null
  content_delta?: string
  sequence?: number
  data?: Record<string, unknown>
}

export type ChatMessage = {
  id: string
  workspace_id?: string
  session_id?: string
  parent_message_id?: string | null
  role: 'user' | 'assistant' | 'system' | string
  version?: number
  status: string
  content: string
  parts: MessagePart[]
  provider_trace?: Record<string, unknown>
  created_at?: string
}

export type ChatSession = {
  id: string
  workspace_id: string
  title: string
  goal_id: string | null
  graph_id: string | null
  parent_session_id: string | null
  source_message_id: string | null
  memory_enabled: boolean
  model_snapshot: Record<string, unknown>
  created_at: string
}

export type FileRecord = {
  id: string
  original_name: string
  mime_type: string
  size_bytes: number
  storage_status: string
  parse_capability: string
  parse_status: string
  error_message: string | null
  created_at: string
}

export type Evidence = {
  id: string
  node_id: string
  source_type: string
  summary: string
  confidence: number
  status: string
  created_at: string
}

export type Mastery = {
  node_id: string
  label: string
  mastery_stars: number
  retrieval_state: string
  evidence_state: string
  attention_state: string
  accepted_evidence_count: number
}

export type Exercise = {
  id: string
  node_id: string
  question_type: string
  prompt: string
  options: string[]
  explanation: string
  created_at: string
}

export type MemoryEntry = {
  id: string
  namespace: string
  title: string
  content_hash: string
  relative_path: string
  revision: number
  source: string
  created_at: string
  updated_at: string
  content?: string | null
}

export type Provider = {
  id: string
  display_name: string
  provider_type: string
  base_url: string | null
  api_key_masked: string | null
  enabled: boolean
  remote_capability: boolean
  capabilities: Record<string, unknown>
  status: string
  created_at: string
}

export type Plugin = {
  id: string
  plugin_key: string
  name: string
  version: string
  plugin_type: string
  status: string
  enabled: boolean
  permissions: string[]
  capabilities: string[]
}

export type AuditEvent = {
  id: string
  actor_id: string
  action: string
  resource_type: string
  resource_id: string
  outcome: string
  trace_id: string
  details: Record<string, unknown>
  created_at: string
}
