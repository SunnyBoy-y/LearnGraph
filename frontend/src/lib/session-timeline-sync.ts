import { apiClient, ApiError, type ApiClient } from '@/api/client'
import { authStore } from '@/api/auth-store'
import type { Message, MessageListPage } from '@/types/sessions'

export const VISIBLE_TIMELINE_POLL_MS = 2_000

/** One currently mounted timeline, bounded to the same latest page as normal history. */
export class ConditionalSessionTimeline {
  private cached = new Map<string, { etag: string | null; page: MessageListPage }>()
  private scope = ''
  private readonly client: Pick<ApiClient, 'getConditional'>
  private readonly identity: () => string
  constructor(client: Pick<ApiClient, 'getConditional'> = apiClient,
    identity: () => string = () => JSON.stringify([apiClient.baseUrl, authStore.getAccessToken(), authStore.getWorkspaceId()])) {
    this.client = client; this.identity = identity
  }

  private async page(sessionId: string, limit: number, beforeId: string | undefined, signal?: AbortSignal): Promise<MessageListPage> {
    const scope = this.identity(), path = `/sessions/${encodeURIComponent(sessionId)}/messages`
    if (this.scope !== scope) { this.cached.clear(); this.scope = scope }
    const key = JSON.stringify([path, limit, beforeId]), prior = this.cached.get(key)
    const response = await this.client.getConditional<MessageListPage>(path, prior?.etag, { signal, query: { limit, before_id: beforeId, compact: 'false' } })
    if (signal?.aborted || scope !== this.identity()) throw new DOMException('Conversation identity changed', 'AbortError')
    if (response.notModified && prior) return prior.page
    if (!response.data) throw new Error('会话同步未返回可用内容，请刷新重试')
    this.cached.set(key, { etag: response.etag, page: response.data })
    if (this.cached.size > 64) this.cached.delete(this.cached.keys().next().value!)
    return response.data
  }

  async read(sessionId: string, limit: number, signal?: AbortSignal, previous: readonly Message[] = [], retryMovedCursor = true): Promise<MessageListPage> {
    let page = await this.page(sessionId, limit, undefined, signal)
    let items = page.items
    const known = new Set(previous.map(item => item.id))
    const target = Math.max(limit, previous.length + items.filter(item => !known.has(item.id)).length)
    // Revalidate only pages the user already opened. Latest-only comparisons
    // cannot observe an edit/deletion in the older visible part of the timeline.
    while (items.length < target && page.has_more_before && items[0]) {
      let older: MessageListPage
      try { older = await this.page(sessionId, limit, items[0].id, signal) }
      catch (error) {
        // A just-deleted page cursor is not proof that the conversation vanished.
        if (error instanceof ApiError && error.status === 404) {
          if (retryMovedCursor) return this.read(sessionId, limit, signal, previous, false)
          throw new DOMException('Timeline cursor moved', 'AbortError')
        }
        throw error
      }
      if (!older.items.length) { page = older; break }
      const ids = new Set(items.map(item => item.id))
      const additional = older.items.filter(item => !ids.has(item.id))
      if (!additional.length) break
      items = [...additional, ...items]
      page = older
    }
    return { ...page, items, oldest_id: items[0]?.id ?? null, newest_id: items.at(-1)?.id ?? null }
  }
}

export function timelinePollInterval(sessionId: string, localStreaming: boolean, status: string): number | false {
  return sessionId && sessionId !== 'new' && !localStreaming && status !== 'streaming' && status !== 'submitted'
    ? VISIBLE_TIMELINE_POLL_MS : false
}

/** Query function boundary: a GET started while idle may finish after a local SSE has begun. */
export async function readVisibleSessionTimeline(timeline: ConditionalSessionTimeline, sessionId: string, limit: number, options: {
  signal?: AbortSignal
  previous: readonly Message[]
  isStreaming: () => boolean
  current: () => Message[] | undefined
  onPage: (page: MessageListPage) => void
}): Promise<Message[]> {
  const page = await timeline.read(sessionId, limit, options.signal, options.previous)
  if (options.isStreaming()) return options.current() ?? [...options.previous]
  options.onPage(page)
  return page.items
}
