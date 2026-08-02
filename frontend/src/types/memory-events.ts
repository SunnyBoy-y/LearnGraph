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

/**
 * Forget cleanup status. The backend destroys the event payload key and fans out
 * deletions across projections; external provider deletions may still be pending
 * or retrying, so the UI must never report success until every target is clean.
 */
export interface MemoryForgetResult {
  memory_id: string
  content_key_destroyed: boolean
  projections_removed: string[]
  external_provider_pending: boolean
  external_provider_last_error: string | null
  completed_at: string | null
}

/**
 * Supersede request: the user says a memory is wrong and supplies the corrected
 * title/content. The backend records a `wrong` feedback event carrying the
 * replacement so a new active memory can be derived without overwriting history.
 */
export interface MemorySupersedeRequest {
  replacement_title: string
  replacement_content: string
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
