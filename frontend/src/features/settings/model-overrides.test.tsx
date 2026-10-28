import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { authStore } from '@/api/auth-store'
import { TooltipProvider } from '@/components/ui/tooltip'
import { SettingsDirtyContext } from '@/lib/settings-dirty'
import { workspaceQueryKey } from '@/lib/query-keys'
import { renderWithProviders } from '@/test/render'
import type { Provider, ProviderModelCapabilities } from '@/types/providers'
import { ModelOverrideDialog } from './provider-pages'

const provider = { id: 'provider-a', provider_type: 'openai_compatible_chat', capabilities: {} } as Provider
beforeEach(() => {
  authStore.setSession({ accessToken: 'test', userId: 'user-a', workspaceId: 'ws-a' })
  vi.spyOn(apiClient, 'get').mockResolvedValue({ provider_id: provider.id, model_id: 'model-a', capabilities: { max_output_tokens: 1000 } })
})

describe('model parameter drafts', () => {
  it.each([
    { edits: ['', '1', '10', '100', '1000', '10000', '100000'], window: 100000, limit: 100000 },
    { edits: ['', '3', '30', '300', '3000', '30000', '300000'], window: 300000, limit: 204000 },
    { edits: ['25600', '2560', '256', '', '100000'], window: 100000, limit: 100000 },
    { edits: ['8000', '300000'], window: 300000, limit: 204000 },
    { edits: ['', '8000'], window: 8000, limit: 8000 },
  ])('saves the final context window after editing through $edits', async ({ edits, window, limit }) => {
    const put = vi.spyOn(apiClient, 'put').mockImplementation(async (_path, payload) => ({
      provider_id: provider.id, model_id: 'model-a', capabilities: payload,
    }))
    const saved = vi.fn()
    renderWithProviders(<ModelOverrideDialog provider={provider} modelId="model-a" globalOverrideOn={false} onSaved={saved} onClose={() => {}} />)
    const input = await screen.findByRole('spinbutton', { name: '最大输入上下文' })
    await waitFor(() => expect(input).toHaveValue(256000))
    for (const value of edits) fireEvent.change(input, { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: '保存该模型配置' }))
    await waitFor(() => expect(saved).toHaveBeenCalledOnce())
    const payload = put.mock.calls[0][1] as ProviderModelCapabilities
    expect(payload).toMatchObject({ context_window_tokens: window, context_limit_tokens: limit, max_output_tokens: 1000 })
    expect(payload.context_limit_tokens).toBeGreaterThanOrEqual(8000)
  })

  it('bounds an invalid loaded legacy context limit by a smaller window', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ provider_id: provider.id, model_id: 'model-a', capabilities: { context_window_tokens: 32000, context_limit_tokens: 0, max_output_tokens: 1000 } })
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ provider_id: provider.id, model_id: 'model-a', capabilities: {} })
    renderWithProviders(<ModelOverrideDialog provider={provider} modelId="model-a" globalOverrideOn={false} onSaved={() => {}} onClose={() => {}} />)
    const input = await screen.findByRole('spinbutton', { name: '最大输入上下文' })
    await waitFor(() => expect(input).toHaveValue(32000))
    fireEvent.click(screen.getByRole('button', { name: '保存该模型配置' }))
    await waitFor(() => expect(put).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ context_window_tokens: 32000, context_limit_tokens: 32000 })))
  })

  it('does not submit an unfinished context value below the minimum', async () => {
    const put = vi.spyOn(apiClient, 'put')
    renderWithProviders(<ModelOverrideDialog provider={provider} modelId="model-a" globalOverrideOn={false} onSaved={() => {}} onClose={() => {}} />)
    const input = await screen.findByRole('spinbutton', { name: '最大输入上下文' })
    await waitFor(() => expect(input).toHaveValue(256000))
    fireEvent.change(input, { target: { value: '1000' } })
    fireEvent.submit(input.closest('form')!)
    expect(put).not.toHaveBeenCalled()
  })

  it('guards dismissal and retains edits when the server snapshot refreshes', async () => {
    const register = vi.fn()
    const close = vi.fn()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const result = renderWithProviders(<SettingsDirtyContext.Provider value={register}><TooltipProvider>
      <ModelOverrideDialog provider={provider} modelId="model-a" globalOverrideOn={false} onSaved={() => {}} onClose={close}/>
    </TooltipProvider></SettingsDirtyContext.Provider>)
    const input = await screen.findByRole('spinbutton', { name: '最大输出上下文' })
    await waitFor(() => expect(input).toHaveValue(1000))
    fireEvent.change(input, { target: { value: '2000' } })
    await waitFor(() => expect(register).toHaveBeenLastCalledWith('model-parameters-provider-a-model-a', true))
    result.queryClient.setQueryData(workspaceQueryKey('ws-a', 'provider-model-capabilities', provider.id, 'model-a'), {
      provider_id: provider.id, model_id: 'model-a', capabilities: { max_output_tokens: 3000 },
    })
    expect(input).toHaveValue(2000)
    fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }))
    expect(confirm).toHaveBeenCalledOnce()
    expect(close).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }))
    expect(close).toHaveBeenCalledOnce()
  })
})
