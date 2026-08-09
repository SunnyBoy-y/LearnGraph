export interface ContextEvidence {
  kind: string
  target_id: string
  title: string
  content: string
  content_hash: string
  source_event_id: string
  scope: string
  confidence: number
  status: string
  retrieval_reason: string
  trust: string
  score: number
  component_scores: Record<string, number>
  reason_codes: string[]
  token_cost: number
  manifest_status: string
}

export interface ContextBuildRequest {
  conversation_id?: string
  message_id?: string
  task_id?: string
  query: string
  token_budget?: number
  agent_id?: string
  provider_id?: string
  model_id?: string
  allowed_sensitivity?: string[]
  debug_manifest?: boolean
}

export interface ContextBuildView {
  context_build_id: string
  task_state: Record<string, unknown> | null
  recent_context: Array<Record<string, unknown>>
  memories: ContextEvidence[]
  candidate_memories: ContextEvidence[]
  retrieved_memories: ContextEvidence[]
  selected_memories: ContextEvidence[]
  injected_memories: ContextEvidence[]
  excluded_memories: ContextEvidence[]
  truncated_memories: ContextEvidence[]
  candidate_count: number
  retrieved_count: number
  selected_count: number
  injected_count: number
  excluded_count: number
  truncated_count: number
  manifest_status: string
  episodes: Array<Record<string, unknown>>
  project_decisions: Array<Record<string, unknown>>
  file_chunks: Array<Record<string, unknown>>
  learning_states: Array<Record<string, unknown>>
  strategies: Array<Record<string, unknown>>
  tool_candidates: Array<Record<string, unknown>>
  provider_messages: Array<Record<string, unknown>>
  context_manifest: Array<Record<string, unknown>>
  section_tokens: Record<string, number>
  total_tokens: number
  package_hash: string
  excluded: Record<string, number>
  degraded_modes: string[]
}

export interface ContextManifestReceipt {
  context_build_id: string
  session_id: string | null
  message_id: string | null
  status: string
  candidate_ids: string[]
  retrieved_ids: string[]
  selected_ids: string[]
  injected_ids: string[]
  excluded_ids: string[]
  truncated_ids: string[]
  reason_codes: Record<string, string[]>
  excluded_counts: Record<string, number>
  section_tokens: Record<string, number>
  total_tokens: number
  injected_tokens: number
  package_hash: string
  created_at: string
}
