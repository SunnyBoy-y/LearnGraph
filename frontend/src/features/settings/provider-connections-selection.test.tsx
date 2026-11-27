import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { authStore } from '@/api/auth-store'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AuthContext, type AuthContextValue } from '@/features/auth/auth-context-value'
import { toast } from '@/lib/notifications'
import { renderWithProviders } from '@/test/render'
import type { Provider, ProviderTypeCatalogItem } from '@/types/providers'
import { ProvidersPage } from './provider-pages'

vi.mock('@/lib/notifications', () => ({
  toast: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    message: vi.fn(),
  },
}))

const WORKSPACE_ID = 'ws-providers-test'

function buildProvider(overrides: Partial<Provider> = {}): Provider {
  return {
    id: 'p-1',
    workspace_id: WORKSPACE_ID,
    display_name: '自定义兼容服务',
    provider_type: 'openai_compatible_chat',
    base_url: 'https://relay.example/v1',
    api_key_masked: 'sk-****1111',
    enabled: true,
    remote_capability: true,
    capabilities: { default_model: 'gpt-5.4' },
    status: 'healthy',
    secret_status: 'active',
    secret_version: 1,
    secret_key_provider: 'environment',
    secret_key_version: 1,
    created_at: '2026-11-20T00:00:00Z',
    ...overrides,
  }
}

const CATALOG: ProviderTypeCatalogItem[] = [
  {
    provider_type: 'openai_compatible_chat',
    role: 'model',
    label: 'OpenAI-compatible Chat',
    description: '',
    requires_base_url: true,
    requires_secret: true,
    supports_model_discovery: true,
    supports_probe: true,
    create_allowed: true,
    default_base_url: null,
    probe_notice: null,
    brand_id: 'openai_compatible',
    brand_icon_url: null,
    documentation_url: null,
    key_management_url: null,
    supports_account_balance: false,
    is_free: false,
  },
  {
    provider_type: 'codex_chatgpt',
    role: 'model',
    label: 'Codex 官方直登（ChatGPT 订阅）',
    description: '',
    requires_base_url: false,
    requires_secret: false,
    supports_model_discovery: true,
    supports_probe: true,
    create_allowed: true,
    default_base_url: null,
    probe_notice: null,
    brand_id: 'openai',
    brand_icon_url: null,
    documentation_url: null,
    key_management_url: null,
    supports_account_balance: false,
    is_free: false,
  },
  {
    provider_type: 'openai_responses',
    role: 'model',
    label: 'OpenAI Responses',
    description: '',
    requires_base_url: true,
    requires_secret: true,
    supports_model_discovery: true,
    supports_probe: true,
    create_allowed: true,
    default_base_url: 'https://api.openai.com/v1',
    probe_notice: null,
    brand_id: 'openai',
    brand_icon_url: null,
    documentation_url: null,
    key_management_url: null,
    supports_account_balance: false,
    is_free: false,
  },
  {
    provider_type: 'tavily_search',
    role: 'search',
    label: 'Tavily Search',
    description: '',
    requires_base_url: false,
    requires_secret: true,
    supports_model_discovery: false,
    supports_probe: true,
    create_allowed: true,
    default_base_url: null,
    probe_notice: null,
    brand_id: null,
    brand_icon_url: null,
    documentation_url: null,
    key_management_url: null,
    supports_account_balance: false,
    is_free: false,
  },
]

function renderProvidersPage() {
  const auth = {
    workspaceId: WORKSPACE_ID,
    username: 'tester',
    workspaceName: '测试空间',
    effectivePermissions: ['workspace.manage'],
    authenticated: true,
  } as unknown as AuthContextValue
  return renderWithProviders(
    <AuthContext.Provider value={auth}>
      <TooltipProvider>
        <ProvidersPage />
      </TooltipProvider>
    </AuthContext.Provider>,
  )
}

describe('ProvidersPage unified single-layout selection', () => {
  let providerRows: Provider[]

  beforeEach(() => {
    vi.clearAllMocks()
    authStore.setSession({ accessToken: 'test', userId: 'u-1', workspaceId: WORKSPACE_ID })
    providerRows = [
      buildProvider({ id: 'p-1', display_name: '自定义兼容服务', provider_type: 'openai_compatible_chat', enabled: true }),
      buildProvider({ id: 'p-2', display_name: '官方登录', provider_type: 'codex_chatgpt', enabled: false, api_key_masked: 'sk-****2222' }),
      buildProvider({ id: 'p-3', display_name: '章鱼', provider_type: 'openai_responses', enabled: true, api_key_masked: 'sk-****3333' }),
      buildProvider({ id: 'p-4', display_name: 'Tavily', provider_type: 'tavily_search', enabled: true, api_key_masked: 'tvly-****4444' }),
    ]
    vi.spyOn(apiClient, 'get').mockImplementation(async (path: string) => {
      if (path === '/providers') return providerRows.map((row) => ({ ...row }))
      if (path === '/providers/catalog') return CATALOG
      if (path === '/providers/secret-store/status') return { available: true }
      if (path === '/providers/host-bridge/status') return null
      return []
    })
  })

  it('uses persistent checkboxes, removes mode toggle and details filler text, and updates the toolbar in-place', async () => {
    const user = userEvent.setup()
    renderProvidersPage()

    expect(await screen.findByText('自定义兼容服务')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '多选' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '退出多选' })).not.toBeInTheDocument()
    expect(screen.queryByText('连接详情与高级操作')).not.toBeInTheDocument()

    const toolbar = screen.getByRole('region', { name: '连接列表工具栏' })
    const selectAll = within(toolbar).getByRole('checkbox', { name: '全选当前列表' })
    expect(selectAll).toHaveAttribute('aria-checked', 'false')
    expect(within(toolbar).getByText('全选')).toBeInTheDocument()
    expect(within(toolbar).getByText('共 3 个连接')).toBeInTheDocument()

    // Every visible row always has a checkbox.
    const row1Box = screen.getByRole('checkbox', { name: '选择供应商 自定义兼容服务' })
    const row2Box = screen.getByRole('checkbox', { name: '选择供应商 官方登录' })
    const row3Box = screen.getByRole('checkbox', { name: '选择供应商 章鱼' })
    expect(row1Box).not.toBeChecked()
    expect(row2Box).not.toBeChecked()
    expect(row3Box).not.toBeChecked()

    // Clicking a row checkbox selects only that row and switches toolbar to partial state without expanding details.
    await user.click(row1Box)
    expect(row1Box).toBeChecked()
    expect(selectAll).toHaveAttribute('aria-checked', 'mixed')
    expect(selectAll).toHaveAttribute('data-state', 'indeterminate')
    expect(selectAll.querySelector('.lucide-minus')).toBeInTheDocument()
    expect(within(toolbar).getByText('已选 1 项')).toBeInTheDocument()
    expect(within(toolbar).getByRole('button', { name: '取消选择' })).toBeInTheDocument()
    expect(within(toolbar).getByRole('button', { name: '启用' })).toBeDisabled()
    expect(within(toolbar).getByRole('button', { name: '停用' })).toBeEnabled()
    expect(within(toolbar).getByRole('button', { name: '删除' })).toBeEnabled()
    expect(screen.queryByText('sk-****1111')).not.toBeInTheDocument()
    expect(row1Box.closest('[role="listitem"]')).toHaveAttribute('data-selected', 'true')

    // Clicking the indeterminate select-all checkbox selects all visible filtered rows.
    await user.click(selectAll)
    expect(selectAll).toHaveAttribute('aria-checked', 'true')
    expect(within(toolbar).getByText('已选 3 项')).toBeInTheDocument()
    expect(row2Box).toBeChecked()
    expect(row3Box).toBeChecked()
    expect(within(toolbar).getByRole('button', { name: '启用' })).toBeEnabled()
    expect(within(toolbar).getByRole('button', { name: '停用' })).toBeEnabled()

    // Clicking 取消选择 resets back to unselected toolbar state.
    await user.click(within(toolbar).getByRole('button', { name: '取消选择' }))
    expect(selectAll).toHaveAttribute('aria-checked', 'false')
    expect(within(toolbar).getByText('共 3 个连接')).toBeInTheDocument()
    expect(row1Box).not.toBeChecked()
  })

  it('scopes select-all to filtered search results and clears selection when switching category tabs', async () => {
    const user = userEvent.setup()
    renderProvidersPage()

    await screen.findByText('自定义兼容服务')
    const toolbar = screen.getByRole('region', { name: '连接列表工具栏' })

    await user.type(screen.getByRole('textbox', { name: '搜索供应商' }), '章鱼')
    expect(within(toolbar).getByText('共 1 个连接')).toBeInTheDocument()

    await user.click(within(toolbar).getByRole('checkbox', { name: '全选当前列表' }))
    expect(within(toolbar).getByText('已选 1 项')).toBeInTheDocument()

    // Clearing search reveals all 3 items with 1 selected -> indeterminate.
    await user.click(screen.getByRole('button', { name: '清空搜索' }))
    expect(within(toolbar).getByRole('checkbox', { name: '全选当前列表' })).toHaveAttribute('aria-checked', 'mixed')
    expect(within(toolbar).getByText('已选 1 项')).toBeInTheDocument()

    // Switching category tab clears selection so hidden selections from another tab cannot be accidentally mutated.
    await user.click(screen.getByRole('tab', { name: '搜索' }))
    expect(within(toolbar).getByText('共 1 个连接')).toBeInTheDocument()
    expect(screen.getByText('Tavily')).toBeInTheDocument()
  })

  it('opens connection details from clicking the connection body or the right chevron without selecting the row', async () => {
    const user = userEvent.setup()
    renderProvidersPage()

    await screen.findByText('自定义兼容服务')
    const row1Box = screen.getByRole('checkbox', { name: '选择供应商 自定义兼容服务' })

    // Click connection body -> expands details, does not check box.
    await user.click(screen.getByRole('button', { name: /自定义兼容服务 OpenAI-compatible Chat/ }))
    expect(await screen.findByText('sk-****1111')).toBeInTheDocument()
    expect(row1Box).not.toBeChecked()

    // Click right chevron -> collapses details.
    await user.click(screen.getByRole('button', { name: '收起 自定义兼容服务 连接详情' }))
    expect(screen.queryByText('sk-****1111')).not.toBeInTheDocument()
    expect(row1Box).not.toBeChecked()

    // Click right chevron again -> expands details.
    await user.click(screen.getByRole('button', { name: '展开 自定义兼容服务 连接详情' }))
    expect(await screen.findByText('sk-****1111')).toBeInTheDocument()
  })

  it('executes batch enable, batch disable, and confirmed batch delete with toast feedback', async () => {
    const user = userEvent.setup()
    const patchSpy = vi.spyOn(apiClient, 'patch').mockImplementation(async (path: string, body) => {
      const id = path.split('/').pop()!
      const target = providerRows.find((row) => row.id === id)!
      Object.assign(target, body)
      return { ...target }
    })
    const deleteSpy = vi.spyOn(apiClient, 'delete').mockImplementation(async (path: string) => {
      const id = path.split('/').pop()!
      providerRows = providerRows.filter((row) => row.id !== id)
      return {}
    })

    renderProvidersPage()
    await screen.findByText('自定义兼容服务')
    const toolbar = screen.getByRole('region', { name: '连接列表工具栏' })

    await user.click(within(toolbar).getByRole('checkbox', { name: '全选当前列表' }))
    await user.click(within(toolbar).getByRole('button', { name: '启用' }))
    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/providers/p-2', { enabled: true }, undefined))
    expect(toast.success).toHaveBeenCalledWith('已启用 1 个供应商')

    await user.click(within(toolbar).getByRole('button', { name: '停用' }))
    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/providers/p-1', { enabled: false }, undefined))
    expect(toast.success).toHaveBeenCalledWith('已停用 3 个供应商')

    // Batch delete requires confirmation dialog before sending DELETE requests.
    await user.click(within(toolbar).getByRole('button', { name: '删除' }))
    const dialog = await screen.findByRole('alertdialog', { name: '删除选中的 3 个供应商实例？' })
    expect(deleteSpy).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: '删除 3 项' }))

    await waitFor(() => expect(deleteSpy).toHaveBeenCalledTimes(3))
    expect(toast.success).toHaveBeenCalledWith('已删除 3 个供应商')
    await waitFor(() => expect(within(toolbar).getByText('共 0 个连接')).toBeInTheDocument())
  })

  it('reports partial failure toast and isolates single-row enable toggle from selection or details', async () => {
    const user = userEvent.setup()
    const patchSpy = vi.spyOn(apiClient, 'patch').mockImplementation(async (path: string, body) => {
      if (path === '/providers/p-3') throw new Error('拒绝停用')
      const id = path.split('/').pop()!
      const target = providerRows.find((row) => row.id === id)!
      Object.assign(target, body)
      return { ...target }
    })

    renderProvidersPage()
    await screen.findByText('自定义兼容服务')

    const row2 = screen.getByRole('checkbox', { name: '选择供应商 官方登录' }).closest('[role="listitem"]')!
    await user.click(within(row2 as HTMLElement).getByRole('button', { name: '启用' }))
    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith('/providers/p-2', expect.objectContaining({ enabled: true }), undefined))
    expect(screen.getByRole('checkbox', { name: '选择供应商 官方登录' })).not.toBeChecked()
    expect(screen.queryByText('sk-****2222')).not.toBeInTheDocument()

    const toolbar = screen.getByRole('region', { name: '连接列表工具栏' })
    await user.click(within(toolbar).getByRole('checkbox', { name: '全选当前列表' }))
    await user.click(within(toolbar).getByRole('button', { name: '停用' }))
    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith('已停用 2 个供应商，1 个失败'))
  })
})
