import type { IsoDateTime, UnknownRecord } from './common'
import type { DeleteImpact } from './workflow'

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
  | 'skill_trigger'
  | 'component'
  | 'magic_card'
  | 'user_confirmation'
  | 'error'

export type MessagePartStatus = 'pending' | 'streaming' | 'completed' | 'failed'

export interface MessagePart {
  id: string
  type: MessagePartType
  status: MessagePartStatus
  content?: string | null
  content_delta?: string
  sequence?: number
  data?: UnknownRecord
}

export interface SessionCreateRequest {
  title?: string
  goal_id?: string | null
  graph_id?: string | null
  project_id?: string | null
  memory_enabled?: boolean
}

export interface SessionUpdateRequest {
  title?: string
  pinned?: boolean
  goal_id?: string
  graph_id?: string
}

export interface SessionAutoTitleRequest {
  source_message_id: string
  expected_title: string
  provider_id?: string
  model_id?: string
}

export interface DictationCleanupRequest {
  text: string
  context?: string
  provider_id?: string
  model_id?: string
}

export interface DictationCleanupResult {
  text: string
}

export interface Session {
  id: string
  workspace_id: string
  title: string
  goal_id: string | null
  graph_id: string | null
  project_id: string | null
  parent_session_id: string | null
  source_message_id: string | null
  memory_enabled: boolean
  pinned: boolean
  model_snapshot: UnknownRecord
  status: string
  closed_at: IsoDateTime | null
  archived_at: IsoDateTime | null
  session_kind: 'main' | 'concept_branch' | 'side' | 'standalone'
  writeback_policy: 'normal' | 'manual_only'
  context_capsule: UnknownRecord
  created_at: IsoDateTime
  /** Last activity (message / rename / pin). Used for sidebar recency sort. */
  updated_at: IsoDateTime
}

export interface ConceptBranchCreateRequest {
  title: string
  source_message_id?: string | null
  document_selection: DocumentSelectionContext
  selected_sentence?: string
  surrounding_text?: string
  source_title?: string
  current_node_id?: string | null
  relevant_parent_message_ids?: string[]
}

export interface SessionBatchDeleteImpact extends DeleteImpact {
  resource_type: 'session_batch'
  session_ids: string[]
}

export interface SessionBatchDeleteResult {
  status: 'deleted'
  deleted_session_ids: string[]
  deleted_count: number
  impacts: DeleteImpact['impacts']
}

export interface SuggestedPrompt {
  id: string
  content: string
}

export interface SuggestedPromptRequest {
  anchor_message_id: string | null
  anchor_message_version_id?: string | null
  provider_id?: string
  model_id?: string
  count?: 2 | 3
}

export interface SuggestedPromptBatch {
  id: string
  session_id: string
  anchor_message_id: string | null
  anchor_message_version_id: string | null
  prompts: SuggestedPrompt[]
  provider_trace: UnknownRecord
  memory_used: boolean
  generated_at: IsoDateTime
  cached: boolean
}

export interface MessageSelectionContext {
  source_message_id: string
  selected_text: string
  prefix: string
  suffix: string
}

export interface DocumentSelectionContext {
  file_id: string
  document_revision_id: string
  chunk_id: string
  locator: UnknownRecord
  selected_text: string
  selected_text_hash: string
}

export interface MessageCreateRequest {
  content: string
  /** Defaults to text for older callers; chat always sends this explicitly. */
  generation_mode?: 'text' | 'image'
  parent_message_id?: string | null
  node_ids?: string[]
  file_ids?: string[]
  document_selection?: DocumentSelectionContext
  /** Exact workspace-scoped provider selected in the chat composer. */
  provider_id?: string
  model_id?: string
  thinking_mode?: 'off' | 'low' | 'medium' | 'high' | 'xhigh'
  /** Enables the server-side, provider-native tool loop. */
  agent_mode?: boolean
  /** Activates the Goal-planning Agent Skill for this turn. Requires agent_mode. */
  goal_mode?: boolean
  search_route?: 'disabled' | 'model_native' | 'external' | 'local' | 'auto'
  web_search?: boolean
  allowed_domains?: string[]
  graph_action?: 'none' | 'propose_create' | 'propose_update'
  graph_id?: string | null
  selection_context?: MessageSelectionContext
}

export interface MessageRetryRequest {
  provider_id?: string
  model_id?: string
  thinking_mode?: MessageCreateRequest['thinking_mode']
  /** Explicitly selects Agent mode for the new version instead of inheriting it. */
  agent_mode?: boolean
  search_route?: MessageCreateRequest['search_route']
  web_search?: boolean
  allowed_domains?: string[]
}

export interface ConversationGraphNodeChange {
  ref: string
  change: 'add' | 'update'
  node_id: string | null
  label: string
  description: string
  node_type: 'root' | 'concept' | 'practice' | 'assessment'
  rationale: string
}

export interface ConversationGraphEdgeChange {
  source_ref: string
  target_ref: string
  relation: 'contains' | 'prerequisite' | 'related' | 'contrast' | 'application'
  rationale: string
}

export interface ConversationGraphProposal {
  graph_title: string
  summary: string
  nodes: ConversationGraphNodeChange[]
  edges: ConversationGraphEdgeChange[]
}

export interface GraphChangeSet {
  id: string
  workspace_id: string
  session_id: string
  goal_id: string
  graph_id: string | null
  source_user_message_id: string
  source_assistant_message_id: string
  mode: 'create' | 'update'
  status: 'proposed' | 'confirmed' | 'rejected'
  base_revision: number
  confirmed_revision: number | null
  proposal: ConversationGraphProposal
  result: UnknownRecord
  provider_trace: UnknownRecord
  reviewed_by: string | null
  reviewed_at: IsoDateTime | null
  rejection_reason: string
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export interface Message {
  id: string
  workspace_id: string
  session_id: string
  parent_message_id: string | null
  role: string
  version: number
  status: string
  content: string
  parts: MessagePart[]
  provider_trace: UnknownRecord
  created_at: IsoDateTime
}

export interface MessageVersion {
  id: string
  message_id: string
  version: number
  status: string
  provider_trace: UnknownRecord
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export interface MessageSnapshot extends Message {
  message_version_id: string
  last_event_id: string | null
  last_sequence: number
  updated_at: IsoDateTime
}

export interface BranchSessionRequest {
  title?: string
}

/** 会话上下文用量估算（展示用，非计费口径）。 */
export interface SessionContextUsage {
  session_id: string
  estimated_tokens: number
  input_budget_tokens: number
  compaction_threshold_tokens: number
  remaining_tokens: number
  used_ratio: number
  context_window_tokens: number
  compaction_ratio: number
  message_count: number
}

export interface MessagePartStreamEvent {
  /** `type` is the durable canonical event; `event` is the SSE compatibility name. */
  type?:
    | 'part.started'
    | 'part.delta'
    | 'part.replaced'
    | 'part.completed'
    | 'part.failed'
    | 'tool.started'
    | 'tool.completed'
  event?: 'message.part.delta' | string
  event_id?: string
  message_id: string
  part: MessagePart
}

export type MessagePartDeltaEvent = MessagePartStreamEvent

export interface MessageCompletedEvent {
  event: 'message.completed'
  event_id?: string
  message_id: string
  status: string
  provider_trace: UnknownRecord
}

export type SessionMessageStreamData =
  | MessagePartStreamEvent
  | MessageCompletedEvent
  | UnknownRecord
