import { apiClient } from './client'
import type { ApiRequestOptions } from './client'
import type { Session } from '@/types/sessions'
import { recordSessionListMetric } from '@/lib/session-list-performance'

export type SessionSummary = Omit<Session, 'model_snapshot' | 'context_capsule' | 'current_node_id'> & { has_children: boolean }
export type SessionScope = {
  project_id?: string
  ungrouped?: boolean
  parent_session_id?: string
  roots_only?: boolean
  include_archived?: boolean
  archived_only?: boolean
  pinned_first?: boolean
  graph_id?: string
  node_id?: string
  session_kind?: string
  q?: string
  created_after?: string
  created_before?: string
}
export type SessionPage = {
  items: SessionSummary[]
  next_cursor: string | null
  previous_cursor: string | null
  has_more: boolean
  has_previous: boolean
}
export type SessionPageParam = { cursor?: string; direction?: 'next' | 'previous' }
export type SessionCounts = { total: number; active: number; archived: number; memory_enabled: number }

export async function listSessionPage(scope: SessionScope = {}, page: SessionPageParam = {}, options: ApiRequestOptions = {}) {
  // Bound a stalled request without slowing successful ones. Cleanup cancellation
  // travels all the way to fetch; timeout becomes a local retryable error.
  const controller = new AbortController()
  let timedOut = false
  const cancel = () => controller.abort(options.signal?.reason)
  if (options.signal?.aborted) cancel()
  else options.signal?.addEventListener('abort', cancel, { once: true })
  const timeout = setTimeout(() => { timedOut = true; controller.abort() }, 12_000)
  const start = performance.now()
  try {
    const result = await apiClient.get<SessionPage>('/sessions/page', { ...options, signal: controller.signal, query: { ...scope, ...page, limit: 20 } })
    recordSessionListMetric({ kind: 'network', duration: performance.now() - start, count: result.items.length })
    performance.measure('session-list:page-network', { start, end: performance.now(), detail: { count: result.items.length, continuation: !!page.cursor } })
    return result
  } catch (error) {
    if (timedOut && !options.signal?.aborted) throw new Error('会话加载超时，请重试')
    throw error
  } finally {
    clearTimeout(timeout)
    options.signal?.removeEventListener('abort', cancel)
    // Bound User Timing entries in long-lived desktop windows.
    if (performance.getEntriesByName('session-list:page-network').length > 100) performance.clearMeasures('session-list:page-network')
  }
}
export function getSession(sessionId: string, options: ApiRequestOptions = {}) {
  return apiClient.get<Session>(`/sessions/${encodeURIComponent(sessionId)}`, options)
}
export function getSessionAncestors(sessionId: string, options: ApiRequestOptions = {}) {
  return apiClient.get<SessionSummary[]>(`/sessions/${encodeURIComponent(sessionId)}/ancestors`, options)
}
export function getSessionActivityDays(start: string, end: string, options: ApiRequestOptions = {}) {
  return apiClient.get<Record<string, number>>('/sessions/activity', { ...options, query: { start, end, timezone_offset: new Date().getTimezoneOffset(), timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone } })
}
export function getSessionCounts(options: ApiRequestOptions = {}) {
  return apiClient.get<SessionCounts>('/sessions/summary', options)
}
