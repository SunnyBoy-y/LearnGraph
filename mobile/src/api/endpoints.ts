/**
 * 端点封装（/api/v1 前缀由 http.ts 注入）。
 */

import { api } from './http'
import type { ApiStreamOptions } from './http'
import type {
  AuthSessionView,
  CurrentUserView,
  DeploymentProfile,
  LoginResponse,
  Message,
  MessageCreateRequest,
  MessageListPage,
  MessageRetryRequest,
  Session,
  SessionBatchDeleteImpact,
  SessionBatchDeleteResult,
  SessionMessageStreamData,
} from '../types'

const enc = encodeURIComponent

// ------------------------------------------------------------------------- //
// 公共（无需认证）
// ------------------------------------------------------------------------- //

export function livez(): Promise<{ status: string }> {
  return api.get('/livez', { auth: false, workspace: false })
}

export function deploymentProfile(): Promise<DeploymentProfile> {
  return api.get('/deployment/profile', { auth: false, workspace: false })
}

// ------------------------------------------------------------------------- //
// 认证
// ------------------------------------------------------------------------- //

export function login(username: string, password: string): Promise<LoginResponse> {
  return api.post<LoginResponse, { username: string; password: string }>(
    '/auth/login',
    { username, password },
    { auth: false, workspace: false },
  )
}

export function logout(): Promise<{ status: string }> {
  return api.post('/auth/logout', undefined, { workspace: false })
}

export function me(): Promise<CurrentUserView> {
  return api.get('/auth/me', { workspace: false })
}

export function listAuthSessions(): Promise<AuthSessionView[]> {
  return api.get('/auth/sessions', { workspace: false })
}

export function revokeAuthSession(sessionId: string): Promise<{ status: string }> {
  return api.delete(`/auth/sessions/${enc(sessionId)}`, { workspace: false })
}

export function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ status: string }> {
  return api.post(
    '/auth/change-password',
    { current_password: currentPassword, new_password: newPassword },
    { workspace: false },
  )
}

// ------------------------------------------------------------------------- //
// 会话
// ------------------------------------------------------------------------- //

export function listSessions(): Promise<Session[]> {
  return api.get('/sessions')
}

export function createSession(payload: { title?: string } = {}): Promise<Session> {
  return api.post<Session, { title?: string }>('/sessions', payload)
}

export function closeSession(sessionId: string): Promise<Session> {
  return api.post<Session>(`/sessions/${enc(sessionId)}/close`)
}

export function autoTitleSession(
  sessionId: string,
  payload: { source_message_id: string; expected_title: string },
): Promise<Session> {
  return api.post<Session, { source_message_id: string; expected_title: string }>(
    `/sessions/${enc(sessionId)}/auto-title`,
    payload,
  )
}

export function sessionDeleteImpact(sessionIds: string[]): Promise<SessionBatchDeleteImpact> {
  return api.post<SessionBatchDeleteImpact, { session_ids: string[] }>(
    '/sessions/batch-delete-impact',
    { session_ids: sessionIds },
  )
}

export function sessionsBatchDelete(
  sessionIds: string[],
  confirmationText: string,
): Promise<SessionBatchDeleteResult> {
  return api.post<SessionBatchDeleteResult, { session_ids: string[]; confirmation_text: string }>(
    '/sessions/batch-delete',
    { session_ids: sessionIds, confirmation_text: confirmationText },
  )
}

// ------------------------------------------------------------------------- //
// 消息
// ------------------------------------------------------------------------- //

export interface ListMessagesOptions {
  limit?: number
  before_id?: string
  compact?: boolean
}

export function listMessages(
  sessionId: string,
  options: ListMessagesOptions = {},
): Promise<MessageListPage> {
  return api.get<MessageListPage>(`/sessions/${enc(sessionId)}/messages`, {
    query: {
      limit: options.limit,
      before_id: options.before_id,
      compact: options.compact === false ? 'false' : undefined,
    },
  })
}

export function streamMessage(
  sessionId: string,
  payload: MessageCreateRequest,
  options: ApiStreamOptions = {},
): AsyncGenerator<{ event: string; id?: string; data: SessionMessageStreamData; rawData: string }> {
  return api.postSse<SessionMessageStreamData, MessageCreateRequest>(
    `/sessions/${enc(sessionId)}/messages/stream`,
    payload,
    options,
  )
}

export function retryMessage(
  sessionId: string,
  messageId: string,
  payload: MessageRetryRequest = {},
  options: ApiStreamOptions = {},
): AsyncGenerator<{ event: string; id?: string; data: SessionMessageStreamData; rawData: string }> {
  return api.postSse<SessionMessageStreamData, MessageRetryRequest>(
    `/sessions/${enc(sessionId)}/messages/${enc(messageId)}/retry`,
    payload,
    options,
  )
}

export function cancelMessage(sessionId: string, messageId: string): Promise<unknown> {
  return api.post(`/sessions/${enc(sessionId)}/messages/${enc(messageId)}/cancel`)
}

export function messageEvents(
  sessionId: string,
  messageId: string,
  options: { after_event_id?: string; message_version_id?: string; signal?: AbortSignal } = {},
): Promise<SessionMessageStreamData[]> {
  return api.get<SessionMessageStreamData[]>(
    `/sessions/${enc(sessionId)}/messages/${enc(messageId)}/events`,
    {
      signal: options.signal,
      query: {
        after_event_id: options.after_event_id,
        message_version_id: options.message_version_id,
      },
    },
  )
}

// ------------------------------------------------------------------------- //
// 文件（附件/图片查看）
// ------------------------------------------------------------------------- //

export function fileContent(fileId: string): Promise<Blob> {
  return api.getBlob(`/files/${enc(fileId)}/content`)
}

/** 消息内引用文件时从 part.data 提取可渲染信息 */
export function partFileRef(part: { data?: Record<string, unknown> }): {
  fileId?: string
  url?: string
  name?: string
} {
  const d = part.data ?? {}
  const url =
    typeof d.url === 'string'
      ? d.url
      : typeof d.file_url === 'string'
        ? d.file_url
        : typeof d.preview_url === 'string'
          ? d.preview_url
          : undefined
  const fileId = typeof d.file_id === 'string' ? d.file_id : undefined
  const name =
    typeof d.name === 'string'
      ? d.name
      : typeof d.filename === 'string'
        ? d.filename
        : typeof d.file_name === 'string'
          ? d.file_name
          : undefined
  return { fileId, url, name }
}

/** 供历史消息列表使用：消息里所有文本 part 拼起来 */
export function messageText(message: Message): string {
  return message.parts
    .filter((p) => p.type === 'text' && typeof p.content === 'string')
    .map((p) => p.content ?? '')
    .join('\n')
}
