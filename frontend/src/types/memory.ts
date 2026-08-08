import type { IsoDateTime } from './common'
import type {
  MemoryAudienceType,
  MemoryLifecycleStatus,
  MemorySensitivity,
} from './memory-events'

export type MemoryNamespace = 'workspace' | 'session'
export type MemoryScopeType = 'workspace' | 'goal' | 'node' | 'session'
export type MemoryZone = 'hot' | 'recent' | 'topics' | 'archive'
export type MemoryState = 'active' | 'deleted' | 'destroyed'
export type MemoryResolutionStatus =
  | 'none'
  | 'active_misconception'
  | 'improving'
  | 'resolved'
  | 'recurring'

export interface MemoryCreateRequest {
  title: string
  content: string
  namespace?: MemoryNamespace
  session_id?: string
  scope_type?: MemoryScopeType
  scope_id?: string
  goal_id?: string
  node_id?: string
  zone?: MemoryZone
  record_kind?: string
  structured_payload?: Record<string, unknown>
  confidence?: number
  importance?: number
  resolution_status?: MemoryResolutionStatus
  source?: string
  source_ids?: string[]
}

export interface MemoryUpdateRequest {
  expected_revision?: number
  title?: string
  content?: string
  zone?: MemoryZone
  source_ids?: string[]
  structured_payload?: Record<string, unknown>
  confidence?: number
  importance?: number
  resolution_status?: MemoryResolutionStatus
  goal_id?: string
  node_id?: string
  scope_type?: MemoryScopeType
  scope_id?: string
  reason?: string
}

export interface MemoryEntry {
  id: string
  lg_memory_id: string
  workspace_id: string
  namespace: MemoryNamespace
  session_id: string | null
  scope_type?: MemoryScopeType
  scope_id?: string | null
  goal_id?: string | null
  node_id?: string | null
  record_kind: string
  merge_strategy?: string
  zone: MemoryZone
  state: MemoryState
  title: string
  content_hash: string
  relative_path: string
  revision: number
  source: string
  source_ids: string[]
  structured_payload?: Record<string, unknown>
  atom_schema_version?: number
  canonical_key?: string
  atom_kind?: string
  ledger_status?: string
  temporal_status?: string
  summary_eligibility?: string
  valid_from?: IsoDateTime | null
  valid_until?: IsoDateTime | null
  event_at?: IsoDateTime | null
  next_review_at?: IsoDateTime | null
  last_verified_at?: IsoDateTime | null
  timezone_name?: string
  evidence_ids?: string[]
  confidence?: number
  importance?: number
  strength?: number
  access_count?: number
  confirmation_count?: number
  successful_use_count?: number
  last_accessed_at?: IsoDateTime | null
  resolution_status?: MemoryResolutionStatus
  decay_policy?: string
  supersedes_id?: string | null
  provider_id: string
  provider_binding_id: string | null
  deleted_at: IsoDateTime | null
  recoverable_until: IsoDateTime | null
  content_destroyed_at: IsoDateTime | null
  tenant_id?: string
  subject_user_id?: string | null
  audience_type?: MemoryAudienceType
  task_id?: string | null
  project_id?: string | null
  conversation_id?: string | null
  file_id?: string | null
  memory_layer?: 'L3' | 'L4' | 'L5' | 'L6'
  assertion_type?: 'explicit' | 'inferred' | 'system_observed' | 'file_derived' | 'tool_observed'
  sensitivity?: MemorySensitivity
  lifecycle_status?: MemoryLifecycleStatus
  superseded_by_id?: string | null
  head_event_id?: string | null
  projection_version?: number
  auto_recall_suppressed?: boolean
  child_agent_denied?: boolean
  restore_available: boolean
  view_source?: 'record' | 'event'
  created_at: IsoDateTime
  updated_at: IsoDateTime
  content: string | null
  retrieval_score?: number | null
}

export interface MemoryRevision {
  id: string
  memory_id: string
  revision: number
  base_revision: number | null
  operation: string
  title: string
  content: string | null
  content_hash: string
  namespace: MemoryNamespace
  session_id: string | null
  record_kind: string
  zone: MemoryZone
  source: string
  source_ids: string[]
  actor_id: string
  reason: string
  is_active: boolean
  created_at: IsoDateTime
}

export interface MemoryJournalEntry {
  id: string
  memory_id: string
  revision: number
  operation: string
  provider_id: string
  provider_epoch: number
  provider_record_id: string | null
  content_hash: string
  payload: Record<string, unknown>
  tombstone: boolean
  recoverable_until: IsoDateTime | null
  audit_retention_until: IsoDateTime | null
  content_scrubbed_at: IsoDateTime | null
  created_at: IsoDateTime
}

export interface MemoryBinding {
  id: string
  provider_instance_id: string
  memory_id: string
  revision: number
  provider_record_id: string
  provider_entity_kind: string
  provider_entity_value: string
  source_content_hash: string
  target_readback_hash: string
  import_event_id: string | null
  binding_status: string
  verified_at: IsoDateTime | null
  last_error: string
}

export interface MemoryPurgeResult {
  content_keys_destroyed: number
  journal_entries_removed: number
}

export interface MemoryPolicy {
  workspace_id: string
  workspace_enabled: boolean
  session_id: string | null
  session_enabled: boolean | null
  effective_enabled: boolean
  workspace_recall_enabled?: boolean
  workspace_learning_enabled?: boolean
  session_recall_enabled?: boolean | null
  session_learning_enabled?: boolean | null
  effective_recall_enabled?: boolean
  effective_learning_enabled?: boolean
}

export interface MemoryPolicyUpdateRequest {
  workspace_enabled?: boolean
  workspace_recall_enabled?: boolean
  workspace_learning_enabled?: boolean
  session_id?: string
  session_enabled?: boolean
  session_recall_enabled?: boolean
  session_learning_enabled?: boolean
  all_sessions_shared?: boolean
}

export interface MemoryProfile {
  id: string | null
  workspace_id: string
  owner_subject_id: string
  version: number
  status: 'empty' | 'atomic_snapshot' | 'ready' | 'stale' | 'building' | 'failed'
  markdown: string
  structured_sections: Array<{
    heading: string
    paragraphs: Array<{
      id?: string
      text: string
      atom_ids: string[]
    }>
  }>
  source_atom_ids: string[]
  source_fingerprint: string
  generated_at: IsoDateTime | null
  updated_at: IsoDateTime | null
  stale_reason: string
}

export interface MemoryProfileIntentRequest {
  text: string
  selected_text?: string
  selected_atom_ids?: string[]
  timezone_name?: string
}

export interface MemoryProfileIntentResult {
  status: string
  drafts_created: number
  auto_committed: number
  affected_memory_ids: string[]
  profile_status: string
}

export interface MemoryProviderStatus {
  provider_id: string
  provider_type: string
  display_name: string
  available: boolean
  remote_capability: boolean
  status: string
  provider_epoch: number
  frozen_memories: number
  details: Record<string, unknown>
}

export interface MemoryProviderMigrationResult {
  migrated: number
  failed: number
  remaining: number
}

export interface MemoryTypeDefinition {
  memory_type: string
  default_scope: MemoryScopeType
  merge_strategy: string
  decay_policy: string
  requires_confirmation: boolean
  description: string
  payload_schema: Record<string, string>
}

export interface MemoryExtractionSettings {
  enabled: boolean
  provider_id: string
  model_id: string
  follow_conversation: boolean
  auto_commit: boolean
}

export interface MemoryEmbeddingSettings {
  enabled: boolean
  provider_id: string
  model_id: string
  semantic_weight: number
}

export interface MemorySummarizationSettings {
  enabled: boolean
  provider_id: string
  model_id: string
  follow_conversation: boolean
}

export interface MemoryEmbeddingStaleInfo {
  model_key: string
  indexed_count: number
}

export interface MemoryEmbeddingCacheInvalidated {
  previous_model_key: string
  previous_indexed_count: number
  current_model_key: string
}

export interface MemoryEnhancement {
  workspace_id: string
  extraction: MemoryExtractionSettings
  embedding: MemoryEmbeddingSettings
  summarization: MemorySummarizationSettings
  active_memories: number
  indexed_memories: number
  current_model_key: string | null
  stale_model_keys: MemoryEmbeddingStaleInfo[]
  cache_invalidated: MemoryEmbeddingCacheInvalidated | null
}

export interface MemoryEnhancementUpdateRequest {
  extraction?: Partial<MemoryExtractionSettings>
  embedding?: Partial<MemoryEmbeddingSettings>
  summarization?: Partial<MemorySummarizationSettings>
}

export interface MemoryEmbeddingReindexResult {
  model_key: string
  total_active: number
  embedded: number
  already_indexed: number
  stale_freed: number
  truncated: boolean
}

export interface MemoryEmbeddingPruneResult {
  freed_count: number
  keep_model_key: string | null
}

export interface MemoryExtractionRunResult {
  status: string
  session_id?: string
  messages_reviewed?: number
  drafts_created: number
  auto_committed?: number
  skipped?: number
  blockers?: string[]
  completion_reason?: string
}

export interface ContextSummary {
  id: string
  session_id: string
  version: number
  kind: string
  source_message_ids: string[]
  summary: string
  estimated_tokens_before: number
  estimated_tokens_after: number
  created_at: string
}

export interface ContextSummarizationRunResult {
  status: string
  session_id?: string
  version?: number
  covered_messages?: number
  newly_summarized?: number
  summary?: ContextSummary | null
}

export interface EffectiveMemoryPackage {
  session_id: string | null
  goal_id: string | null
  node_ids: string[]
  effective_memories: MemoryEntry[]
  conflicts: Array<Record<string, unknown>>
  prompt_block: string
  token_estimate: number
}
