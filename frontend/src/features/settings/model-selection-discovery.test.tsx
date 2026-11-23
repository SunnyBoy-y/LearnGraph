import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { authStore } from '@/api/auth-store'
import { TooltipProvider } from '@/components/ui/tooltip'
import { renderWithProviders } from '@/test/render'
import type { Provider, ProviderModelsResponse } from '@/types/providers'
import { ModelCapabilitiesDialog } from './provider-pages'

const authState = vi.hoisted(() => ({
  workspaceId: 'ws-a',
  workspaceName: '学习区 A',
  userId: 'user-a',
  effectivePermissions: ['workspace.manage'],
}))
vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => authState }))

function buildProvider(overrides: Partial<Provider> = {}): Provider {
  return {
    id: 'provider-a',
    workspace_id: 'ws-a',
    display_name: '测试供应商',
    provider_type: 'openai_compatible_chat',
    base_url: 'https://relay.invalid/v1',
    api_key_masked: 'sk-****',
    enabled: true,
    remote_capability: true,
    capabilities: {
      discovered_model_ids: ['gpt-5.4', 'gpt-5.4-mini'],
      model_states: { 'gpt-5.4': true, 'gpt-5.4-mini': false },
      default_model: 'gpt-5.4',
    },
    status: 'healthy',
    secret_status: 'active',
    secret_version: 1,
    secret_key_provider: 'environment',
    secret_key_version: 1,
    created_at: '2026-11-20T00:00:00Z',
    ...overrides,
  }
}

describe('ModelCapabilitiesDialog selection and discovery', () => {
  beforeEach(() => {
    authStore.setSession({ accessToken: 'test', userId: 'user-a', workspaceId: 'ws-a' })
    vi.restoreAllMocks()
    vi.spyOn(apiClient, 'get').mockImplementation(async (path: string) => {
      if (path === '/providers/catalog') return []
      if (path.endsWith('/balance-status')) {
        return { provider_id: 'provider-a', supported: false, state: 'unsupported', reason: 'not_applicable' }
      }
      throw new Error(`Unexpected GET ${path}`)
    })
  })

  it('leaves manually added models unchecked and supports Select All', async () => {
    const provider = buildProvider({
      capabilities: {
        discovered_model_ids: ['gpt-5.4', 'gpt-5.4-mini'],
        model_states: { 'gpt-5.4': false, 'gpt-5.4-mini': false },
      },
    })
    const initialModels: ProviderModelsResponse = {
      provider_id: provider.id,
      status: 'persisted_discovery',
      models: [
        { id: 'gpt-5.4', roles: ['llm'], streaming: true, remote: true, enabled: false },
        { id: 'gpt-5.4-mini', roles: ['llm'], streaming: true, remote: true, enabled: false },
      ],
    }

    vi.spyOn(apiClient, 'post').mockResolvedValue({
      provider_id: provider.id,
      models: [
        {
          provider_id: provider.id,
          model_id: 'custom-model-1',
          enabled: false,
          capabilities: { context_window_tokens: 128000 },
        },
      ],
    })
    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValue({
      provider_id: provider.id,
      states: {
        'gpt-5.4': true,
        'gpt-5.4-mini': true,
        'custom-model-1': true,
      },
      default_model: 'gpt-5.4',
    })

    const onStatesSaved = vi.fn()
    renderWithProviders(
      <TooltipProvider>
        <ModelCapabilitiesDialog
          modelId="gpt-5.4"
          models={initialModels}
          onClose={() => {}}
          onConfigureBalance={() => {}}
          onSaved={() => {}}
          onSetDefault={async () => {}}
          onStatesSaved={onStatesSaved}
          provider={provider}
        />
      </TooltipProvider>,
    )

    expect(screen.getByText('已启用 0 / 2 个模型')).toBeInTheDocument()

    // 手动添加模型后默认未启用
    fireEvent.change(screen.getByLabelText('批量添加模型'), {
      target: { value: 'custom-model-1' },
    })
    fireEvent.click(screen.getByRole('button', { name: '手动添加' }))
    await screen.findByText('custom-model-1')
    expect(screen.getByText('已启用 0 / 3 个模型')).toBeInTheDocument()

    // 点击全选全部勾选，然后保存
    fireEvent.click(screen.getByRole('button', { name: '全选' }))
    expect(screen.getByText('已启用 3 / 3 个模型')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '保存模型开关' }))
    await waitFor(() => expect(onStatesSaved).toHaveBeenCalledOnce())
    expect(patchSpy).toHaveBeenCalledWith(
      '/providers/provider-a/models',
      {
        states: {
          'gpt-5.4': true,
          'gpt-5.4-mini': true,
          'custom-model-1': true,
        },
      },
    )
  })

  it('sorts newly discovered models to the front, marks them 新增, and leaves them unchecked', async () => {
    const provider = buildProvider()
    const initialModels: ProviderModelsResponse = {
      provider_id: provider.id,
      status: 'persisted_discovery',
      models: [
        { id: 'gpt-5.4', roles: ['llm'], streaming: true, remote: true, enabled: true },
        { id: 'gpt-5.4-mini', roles: ['llm'], streaming: true, remote: true, enabled: false },
      ],
    }

    vi.spyOn(apiClient, 'get').mockImplementation(async (path: string) => {
      if (path === '/providers/catalog') return []
      if (path.endsWith('/balance-status')) {
        return { provider_id: 'provider-a', supported: false, state: 'unsupported', reason: 'not_applicable' }
      }
      if (path === '/providers/provider-a/models') {
        return {
          provider_id: 'provider-a',
          status: 'discovered',
          models: [
            { id: 'gpt-5.6-terra', roles: ['llm'], streaming: true, remote: true, enabled: false, is_new: true },
            { id: 'gpt-5.4', roles: ['llm'], streaming: true, remote: true, enabled: true, is_new: false },
            { id: 'gpt-5.4-mini', roles: ['llm'], streaming: true, remote: true, enabled: false, is_new: false },
          ],
        }
      }
      throw new Error(`Unexpected GET ${path}`)
    })

    renderWithProviders(
      <TooltipProvider>
        <ModelCapabilitiesDialog
          modelId="gpt-5.4"
          models={initialModels}
          onClose={() => {}}
          onConfigureBalance={() => {}}
          onSaved={() => {}}
          onSetDefault={async () => {}}
          onStatesSaved={() => {}}
          provider={provider}
        />
      </TooltipProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: '刷新模型列表' }))

    await screen.findByText('新增')
    expect(screen.getByText('发现 1 个新增模型（已置顶，默认未启用）')).toBeInTheDocument()
    expect(screen.getByText('已启用 1 / 3 个模型')).toBeInTheDocument()

    // 验证置顶顺序与默认未启用状态
    const rows = [...document.body.querySelectorAll('.divide-y > div')]
    expect(rows.map((row) => row.querySelector('.font-mono')?.textContent)).toEqual([
      'gpt-5.6-terra',
      'gpt-5.4',
      'gpt-5.4-mini',
    ])
    const switches = screen.getAllByRole('switch')
    expect(switches[0]).toHaveAttribute('aria-checked', 'false')
    expect(switches[1]).toHaveAttribute('aria-checked', 'true')
    expect(switches[2]).toHaveAttribute('aria-checked', 'false')
  })
})
