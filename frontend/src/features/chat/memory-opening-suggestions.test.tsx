import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { generateOpeningPrompts } from '@/api/opening-prompts'
import { toast } from '@/lib/notifications'
import { MemoryOpeningSuggestions, type MemoryOpeningSuggestionsProps } from './memory-opening-suggestions'

vi.mock('@/api/opening-prompts', () => ({ generateOpeningPrompts: vi.fn() }))
vi.mock('@/lib/notifications', () => ({ toast: { error: vi.fn() } }))

function group(prefix: string) {
  return { prompts: [1, 2, 3].map(index => ({ id: `${prefix}-${index}`, content: `${prefix}学习问题${index}？` })) }
}

function setup(overrides: Partial<MemoryOpeningSuggestionsProps> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const props: MemoryOpeningSuggestionsProps = {
    workspaceId: 'workspace-a', projectId: 'project-a', providerId: 'provider-a', modelId: 'model-a',
    enabled: true, disabled: false, onSelect: vi.fn(), ...overrides,
  }
  const view = render(<QueryClientProvider client={client}><MemoryOpeningSuggestions {...props} /></QueryClientProvider>)
  return { props, client, ...view, change: (patch: Partial<MemoryOpeningSuggestionsProps>) => {
    Object.assign(props, patch)
    view.rerender(<QueryClientProvider client={client}><MemoryOpeningSuggestions {...props} /></QueryClientProvider>)
  } }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(generateOpeningPrompts).mockResolvedValue(group('记忆生成'))
})

describe('AI memory-backed opening suggestions', () => {
  it('generates from the server without sending browser memory and selects the actual generated question', async () => {
    const { props } = setup()
    const prompt = await screen.findByRole('button', { name: '记忆生成学习问题1？' })
    expect(generateOpeningPrompts).toHaveBeenCalledExactlyOnceWith({ provider_id: 'provider-a', model_id: 'model-a', project_id: 'project-a', count: 3 }, { signal: expect.any(AbortSignal), headers: { 'X-Workspace-ID': 'workspace-a' } })
    expect(screen.getAllByRole('button').filter(button => button.classList.contains('new-chat-suggestions__prompt'))).toHaveLength(3)
    fireEvent.click(prompt)
    expect(props.onSelect).toHaveBeenCalledWith('记忆生成学习问题1？')
    expect(screen.queryByText('我现在最应该先学什么？')).not.toBeInTheDocument()
  })

  it('refreshes in the background while keeping the visible questions selectable', async () => {
    setup()
    await screen.findByRole('button', { name: '记忆生成学习问题1？' })
    let complete!: (value: ReturnType<typeof group>) => void
    vi.mocked(generateOpeningPrompts).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
    const refresh = screen.getByRole('button', { name: '换一组推荐问题' })
    fireEvent.click(refresh)
    await waitFor(() => expect(refresh).toBeDisabled())
    expect(screen.getByRole('button', { name: '记忆生成学习问题1？' })).toBeEnabled()
    fireEvent.click(refresh)
    expect(generateOpeningPrompts).toHaveBeenCalledTimes(2)
    await act(async () => complete(group('新生成')))
    expect(await screen.findByRole('button', { name: '新生成学习问题1？' })).toBeEnabled()
    expect(screen.queryByText('记忆生成学习问题1？')).not.toBeInTheDocument()
  })

  it('rotates three cached groups instantly without more requests during the cooldown', async () => {
    const pool = Array.from({ length: 9 }, (_, i) => ({ id: `pool-${i}`, content: `缓存问题${i + 1}？` }))
    vi.mocked(generateOpeningPrompts).mockResolvedValueOnce({ prompts: pool.slice(0, 3), pool, metadata: { refresh_after_seconds: 600, request_id: 'pool-batch-a' } })
    const { props } = setup()
    await screen.findByRole('button', { name: '缓存问题1？' })
    for (const first of [4, 7, 1, 4]) {
      fireEvent.click(screen.getByRole('button', { name: '换一组推荐问题' }))
      expect(screen.getByRole('button', { name: `缓存问题${first}？` })).toBeEnabled()
    }
    fireEvent.click(screen.getByRole('button', { name: '缓存问题4？' }))
    expect(props.onSelect).toHaveBeenCalledWith('缓存问题4？')
    expect(generateOpeningPrompts).toHaveBeenCalledTimes(1)
  })

  it('refills an exhausted pool only after the server cooldown has elapsed', async () => {
    const pool = Array.from({ length: 9 }, (_, i) => ({ id: `pool-${i}`, content: `缓存问题${i + 1}？` }))
    vi.mocked(generateOpeningPrompts).mockResolvedValueOnce({ prompts: pool.slice(0, 3), pool, metadata: { refresh_after_seconds: 600, request_id: 'pool-batch-a' } })
    setup()
    await screen.findByRole('button', { name: '缓存问题1？' })
    const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 601_000)
    try {
      const refresh = screen.getByRole('button', { name: '换一组推荐问题' })
      fireEvent.click(refresh)
      fireEvent.click(refresh)
      expect(generateOpeningPrompts).toHaveBeenCalledTimes(1)
      fireEvent.click(refresh)
      await screen.findByRole('button', { name: '记忆生成学习问题1？' })
      expect(generateOpeningPrompts).toHaveBeenCalledTimes(2)
      expect(generateOpeningPrompts).toHaveBeenLastCalledWith(expect.objectContaining({ exhausted_pool_id: 'pool-batch-a' }), expect.anything())
    } finally { clock.mockRestore() }
  })

  it('hides the section while loading instead of showing a placeholder', async () => {
    vi.mocked(generateOpeningPrompts).mockImplementationOnce(() => new Promise(() => {}))
    const { container } = setup()
    await waitFor(() => expect(generateOpeningPrompts).toHaveBeenCalledTimes(1))
    expect(container).toBeEmptyDOMElement()
  })

  it('does not generate when the feature is disabled or the provider has not resolved', () => {
    const { container } = setup({ enabled: false })
    expect(generateOpeningPrompts).not.toHaveBeenCalled()
    expect(container).toBeEmptyDOMElement()
  })

  it('hides the section when no authorized context exists instead of inserting defaults', async () => {
    vi.mocked(generateOpeningPrompts).mockResolvedValueOnce({ prompts: [] })
    const { container } = setup()
    await waitFor(() => expect(generateOpeningPrompts).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(container).toBeEmptyDOMElement())
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('silently hides on generation failures without showing a toast', async () => {
    vi.mocked(generateOpeningPrompts).mockRejectedValueOnce(new Error('generation failed'))
    const view = setup()
    await waitFor(() => expect(generateOpeningPrompts).toHaveBeenCalledTimes(1))
    expect(view.container).toBeEmptyDOMElement()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('does not regenerate when switching provider or model', async () => {
    const view = setup()
    await screen.findByRole('button', { name: '记忆生成学习问题1？' })
    view.change({ providerId: 'provider-b', modelId: 'model-b' })
    expect(generateOpeningPrompts).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '记忆生成学习问题1？' })).toBeEnabled()
  })

  it('keeps workspace and project responses separate and never flashes the old workspace prompts', async () => {
    const view = setup()
    await screen.findByRole('button', { name: '记忆生成学习问题1？' })
    vi.mocked(generateOpeningPrompts).mockResolvedValueOnce(group('另一个工作区'))
    view.change({ workspaceId: 'workspace-b', projectId: 'project-b' })
    expect(screen.queryByText('记忆生成学习问题1？')).not.toBeInTheDocument()
    await screen.findByRole('button', { name: '另一个工作区学习问题1？' })
    expect(generateOpeningPrompts).toHaveBeenLastCalledWith(expect.objectContaining({ project_id: 'project-b' }), expect.objectContaining({ headers: { 'X-Workspace-ID': 'workspace-b' } }))
  })

  it('reuses a recent response on remount without blocking the composer', async () => {
    const view = setup()
    await screen.findByRole('button', { name: '记忆生成学习问题1？' })
    view.change({ enabled: false })
    view.change({ enabled: true })
    await screen.findByRole('button', { name: '记忆生成学习问题1？' })
    expect(generateOpeningPrompts).toHaveBeenCalledTimes(1)
  })
})
