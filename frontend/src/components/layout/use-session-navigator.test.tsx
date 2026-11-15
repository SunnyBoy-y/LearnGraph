import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { useSessionNavigator } from './use-session-navigator'
import { listSessionPage, type SessionPage } from '@/api/session-browser'
import type { ReactNode } from 'react'
vi.mock('@/api/session-browser', () => ({ listSessionPage: vi.fn() }))

function fixture() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return { client, ...renderHook(() => useSessionNavigator('w'), { wrapper }) }
}
const page = (id: string, more = false): SessionPage => ({
  items: [{ id, title: id, workspace_id: 'w', pinned: false, status: 'active', project_id: null, parent_session_id: null, has_children: false } as SessionPage['items'][number]],
  next_cursor: more ? id : null, previous_cursor: null, has_more: more, has_previous: false,
})

describe('lazy session navigator', () => {
  it('does no work until a visible scope activates, and deduplicates overlapping loads', async () => {
    vi.mocked(listSessionPage).mockReset().mockResolvedValueOnce(page('first', true))
    const hook = fixture()
    expect(listSessionPage).not.toHaveBeenCalled()
    act(() => hook.result.current.activate({ ungrouped: true, roots_only: true }))
    await waitFor(() => expect(hook.result.current.data[0]?.id).toBe('first'))
    let resolve!: (page: SessionPage) => void
    vi.mocked(listSessionPage).mockImplementationOnce(() => new Promise(done => { resolve = done }))
    let requests!: Promise<unknown>[]
    act(() => { requests = [hook.result.current.load({ ungrouped: true, roots_only: true }), hook.result.current.load({ ungrouped: true, roots_only: true })] })
    expect(listSessionPage).toHaveBeenCalledTimes(2)
    await act(async () => { resolve(page('second')); await Promise.all(requests) })
    await waitFor(() => expect(hook.result.current.data.map(item => item.id)).toEqual(['first', 'second']))
    hook.unmount(); hook.client.clear()
  })
  it('keeps prior items during slow/error continuation and does not auto-retry forever', async () => {
    vi.mocked(listSessionPage).mockReset().mockResolvedValueOnce(page('first', true))
    const hook = fixture()
    act(() => hook.result.current.activate({ ungrouped: true }))
    await waitFor(() => expect(hook.result.current.data).toHaveLength(1))
    vi.mocked(listSessionPage).mockRejectedValue(new Error('offline'))
    await act(async () => { await hook.result.current.load({ ungrouped: true }) })
    expect(hook.result.current.data[0].id).toBe('first')
    const calls = vi.mocked(listSessionPage).mock.calls.length
    await act(async () => { await hook.result.current.load({ ungrouped: true }) })
    expect(listSessionPage).toHaveBeenCalledTimes(calls)
    hook.unmount(); hook.client.clear()
  })
  it('does not resurrect a confirmed deletion from a late page response', async () => {
    let resolve!: (page: SessionPage) => void
    vi.mocked(listSessionPage).mockReset().mockImplementationOnce(() => new Promise(done => { resolve = done }))
    const hook = fixture()
    act(() => hook.result.current.activate({ ungrouped: true }))
    await waitFor(() => expect(listSessionPage).toHaveBeenCalledTimes(1))
    act(() => window.dispatchEvent(new CustomEvent('learngraph:sessions-deleted', { detail: { workspaceId: 'w', sessionIds: ['deleted'], projectIds: [] } })))
    await act(async () => resolve(page('deleted')))
    await waitFor(() => expect(hook.result.current.scopes.get('recent')?.isSuccess).toBe(true))
    expect(hook.result.current.data).toEqual([])
    hook.unmount(); hook.client.clear()
  })
  it('bounds cached pages and can reload evicted history in the reverse direction', async () => {
    vi.mocked(listSessionPage).mockReset().mockImplementation(async (_scope, param) => {
      const offset = param?.direction === 'previous' ? Math.max(0, Number(param.cursor) - 20) : Number(param?.cursor ?? 0)
      return { items: Array.from({ length: 20 }, (_, index) => page(`s${offset + index}`).items[0]), has_more: true,
        next_cursor: String(offset + 20), has_previous: offset > 0, previous_cursor: offset > 0 ? String(offset) : null }
    })
    const hook = fixture()
    act(() => hook.result.current.activate({ ungrouped: true }))
    await waitFor(() => expect(hook.result.current.data).toHaveLength(20))
    for (let index = 0; index < 11; index++) await act(async () => { await hook.result.current.load({ ungrouped: true }) })
    expect(hook.result.current.data).toHaveLength(200)
    expect(hook.result.current.data[0].id).toBe('s40')
    await act(async () => { await hook.result.current.load({ ungrouped: true }, 'previous') })
    expect(hook.result.current.data).toHaveLength(200)
    expect(hook.result.current.data[0].id).toBe('s20')
    hook.unmount(); hook.client.clear()
  })
  it('cancels an in-flight child scope when its ancestor collapses', async () => {
    let signal: AbortSignal | undefined
    vi.mocked(listSessionPage).mockReset().mockImplementation((_scope, _page, options) => new Promise((_resolve, reject) => {
      signal = options?.signal
      signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    }))
    const hook = fixture()
    act(() => hook.result.current.activate({ parent_session_id: 'child', roots_only: false }))
    await waitFor(() => expect(listSessionPage).toHaveBeenCalledTimes(1))
    act(() => hook.result.current.retainScopes(new Set()))
    expect(signal?.aborted).toBe(true)
    await act(async () => { await hook.client.invalidateQueries() })
    expect(listSessionPage).toHaveBeenCalledTimes(1)
    hook.unmount(); hook.client.clear()
  })
  it('does not request collapsed projects or unexpanded children', async () => {
    vi.mocked(listSessionPage).mockReset().mockResolvedValue(page('root'))
    const hook = fixture()
    act(() => hook.result.current.activate({ ungrouped: true }))
    await waitFor(() => expect(hook.result.current.data).toHaveLength(1))
    expect(listSessionPage).toHaveBeenCalledTimes(1)
    expect(vi.mocked(listSessionPage).mock.calls[0][0]).toEqual({ ungrouped: true })
    hook.unmount(); hook.client.clear()
  })
})
