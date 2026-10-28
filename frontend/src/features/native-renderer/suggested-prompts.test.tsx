import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { authStore } from '@/api/auth-store'
import { listProviders, discoverProviderModels } from '@/api/providers'
import { listSettings } from '@/api/settings'
import { getSessionSuggestedPrompts, generateSessionSuggestedPrompts, listMessageVersions, listSessions } from '@/api/sessions'
import { toast } from 'sonner'
import { NativeSuggestedPrompts } from './suggested-prompts'

vi.mock('@/api/providers', () => ({ listProviders: vi.fn(), discoverProviderModels: vi.fn() }))
vi.mock('@/api/settings', () => ({ listSettings: vi.fn() }))
vi.mock('@/api/sessions', () => ({ getSessionSuggestedPrompts: vi.fn(), generateSessionSuggestedPrompts: vi.fn(), listMessageVersions: vi.fn(), listSessions: vi.fn() }))
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }))

const anchor = { anchorMessageId: 'a', anchorSessionId: 'source-session', anchorVersion: 1, completedMessageFingerprint: 'a:1:completed', providerId: 'p', modelId: 'm' }
const batch = (version = 'v1', content = '下一步如何练习？') => ({ id: `batch-${version}`, session_id: 's', anchor_message_id: 'a', anchor_message_version_id: version,
  prompts: [{ id: 'q1', content }], provider_trace: {}, memory_used: false, generated_at: '2026-10-18T00:00:00Z', cached: true })

describe('native follow-up prompts use the original Web component and generation contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    authStore.setSession({ accessToken: 'fixture-account', workspaceId: 'w' })
    vi.mocked(listSettings).mockResolvedValue([])
    vi.mocked(listSessions).mockResolvedValue([{ id: 's', status: 'active' }] as never)
    vi.mocked(listProviders).mockResolvedValue([{ id: 'p', enabled: true, remote_capability: true, provider_type: 'openai_compatible_chat', capabilities: { default_model: 'm' } }] as never)
    vi.mocked(discoverProviderModels).mockResolvedValue({ models: [{ id: 'm', roles: ['llm'], remote: true, enabled: true }] } as never)
    vi.mocked(listMessageVersions).mockResolvedValue([{ id: 'v1', version: 1, status: 'completed' }] as never)
    vi.mocked(getSessionSuggestedPrompts).mockResolvedValue(undefined)
    vi.mocked(generateSessionSuggestedPrompts).mockResolvedValue(batch())
  })
  function mount(props: Partial<Parameters<typeof NativeSuggestedPrompts>[0]> = {}, copies = 1) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: 0 } } })
    const onSelect = vi.fn(), onConfigureProvider = vi.fn()
    const value = { workspaceId: 'w', sessionId: 's', anchor, native: true, onSelect, onConfigureProvider, ...props }
    const view = render(<QueryClientProvider client={client}>{Array.from({ length: copies }, (_, index) => <NativeSuggestedPrompts key={index} {...value} />)}</QueryClientProvider>)
    return { ...view, client, onSelect, onConfigureProvider, value }
  }

  it('reads and displays a matching persisted anchor without issuing another generation', async () => {
    vi.mocked(getSessionSuggestedPrompts).mockResolvedValue(batch())
    const view = mount()
    fireEvent.click(await screen.findByRole('button', { name: '下一步如何练习？' }))
    expect(view.onSelect).toHaveBeenCalledExactlyOnceWith('下一步如何练习？')
    expect(generateSessionSuggestedPrompts).not.toHaveBeenCalled()
    expect(listMessageVersions).toHaveBeenCalledWith('source-session', 'a')
    expect(screen.getByLabelText('对话问题提示')).toHaveClass('chat-follow-up-prompts')
  })

  it('generates only after GET, uses the exact Web count/model fields, and deduplicates concurrent consumers', async () => {
    mount({}, 2)
    expect(await screen.findAllByRole('button', { name: '下一步如何练习？' })).toHaveLength(2)
    expect(generateSessionSuggestedPrompts).toHaveBeenCalledTimes(1)
    expect(generateSessionSuggestedPrompts).toHaveBeenCalledWith('s', { anchor_message_id: 'a', anchor_message_version_id: 'v1', provider_id: 'p', model_id: 'm', count: 2 }, { signal: expect.any(AbortSignal) })
    expect(vi.mocked(getSessionSuggestedPrompts).mock.invocationCallOrder[0]!).toBeLessThan(vi.mocked(generateSessionSuggestedPrompts).mock.invocationCallOrder[0]!)
  })

  it('honors the suggested-prompt feature model instead of silently using a different conversation model', async () => {
    vi.mocked(listSettings).mockResolvedValue([{ key: 'chat.suggested_prompts_model', value: { provider_id: 'feature-p', model_id: 'feature-m' } }] as never)
    mount()
    await screen.findByRole('button', { name: '下一步如何练习？' })
    expect(generateSessionSuggestedPrompts).toHaveBeenCalledWith('s', expect.objectContaining({ provider_id: 'feature-p', model_id: 'feature-m', count: 2 }), expect.anything())
  })

  it('does not read or generate prompts when the workspace setting is disabled', async () => {
    vi.mocked(listSettings).mockResolvedValue([{ key: 'chat.suggested_prompts', value: { enabled: false } }] as never)
    const view = mount()
    await waitFor(() => expect(view.client.getQueryState(['workspace', 'w', 'settings'])?.status).toBe('success'))
    expect(getSessionSuggestedPrompts).not.toHaveBeenCalled()
    expect(generateSessionSuggestedPrompts).not.toHaveBeenCalled()
    expect(listMessageVersions).not.toHaveBeenCalled()
    expect(screen.queryByText('接下来可以问')).toBeNull()
  })

  it('rejects a persisted old version and refetches the inherited anchor when a retry version changes', async () => {
    vi.mocked(getSessionSuggestedPrompts).mockResolvedValue(batch('v1', '旧版本问题'))
    const view = mount()
    await screen.findByRole('button', { name: '旧版本问题' })
    vi.mocked(listMessageVersions).mockResolvedValue([{ id: 'v1', version: 1, status: 'completed' }, { id: 'v2', version: 2, status: 'completed' }] as never)
    vi.mocked(generateSessionSuggestedPrompts).mockResolvedValue(batch('v2', '当前版本问题'))
    view.rerender(<QueryClientProvider client={view.client}><NativeSuggestedPrompts {...view.value} anchor={{ ...anchor, anchorVersion: 2, completedMessageFingerprint: 'a:2:completed' }} /></QueryClientProvider>)
    await screen.findByRole('button', { name: '当前版本问题' })
    expect(screen.queryByRole('button', { name: '旧版本问题' })).toBeNull()
    expect(generateSessionSuggestedPrompts).toHaveBeenCalledWith('s', expect.objectContaining({ anchor_message_version_id: 'v2' }), expect.anything())
    expect(listMessageVersions).toHaveBeenCalledTimes(2)
  })

  it('shows original provider configuration affordance when no real model is available', async () => {
    vi.mocked(listProviders).mockResolvedValue([])
    const view = mount()
    fireEvent.click(await screen.findByRole('button', { name: '前往设置' }))
    expect(view.onConfigureProvider).toHaveBeenCalledOnce()
    expect(generateSessionSuggestedPrompts).not.toHaveBeenCalled()
  })

  it('generation failures remain toast-only and the original retry action retries the failed step', async () => {
    vi.mocked(getSessionSuggestedPrompts).mockRejectedValueOnce(new Error('fixture unavailable'))
    mount()
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('问题提示生成失败', expect.objectContaining({ action: expect.objectContaining({ label: '重试' }) })))
    expect(screen.queryByText('接下来可以问')).toBeNull()
    vi.mocked(getSessionSuggestedPrompts).mockResolvedValue(batch())
    const options = vi.mocked(toast.error).mock.calls.at(-1)?.[1]
    await act(async () => { (options?.action as { onClick: () => void }).onClick() })
    await screen.findByRole('button', { name: '下一步如何练习？' })
    expect(generateSessionSuggestedPrompts).not.toHaveBeenCalled()
  })

  it('preview, new sessions, absent anchors and other-account payloads never call account APIs', async () => {
    const view = mount({ native: false })
    fireEvent.click(screen.getByRole('button', { name: '能用一个具体例子解释吗？' }))
    expect(view.onSelect).toHaveBeenCalledWith('能用一个具体例子解释吗？')
    expect(listSettings).not.toHaveBeenCalled()
    view.rerender(<QueryClientProvider client={view.client}><NativeSuggestedPrompts {...view.value} workspaceId="other" /></QueryClientProvider>)
    expect(listSettings).not.toHaveBeenCalled()
    expect(generateSessionSuggestedPrompts).not.toHaveBeenCalled()
  })
})
