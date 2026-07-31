export interface ContextEvidence {
  kind: string
  target_id: string
  title: string
  content: string
  source_event_id: string
  scope: string
  confidence: number
  status: string
  retrieval_reason: string
  trust: string
  score: number
  component_scores: Record<string, number>
}

export interface ContextBuildRequest {
  conversation_id?: string
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
