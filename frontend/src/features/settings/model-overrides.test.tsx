import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { authStore } from '@/api/auth-store'
import { TooltipProvider } from '@/components/ui/tooltip'
import { SettingsDirtyContext } from '@/lib/settings-dirty'
import { workspaceQueryKey } from '@/lib/query-keys'
import { renderWithProviders } from '@/test/render'
import type { Provider } from '@/types/providers'
import { ModelOverrideDialog } from './provider-pages'

const provider = { id: 'provider-a', provider_type: 'openai_compatible_chat', capabilities: {} } as Provider
beforeEach(() => {
  authStore.setSession({ accessToken: 'test', userId: 'user-a', workspaceId: 'ws-a' })
  vi.spyOn(apiClient, 'get').mockResolvedValue({ provider_id: provider.id, model_id: 'model-a', capabilities: { max_output_tokens: 1000 } })
})

describe('model parameter drafts', () => {
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
