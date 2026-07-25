import type {
  BranchSessionRequest,
  ConceptBranchCreateRequest,
  GraphChangeSet,
  Message,
  MessageSnapshot,
  MessageVersion,
  MessageCreateRequest,
  MessageRetryRequest,
  Session,
  SessionAutoTitleRequest,
  SessionCreateRequest,
  SessionUpdateRequest,
  SessionMessageStreamData,
  SessionBatchDeleteImpact,
  SessionBatchDeleteResult,
  SuggestedPromptBatch,
  SuggestedPromptRequest,
} from '@/types/sessions'

import { apiClient } from './client'
import type { ApiRequestOptions, ApiStreamOptions } from './client'
import type { SseEvent } from './sse'

export function listSessions(): Promise<Session[]> {
  return apiClient.get<Session[]>('/sessions')
}

export function createSession(payload: SessionCreateRequest = {}): Promise<Session> {
  return apiClient.post<Session, SessionCreateRequest>('/sessions', payload)
}

export function autoTitleSession(
  sessionId: string,
  payload: SessionAutoTitleRequest,
): Promise<Session> {
  return apiClient.post<Session, SessionAutoTitleRequest>(
    `/sessions/${encodeURIComponent(sessionId)}/auto-title`,
    payload,
  )
}

export function closeSession(sessionId: string): Promise<Session> {
  return apiClient.post<Session>(
    `/sessions/${encodeURIComponent(sessionId)}/close`,
  )
}

export function updateSession(
  sessionId: string,
  payload: SessionUpdateRequest,
): Promise<Session> {
  return apiClient.patch<Session, SessionUpdateRequest>(
    `/sessions/${encodeURIComponent(sessionId)}`,
    payload,
  )
}

export function getSessionBatchDeleteImpact(
  sessionIds: string[],
): Promise<SessionBatchDeleteImpact> {
  return apiClient.post<SessionBatchDeleteImpact, { session_ids: string[] }>(
    '/sessions/batch-delete-impact',
    { session_ids: sessionIds },
  )
}

export function deleteSessionsBatch(
  sessionIds: string[],
  confirmationText: string,
): Promise<SessionBatchDeleteResult> {
  return apiClient.post<
    SessionBatchDeleteResult,
    { session_ids: string[]; confirmation_text: string }
  >('/sessions/batch-delete', {
    session_ids: sessionIds,
    confirmation_text: confirmationText,
  })
}

export function listSessionMessages(sessionId: string): Promise<Message[]> {
  return apiClient.get<Message[]>(`/sessions/${encodeURIComponent(sessionId)}/messages`)
}

export function getSessionSuggestedPrompts(
  sessionId: string,
  options: ApiRequestOptions = {},
): Promise<SuggestedPromptBatch | undefined> {
  return apiClient.get<SuggestedPromptBatch | undefined>(
    `/sessions/${encodeURIComponent(sessionId)}/suggested-prompts`,
    options,
  )
}

export function generateSessionSuggestedPrompts(
  sessionId: string,
  payload: SuggestedPromptRequest,
  options: ApiRequestOptions = {},
): Promise<SuggestedPromptBatch> {
  return apiClient.post<SuggestedPromptBatch, SuggestedPromptRequest>(
    `/sessions/${encodeURIComponent(sessionId)}/suggested-prompts`,
    payload,
    options,
  )
}

export function confirmGraphChangeSet(
  sessionId: string,
  changeSetId: string,
): Promise<GraphChangeSet> {
  return apiClient.post<GraphChangeSet>(
    `/sessions/${encodeURIComponent(sessionId)}/graph-change-sets/${encodeURIComponent(changeSetId)}/confirm`,
  )
}

export function rejectGraphChangeSet(
  sessionId: string,
  changeSetId: string,
  reason = '',
): Promise<GraphChangeSet> {
  return apiClient.post<GraphChangeSet, { reason: string }>(
    `/sessions/${encodeURIComponent(sessionId)}/graph-change-sets/${encodeURIComponent(changeSetId)}/reject`,
    { reason },
  )
}

export function listMessageVersions(sessionId: string, messageId: string): Promise<MessageVersion[]> {
  return apiClient.get<MessageVersion[]>(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/versions`)
}

export function getMessageSnapshot(sessionId: string, messageId: string, messageVersionId?: string): Promise<MessageSnapshot> {
  return apiClient.get<MessageSnapshot>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`,
    { query: { message_version_id: messageVersionId } },
  )
}

export function streamSessionMessage(
  sessionId: string,
  payload: MessageCreateRequest,
  options: ApiStreamOptions = {},
): AsyncGenerator<SseEvent<SessionMessageStreamData>> {
  return apiClient.postSse<SessionMessageStreamData, MessageCreateRequest>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/stream`,
    payload,
    options,
  )
}

export function branchSession(
  sessionId: string,
  messageId: string,
  payload: BranchSessionRequest = {},
): Promise<Session> {
  return apiClient.post<Session, BranchSessionRequest>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/branch`,
    payload,
  )
}

export function createConceptBranch(
  sessionId: string,
  payload: ConceptBranchCreateRequest,
): Promise<Session> {
  return apiClient.post<Session, ConceptBranchCreateRequest>(
    `/sessions/${encodeURIComponent(sessionId)}/concept-branches`,
    payload,
  )
}

export function promoteConceptBranch(
  sessionId: string,
  payload: { action: 'merge_summary' | 'standalone'; summary?: string },
): Promise<Session> {
  return apiClient.post<Session, typeof payload>(
    `/sessions/${encodeURIComponent(sessionId)}/promote`,
    payload,
  )
}

export function retrySessionMessage(
  sessionId: string,
  messageId: string,
  payload: MessageRetryRequest = {},
  options: ApiStreamOptions = {},
): AsyncGenerator<SseEvent<SessionMessageStreamData>> {
  return apiClient.postSse<SessionMessageStreamData, MessageRetryRequest>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/retry`,
    payload,
    options,
  )
}

export function listSessionMessageEvents(
  sessionId: string,
  messageId: string,
  options: { afterEventId?: string; messageVersionId?: string } = {},
): Promise<SessionMessageStreamData[]> {
  return apiClient.get<SessionMessageStreamData[]>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/events`,
    {
      query: {
        after_event_id: options.afterEventId,
        message_version_id: options.messageVersionId,
      },
    },
  )
}

export function cancelSessionMessage(sessionId: string, messageId: string): Promise<void> {
  return apiClient.post<void>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/cancel`,
  )
}
