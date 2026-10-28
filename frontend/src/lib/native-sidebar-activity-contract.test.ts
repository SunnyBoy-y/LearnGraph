import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { authStore } from '@/api/auth-store'
import { markVoiceSessionResumable, setVoiceSessionActive } from '@/features/voice/voice-session-markers'
import { getSessionActivity, markSessionGenerationFinished, markSessionRunning, markSessionTouched, markSessionViewed, seedSessionActivity } from './session-activity'
import { useNativeSidebarActivityContract } from './native-sidebar-activity-contract'

describe('the scoped public sidebar activity contract', () => {
  const scope = { token: 'account-a', workspaceId: 'w' }
  beforeEach(() => {
    localStorage.clear(); sessionStorage.clear(); seedSessionActivity(new Map())
    authStore.setSession({ accessToken: scope.token, workspaceId: scope.workspaceId })
  })
  afterEach(() => { cleanup(); authStore.clear() })
  const mount = () => renderHook(({ path }) => useNativeSidebarActivityContract('w', path), { initialProps: { path: '/w/w/chat/a' } })

  it('relays real generation events and route views without inventing navigation touches', () => {
    const hook = mount(), events = vi.fn()
    window.LearnGraphSidebarActivity!.subscribe(scope, events)
    expect(events).toHaveBeenLastCalledWith({ kind: 'view', sessionId: 'a' })
    act(() => { markSessionTouched('a', 10); markSessionRunning('a', true); markSessionGenerationFinished('a', { viewing: false, at: 20 }) })
    expect(events.mock.calls.slice(1).map(([event]) => event)).toEqual([
      { kind: 'touched', sessionId: 'a', at: 10 }, { kind: 'running', sessionId: 'a', running: true },
      { kind: 'finished', sessionId: 'a', viewing: false, at: 20 },
    ])
    hook.rerender({ path: '/w/w/chat/b' })
    expect(events).toHaveBeenLastCalledWith({ kind: 'view', sessionId: 'b' })
    expect(getSessionActivity('b').lastTouchedAt).toBe(0)
    act(() => markSessionViewed('a'))
    expect(getSessionActivity('a')).toMatchObject({ unreadCompleted: false, completedAt: null, lastTouchedAt: 10 })
    expect(events).toHaveBeenLastCalledWith({ kind: 'viewed', sessionId: 'a' })
    hook.rerender({ path: '/w/w/chat/new' })
    expect(events).toHaveBeenLastCalledWith({ kind: 'view', sessionId: null })
  })

  it('hydrates the original store without echo and preserves unacknowledged local session changes', () => {
    mount(); const events = vi.fn(); const contract = window.LearnGraphSidebarActivity!
    contract.subscribe(scope, events); events.mockClear()
    const remote = { running: false, unreadCompleted: true, completedAt: 20, lastTouchedAt: 5 }
    expect(contract.seed(scope, { activities: { a: remote, new: remote } })).toBe(true)
    expect(getSessionActivity('a')).toEqual(remote)
    expect(getSessionActivity('new').unreadCompleted).toBe(false)
    expect(events).not.toHaveBeenCalled()
    act(() => markSessionTouched('a', 30)); events.mockClear()
    contract.seed(scope, { activities: { a: remote }, preserveIds: ['a'] })
    expect(getSessionActivity('a').lastTouchedAt).toBe(30)
    expect(events).not.toHaveBeenCalled()
  })

  it('relays only active voice in this workspace, excludes resumable calls, and unsubscribes on unmount', () => {
    setVoiceSessionActive('w', 'live', true); markVoiceSessionResumable('w', 'old')
    const hook = mount(), events = vi.fn()
    window.LearnGraphSidebarActivity!.subscribe(scope, events)
    expect(events.mock.calls.map(([event]) => event)).toEqual([{ kind: 'view', sessionId: 'a' }, { kind: 'voice', sessionId: 'live', active: true }])
    events.mockClear()
    setVoiceSessionActive('other', 'foreign', true); markVoiceSessionResumable('w', 'old')
    expect(events).not.toHaveBeenCalled()
    setVoiceSessionActive('w', 'live', false)
    expect(events).toHaveBeenCalledExactlyOnceWith({ kind: 'voice', sessionId: 'live', active: false })
    hook.unmount(); events.mockClear()
    markSessionTouched('a', 90); setVoiceSessionActive('w', 'live', true)
    expect(events).not.toHaveBeenCalled()
    expect(window.LearnGraphSidebarActivity).toBeUndefined()
  })

  it('rejects another token/workspace and prevents old subscriptions crossing account changes', () => {
    mount(); const events = vi.fn(), contract = window.LearnGraphSidebarActivity!
    expect(contract.subscribe({ ...scope, workspaceId: 'elsewhere' }, events)).toBeNull()
    expect(contract.seed({ ...scope, token: 'account-b' }, { activities: {} })).toBe(false)
    contract.subscribe(scope, events); events.mockClear()
    authStore.setAccessToken('account-b')
    markSessionRunning('a', true)
    expect(events).not.toHaveBeenCalled()
    expect(contract.seed(scope, { activities: {} })).toBe(false)
  })

  it('a disconnected native listener never interrupts the Web generation path', () => {
    mount()
    window.LearnGraphSidebarActivity!.subscribe(scope, () => { throw new Error('bridge closed') })
    expect(() => markSessionRunning('a', true)).not.toThrow()
    expect(getSessionActivity('a').running).toBe(true)
  })
})
