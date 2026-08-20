import type {
  BranchSessionRequest,
  CompactContextResult,
  ConceptBranchCreateRequest,
  DictationCleanupRequest,
  DictationCleanupResult,
  GraphChangeSet,
  Message,
  MessageSnapshot,
  MessageVersion,
  MessageCreateRequest,
  MessageRetryRequest,
  MessageListPage,
  Session,
  SessionAutoTitleRequest,
  SessionActivitySummaryRequest,
  SessionContextUsage,
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

/**
 * Generate a one-line "learning event" summary for the dashboard activity view.
 * On provider-unavailable the backend degrades gracefully and returns the
 * session with `activity_summary` still null — the caller keeps the title.
 */
export function renderSessionActivitySummary(
  sessionId: string,
  payload: SessionActivitySummaryRequest,
): Promise<Session> {
  return apiClient.post<Session, SessionActivitySummaryRequest>(
    `/sessions/${encodeURIComponent(sessionId)}/activity-summary`,
    payload,
  )
}

export function cleanupDictation(
  payload: DictationCleanupRequest,
): Promise<DictationCleanupResult> {
  return apiClient.post<DictationCleanupResult, DictationCleanupRequest>(
    '/sessions/dictation/cleanup',
    payload,
  )
}

export interface DictationTranscriptionResult {
  text: string
  language: string | null
  duration_seconds: number | null
  request_id: string | null
}

/** Transcribe one live microphone segment via the workspace ASR Provider. */
export function transcribeDictationSegment(
  segment: Blob,
  options: { provider_id?: string; model_id?: string; language?: string } = {},
): Promise<DictationTranscriptionResult> {
  const formData = new FormData()
  const extension = segment.type.includes('mp4')
    ? 'm4a'
    : segment.type.includes('ogg')
      ? 'ogg'
      : 'webm'
  formData.set('file', segment, `dictation-segment.${extension}`)
  if (options.provider_id) formData.set('provider_id', options.provider_id)
  if (options.model_id) formData.set('model_id', options.model_id)
  if (options.language) formData.set('language', options.language)
  return apiClient.upload<DictationTranscriptionResult>(
    '/sessions/dictation/transcriptions',
    formData,
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

export type ListSessionMessagesOptions = {
  /** Newest-window size. Omit for the full timeline (still compact by default). */
  limit?: number
  /** Load the page ending just before this message id (scroll-up). */
  beforeId?: string
  /** When false, return full durable parts/provider_trace (rare). Default true. */
  compact?: boolean
}

/**
 * Session timeline for the chat UI. Backend returns a page envelope; callers that
 * only need the items array can keep using this helper. Use
 * {@link listSessionMessagesPage} when you need `has_more_before` / cursors.
 */
export function listSessionMessages(
  sessionId: string,
  options: ListSessionMessagesOptions = {},
): Promise<Message[]> {
  return listSessionMessagesPage(sessionId, options).then((page) => page.items)
}

export function listSessionMessagesPage(
  sessionId: string,
  options: ListSessionMessagesOptions = {},
): Promise<MessageListPage> {
  return apiClient.get<MessageListPage>(
    `/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      query: {
        limit: options.limit,
        before_id: options.beforeId,
        compact: options.compact === false ? 'false' : undefined,
      },
    },
  )
}

export function getSessionContextUsage(
  sessionId: string,
  options: { provider_id?: string; model_id?: string; agent_mode?: boolean } = {},
): Promise<SessionContextUsage> {
  return apiClient.get<SessionContextUsage>(
    `/sessions/${encodeURIComponent(sessionId)}/context-usage`,
    {
      query: {
        provider_id: options.provider_id,
        model_id: options.model_id,
        agent_mode: options.agent_mode ? 'true' : undefined,
      },
    },
  )
}

export function compactSessionContext(
  sessionId: string,
  options: { provider_id?: string; model_id?: string; agent_mode?: boolean } = {},
): Promise<CompactContextResult> {
  return apiClient.post<CompactContextResult>(
    `/sessions/${encodeURIComponent(sessionId)}/compact`,
    {
      query: {
        provider_id: options.provider_id,
        model_id: options.model_id,
        agent_mode: options.agent_mode ? 'true' : undefined,
      },
    },
  )
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

export function undoGraphChangeSet(
  sessionId: string,
  changeSetId: string,
): Promise<GraphChangeSet> {
  return apiClient.post<GraphChangeSet>(
    `/sessions/${encodeURIComponent(sessionId)}/graph-change-sets/${encodeURIComponent(changeSetId)}/undo`,
  )
}

export function listMessageVersions(sessionId: string, messageId: string): Promise<MessageVersion[]> {
  return apiClient.get<MessageVersion[]>(`/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/versions`)
}

export function getMessageSnapshot(
  sessionId: string,
  messageId: string,
  messageVersionId?: string,
  options: ApiRequestOptions = {},
): Promise<MessageSnapshot> {
  return apiClient.get<MessageSnapshot>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`,
    {
      ...options,
      query: {
        ...options.query,
        message_version_id: messageVersionId,
      },
    },
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
  options: {
    afterEventId?: string
    messageVersionId?: string
    signal?: AbortSignal
  } = {},
): Promise<SessionMessageStreamData[]> {
  return apiClient.get<SessionMessageStreamData[]>(
    `/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/events`,
    {
      signal: options.signal,
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

// --------------------------------------------------------------------------- //
// Sub-application event-driven Agent tasks
// --------------------------------------------------------------------------- //

export type SubappAgentConsentDecision =
  | 'allow_session'
  | 'allow_app'
  | 'allow_global'
  | 'deny'

export async function decideSubappAgentConsent(
  sessionId: string,
  token: string,
  decision: SubappAgentConsentDecision,
): Promise<boolean> {
  await apiClient.post<unknown, { token: string; decision: SubappAgentConsentDecision }>(
    `/subapps/sessions/${encodeURIComponent(sessionId)}/agent-consent`,
    { token, decision },
  )
  return true
}

export async function retrySubappAgentTask(sessionId: string, token: string): Promise<boolean> {
  await apiClient.post<unknown, { token: string }>(
    `/subapps/sessions/${encodeURIComponent(sessionId)}/agent-task/retry`,
    { token },
  )
  return true
}

export async function cancelSubappAgentTask(sessionId: string, token: string): Promise<boolean> {
  await apiClient.post<unknown, { token: string }>(
    `/subapps/sessions/${encodeURIComponent(sessionId)}/agent-task/cancel`,
    { token },
  )
  return true
}
