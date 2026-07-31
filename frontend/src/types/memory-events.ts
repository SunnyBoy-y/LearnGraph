export type MemoryAudienceType = 'tenant' | 'user' | 'workspace' | 'task'
export type MemorySensitivity = 'public' | 'normal' | 'private' | 'sensitive' | 'restricted'
export type MemoryLifecycleStatus =
  | 'active'
  | 'superseded'
  | 'resolved'
  | 'archived'
  | 'deleted'
  | 'disputed'
  | 'needs_confirmation'
  | 'stale'
  | 'retracted'
  | 'forgotten'

export interface MemoryEventView {
  event_id: string
  stream_id: string
  stream_version: number
  global_position: number
  event_type: string
  event_schema_version: number
  payload_hash: string
  occurred_at: string
  ingested_at: string
  idempotent_replay: boolean
}

export interface MemoryFeedbackRequest {
  feedback_type:
    | 'correct'
    | 'stale'
    | 'wrong'
    | 'should_not_store'
    | 'project_only'
    | 'durable'
    | 'deny_child'
    | 'suppress_auto_recall'
  payload?: Record<string, unknown>
}

export interface MemoryForgetRequest {
  confirmation: string
  reason?: string
}

export interface MemoryArchitectureStatus {
  write_mode: 'legacy' | 'dual' | 'events'
  read_mode: 'legacy' | 'shadow' | 'events'
  shadow_sample_rate: number
  context_builder_v2: boolean
  task_episode_enabled: boolean
  file_revision_invalidation_enabled: boolean
  agent_run_enabled: boolean
  strategy_enabled: boolean
}
