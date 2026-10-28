import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '@/api/client'
import type { Message, MessageListPage } from '@/types/sessions'
import { ConditionalSessionTimeline, readVisibleSessionTimeline, timelinePollInterval } from './session-timeline-sync'

const message = (id: string, content = id) => ({ id, session_id: 's', role: 'assistant', content, status: 'completed', parts: [], version: 1 } as unknown as Message)
const page = (items: Message[], more = false): MessageListPage => ({ items, has_more_before: more, total_count: items.length, oldest_id: items[0]?.id ?? null, newest_id: items.at(-1)?.id ?? null })

describe('visible conversation conditional synchronization', () => {
  it('checks the actual scoped HTTP path and reuses a body on 304', async () => {
    const body = page([message('a')])
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(body), { headers: { ETag: 'first' } }))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { ETag: 'first' } }))
    const client = new ApiClient({ baseUrl: 'https://fixture.invalid', fetch: fetcher, accessToken: () => 'fixture-token', workspaceId: () => 'w' })
    const timeline = new ConditionalSessionTimeline(client, () => 'account-a:w')
    expect((await timeline.read('s', 50)).items).toEqual(body.items)
    expect((await timeline.read('s', 50)).items).toEqual(body.items)
    const [url, request] = fetcher.mock.calls[1]!
    expect(String(url)).toBe('https://fixture.invalid/api/v1/sessions/s/messages?limit=50&compact=false')
    const headers = new Headers(request?.headers)
    expect(headers.get('If-None-Match')).toBe('first')
    expect(headers.get('Authorization')).toBe('Bearer fixture-token')
    expect(headers.get('X-Workspace-ID')).toBe('w')
  })

  it('replaces same-length edits, retry versions, and deletions using authoritative bodies', async () => {
    const getConditional = vi.fn()
      .mockResolvedValueOnce({ data: page([message('a', 'old'), message('b')]), etag: 'old' })
      .mockResolvedValueOnce({ data: page([{ ...message('a', 'new'), version: 2 }, message('b')]), etag: 'edit' })
      .mockResolvedValueOnce({ data: page([message('a', 'new')]), etag: 'delete' })
    const timeline = new ConditionalSessionTimeline({ getConditional }, () => 'a:w')
    await timeline.read('s', 50)
    expect((await timeline.read('s', 50)).items[0]).toMatchObject({ content: 'new', version: 2 })
    expect((await timeline.read('s', 50)).items.map(item => item.id)).toEqual(['a'])
  })

  it('revalidates previously opened older pages instead of retaining deleted stale rows', async () => {
    const getConditional = vi.fn()
      .mockResolvedValueOnce({ data: page([message('c'), message('d')], true), etag: 'latest' })
      .mockResolvedValueOnce({ data: page([message('b', 'edited')]), etag: 'older' })
    const timeline = new ConditionalSessionTimeline({ getConditional }, () => 'a:w')
    const result = await timeline.read('s', 2, undefined, [message('a'), message('b'), message('c'), message('d')])
    expect(result.items.map(item => item.id)).toEqual(['b', 'c', 'd'])
    expect(result.items[0]?.content).toBe('edited')
    expect(getConditional.mock.calls[1]?.[2].query).toMatchObject({ before_id: 'c', compact: 'false' })
  })

  it('never sends another account validator or applies a late response after account/session cancellation', async () => {
    let account = 'a'
    let resolve!: (value: unknown) => void
    const getConditional = vi.fn().mockResolvedValueOnce({ data: page([message('a')]), etag: 'private-a' })
      .mockImplementationOnce(() => new Promise(r => { resolve = r }))
      .mockResolvedValueOnce({ data: page([message('b')]), etag: 'private-b' })
    const timeline = new ConditionalSessionTimeline({ getConditional }, () => account)
    await timeline.read('s', 50)
    const pending = timeline.read('s', 50)
    account = 'b'; resolve({ data: page([message('secret-a')]), etag: 'old' })
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    await timeline.read('s', 50)
    expect(getConditional.mock.calls[2]?.[1]).toBeUndefined()
    const controller = new AbortController(); controller.abort()
    await expect(timeline.read('s', 50, controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('only schedules idle real conversations and leaves local or replay streams alone', () => {
    expect(timelinePollInterval('new', false, 'ready')).toBe(false)
    expect(timelinePollInterval('s', true, 'ready')).toBe(false)
    expect(timelinePollInterval('s', false, 'streaming')).toBe(false)
    expect(timelinePollInterval('s', false, 'submitted')).toBe(false)
    expect(timelinePollInterval('s', false, 'ready')).toBe(2_000)
  })

  it('an idle query already in flight preserves the current cache once a local SSE begins', async () => {
    let resolve!: (value: unknown) => void
    const getConditional = vi.fn().mockImplementation(() => new Promise(r => { resolve = r }))
    const timeline = new ConditionalSessionTimeline({ getConditional }, () => 'a:w')
    let streaming = false
    const previous = [message('a', 'before request')]
    let current = previous
    const onPage = vi.fn()
    const pending = readVisibleSessionTimeline(timeline, 's', 50, { previous, isStreaming: () => streaming, current: () => current, onPage })
    // A send starts before the old history GET completes and owns the query cache now.
    streaming = true
    current = [message('a', 'new SSE output'), message('local-user')]
    resolve({ data: page([message('a', 'stale GET output')]), etag: 'stale' })
    expect(await pending).toBe(current)
    expect(onPage).not.toHaveBeenCalled()
  })
})
