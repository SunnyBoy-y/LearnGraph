import { useEffect, useMemo, useSyncExternalStore } from 'react'
import type { Session } from '@/types/sessions'
import { ApiError } from '@/api/client'
import {
  InfiniteQueryObserver, useQueryClient,
  type InfiniteData, type InfiniteQueryObserverResult, type QueryClient, type QueryKey,
} from '@tanstack/react-query'
import { listSessionPage, type SessionPage, type SessionPageParam, type SessionScope, type SessionSummary } from '@/api/session-browser'
import { sessionBrowserKey, sessionScopeId, summaryOf, updateSessionCaches, cacheCreatedSession } from '@/lib/session-queries'
import { scheduleSessionRefresh } from '@/lib/session-refresh'
import { workspaceQueryKey } from '@/lib/query-keys'

type PageData = InfiniteData<SessionPage, SessionPageParam>
export type SessionScopeState = InfiniteQueryObserverResult<PageData, Error>
type Observer = InfiniteQueryObserver<SessionPage, Error, PageData, QueryKey, SessionPageParam>
type Entry = { scope: SessionScope; observer: Observer; result: SessionScopeState; unsubscribe?: () => void }
type Snapshot = { data: SessionSummary[]; scopes: ReadonlyMap<string, SessionScopeState> }

/** Public query-core observers let a tree have dynamic independently paged scopes
 * without violating the Rules of Hooks or loading collapsed folders.
 */
export class SessionNavigatorStore {
  private entries = new Map<string, Entry>()
  private listeners = new Set<() => void>()
  private locks = new Set<string>()
  private flattened = new WeakMap<PageData, SessionSummary[]>()
  private snapshot: Snapshot = { data: [], scopes: new Map() }
  private created = new Map<string, SessionSummary>()
  private removedSessions = new Set<string>()
  private removedProjects = new Set<string>()
  forgetSessions = (sessionIds: string[], projectIds: string[]) => {
    for (const id of sessionIds) this.removedSessions.add(id)
    for (const id of projectIds) this.removedProjects.add(id)
    while (this.removedSessions.size > 1000) this.removedSessions.delete(this.removedSessions.values().next().value!)
    while (this.removedProjects.size > 100) this.removedProjects.delete(this.removedProjects.values().next().value!)
    for (const [id, item] of this.created) if (this.removedSessions.has(id) || (item.project_id && this.removedProjects.has(item.project_id))) this.created.delete(id)
    this.client.setQueryData(workspaceQueryKey(this.workspaceId, 'sessions', 'tombstones'), { sessions: [...this.removedSessions], projects: [...this.removedProjects] })
    this.client.setQueryData<SessionSummary[]>(workspaceQueryKey(this.workspaceId, 'sessions', 'recent-created'), previous => previous?.filter(item => !this.removedSessions.has(item.id) && !(item.project_id && this.removedProjects.has(item.project_id))))
    this.client.setQueriesData<PageData>({ queryKey: sessionBrowserKey(this.workspaceId) }, previous => previous ? ({
      ...previous, pages: previous.pages.map(page => ({ ...page, items: page.items.filter(item => !this.removedSessions.has(item.id) && !(item.project_id && this.removedProjects.has(item.project_id))) })),
    }) : previous)
    this.publish()
  }
  acceptCreated = (session: Session) => {
    if (session.workspace_id !== this.workspaceId) return
    cacheCreatedSession(this.client, this.workspaceId, session)
    this.created.set(session.id, summaryOf(session))
    if (this.created.size > 20) this.created.delete(this.created.keys().next().value!)
    this.publish()
  }
  private client: QueryClient
  private workspaceId: string
  constructor(client: QueryClient, workspaceId: string) {
    this.client = client; this.workspaceId = workspaceId
    const key = workspaceQueryKey(workspaceId, 'sessions', 'tombstones')
    this.client.setQueryDefaults(key, { gcTime: 120_000 })
    const deleted = client.getQueryData<{ sessions: string[]; projects: string[] }>(key)
    this.removedSessions = new Set(deleted?.sessions)
    this.removedProjects = new Set(deleted?.projects)
  }

  getSnapshot = () => this.snapshot
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    for (const entry of this.entries.values()) this.watch(entry)
    return () => {
      this.listeners.delete(listener)
      if (!this.listeners.size) {
        for (const entry of this.entries.values()) { entry.unsubscribe?.(); entry.unsubscribe = undefined }
      }
    }
  }
  private watch(entry: Entry) {
    if (entry.unsubscribe || !this.listeners.size) return
    entry.unsubscribe = entry.observer.subscribe(result => { entry.result = result; this.publish() })
    entry.result = entry.observer.getCurrentResult()
  }
  private publish() {
    const scopes = new Map<string, SessionScopeState>()
    const items = new Map<string, SessionSummary>()
    for (const [key, entry] of this.entries) {
      scopes.set(key, entry.result)
      const data = entry.result.data
      if (!data) continue
      let flat = this.flattened.get(data)
      if (!flat) { flat = data.pages.flatMap(page => page.items); this.flattened.set(data, flat) }
      for (const item of flat) {
        if (this.removedSessions.has(item.id) || (item.project_id && this.removedProjects.has(item.project_id))) continue
        items.set(item.id, item)
      }
    }
    for (const [id, item] of this.created) {
      if (items.has(id)) this.created.delete(id)
      else items.set(id, item)
    }
    const next = [...items.values()]
    const data = next.length === this.snapshot.data.length && next.every((item, index) => item === this.snapshot.data[index]) ? this.snapshot.data : next
    this.snapshot = { data, scopes }
    for (const listener of this.listeners) listener()
  }
  retainScopes = (ids: ReadonlySet<string>) => {
    let changed = false
    for (const [id, entry] of this.entries) {
      if (ids.has(id)) continue
      entry.unsubscribe?.(); entry.unsubscribe = undefined
      // React Query owns the inactive cache and its gcTime; no second forever-cache.
      this.entries.delete(id)
      changed = true
    }
    if (changed) this.publish()
  }
  activate = (scope: SessionScope) => {
    const id = sessionScopeId(scope)
    if (this.entries.has(id)) { this.watch(this.entries.get(id)!); return }
    const observer: Observer = new InfiniteQueryObserver<SessionPage, Error, PageData, QueryKey, SessionPageParam>(this.client, {
      queryKey: [...sessionBrowserKey(this.workspaceId), scope],
      queryFn: async ({ pageParam, signal }) => {
        try { return await listSessionPage(scope, pageParam, { signal }) }
        catch (error) {
          // A parent moved to another project while this window was cached.
          // Start a fresh chain rather than trapping the user on a stale cursor.
          if (pageParam.cursor && error instanceof ApiError && error.code === 'invalid_session_cursor') return listSessionPage(scope, {}, { signal })
          throw error
        }
      },
      initialPageParam: {} as SessionPageParam,
      getNextPageParam: page => page.has_more && page.next_cursor ? { cursor: page.next_cursor, direction: 'next' as const } : undefined,
      getPreviousPageParam: page => page.has_previous && page.previous_cursor ? { cursor: page.previous_cursor, direction: 'previous' as const } : undefined,
      // A bounded bidirectional cache: evicted history remains reachable upwards.
      maxPages: 10, staleTime: 30_000, gcTime: 120_000, retry: false,
      refetchOnWindowFocus: false,
    })
    const entry: Entry = { scope, observer, result: observer.getCurrentResult() }
    this.entries.set(id, entry)
    this.watch(entry)
    this.publish()
  }
  load = async (scope: SessionScope, direction: 'next' | 'previous' = 'next', retry = false) => {
    this.activate(scope)
    const id = sessionScopeId(scope), entry = this.entries.get(id)!
    const state = entry.observer.getCurrentResult()
    if (this.locks.has(id) || state.isFetching) return
    // A failed sentinel must not create an automatic infinite retry loop.
    if (state.isError && !retry) return
    this.locks.add(id)
    try {
      if (!state.data) await entry.observer.refetch()
      else if (direction === 'previous' && state.hasPreviousPage) await entry.observer.fetchPreviousPage({ cancelRefetch: false })
      else if (direction === 'next' && state.hasNextPage) await entry.observer.fetchNextPage({ cancelRefetch: false })
      else if (retry) await entry.observer.refetch()
    } finally { this.locks.delete(id) }
  }
  refetch = () => scheduleSessionRefresh(this.client, this.workspaceId)
}

export function useSessionNavigator(workspaceId: string) {
  const client = useQueryClient()
  const store = useMemo(() => new SessionNavigatorStore(client, workspaceId), [client, workspaceId])
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)
  useEffect(() => {
    const created = (event: Event) => {
      const session = (event as CustomEvent<{ session?: Session }>).detail?.session
      if (session) store.acceptCreated(session)
    }
    const updated = (event: Event) => {
      const session = (event as CustomEvent<{ session?: Session }>).detail?.session
      if (session?.workspace_id === workspaceId) updateSessionCaches(client, workspaceId, session)
    }
    const deleted = (event: Event) => {
      const detail = (event as CustomEvent<{ workspaceId: string; sessionIds: string[]; projectIds: string[] }>).detail
      if (detail?.workspaceId === workspaceId) store.forgetSessions(detail.sessionIds, detail.projectIds)
    }
    window.addEventListener('learngraph:session-created', created)
    window.addEventListener('learngraph:session-updated', updated)
    window.addEventListener('learngraph:sessions-deleted', deleted)
    return () => {
      window.removeEventListener('learngraph:session-created', created)
      window.removeEventListener('learngraph:session-updated', updated)
      window.removeEventListener('learngraph:sessions-deleted', deleted)
    }
  }, [store, client, workspaceId])
  return useMemo(() => ({ ...snapshot, activate: store.activate, load: store.load, refetch: store.refetch, retainScopes: store.retainScopes }), [snapshot, store])
}
export type SessionNavigator = ReturnType<typeof useSessionNavigator>
