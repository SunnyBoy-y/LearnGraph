import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { authStore } from './auth-store'
import { updateSetting } from './settings'

const { put } = vi.hoisted(() => ({ put: vi.fn() }))
vi.mock('./client', () => ({ apiClient: { put } }))

describe('native thinking preference follows a successful authenticated setting save', () => {
  const bridge = vi.fn()
  const session = { accessToken: 'token-a', workspaceId: 'workspace-a', userId: 'account-a' }
  const saved = { key: 'chat.thinking_chain_default', value: { default_state: 'collapsed' } }
  beforeEach(() => {
    put.mockReset(); bridge.mockReset()
    authStore.setSession(session)
    Object.assign(window, { LearnGraphNative: { setThinkingChainDefaultOpen: bridge } })
  })
  afterEach(() => { Object.assign(window, { LearnGraphNative: undefined }) })

  it('waits for the server and forwards its actual saved value without another request', async () => {
    let resolve!: (value: typeof saved) => void
    put.mockReturnValue(new Promise<typeof saved>(done => { resolve = done }))
    const pending = updateSetting(saved.key, { default_state: 'open' })
    expect(bridge).not.toHaveBeenCalled()
    resolve(saved)
    await expect(pending).resolves.toEqual(saved)
    expect(put).toHaveBeenCalledExactlyOnceWith('/settings/chat.thinking_chain_default', { value: { default_state: 'open' } }, undefined)
    expect(bridge).toHaveBeenCalledTimes(1)
    expect(JSON.parse(bridge.mock.calls[0][0])).toEqual({ origin: location.origin, token: session.accessToken, workspaceId: session.workspaceId, open: false })
  })

  it('never changes the native preference when the server rejects the save', async () => {
    put.mockRejectedValue(new Error('保存失败'))
    await expect(updateSetting(saved.key, saved.value)).rejects.toThrow('保存失败')
    expect(bridge).not.toHaveBeenCalled()
  })

  it.each([
    { ...session, accessToken: 'token-b', userId: 'account-b' },
    { ...session, workspaceId: 'workspace-b' },
  ])('discards a delayed completion after authentication scope changes to $workspaceId/$userId', async (next) => {
    let resolve!: (value: typeof saved) => void
    put.mockReturnValue(new Promise<typeof saved>(done => { resolve = done }))
    const pending = updateSetting(saved.key, saved.value)
    authStore.setSession(next)
    resolve(saved)
    await expect(pending).resolves.toEqual(saved)
    expect(bridge).not.toHaveBeenCalled()
  })

  it('only forwards this preference and only for the authenticated workspace request', async () => {
    put.mockResolvedValue({ key: 'ui.preferences', value: { default_state: 'collapsed' } })
    await updateSetting('ui.preferences', { default_state: 'collapsed' })
    expect(bridge).not.toHaveBeenCalled()
    put.mockResolvedValue(saved)
    await updateSetting(saved.key, saved.value, { workspace: false })
    await updateSetting(saved.key, saved.value, { headers: { Authorization: 'Bearer another-account' } })
    await updateSetting(saved.key, saved.value, { headers: { 'X-Workspace-ID': 'another-workspace' } })
    expect(bridge).not.toHaveBeenCalled()
  })

  it('keeps a successful server save successful on older or detached native bridges', async () => {
    put.mockResolvedValue({ ...saved, value: { default_state: 'open' } })
    bridge.mockImplementation(() => { throw new Error('WebView detached') })
    await expect(updateSetting(saved.key, { default_state: 'open' })).resolves.toMatchObject({ value: { default_state: 'open' } })
    expect(JSON.parse(bridge.mock.calls[0][0]).open).toBe(true)
    Object.assign(window, { LearnGraphNative: undefined })
    await expect(updateSetting(saved.key, { default_state: 'open' })).resolves.toMatchObject({ value: { default_state: 'open' } })
  })
})
