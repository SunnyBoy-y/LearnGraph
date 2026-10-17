import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getMemoryPolicy, listContextManifests } from '@/api'
import { renderWithProviders } from '@/test/render'
import type { MemoryProfile } from '@/types/memory'
import { MemoryReport } from './memory-report'

vi.mock('@/api', () => ({
  getMemoryPolicy: vi.fn().mockResolvedValue({ workspace_enabled: true, workspace_recall_enabled: true }),
  listContextManifests: vi.fn().mockResolvedValue([]),
}))
vi.mock('@/components/ai-elements/message', () => ({
  MessageResponse: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

beforeEach(() => {
  vi.mocked(getMemoryPolicy).mockResolvedValue({ workspace_enabled: true, workspace_recall_enabled: true } as Awaited<ReturnType<typeof getMemoryPolicy>>)
  vi.mocked(listContextManifests).mockResolvedValue([])
})

function failedProfile(hasMemories: boolean): MemoryProfile {
  return {
    id: 'report', workspace_id: 'test-workspace', owner_subject_id: 'user', version: 2,
    status: 'failed', scope: 'workspace', source_count: hasMemories ? 1 : 0,
    overview: '', markdown: '', structured_sections: [],
    dimensions: hasMemories ? [{
      key: 'topics', title: '主题记忆',
      paragraphs: [{ text: '喜欢先看例子再讲原理。', atom_ids: ['current'] }],
    }] : [],
    source_atom_ids: hasMemories ? ['current'] : [], source_fingerprint: '',
    generated_at: null, updated_at: null, stale_reason: 'refresh_failed|timeout|',
  }
}

function report(profile: MemoryProfile, onRefresh = vi.fn().mockResolvedValue(undefined)) {
  return <MemoryReport
    busy={false} legacyCount={0} pendingCount={0} profile={profile}
    onForgetSelection={vi.fn()} onMigrate={vi.fn()} onOpenPending={vi.fn()}
    onRefresh={onRefresh} onRequestComposer={vi.fn()} onSelectionChange={vi.fn()}
  />
}

describe('memory report refresh failures', () => {
  it('keeps a valid previous report alongside the failure and offers retry', async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined)
    renderWithProviders(report(failedProfile(true), onRefresh))

    await waitFor(() => expect(screen.getByText('喜欢先看例子再讲原理。')).toBeVisible())
    expect(screen.getByText(/上次刷新失败：模型响应超时/)).toBeVisible()
    expect(screen.queryByText(/还没有可以生成报告/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '立即刷新' }))
    expect(onRefresh).toHaveBeenCalledOnce()
  })

  it('explains failure instead of claiming there are no atoms when content is empty', async () => {
    renderWithProviders(report(failedProfile(false)))

    await waitFor(() => expect(screen.getByText('学习报告暂时无法生成')).toBeVisible())
    expect(screen.getByRole('button', { name: '立即刷新' })).toBeEnabled()
    expect(screen.queryByText(/还没有可以生成报告/)).not.toBeInTheDocument()
  })
})

describe('memory correction feedback', () => {
  it('does not flash unchanged prose when a generated report reorders paragraph IDs', async () => {
    const original = failedProfile(true)
    original.dimensions[0].paragraphs.push({ id: 'second', text: '每天晚上学习。', atom_ids: ['steady'] })
    original.dimensions[0].paragraphs[0].id = 'first'
    const view = renderWithProviders(report(original))
    await screen.findByText('每天晚上学习。')
    const revised = structuredClone(original)
    revised.version++
    revised.dimensions[0].paragraphs.reverse()
    revised.dimensions[0].paragraphs.forEach((paragraph, index) => { paragraph.id = `regenerated-${index}` })
    view.rerender(report(revised))
    expect(view.container.querySelector('.memory-content-updated')).toBeNull()
  })
  it('keeps unchanged paragraphs mounted and highlights only changed content', async () => {
    const original = failedProfile(true)
    original.dimensions[0].paragraphs.push({ id: 'steady', text: '每天晚上学习。', atom_ids: ['steady'] })
    const view = renderWithProviders(report(original))
    const untouched = await screen.findByText('每天晚上学习。')
    expect(view.container.querySelector('.memory-content-updated')).toBeNull()
    view.rerender(report({ ...original, version: 3 }))
    expect(view.container.querySelector('.memory-content-updated')).toBeNull()

    const revised = structuredClone(original)
    revised.version = 4
    revised.dimensions[0].paragraphs[0].text = '喜欢先做练习，再回顾原理。'
    view.rerender(report(revised))
    await waitFor(() => expect(screen.getByText('喜欢先做练习，再回顾原理。')).toHaveClass('memory-content-updated'))
    expect(screen.getByText('每天晚上学习。')).toBe(untouched)
    expect(untouched).not.toHaveClass('memory-content-updated')
  })

  it('does not interpret opening another workspace as a correction', async () => {
    const original = failedProfile(true)
    const view = renderWithProviders(report(original))
    await screen.findByText('喜欢先看例子再讲原理。')
    view.rerender(report({ ...original, workspace_id: 'another-workspace', overview: '另一个工作区的报告。' }))
    await screen.findByText('另一个工作区的报告。')
    expect(view.container.querySelector('.memory-content-updated')).toBeNull()
  })
})
