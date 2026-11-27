import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, type InfiniteData } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { listSessionPage, type SessionPage } from '@/api/session-browser'
import { cacheCreatedSession, removeSessionsFromCaches, sessionBrowserKey, sessionDetailKey, summaryOf, updateSessionCaches, useSessionPicker } from './session-queries'
import { workspaceQueryKey } from './query-keys'
import type { Session } from '@/types/sessions'

vi.mock('@/api/session-browser', () => ({ listSessionPage: vi.fn(), getSession: vi.fn() }))
const session = (id: string): Session => ({ id, workspace_id: 'w', title: id, pinned: false, status: 'active', parent_session_id: null, project_id: null, model_snapshot: {}, context_capsule: {} } as Session)
const page = (id: string): SessionPage => ({ items: [summaryOf(session(id))], has_more: false, next_cursor: null, has_previous: false, previous_cursor: null })

describe('session cache representations', () => {
  it('patches only the changed row and preserves the infinite-page shape', () => {
    const client = new QueryClient()
    const key = [...sessionBrowserKey('w'), { ungrouped: true }]
    const other = summaryOf(session('other'))
    const initial = { pages: [{ ...page('a'), items: [summaryOf(session('a')), other] }], pageParams: [{}] }
    client.setQueryData(key, initial)
    updateSessionCaches(client, 'w', { ...session('a'), title: 'renamed' })
    const result = client.getQueryData<InfiniteData<SessionPage>>(key)!
    expect(result.pages[0].items[0].title).toBe('renamed')
    expect(result.pages[0].items[1]).toBe(other)
    expect(result.pageParams).toEqual([{}])
    expect(client.getQueryData(workspaceQueryKey('other-workspace', 'sessions'))).toBeUndefined()
    client.clear()
  })
  it('does not lose a newly created session to an older picker response', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let resolve!: (value: SessionPage) => void
    vi.mocked(listSessionPage).mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    const hook = renderHook(() => useSessionPicker('w', '', true), { wrapper })
    await waitFor(() => expect(listSessionPage).toHaveBeenCalled())
    act(() => cacheCreatedSession(client, 'w', session('created')))
    await act(async () => resolve(page('existing')))
    await waitFor(() => expect(hook.result.current.data.map(item => item.id)).toEqual(['created', 'existing']))
    hook.unmount(); client.clear()
  })
  it('removes deleted sessions immediately from browser, list, recent-created, and detail caches', () => {
    const client = new QueryClient()
    const browserKey = [...sessionBrowserKey('w'), { ungrouped: true }]
    const keep = summaryOf(session('keep'))
    client.setQueryData(browserKey, { pages: [{ ...page('del'), items: [summaryOf(session('del')), keep] }], pageParams: [{}] })
    client.setQueryData(workspaceQueryKey('w', 'sessions'), [session('del'), session('keep')])
    client.setQueryData(workspaceQueryKey('w', 'sessions', 'recent-created'), [summaryOf(session('del')), keep])
    client.setQueryData(sessionDetailKey('w', 'del'), session('del'))
    client.setQueryData(sessionDetailKey('w', 'keep'), session('keep'))

    removeSessionsFromCaches(client, 'w', ['del'])

    expect(client.getQueryData<InfiniteData<SessionPage>>(browserKey)?.pages[0].items.map(i => i.id)).toEqual(['keep'])
    expect(client.getQueryData<Session[]>(workspaceQueryKey('w', 'sessions'))?.map(i => i.id)).toEqual(['keep'])
    expect(client.getQueryData(workspaceQueryKey('w', 'sessions', 'recent-created'))).toEqual([keep])
    expect(client.getQueryData(sessionDetailKey('w', 'del'))).toBeUndefined()
    expect(client.getQueryData(sessionDetailKey('w', 'keep'))).toBeDefined()
    client.clear()
  })
})
