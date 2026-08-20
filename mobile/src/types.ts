/**
 * LearnGraph mobile API 契约类型（与 backend/app/domain/schemas 对齐的子集，
 * 自包含快照，不依赖 frontend/src 的别名与构建链）。
 */

export type IsoDateTime = string

// ------------------------------------------------------------------------- //
// 认证
// ------------------------------------------------------------------------- //

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_at: IsoDateTime
  session_id: string
  user_id: string
  username: string
  display_name: string
  default_workspace_id: string | null
  must_change_password: boolean
  demo_only?: boolean
}

export interface CurrentUserView {
  id: string
  tenant_id: string
  username: string
  email: string | null
  display_name: string
  status: string
  is_system_admin: boolean
  must_change_password: boolean
  session_id: string
}

export interface AuthSessionView {
  id: string
  created_at: IsoDateTime
  expires_at: IsoDateTime
  last_seen_at: IsoDateTime
  revoked_at: IsoDateTime | null
  revoked_reason: string
  user_agent: string
  ip_address: string
  current: boolean
}

/** GET /api/v1/deployment/profile（公开，登录页即可读） */
export interface DeploymentProfile {
  deployment_profile: string
  single_user: boolean
  registration_enabled: boolean
  demo_login_enabled: boolean
  sandbox_enabled: boolean
}

// ------------------------------------------------------------------------- //
// 会话 / 消息
// ------------------------------------------------------------------------- //

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
  | 'subapp_artifact'
  | 'subapp_event'
  | 'sandbox_status'
  | 'skill_trigger'
  | 'component'
  | 'magic_card'
  | 'user_confirmation'
  | 'fetch_authorization'
  | 'fetch_setup_notice'
  | 'graph_progress'
  | 'egress_authorization'
  | 'error'

export type MessagePartStatus = 'pending' | 'streaming' | 'completed' | 'failed'

export interface MessagePart {
  id: string
  type: MessagePartType
  status: MessagePartStatus
  content?: string | null
  content_delta?: string
  sequence?: number
  data?: Record<string, unknown>
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
  model_snapshot: Record<string, unknown>
  status: string
  closed_at: IsoDateTime | null
  archived_at: IsoDateTime | null
  session_kind: 'main' | 'concept_branch' | 'side' | 'standalone'
  writeback_policy: 'normal' | 'manual_only'
  context_capsule: Record<string, unknown>
  activity_summary: string | null
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
  provider_trace: Record<string, unknown>
  created_at: IsoDateTime
  /** 前端瞬时字段：最终回答边界（来自 answer.started 事件） */
  finalAnswerStarted?: {
    finalPartId?: string
    boundarySequence?: number
    thinkingDurationMs?: number
  }
}

export interface MessageListPage {
  items: Message[]
  has_more_before: boolean
  oldest_id: string | null
  newest_id: string | null
  total_count: number
}

export interface MessageCreateRequest {
  content: string
  generation_mode?: 'text' | 'image'
  image_size?: string
  source_file_ids?: string[]
  parent_message_id?: string | null
  node_ids?: string[]
  file_ids?: string[]
  provider_id?: string
  model_id?: string
  thinking_mode?: 'off' | 'low' | 'medium' | 'high' | 'xhigh'
  agent_mode?: boolean
  goal_mode?: boolean
  message_kind?: 'normal' | 'subapp_event'
  subapp_event_id?: string
  search_route?: 'disabled' | 'model_native' | 'external' | 'local' | 'auto'
  web_search?: boolean
  allowed_domains?: string[]
  graph_action?: 'none' | 'propose_create' | 'propose_update'
  graph_id?: string | null
}

export interface MessageRetryRequest {
  provider_id?: string
  model_id?: string
  thinking_mode?: MessageCreateRequest['thinking_mode']
  agent_mode?: boolean
  search_route?: MessageCreateRequest['search_route']
  web_search?: boolean
  allowed_domains?: string[]
}

export interface SessionBatchDeleteImpact {
  resource_type: 'session_batch'
  resource_id: string
  title: string
  confirmation_text: string
  impacts: Array<{
    resource_type: string
    resource_id: string
    title: string
    count: number
    action: string
  }>
}

export interface SessionBatchDeleteResult {
  status: 'deleted'
  deleted_session_ids: string[]
  deleted_count: number
  impacts: SessionBatchDeleteImpact['impacts']
}

// ------------------------------------------------------------------------- //
// SSE 流事件（与 backend X-SSE-Schema-Version: 1.0 对齐）
// ------------------------------------------------------------------------- //

export interface MessagePartStreamEvent {
  /** `type` 为持久化规范事件名；`event` 为 SSE 兼容名 */
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

export interface MessageCompletedEvent {
  event: 'message.completed'
  event_id?: string
  message_id: string
  status: string
  provider_trace: Record<string, unknown>
}

export interface AnswerStartedEvent {
  type?: 'answer.started'
  event?: 'answer.started' | string
  event_id?: string
  message_id: string
  final_part_id: string
  boundary_sequence?: number
  started_at?: string
  thinking_duration_ms?: number
}

export type SessionMessageStreamData =
  | MessagePartStreamEvent
  | MessageCompletedEvent
  | AnswerStartedEvent
  | Record<string, unknown>
