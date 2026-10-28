import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'

const script = readFileSync('../mobile/native/app/src/main/assets/native-sidebar-activity.js', 'utf8')
type Event = { kind: string; sessionId?: string; [key: string]: unknown }
type HostWindow = Window & {
  __lgSidebarSeed?: (value: unknown) => void
  __lgSidebarDispose?: () => void
}

describe('native sidebar relay lifecycle and acknowledged hydration', () => {
  const host = window as HostWindow
  const request = { owner: 'doc-1', origin: window.location.origin, token: 'account-a', workspaceId: 'w', seed: { revision: 1, activities: {} } }
  let listener: ((event: Event) => void) | undefined
  const send = vi.fn(), seed = vi.fn(() => true), unsubscribe = vi.fn()
  beforeEach(() => {
    vi.useFakeTimers(); listener = undefined
    send.mockReset(); seed.mockClear(); unsubscribe.mockClear()
    Object.assign(window, {
      LearnGraphNative: { sidebarActivity: send },
      LearnGraphSidebarActivity: { version: 1, seed, subscribe: vi.fn((_scope: unknown, fn: (event: Event) => void) => { listener = fn; fn({ kind: 'view', sessionId: 'a' }); return unsubscribe }) },
    })
  })
  afterEach(() => { host.__lgSidebarDispose?.(); vi.clearAllTimers(); vi.useRealTimers(); delete window.LearnGraphSidebarActivity })
  const run = (value = request) => window.eval(`${script}(${JSON.stringify(value)});`)

  it('scopes real events, preserves pending local changes, then accepts acknowledged host state', () => {
    run()
    expect(seed).toHaveBeenCalledWith({ token: 'account-a', workspaceId: 'w' }, { activities: {}, preserveIds: [] })
    listener!({ kind: 'running', sessionId: 'a', running: true })
    const envelope = JSON.parse(send.mock.calls[1][0])
    expect(envelope).toMatchObject({ version: 1, owner: 'doc-1', sequence: 2, token: 'account-a', workspaceId: 'w', event: { kind: 'running', sessionId: 'a', running: true } })
    host.__lgSidebarSeed!({ revision: 2, activities: { a: { running: false } }, acknowledged: { 'doc-1': 1 } })
    expect(seed).toHaveBeenLastCalledWith(expect.anything(), { activities: { a: { running: false } }, preserveIds: ['a'] })
    host.__lgSidebarSeed!({ revision: 3, activities: { a: { running: true } }, acknowledged: { 'doc-1': 2 } })
    expect(seed).toHaveBeenLastCalledWith(expect.anything(), { activities: { a: { running: true } }, preserveIds: [] })
    const count = seed.mock.calls.length
    host.__lgSidebarSeed!({ revision: 1, activities: {} })
    expect(seed).toHaveBeenCalledTimes(count)
  })

  it('duplicate injections do not subscribe twice and pagehide releases only this document owner', () => {
    run(); run()
    expect(window.LearnGraphSidebarActivity!.subscribe).toHaveBeenCalledTimes(1)
    window.dispatchEvent(new PageTransitionEvent('pagehide'))
    expect(JSON.parse(send.mock.calls.at(-1)![0])).toMatchObject({ owner: 'doc-1', event: { kind: 'release' } })
    expect(unsubscribe).toHaveBeenCalledTimes(1)
    const count = send.mock.calls.length
    listener!({ kind: 'voice', sessionId: 'a', active: true })
    expect(send).toHaveBeenCalledTimes(count)
    expect(host.__lgSidebarSeed).toBeUndefined()
  })

  it('wrong origins cannot bind and bridge errors never escape into a stream callback', () => {
    run({ ...request, origin: 'https://elsewhere.invalid' })
    expect(seed).not.toHaveBeenCalled()
    run(); send.mockImplementation(() => { throw new Error('WebView destroyed') })
    expect(() => listener!({ kind: 'finished', sessionId: 'a', viewing: false, at: 99 })).not.toThrow()
  })

  it('canonicalizes explicit default ports for a legitimate server', () => {
    const dom = new JSDOM('', { url: 'https://example.test/w/w/chat/a', runScripts: 'outside-only' })
    const bound = vi.fn(() => () => {})
    Object.assign(dom.window, { LearnGraphSidebarActivity: { version: 1, seed: () => true, subscribe: bound } })
    dom.window.eval(`${script}(${JSON.stringify({ ...request, origin: 'https://example.test:443/' })});`)
    expect(bound).toHaveBeenCalledOnce()
    dom.window.close()
  })
})
