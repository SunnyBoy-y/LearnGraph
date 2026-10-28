import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useState } from 'react'
import { authStore } from '@/api/auth-store'
import { defaultComposerPrefs } from './session-composer-prefs'
import { useNativeComposerContract, validateNativeComposerPrefs } from './native-composer-contract'

describe('the public native composer contract', () => {
  const scope = { workspaceId: 'w', sessionId: 'new' }
  beforeEach(() => { localStorage.clear(); sessionStorage.clear(); authStore.setSession({ accessToken: 'account-a', workspaceId: 'w' }) })
  afterEach(() => { cleanup(); authStore.clear() })

  function useComposer(props: { sessionId: string; ready: boolean }) {
    const [prefs, setPrefs] = useState(defaultComposerPrefs())
    useNativeComposerContract({ workspaceId: 'w', ...props, prefs }, setPrefs)
    return { prefs, setPrefs }
  }

  it('waits for session restoration, applies once, and preserves later user changes in chat/new', () => {
    const hook = renderHook(useComposer, { initialProps: { sessionId: 'new', ready: false } })
    const request = { ...scope, id: 'handoff-1', prefs: defaultComposerPrefs({ responseMode: 'fast', thinkingMode: 'off', searchRoute: 'disabled', modelId: 'chosen' }) }
    expect(window.LearnGraphComposer!.apply(request)).toBe('pending')
    expect(hook.result.current.prefs.responseMode).toBe('agentic')
    hook.rerender({ sessionId: 'new', ready: true })
    act(() => { expect(window.LearnGraphComposer!.apply(request)).toBe('accepted') })
    expect(window.LearnGraphComposer!.snapshot(scope)).toMatchObject({ ready: true, appliedRequestId: 'handoff-1', prefs: request.prefs })
    act(() => hook.result.current.setPrefs(defaultComposerPrefs({ thinkingMode: 'xhigh' })))
    act(() => { expect(window.LearnGraphComposer!.apply(request)).toBe('accepted') })
    expect(window.LearnGraphComposer!.snapshot(scope)?.prefs.thinkingMode).toBe('xhigh')
    expect(localStorage.getItem('learngraph:session-composer-prefs')).toBeNull()
  })

  it('snapshots both model families and rejects another session, workspace, or account', () => {
    const hook = renderHook(useComposer, { initialProps: { sessionId: 'new', ready: true } })
    const prefs = defaultComposerPrefs({ generationMode: 'image', providerId: 'text-p', modelId: 'text-m', imageProviderId: 'image-p', imageModelId: 'image-m' })
    act(() => { window.LearnGraphComposer!.apply({ ...scope, id: 'image', prefs }) })
    expect(window.LearnGraphComposer!.snapshot(scope)?.prefs).toEqual(prefs)
    expect(window.LearnGraphComposer!.snapshot({ ...scope, workspaceId: 'other' })).toBeNull()
    expect(window.LearnGraphComposer!.apply({ ...scope, sessionId: 'other', id: 'bad', prefs })).toBe('rejected')
    hook.rerender({ sessionId: 'created-session', ready: true })
    expect(window.LearnGraphComposer!.snapshot(scope)).toBeNull()
    expect(window.LearnGraphComposer!.snapshot({ ...scope, sessionId: 'created-session' })?.prefs).toEqual(prefs)
    authStore.setAccessToken('account-b')
    expect(window.LearnGraphComposer!.snapshot({ ...scope, sessionId: 'created-session' })).toBeNull()
  })

  it('rejects malformed preference values instead of resetting user selections', () => {
    expect(validateNativeComposerPrefs({})).toBeNull()
    expect(validateNativeComposerPrefs({ ...defaultComposerPrefs(), thinkingMode: 'unknown' })).toBeNull()
    expect(validateNativeComposerPrefs({ ...defaultComposerPrefs(), modelId: 42 })).toBeNull()
    expect(validateNativeComposerPrefs({ ...defaultComposerPrefs(), providerId: 'p'.repeat(37) })).toBeNull()
    expect(validateNativeComposerPrefs({ ...defaultComposerPrefs(), responseMode: 'thinking' })?.responseMode).toBe('agentic')
  })
})
