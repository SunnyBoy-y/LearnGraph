import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { authStore } from '@/api/auth-store'
import { renderWithProviders } from '@/test/render'
import { workspaceQueryKey } from '@/lib/query-keys'
import { settingsNav, legacySettingsRoutes } from '@/components/shared/settings-nav-data'
import { settingsCatalog } from './settings-catalog'
import { MemorySettingsPage } from './memory-settings-page'
import { ConversationSettingsPage } from './conversation-settings-page'
import { PersonalizationPage } from './personalization-page'
import { TaskModelSettings } from './task-model-settings'
import { VoiceInputSettings } from './voice-input-settings'
import { discoverProviderModels, getMemoryEnhancement, getMemoryPolicy, listProviders, listProviderCatalog, listSessions, listSettings, updateMemoryEnhancement, updateMemoryPolicy, updateSetting } from '@/api'
import type { MemoryEnhancement, MemoryPolicy } from '@/types/memory'
import type { WorkspaceSetting } from '@/types/settings'

vi.mock('@/api', () => ({
  getMemoryEnhancement: vi.fn(), getMemoryPolicy: vi.fn(), listProviders: vi.fn(), listProviderCatalog: vi.fn(), discoverProviderModels: vi.fn(async () => ({ models: [] })),
  listSessions: vi.fn(), listSettings: vi.fn(), updateMemoryEnhancement: vi.fn(), updateMemoryPolicy: vi.fn(),
  updateSetting: vi.fn(), pruneMemoryEmbeddings: vi.fn(), reindexMemoryEmbeddings: vi.fn(),
  getMemoryArchitectureStatus: vi.fn(), replayValidateMemory: vi.fn(), exportMemoryEventManifest: vi.fn(),
}))
vi.mock('@/api/settings', async () => {
  const api = await import('@/api')
  return { listSettings: api.listSettings, updateSetting: api.updateSetting }
})
const authState = vi.hoisted(() => ({ workspaceId: 'ws-a', workspaceName: '学习区 A' }))
vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => authState }))

let enhancement: MemoryEnhancement
let policy: MemoryPolicy
beforeEach(() => {
  authState.workspaceId = 'ws-a'
  authState.workspaceName = '学习区 A'
  localStorage.removeItem('learngraph.showStreamStats')
  localStorage.removeItem('learngraph.showCacheHit')
  authStore.setSession({ accessToken: 'test', userId: 'user-a', workspaceId: 'ws-a' })
  enhancement = {
    workspace_id: 'ws-a', extraction: { enabled: true, auto_commit: true, provider_id: '', model_id: '', follow_conversation: false },
    summarization: { enabled: false, provider_id: '', model_id: '', follow_conversation: false },
    embedding: { enabled: true, provider_id: '', model_id: '', semantic_weight: 0.5 }, active_memories: 7, indexed_memories: 0,
    current_model_key: null, stale_model_keys: [], cache_invalidated: null,
  } as MemoryEnhancement
  policy = { workspace_id: 'ws-a', workspace_enabled: true, workspace_recall_enabled: true, workspace_learning_enabled: false,
    session_id: null, session_enabled: null, effective_enabled: false } as MemoryPolicy
  vi.mocked(getMemoryEnhancement).mockImplementation(async () => structuredClone(enhancement))
  vi.mocked(getMemoryPolicy).mockImplementation(async () => structuredClone(policy))
  vi.mocked(listProviders).mockResolvedValue([])
  vi.mocked(listProviderCatalog).mockResolvedValue([])
  vi.mocked(listSessions).mockResolvedValue([])
  vi.mocked(listSettings).mockResolvedValue([])
  vi.mocked(updateMemoryPolicy).mockImplementation(async patch => {
    policy = { ...policy, ...patch }
    return structuredClone(policy)
  })
  vi.mocked(updateMemoryEnhancement).mockImplementation(async patch => {
    enhancement = { ...enhancement, ...patch,
      extraction: { ...enhancement.extraction, ...patch.extraction },
      embedding: { ...enhancement.embedding, ...patch.embedding },
      summarization: { ...enhancement.summarization, ...patch.summarization } }
    return structuredClone(enhancement)
  })
})

describe('settings information architecture', () => {
  it('does not write a late memory save into the newly selected workspace', async () => {
    let finishSave!: (value: MemoryEnhancement) => void
    const original = structuredClone(enhancement)
    vi.mocked(updateMemoryEnhancement).mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve }))
    const result = renderWithProviders(<MemorySettingsPage/>)
    fireEvent.click(await screen.findByRole('switch', { name: '语义检索' }))
    await waitFor(() => expect(finishSave).toBeDefined())
    authState.workspaceId = 'ws-b'
    authStore.setWorkspaceId('ws-b')
    enhancement = { ...enhancement, workspace_id: 'ws-b', active_memories: 12 }
    policy = { ...policy, workspace_id: 'ws-b' }
    result.rerender(<MemorySettingsPage/>)
    await screen.findByText('活跃记忆 12 条 · 已索引 0 条')
    finishSave({ ...original, embedding: { ...original.embedding, enabled: false } })
    await waitFor(() => expect(screen.getByRole('switch', { name: '语义检索' })).toBeEnabled())
    expect(result.queryClient.getQueryData<MemoryEnhancement>(workspaceQueryKey('ws-b', 'memory-enhancement'))?.workspace_id).toBe('ws-b')
    expect(screen.getByRole('switch', { name: '语义检索' })).toBeChecked()
  })

  it('retains a hotword on failure and serializes hotword writes', async () => {
    vi.mocked(listSettings).mockResolvedValue([{ key: 'chat.asr_hotwords', value: [{ text: '已有词' }], updated_at: '' }])
    let rejectSave!: (error: Error) => void
    vi.mocked(updateSetting).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectSave = reject }))
    renderWithProviders(<VoiceInputSettings/>)
    const input = await screen.findByRole('textbox', { name: '新增 ASR 热词' })
    fireEvent.change(input, { target: { value: '待保存词' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(rejectSave).toBeDefined())
    expect(screen.getByRole('button', { name: '删除热词 已有词' })).toBeDisabled()
    rejectSave(new Error('保存失败'))
    await waitFor(() => expect(screen.getByRole('button', { name: '删除热词 已有词' })).toBeEnabled())
    expect(input).toHaveValue('待保存词')
  })

  it('saves every migrated ASR option into the workspace settings cache', async () => {
    vi.mocked(updateSetting).mockImplementation(async (key, value) => ({ key, value, updated_at: '' }))
    const result = renderWithProviders(<VoiceInputSettings/>)
    fireEvent.click(await screen.findByRole('switch', { name: '语音转写智能整理' }))
    await waitFor(() => expect(screen.getByRole('switch', { name: '语音转写智能整理' })).toBeEnabled())
    fireEvent.click(screen.getByRole('combobox', { name: 'ASR 识别语言' }))
    fireEvent.click(await screen.findByRole('option', { name: '英文', exact: true }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'ASR 识别语言' })).toHaveTextContent('英文'))
    fireEvent.click(screen.getByRole('combobox', { name: 'VAD 分工模式' }))
    fireEvent.click(await screen.findByRole('option', { name: '本机优先', exact: true }))
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'VAD 分工模式' })).toHaveTextContent('本机优先'))
    const details = screen.getByText('高级语音处理').closest('details')!
    details.open = true
    fireEvent(details, new Event('toggle'))
    fireEvent.click(await screen.findByRole('switch', { name: '自适应底噪基线' }))
    await waitFor(() => expect(screen.getByRole('switch', { name: '自适应底噪基线' })).toBeEnabled())
    const input = screen.getByRole('textbox', { name: '新增 ASR 热词' })
    fireEvent.change(input, { target: { value: 'LearnGraph' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(input).toHaveValue(''))
    const saved = result.queryClient.getQueryData<WorkspaceSetting[]>(workspaceQueryKey('ws-a', 'settings'))!
    expect(saved.map(item => item.key).sort()).toEqual(['chat.dictation_cleanup', 'chat.asr_language', 'chat.asr_vad_mode', 'chat.asr_vad_adaptive', 'chat.asr_hotwords'].sort())
    expect(saved.find(item => item.key === 'chat.asr_hotwords')?.value).toEqual([{ text: 'LearnGraph' }])
    fireEvent.click(screen.getByRole('button', { name: '删除热词 LearnGraph' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: '删除热词 LearnGraph' })).not.toBeInTheDocument())
  })

  it('does not carry a voice draft into another workspace', async () => {
    const result = renderWithProviders(<VoiceInputSettings/>)
    fireEvent.change(await screen.findByRole('textbox', { name: '新增 ASR 热词' }), { target: { value: 'A 的热词' } })
    authState.workspaceId = 'ws-b'
    authStore.setWorkspaceId('ws-b')
    vi.mocked(listSettings).mockResolvedValue([{ key: 'chat.asr_language', value: 'en-US', updated_at: '' }])
    result.rerender(<VoiceInputSettings/>)
    expect(await screen.findByRole('textbox', { name: '新增 ASR 热词' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'ASR 识别语言' })).toHaveTextContent('英文')
  })

  it('preserves a manual embedding draft when another enhancement setting refreshes', async () => {
    const result = renderWithProviders(<TaskModelSettings/>)
    const details = (await screen.findByText('手动配置检索模型')).closest('details')!
    details.open = true
    fireEvent(details, new Event('toggle'))
    const input = await screen.findByRole('textbox', { name: '手动 Embedding 模型 ID' })
    fireEvent.change(input, { target: { value: 'unsaved-model' } })
    result.queryClient.setQueryData(workspaceQueryKey('ws-a', 'memory-enhancement'), { ...enhancement, embedding: { ...enhancement.embedding, model_id: 'server-refreshed-model' } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Embedding 模型', exact: true })).toHaveTextContent('server-refreshed-model'))
    expect(input).toHaveValue('unsaved-model')
  })

  it('shows removed embedding connections and discovery errors without discarding configuration', async () => {
    enhancement.embedding = { ...enhancement.embedding, provider_id: 'deleted-provider', model_id: 'saved-model' }
    const result = renderWithProviders(<TaskModelSettings/>)
    expect(await screen.findByRole('combobox', { name: 'Embedding Provider' })).toHaveTextContent('当前连接已删除或不可访问')
    expect(screen.getByRole('button', { name: 'Embedding 模型', exact: true })).toBeDisabled()
    vi.mocked(discoverProviderModels).mockRejectedValue(new Error('服务离线'))
    result.queryClient.setQueryData(workspaceQueryKey('ws-a', 'providers'), [{ id: 'deleted-provider', display_name: '恢复连接', enabled: true, provider_type: 'embedding_test', capabilities: {} }])
    await screen.findByRole('alert')
    expect(screen.getByRole('alert')).toHaveTextContent('服务离线')
    expect(screen.getByRole('button', { name: 'Embedding 模型', exact: true })).toHaveTextContent('saved-model')
  })

  it('does not mistake an unavailable session list for an empty workspace', async () => {
    vi.mocked(listSessions).mockRejectedValue(new Error('会话不可访问'))
    renderWithProviders(<MemorySettingsPage/>)
    expect(await screen.findByRole('alert')).toHaveTextContent('会话列表读取失败：会话不可访问')
    expect(screen.queryByText('当前没有 Session；创建会话后可设置独立策略。')).not.toBeInTheDocument()
  })

  it('disables both rebuild actions when semantic retrieval is turned off', async () => {
    enhancement.embedding = { ...enhancement.embedding, enabled: false, provider_id: 'embedding-a', model_id: 'model-a' }
    enhancement.cache_invalidated = { current_model_key: 'new', previous_model_key: 'old', previous_indexed_count: 3 }
    vi.mocked(listProviders).mockResolvedValue([{ id: 'embedding-a', enabled: true, capabilities: {} }] as Awaited<ReturnType<typeof listProviders>>)
    renderWithProviders(<MemorySettingsPage/>)
    expect(await screen.findByRole('button', { name: '重建索引并清理旧缓存' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重建索引', exact: true })).toBeDisabled()
  })

  it('persists all five task settings and preserves the paired memory model assignment', async () => {
    const provider = { id: 'chat-a', display_name: '服务 A', enabled: true, remote_capability: true, provider_type: 'openai_compatible_chat', capabilities: { default_model: 'chat-model', discovered_model_ids: ['chat-model'] } }
    vi.mocked(listProviders).mockResolvedValue([provider] as Awaited<ReturnType<typeof listProviders>>)
    vi.mocked(listProviderCatalog).mockResolvedValue([{ provider_type: provider.provider_type, role: 'model' }] as Awaited<ReturnType<typeof listProviderCatalog>>)
    vi.mocked(updateSetting).mockImplementation(async (key, value) => ({ key, value, updated_at: '' }))
    renderWithProviders(<TaskModelSettings/>)
    const fields = [
      ['生成标题模型', 'chat.auto_title_model'], ['生成下一步问题提示模型', 'chat.suggested_prompts_model'],
      ['语音转写整理模型', 'chat.dictation_cleanup_model'], ['练习出题与判分模型', 'practice.exercise_model'],
      ['教学包生成模型', 'learning.package_model'],
    ]
    for (const [label, key] of fields) {
      fireEvent.click(await screen.findByRole('button', { name: label, exact: true }))
      fireEvent.click(await screen.findByRole('option', { name: '服务 A · chat-model', exact: true }))
      await waitFor(() => expect(updateSetting).toHaveBeenCalledWith(key, { provider_id: 'chat-a', model_id: 'chat-model' }, { headers: { 'X-Workspace-ID': 'ws-a' } }))
      await waitFor(() => expect(screen.getByRole('button', { name: label, exact: true })).toBeEnabled())
    }
    fireEvent.click(screen.getByRole('button', { name: '记忆整理模型', exact: true }))
    fireEvent.click(await screen.findByRole('option', { name: '跟随对话模型', exact: true }))
    const target = { provider_id: '', model_id: '', follow_conversation: true }
    await waitFor(() => expect(updateMemoryEnhancement).toHaveBeenCalledWith({ extraction: target, summarization: target }, { headers: { 'X-Workspace-ID': 'ws-a' } }))
  })

  it('resets pending embedding confirmation on a workspace switch without submitting it', async () => {
    enhancement.embedding = { ...enhancement.embedding, provider_id: 'embedding-a', model_id: 'old-model' }
    enhancement.indexed_memories = 7
    vi.mocked(listProviders).mockResolvedValue([{ id: 'embedding-a', display_name: 'Embedding A', enabled: true, provider_type: 'embedding_test', capabilities: {} }] as Awaited<ReturnType<typeof listProviders>>)
    const result = renderWithProviders(<TaskModelSettings/>)
    const details = (await screen.findByText('手动配置检索模型')).closest('details')!
    details.open = true
    fireEvent(details, new Event('toggle'))
    fireEvent.change(await screen.findByRole('textbox', { name: '手动 Embedding 模型 ID' }), { target: { value: 'new-model' } })
    fireEvent.click(screen.getByRole('button', { name: '保存模型', exact: true }))
    await screen.findByRole('button', { name: '确认切换' })
    authState.workspaceId = 'ws-b'
    authStore.setWorkspaceId('ws-b')
    enhancement = { ...enhancement, workspace_id: 'ws-b' }
    result.rerender(<TaskModelSettings/>)
    await screen.findByText('手动配置检索模型')
    expect(screen.queryByRole('button', { name: '确认切换' })).not.toBeInTheDocument()
    expect(updateMemoryEnhancement).not.toHaveBeenCalled()
  })

  it('has nine categories plus About and unique configuration ownership metadata', () => {
    expect(settingsNav).toHaveLength(10)
    expect(settingsNav.at(-1)?.label).toBe('关于')
    expect(new Set(settingsNav.map(item => item.section).filter(Boolean))).toEqual(new Set(['使用体验', '能力配置', '管理']))
    expect(new Set(settingsCatalog.map(item => item.settingId)).size).toBe(settingsCatalog.length)
    expect(settingsCatalog.every(item => item.field && item.scope && item.previousPage && item.save && item.keywords.length)).toBe(true)
    expect(legacySettingsRoutes['voice-relay']).toBe('voice?tab=relay')
    expect(legacySettingsRoutes['storage/migrations']).toBe('data?tab=migration')
    expect(legacySettingsRoutes.usage).toBe('models?tab=usage')
  })

  it('disables memory children while preserving saved values across the master toggle', async () => {
    renderWithProviders(<MemorySettingsPage/>)
    const master = await screen.findByRole('switch', { name: '工作区共同记忆' })
    expect(screen.getByRole('switch', { name: '工作区读取记忆' })).toBeChecked()
    expect(screen.getByRole('switch', { name: '工作区学习记忆' })).not.toBeChecked()
    fireEvent.click(master)
    await waitFor(() => expect(screen.getByRole('switch', { name: '工作区读取记忆' })).toBeDisabled())
    expect(updateMemoryPolicy).toHaveBeenCalledWith({ workspace_enabled: false }, expect.anything())
    expect(policy.workspace_recall_enabled).toBe(true)
    expect(policy.workspace_learning_enabled).toBe(false)
    expect(screen.getByRole('switch', { name: '记忆整理' })).toBeDisabled()
    expect(screen.getByRole('switch', { name: '高置信新记忆自动写入 Active Memory' })).toBeDisabled()
    fireEvent.click(master)
    await waitFor(() => expect(screen.getByRole('switch', { name: '工作区读取记忆' })).toBeEnabled())
    expect(screen.getByRole('switch', { name: '工作区读取记忆' })).toBeChecked()
    expect(screen.getByRole('switch', { name: '工作区学习记忆' })).not.toBeChecked()
  })

  it('shows real counts and the embedding fallback without a second model editor', async () => {
    policy.workspace_learning_enabled = true
    renderWithProviders(<MemorySettingsPage/>)
    await screen.findByRole('heading', { name: '记忆与检索' })
    expect(screen.getByText('活跃记忆 7 条 · 已索引 0 条')).toBeInTheDocument()
    expect(screen.getByText('尚未配置可用的 Embedding 模型，当前保持默认召回。')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '配置检索模型' })).toHaveAttribute('href', '/w/ws-a/settings/models?tab=tasks')
    expect(screen.queryByRole('combobox', { name: 'Embedding Provider' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('switch', { name: '高置信新记忆自动写入 Active Memory' }))
    await waitFor(() => expect(updateMemoryEnhancement).toHaveBeenCalledWith({ extraction: { auto_commit: false } }, { headers: { "X-Workspace-ID": "ws-a" } }))
  })

  it('keeps session policy writes separate from the workspace policy', async () => {
    vi.mocked(listSessions).mockResolvedValue([{ id: 'session-a', title: '独立会话', updated_at: '2026-10-15T00:00:00Z' }] as Awaited<ReturnType<typeof listSessions>>)
    vi.mocked(getMemoryPolicy).mockImplementation(async sessionId => ({ ...policy, session_id: sessionId ?? null, session_enabled: sessionId ? true : null, session_recall_enabled: true, session_learning_enabled: true }))
    renderWithProviders(<MemorySettingsPage/>)
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Session 读取记忆' })).toBeEnabled())
    expect(screen.getByRole('switch', { name: 'Session 学习记忆' })).toBeDisabled()
    fireEvent.click(screen.getByRole('switch', { name: 'Session 读取记忆' }))
    await waitFor(() => expect(updateMemoryPolicy).toHaveBeenCalledWith({ session_id: 'session-a', session_recall_enabled: false }, expect.anything()))
    expect(policy.workspace_recall_enabled).toBe(true)
  })

  it('loads the new workspace without reusing the previous memory configuration', async () => {
    const result = renderWithProviders(<MemorySettingsPage/>)
    await screen.findByText('活跃记忆 7 条 · 已索引 0 条')
    authState.workspaceId = 'ws-b'
    authState.workspaceName = '学习区 B'
    authStore.setSession({ accessToken: 'test', userId: 'user-a', workspaceId: 'ws-b' })
    enhancement = { ...enhancement, workspace_id: 'ws-b', active_memories: 12 }
    policy = { ...policy, workspace_id: 'ws-b', workspace_enabled: false }
    result.rerender(<MemorySettingsPage/>)
    await screen.findByText('活跃记忆 12 条 · 已索引 0 条')
    expect(screen.getByRole('switch', { name: '工作区共同记忆' })).not.toBeChecked()
    expect(result.queryClient.getQueryData<MemoryEnhancement>(workspaceQueryKey('ws-b', 'memory-enhancement'))?.workspace_id).toBe('ws-b')
  })

  it('only changes summarization when enabling conversation compression', async () => {
    const { queryClient } = renderWithProviders(<ConversationSettingsPage/>)
    fireEvent.click(await screen.findByRole('switch', { name: '会话摘要' }))
    await waitFor(() => expect(updateMemoryEnhancement).toHaveBeenCalledWith({ summarization: { enabled: true } }))
    expect(queryClient.getQueryData<MemoryEnhancement>(workspaceQueryKey('ws-a', 'memory-enhancement'))?.extraction.enabled).toBe(true)
    expect(screen.queryByRole('switch', { name: '显示缓存命中' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('高级诊断'))
    // jsdom does not dispatch native details toggle consistently; exercise its real toggle handler.
    const details = screen.getByText('高级诊断').closest('details')!
    details.open = true
    fireEvent(details, new Event('toggle'))
    fireEvent.click(await screen.findByRole('switch', { name: '显示流式指标' }))
    expect(localStorage.getItem('learngraph.showStreamStats')).toBe('1')
    expect(screen.getByRole('switch', { name: '显示缓存命中' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('switch', { name: '显示缓存命中' }))
    expect(localStorage.getItem('learngraph.showCacheHit')).toBe('1')
  })

  it('retains manual embedding IDs and confirms index invalidation before writing', async () => {
    enhancement.embedding = { ...enhancement.embedding, provider_id: 'embedding-a', model_id: 'old-model' }
    enhancement.indexed_memories = 7
    vi.mocked(listProviders).mockResolvedValue([{ id: 'embedding-a', display_name: 'Embedding A', enabled: true, remote_capability: true, provider_type: 'embedding_test', capabilities: {} }] as Awaited<ReturnType<typeof listProviders>>)
    vi.mocked(listProviderCatalog).mockResolvedValue([{ provider_type: 'embedding_test', role: 'embedding' }] as Awaited<ReturnType<typeof listProviderCatalog>>)
    renderWithProviders(<TaskModelSettings/>)
    const summary = await screen.findByText('手动配置检索模型')
    fireEvent.click(summary)
    const details = summary.closest('details')!
    details.open = true
    fireEvent(details, new Event('toggle'))
    fireEvent.change(await screen.findByRole('textbox', { name: '手动 Embedding 模型 ID' }), { target: { value: 'custom-embedding-v3' } })
    fireEvent.click(screen.getByRole('button', { name: '保存模型', exact: true }))
    expect(updateMemoryEnhancement).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: '确认切换' }))
    await waitFor(() => expect(updateMemoryEnhancement).toHaveBeenCalledWith({ embedding: { model_id: 'custom-embedding-v3' } }, { headers: { "X-Workspace-ID": "ws-a" } }))
  })

  it('keeps an unsaved response style when an immediate theme setting refreshes the same cache', async () => {
    const { queryClient } = renderWithProviders(<PersonalizationPage/>)
    const reset = await screen.findByRole('button', { name: '恢复默认' })
    expect(reset).toBeDisabled()
    fireEvent.click(screen.getByRole('combobox', { name: '基础风格' }))
    const options = await screen.findAllByRole('option')
    fireEvent.click(options.find(option => option.getAttribute('data-state') !== 'checked')!)
    await waitFor(() => expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled())
    queryClient.setQueryData<WorkspaceSetting[]>(workspaceQueryKey('ws-a', 'settings'), [{ key: 'ui.preferences', value: { theme: 'dark' }, updated_at: '' }])
    await waitFor(() => expect(screen.getByRole('button', { name: '保存', exact: true })).toBeEnabled())
    expect(updateSetting).not.toHaveBeenCalled()
  })
})
