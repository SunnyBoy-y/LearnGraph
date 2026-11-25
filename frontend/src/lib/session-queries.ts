import { useEffect, useMemo, useState } from 'react'
import { skipToken, useInfiniteQuery, useQuery, useQueryClient, type InfiniteData, type QueryClient } from '@tanstack/react-query'
import { getSession, listSessionPage, type SessionPage, type SessionPageParam, type SessionScope, type SessionSummary } from '@/api/session-browser'
import type { Session } from '@/types/sessions'
import { workspaceQueryKey } from './query-keys'

export const sessionDetailKey = (workspaceId: string, id: string) => workspaceQueryKey(workspaceId, 'sessions', 'detail', id)
export const sessionBrowserKey = (workspaceId: string) => workspaceQueryKey(workspaceId, 'sessions', 'browser')
export const sessionScopeId = (scope: SessionScope) => scope.archived_only ? 'archived' : scope.parent_session_id ? `session:${scope.parent_session_id}` : scope.project_id ? `project:${scope.project_id}` : 'recent'

export function useSessionDetail(workspaceId: string, id?: string, enabled = true) {
  const client = useQueryClient()
  useEffect(() => {
    const update = (event: Event) => {
      const session = (event as CustomEvent<{ session?: Session }>).detail?.session
      if (session?.workspace_id === workspaceId && session.id === id) updateSessionCaches(client, workspaceId, session)
    }
    window.addEventListener('learngraph:session-updated', update)
    return () => window.removeEventListener('learngraph:session-updated', update)
  }, [client, workspaceId, id])
  return useQuery({
    queryKey: sessionDetailKey(workspaceId, id ?? 'new'),
    queryFn: ({ signal }) => getSession(id!, { signal }),
    enabled: enabled && !!workspaceId && !!id && id !== 'new',
    staleTime: 30_000, gcTime: 120_000,
    // Only a complete legacy representation may seed a detail query.
    initialData: () => client.getQueryData<Session[]>(workspaceQueryKey(workspaceId, 'sessions'))?.find(item => item.id === id),
    initialDataUpdatedAt: () => client.getQueryState(workspaceQueryKey(workspaceId, 'sessions'))?.dataUpdatedAt,
    retry: (count, error) => !('status' in error && (error.status === 403 || error.status === 404)) && count < 1,
  })
}

export function useSessionNeighborhood(workspaceId: string, id?: string) {
  const current = useSessionDetail(workspaceId, id)
  const parent = useSessionDetail(workspaceId, current.data?.parent_session_id ?? undefined)
  const related = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'sessions', 'related', current.data?.parent_session_id ?? id),
    queryFn: ({ signal }) => listSessionPage({ parent_session_id: current.data?.parent_session_id ?? id, roots_only: false, include_archived: true }, {}, { signal }),
    enabled: !!current.data && id !== 'new', staleTime: 30_000, gcTime: 120_000,
  })
  const data = useMemo(() => {
    if (!current.data) return undefined
    const records = new Map<string, SessionMetadata>()
    for (const record of related.data?.items ?? []) records.set(record.id, record)
    for (const record of [parent.data, current.data]) {
      if (record) records.set(record.id, { ...record, has_children: records.get(record.id)?.has_children ?? false })
    }
    return [...records.values()]
  }, [current.data, parent.data, related.data])
  return { ...current, data, session: current.data, hasMoreRelated: related.data?.has_more ?? false }
}
export type SessionMetadata = SessionSummary & Partial<Pick<Session, 'model_snapshot' | 'context_capsule' | 'current_node_id'>>

export function useSessionPicker(workspaceId: string, query: string, enabled: boolean, composing = false) {
  const [debounced, setDebounced] = useState(query.trim())
  useEffect(() => {
    if (composing) return
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [query, composing])
  const pages = useInfiniteQuery({
    queryKey: workspaceQueryKey(workspaceId, 'sessions', 'picker', debounced),
    queryFn: ({ pageParam, signal }) => listSessionPage({ roots_only: false, q: debounced || undefined }, pageParam, { signal }),
    initialPageParam: {} as SessionPageParam,
    getNextPageParam: page => page.has_more && page.next_cursor ? { cursor: page.next_cursor, direction: 'next' as const } : undefined,
    getPreviousPageParam: page => page.has_previous && page.previous_cursor ? { cursor: page.previous_cursor, direction: 'previous' as const } : undefined,
    enabled: enabled && !composing && query.trim() === debounced,
    maxPages: 5, staleTime: 30_000, gcTime: 120_000,
  })
  const recentCreated = useQuery<SessionSummary[]>({
    queryKey: workspaceQueryKey(workspaceId, 'sessions', 'recent-created'), queryFn: skipToken, initialData: [], gcTime: 120_000,
  })
  const data = useMemo(() => {
    const local = (recentCreated.data ?? []).filter(item => item.status !== 'archived' && (!debounced || item.title.toLocaleLowerCase().includes(debounced.toLocaleLowerCase())))
    return [...new Map([...local, ...(pages.data?.pages.flatMap(page => page.items) ?? [])].map(item => [item.id, item])).values()]
  }, [pages.data, recentCreated.data, debounced])
  return { ...pages, data, waiting: composing || query.trim() !== debounced }
}

/** Immutable, identity-preserving updates; never replace a paginated cache with an array. */
export function updateSessionCaches(client: QueryClient, workspaceId: string, updated: Session) {
  client.setQueryData(sessionDetailKey(workspaceId, updated.id), updated)
  client.setQueryData<Session[]>(workspaceQueryKey(workspaceId, 'sessions'), previous => previous?.map(item => item.id === updated.id ? updated : item))
  client.setQueryData<SessionSummary[]>(workspaceQueryKey(workspaceId, 'sessions', 'recent-created'), previous => previous?.map(item => item.id === updated.id ? summaryOf(updated, item.has_children) : item))
  client.setQueriesData<InfiniteData<SessionPage>>({ queryKey: sessionBrowserKey(workspaceId) }, previous => {
    if (!previous?.pages) return previous
    let changed = false
    const pages = previous.pages.map(page => {
      if (!page.items.some(item => item.id === updated.id)) return page
      changed = true
      return { ...page, items: page.items.map(item => item.id === updated.id ? { ...item, ...summaryOf(updated, item.has_children) } : item) }
    })
    return changed ? { ...previous, pages } : previous
  })
}
export function cacheCreatedSession(client: QueryClient, workspaceId: string, session: Session) {
  updateSessionCaches(client, workspaceId, session)
  // A picker request can have started before create committed. Keep a tiny,
  // workspace-scoped overlay so that late response cannot hide the new row.
  client.setQueryData<SessionSummary[]>(workspaceQueryKey(workspaceId, 'sessions', 'recent-created'), previous =>
    [summaryOf(session), ...(previous ?? []).filter(item => item.id !== session.id)].slice(0, 20))
  for (const [key, previous] of client.getQueriesData<InfiniteData<SessionPage>>({ queryKey: workspaceQueryKey(workspaceId, 'sessions') })) {
    if (!previous?.pages?.length || previous.pages.some(page => page.items.some(item => item.id === session.id))) continue
    let include = false
    if (key[3] === 'picker') include = !key[4] || session.title.toLocaleLowerCase().includes(String(key[4]).toLocaleLowerCase())
    if (key[3] === 'browser') {
      const scope = key[4] as SessionScope
      const sameGroup = scope.project_id ? scope.project_id === session.project_id : scope.ungrouped ? !session.project_id : true
      include = sameGroup && (scope.parent_session_id ? session.parent_session_id === scope.parent_session_id : !session.parent_session_id)
    }
    if (!include || session.status === 'archived') continue
    if (previous.pages[0].items.length >= 40) { void client.invalidateQueries({ queryKey: key, exact: true }); continue }
    client.setQueryData<InfiniteData<SessionPage>>(key, {
      ...previous, pages: previous.pages.map((page, index) => index ? page : { ...page, items: [summaryOf(session), ...page.items] }),
    })
  }
}

export function summaryOf(session: Session, hasChildren = false): SessionSummary {
  const { model_snapshot: _model, context_capsule: _capsule, current_node_id: _node, ...summary } = session
  void _model; void _capsule; void _node
  return { ...summary, has_children: hasChildren }
}
