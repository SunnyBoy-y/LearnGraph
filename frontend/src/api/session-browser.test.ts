import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from './client'
import { getSession, listSessionPage, type SessionPage } from './session-browser'

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })
describe('session browser requests', () => {
  it('requests a bounded page and forwards cursor/scope', async () => {
    const page: SessionPage = { items: [], next_cursor: null, previous_cursor: null, has_more: false, has_previous: false }
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue(page)
    await expect(listSessionPage({ ungrouped: true, roots_only: true }, { cursor: 'next' })).resolves.toBe(page)
    expect(get).toHaveBeenCalledWith('/sessions/page', expect.objectContaining({ query: { ungrouped: true, roots_only: true, cursor: 'next', limit: 20 }, signal: expect.any(AbortSignal) }))
  })
  it('aborts actual fetch when the query scope is cancelled', async () => {
    const parent = new AbortController()
    let actual: AbortSignal | undefined
    vi.spyOn(apiClient, 'get').mockImplementation((_path, options) => new Promise((_resolve, reject) => {
      actual = options?.signal
      actual?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    }))
    const pending = listSessionPage({}, {}, { signal: parent.signal })
    const check = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    parent.abort(); await check
    expect(actual?.aborted).toBe(true)
  })
  it('bounds a stalled continuation and exposes retry instead of an endless spinner', async () => {
    vi.useFakeTimers()
    vi.spyOn(apiClient, 'get').mockImplementation((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    }))
    const check = expect(listSessionPage()).rejects.toThrow('会话加载超时，请重试')
    await vi.advanceTimersByTimeAsync(12_000)
    await check
  })
  it('loads an old/deep-linked session directly without fetching the collection', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ id: 'old/id' })
    await getSession('old/id')
    expect(get).toHaveBeenCalledExactlyOnceWith('/sessions/old%2Fid', {})
  })
})
