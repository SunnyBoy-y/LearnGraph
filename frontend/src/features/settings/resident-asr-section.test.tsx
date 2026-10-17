import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { authStore } from '@/api/auth-store'
import { createProvider, listSettings, rotateProviderSecret, updateProvider, updateSetting } from '@/api'
import { renderWithProviders } from '@/test/render'
import { workspaceQueryKey } from '@/lib/query-keys'
import type { Provider, ProviderTypeCatalogItem, SpeechModelPreset } from '@/types/providers'
import { ResidentAsrSection } from './resident-asr-section'

vi.mock('@/api', () => ({ createProvider: vi.fn(), listSettings: vi.fn(), rotateProviderSecret: vi.fn(), updateProvider: vi.fn(), updateSetting: vi.fn() }))
const model: SpeechModelPreset = {
  id: 'qwen3-asr-flash-realtime', label: '实时 ASR', purpose: 'realtime', provider_type: 'asr',
  default_base_url: 'https://asr.example/v1', default_capabilities: { realtime_ws_url: 'wss://asr.example/ws' }, description: '',
}
const row = { id: 'asr-a', enabled: false, provider_type: 'asr', base_url: model.default_base_url,
  capabilities: { default_realtime_transcription_model_id: model.id, realtime_ws_url: 'wss://asr.example/ws' } } as Provider
const catalog = [{ role: 'transcription', create_allowed: true, speech_models: [model] }] as ProviderTypeCatalogItem[]
const options = { headers: { 'X-Workspace-ID': 'ws-a' } }
const otherBinding = { vision: { provider_id: 'vision-a', model_id: 'vision-model' } }
const ui = () => <ResidentAsrSection catalog={catalog} providers={[row]} providersPending={false} secretStoreAvailable/>

beforeEach(() => {
  vi.clearAllMocks()
  authStore.setSession({ accessToken: 'test', userId: 'user-a', workspaceId: 'ws-a' })
  vi.mocked(listSettings).mockResolvedValue([{ key: 'models.functional_defaults', value: otherBinding, updated_at: '' }])
  vi.mocked(updateProvider).mockResolvedValue(row)
  vi.mocked(updateSetting).mockImplementation(async (key, value) => ({ key, value, updated_at: '' }))
})

describe('resident ASR persistence', () => {
  it('keeps multi-step saves in the originating workspace and preserves other bindings', async () => {
    let finishFirst!: (value: Provider) => void
    vi.mocked(updateProvider).mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }))
    const result = renderWithProviders(ui())
    const invalidate = vi.spyOn(result.queryClient, 'invalidateQueries')
    await waitFor(() => expect(screen.getByRole('switch', { name: '启用 实时 ASR' })).toBeEnabled())
    fireEvent.click(screen.getByRole('switch', { name: '启用 实时 ASR' }))
    await waitFor(() => expect(finishFirst).toBeDefined())
    authStore.setWorkspaceId('ws-b')
    result.rerender(ui())
    finishFirst(row)
    await waitFor(() => expect(updateSetting).toHaveBeenCalledWith('models.functional_defaults', {
      ...otherBinding, realtime_transcription: { provider_id: 'asr-a', model_id: model.id },
    }, options))
    expect(vi.mocked(updateProvider).mock.calls.every(call => JSON.stringify(call[2]) === JSON.stringify(options))).toBe(true)
    expect(listSettings).toHaveBeenCalledWith(options)
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: workspaceQueryKey('ws-a', 'providers') }))
    expect(result.queryClient.getQueryData(workspaceQueryKey('ws-b', 'settings'))).toEqual([{ key: 'models.functional_defaults', value: otherBinding, updated_at: '' }])
    expect(createProvider).not.toHaveBeenCalled()
    expect(rotateProviderSecret).not.toHaveBeenCalled()
  })

  it('blocks saves when settings cannot be loaded instead of overwriting bindings', async () => {
    vi.mocked(listSettings).mockRejectedValue(new Error('读取失败'))
    renderWithProviders(ui())
    await screen.findByRole('alert')
    expect(screen.getByRole('switch', { name: '启用 实时 ASR' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '保存', exact: true })).toBeDisabled()
    expect(updateSetting).not.toHaveBeenCalled()
  })
})
