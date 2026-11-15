import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createGraphOpenCoordinator, graphVisit, rememberGraphVisit, pickGraphConversation } from './graph-folder-navigation'
import type { SessionSummary } from '@/api/session-browser'

const record = (id: string, overrides = {}) => ({ id, project_id: 'p1', graph_id: 'g1', status: 'active',
  session_kind: 'main', updated_at: id === 'old' ? '2026-01-01' : '2026-02-01', ...overrides }) as SessionSummary
beforeEach(() => localStorage.clear())
describe('graph folder navigation ownership', () => {
  it('ignores corrupted optional local history', () => {
    localStorage.setItem('learngraph:graph-learning-history:v1', 'null')
    expect(graphVisit('ws', 'g1')).toBeUndefined()
    rememberGraphVisit('ws', 'g1', 'valid')
    expect(graphVisit('ws', 'g1')?.sessionId).toBe('valid')
  })
  it('remembers visits by workspace and stable graph ID, not title', () => {
    rememberGraphVisit('ws', 'g1', 's1')
    rememberGraphVisit('ws', 'g2', 's2')
    rememberGraphVisit('other', 'g1', 'foreign')
    expect(graphVisit('ws', 'g1')?.sessionId).toBe('s1')
    expect(graphVisit('ws', 'g2')?.sessionId).toBe('s2')
    expect(graphVisit('other', 'g1')?.sessionId).toBe('foreign')
    rememberGraphVisit('ws', 'g1')
    expect(graphVisit('ws', 'g1')?.sessionId).toBe('s1')
  })
  it('resumes the remembered conversation or most recent eligible conversation', () => {
    const sessions = [record('old'), record('recent'), record('foreign', { project_id: 'p2' }), record('other-graph', { graph_id: 'g2' }), record('archived', { status: 'archived' }), record('side', { session_kind: 'side' })]
    expect(pickGraphConversation(sessions, 'p1', 'g1')?.id).toBe('recent')
    expect(pickGraphConversation(sessions, 'p1', 'g1', { rememberedSessionId: 'old' })?.id).toBe('old')
    expect(pickGraphConversation(sessions, 'missing', 'g1')).toBeUndefined()
  })
  it('coalesces repeated clicks without changing the latest navigation owner', async () => {
    const controller = createGraphOpenCoordinator()
    let finish!: () => void
    const wait = new Promise<void>(resolve => { finish = resolve })
    const navigate = vi.fn()
    const open = vi.fn(async (latest: () => boolean) => { await wait; if (latest()) navigate('g1') })
    const first = controller.run('g1', open)
    const second = controller.run('g1', open)
    expect(first).toBe(second)
    finish(); await first
    expect(open).toHaveBeenCalledOnce()
    expect(navigate).toHaveBeenCalledExactlyOnceWith('g1')
  })
  it('prevents a slow old response from switching back after a newer graph', async () => {
    const controller = createGraphOpenCoordinator()
    let finish!: () => void
    const wait = new Promise<void>(resolve => { finish = resolve })
    const navigate = vi.fn()
    const old = controller.run('g1', async latest => { await wait; if (latest()) navigate('g1') })
    await controller.run('g2', async latest => { if (latest()) navigate('g2') })
    finish(); await old
    expect(navigate).toHaveBeenCalledExactlyOnceWith('g2')
  })
  it('honors A → B → A even when the first A is still pending', async () => {
    const controller = createGraphOpenCoordinator()
    let finish!: () => void
    const wait = new Promise<void>(resolve => { finish = resolve })
    const navigate = vi.fn()
    const old = controller.run('A', async latest => { await wait; if (latest()) navigate('old-A') })
    const middle = controller.run('B', async latest => { await wait; if (latest()) navigate('B') })
    await controller.run('A', async latest => { if (latest()) navigate('new-A') })
    finish(); await Promise.all([old, middle])
    expect(navigate).toHaveBeenCalledExactlyOnceWith('new-A')
  })
  it('invalidates pending navigation on workspace change/unmount', async () => {
    const controller = createGraphOpenCoordinator()
    const navigate = vi.fn()
    const pending = controller.run('g1', async latest => { if (latest()) navigate() })
    controller.invalidate(); await pending
    expect(navigate).not.toHaveBeenCalled()
  })
})
