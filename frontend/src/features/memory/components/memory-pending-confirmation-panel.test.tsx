import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '@/test/render'
import type { MemoryDraft, MemoryEntry } from '@/types/memory'
import { MemoryPendingConfirmationPanel } from './memory-pending-confirmation-panel'

const apiMocks = vi.hoisted(() => ({
  batchRecordMemoryFeedback: vi.fn(),
  batchRetractMemories: vi.fn(),
  decideMemoryDraft: vi.fn(),
  decideMemoryDraftsBatch: vi.fn(),
  forgetMemory: vi.fn(),
  listMemoryDrafts: vi.fn(),
  listMemories: vi.fn(),
  recordMemoryFeedback: vi.fn(),
  retractMemory: vi.fn(),
  supersedeMemory: vi.fn(),
}))

vi.mock('@/api', () => apiMocks)

const sampleDrafts = [
  {
    id: 'draft-1',
    workspace_id: 'ws-1',
    operation: 'CREATE',
    status: 'PENDING',
    memory_type: 'learning_preference',
    target_memory_id: null,
    proposed_scope_type: 'workspace',
    proposed_scope_id: null,
    goal_id: null,
    node_id: null,
    session_id: null,
    branch_session_id: null,
    title: '用户想学英语',
    content: '用户想学英语。',
    structured_payload: {},
    source_refs: [],
    confidence: 0.6,
    importance: 0.5,
    suggested_decay_policy: 'SLOW',
    conflicts_with: [],
    created_by: 'memory_extraction',
    reviewed_by: null,
    reviewed_at: null,
    rejection_reason: '',
    result_memory_id: null,
    result_revision: null,
    created_at: '2026-11-24T00:00:00Z',
    updated_at: '2026-11-24T00:00:00Z',
  },
  {
    id: 'draft-2',
    workspace_id: 'ws-1',
    operation: 'CREATE',
    status: 'PENDING',
    memory_type: 'learning_preference',
    target_memory_id: null,
    proposed_scope_type: 'workspace',
    proposed_scope_id: null,
    goal_id: null,
    node_id: null,
    session_id: null,
    branch_session_id: null,
    title: '用户偏好环形座位图',
    content: '用户偏好环形座位图讲解。',
    structured_payload: {},
    source_refs: [],
    confidence: 0.6,
    importance: 0.5,
    suggested_decay_policy: 'SLOW',
    conflicts_with: [],
    created_by: 'memory_extraction',
    reviewed_by: null,
    reviewed_at: null,
    rejection_reason: '',
    result_memory_id: null,
    result_revision: null,
    created_at: '2026-11-24T00:00:00Z',
    updated_at: '2026-11-24T00:00:00Z',
  },
] as MemoryDraft[]

const sampleMemories = [
  {
    id: 'mem-1',
    title: '推测喜欢早上学习',
    content: '推测喜欢早上学习。',
    assertion_type: 'inferred',
    lifecycle_status: 'active',
    created_at: '2026-11-24T00:00:00Z',
    updated_at: '2026-11-24T00:00:00Z',
  },
  {
    id: 'mem-2',
    title: '推测偏好选择题练习',
    content: '推测偏好选择题练习。',
    assertion_type: 'inferred',
    lifecycle_status: 'active',
    created_at: '2026-11-24T00:00:00Z',
    updated_at: '2026-11-24T00:00:00Z',
  },
] as MemoryEntry[]

describe('MemoryPendingConfirmationPanel batch selection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.listMemoryDrafts.mockResolvedValue(sampleDrafts)
    apiMocks.listMemories.mockResolvedValue(sampleMemories)
    apiMocks.decideMemoryDraftsBatch.mockResolvedValue({ processed_count: 2, decision: 'commit' })
    apiMocks.batchRecordMemoryFeedback.mockResolvedValue({ processed_count: 2, feedback_type: 'correct' })
    apiMocks.batchRetractMemories.mockResolvedValue({ processed_count: 2 })
  })

  it('supports select all, partial selection, and batch commit on drafts', async () => {
    renderWithProviders(<MemoryPendingConfirmationPanel />)

    await screen.findByText('用户想学英语。')
    const toolbar = screen.getByRole('region', { name: '批量操作' })
    const selectAll = within(toolbar).getByRole('checkbox', { name: '全选当前列表' })
    const batchCommitBtn = within(toolbar).getByRole('button', { name: '是的，记住' })
    expect(batchCommitBtn).toBeDisabled()

    fireEvent.click(selectAll)
    expect(selectAll).toHaveAttribute('aria-checked', 'true')
    expect(within(toolbar).getByText('已选 2 条')).toBeVisible()
    expect(batchCommitBtn).toBeEnabled()

    fireEvent.click(screen.getByRole('checkbox', { name: '选择理解：用户想学英语。' }))
    expect(selectAll).toHaveAttribute('aria-checked', 'mixed')
    expect(within(toolbar).getByText('已选 1 条')).toBeVisible()

    fireEvent.click(selectAll)
    expect(within(toolbar).getByText('已选 2 条')).toBeVisible()

    fireEvent.click(batchCommitBtn)
    await waitFor(() =>
      expect(apiMocks.decideMemoryDraftsBatch).toHaveBeenCalledWith({
        draft_ids: ['draft-1', 'draft-2'],
        decision: 'commit',
        reason: 'user_confirmed',
      }),
    )
    await waitFor(() => expect(within(toolbar).getByText('已选 0 条')).toBeVisible())
  })

  it('supports batch reject dialog with optional reason on drafts', async () => {
    renderWithProviders(<MemoryPendingConfirmationPanel />)

    await screen.findByText('用户想学英语。')
    const toolbar = screen.getByRole('region', { name: '批量操作' })
    fireEvent.click(within(toolbar).getByRole('checkbox', { name: '全选当前列表' }))
    fireEvent.click(within(toolbar).getByRole('button', { name: '不是这样' }))

    expect(screen.getByText('这 2 条理解不对？')).toBeVisible()
    fireEvent.change(screen.getByLabelText('补充说明（可选）'), {
      target: { value: '已经学完了' },
    })
    fireEvent.click(screen.getByRole('button', { name: '确定，不是这样' }))

    await waitFor(() =>
      expect(apiMocks.decideMemoryDraftsBatch).toHaveBeenCalledWith({
        draft_ids: ['draft-1', 'draft-2'],
        decision: 'reject',
        reason: '已经学完了',
      }),
    )
  })

  it('clears selection on tab switch and supports batch confirm and retract on inferred memories', async () => {
    renderWithProviders(<MemoryPendingConfirmationPanel />)

    await screen.findByText('用户想学英语。')
    const draftsToolbar = screen.getByRole('region', { name: '批量操作' })
    fireEvent.click(within(draftsToolbar).getByRole('checkbox', { name: '全选当前列表' }))
    expect(within(draftsToolbar).getByText('已选 2 条')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: /AI 的推测/ }))
    await screen.findByText('推测喜欢早上学习。')

    const inferredToolbar = screen.getByRole('region', { name: '批量操作' })
    expect(within(inferredToolbar).getByText('已选 0 条')).toBeVisible()

    fireEvent.click(within(inferredToolbar).getByRole('checkbox', { name: '全选当前列表' }))
    fireEvent.click(within(inferredToolbar).getByRole('button', { name: '是的，记住' }))
    await waitFor(() =>
      expect(apiMocks.batchRecordMemoryFeedback).toHaveBeenCalledWith({
        memory_ids: ['mem-1', 'mem-2'],
        feedback_type: 'correct',
      }),
    )

    fireEvent.click(within(inferredToolbar).getByRole('checkbox', { name: '全选当前列表' }))
    fireEvent.click(within(inferredToolbar).getByRole('button', { name: '不是这样' }))
    fireEvent.click(screen.getByRole('button', { name: '确定，不是这样' }))
    await waitFor(() =>
      expect(apiMocks.batchRetractMemories).toHaveBeenCalledWith(
        ['mem-1', 'mem-2'],
        'user_says_incorrect',
      ),
    )
  })
})
