import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '@/test/render'
import type { MemoryEntry } from '@/types/memory'
import { MemoryAllList } from './memory-all-list'

vi.mock('@/api', () => ({ getMemory: vi.fn(), listMemoryRevisions: vi.fn(), updateMemory: vi.fn() }))

const items = [
  { id: 'a', title: '喜欢例子', record_kind: 'learning_preference' },
  { id: 'b', title: '晚上学习', record_kind: 'learning_preference' },
  { id: 'c', title: '学习数据库', record_kind: 'teacher_focus', view_source: 'event' },
] as MemoryEntry[]

function setup(onDeleteMany = vi.fn().mockResolvedValue(undefined), busy = false) {
  renderWithProviders(<MemoryAllList busy={busy} items={items} workspaceId="test-workspace"
    onDelete={vi.fn()} onDeleteMany={onDeleteMany} onRestoreRevision={vi.fn()} onUpdate={vi.fn()} />)
  return onDeleteMany
}

describe('memory batch selection', () => {
  it('selects the current category and preserves selections across filters', async () => {
    const remove = setup()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择记忆：学习数据库' }))
    fireEvent.click(screen.getByRole('button', { name: /学习偏好/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: '全选当前分类' }))
    expect(screen.getByText('已选 3 条')).toBeVisible()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择记忆：晚上学习' }))
    expect(screen.getByRole('checkbox', { name: '全选当前分类' })).toHaveAttribute('aria-checked', 'mixed')
    fireEvent.click(screen.getByRole('button', { name: '删除所选' }))
    expect(screen.getByText('删除这 2 条记忆？')).toBeVisible()
    expect(remove).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith(['a', 'c']))
    await waitFor(() => expect(screen.queryByText('删除这 2 条记忆？')).not.toBeInTheDocument())
  })

  it('clears every category only after confirmation', async () => {
    const remove = setup()
    fireEvent.click(screen.getByRole('button', { name: /学习偏好/ }))
    fireEvent.click(screen.getByRole('button', { name: '清空全部' }))
    expect(screen.getByText('删除这 3 条记忆？')).toBeVisible()
    expect(screen.getByText(/只读记忆将永久删除/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(remove).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '清空全部' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith(['a', 'b', 'c']))
  })

  it('keeps the selection and dialog when deletion fails', async () => {
    const remove = setup(vi.fn().mockRejectedValue(new Error('offline')))
    fireEvent.click(screen.getByRole('checkbox', { name: '选择记忆：喜欢例子' }))
    fireEvent.click(screen.getByRole('button', { name: '删除所选' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(remove).toHaveBeenCalledOnce())
    expect(screen.getByText('删除这 1 条记忆？')).toBeVisible()
  })

  it('disables selection and deletion while a mutation is running', () => {
    setup(undefined, true)
    expect(screen.getByRole('button', { name: '清空全部' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: '选择记忆：喜欢例子' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '删除记忆：喜欢例子' })).toBeDisabled()
  })
})
