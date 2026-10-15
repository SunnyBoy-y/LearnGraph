import { beforeEach, describe, expect, it } from 'vitest'

import { authStore } from './auth-store'

describe('authStore mustChangePassword persistence', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
  })

  it('restores the password-change requirement after a session reload', () => {
    authStore.setSession({
      accessToken: 'token',
      workspaceId: 'ws-1',
      mustChangePassword: true,
    })
    expect(authStore.getSession()?.mustChangePassword).toBe(true)
    expect(sessionStorage.getItem('learngraph.must_change_password')).toBe('true')
  })

  it('restores false explicitly and removes the flag on logout', () => {
    authStore.setSession({
      accessToken: 'token',
      workspaceId: 'ws-1',
      mustChangePassword: false,
    })
    expect(authStore.getSession()?.mustChangePassword).toBe(false)

    authStore.clear()
    expect(authStore.getSession()).toBeNull()
    expect(sessionStorage.getItem('learngraph.must_change_password')).toBeNull()
  })

  it('keeps legacy sessions without the flag as undefined until hydrated', () => {
    authStore.setSession({ accessToken: 'token', workspaceId: 'ws-1' })
    expect(authStore.getSession()?.mustChangePassword).toBeUndefined()
  })

  describe('persistent login layer (sessionStorage + localStorage double-write)', () => {
    beforeEach(() => {
      sessionStorage.clear()
      localStorage.clear()
    })

    it('writes auth state to both storages so a browser restart stays logged in', () => {
      authStore.setSession({ accessToken: 'token-persist', workspaceId: 'ws-9' })
      expect(sessionStorage.getItem('learngraph.access_token')).toBe('token-persist')
      expect(localStorage.getItem('learngraph.access_token')).toBe('token-persist')
      expect(localStorage.getItem('learngraph.workspace_id')).toBe('ws-9')
    })

    it('restores the session from localStorage when sessionStorage is empty (restart / WebView re-creation)', () => {
      authStore.setSession({ accessToken: 'token-persist', workspaceId: 'ws-9' })
      sessionStorage.clear() // simulate browser/app restart
      const restored = authStore.getSession()
      expect(restored?.accessToken).toBe('token-persist')
      expect(restored?.workspaceId).toBe('ws-9')
    })

    it('prefers sessionStorage over localStorage (per-tab account isolation)', () => {
      authStore.setSession({ accessToken: 'token-persist', workspaceId: 'ws-9' })
      sessionStorage.setItem('learngraph.access_token', 'token-tab')
      expect(authStore.getAccessToken()).toBe('token-tab')
    })

    it('clears both storages on logout', () => {
      authStore.setSession({ accessToken: 'token-persist', workspaceId: 'ws-9' })
      authStore.clear()
      expect(sessionStorage.getItem('learngraph.access_token')).toBeNull()
      expect(localStorage.getItem('learngraph.access_token')).toBeNull()
      expect(localStorage.getItem('learngraph.workspace_id')).toBeNull()
      expect(authStore.getSession()).toBeNull()
    })
  })
})
