import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listMemoryTimeline } from '@/api/memory'
import { renderWithProviders } from '@/test/render'
import { MemoryTimelinePanel } from './memory-timeline-panel'

vi.mock('@/api/memory', () => ({ listMemoryTimeline: vi.fn() }))
const empty = { time_window: { start_at: null, end_at: null, display: '2026年10月18日' }, memories: [], episodes: [] }

beforeEach(() => { vi.mocked(listMemoryTimeline).mockReset() })

describe('occurrence timeline', () => {
  it('is opt-in, preserves the time expression, and has an honest empty state', async () => {
    vi.mocked(listMemoryTimeline).mockResolvedValue(empty)
    renderWithProviders(<MemoryTimelinePanel workspaceId="test-workspace" />)
    expect(listMemoryTimeline).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('textbox', { name: '按发生时间回忆' }), { target: { value: '  昨天  ' } })
    fireEvent.click(screen.getByRole('button', { name: '查询时间线' }))
    await waitFor(() => expect(listMemoryTimeline).toHaveBeenCalledWith({ time_query: '  昨天  ', timezone_name: 'Asia/Shanghai' }))
    expect(await screen.findByText(/不会用其他日期的记忆补齐/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '清除查询' }))
    expect(screen.queryByText(/不会用其他日期的记忆补齐/)).not.toBeInTheDocument()
  })

  it('accepts time-only ISO bounds and rejects reversed ranges before requesting', async () => {
    vi.mocked(listMemoryTimeline).mockResolvedValue(empty)
    renderWithProviders(<MemoryTimelinePanel workspaceId="test-workspace" />)
    fireEvent.click(screen.getByText('精确时间范围（可选）'))
    fireEvent.change(screen.getByLabelText('开始时间（含）'), { target: { value: '2026-10-19T00:00:00+08:00' } })
    fireEvent.change(screen.getByLabelText('结束时间（不含）'), { target: { value: '2026-10-18T00:00:00+08:00' } })
    fireEvent.click(screen.getByRole('button', { name: '查询时间线' }))
    expect(screen.getByRole('alert')).toHaveTextContent('结束时间必须晚于开始时间')
    expect(listMemoryTimeline).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('结束时间（不含）'), { target: { value: '2026-10-20T00:00:00+08:00' } })
    fireEvent.click(screen.getByRole('button', { name: '查询时间线' }))
    await waitFor(() => expect(listMemoryTimeline).toHaveBeenCalledWith({ start_at: '2026-10-19T00:00:00+08:00', end_at: '2026-10-20T00:00:00+08:00', timezone_name: 'Asia/Shanghai' }))
  })

  it('surfaces query errors instead of substituting current memories', async () => {
    let failRequest!: (error: Error) => void
    vi.mocked(listMemoryTimeline).mockImplementation(() => new Promise((_resolve, reject) => { failRequest = reject }))
    renderWithProviders(<MemoryTimelinePanel workspaceId="test-workspace" />)
    fireEvent.change(screen.getByRole('textbox', { name: '按发生时间回忆' }), { target: { value: 'not-a-date' } })
    fireEvent.click(screen.getByRole('button', { name: '查询时间线' }))
    await waitFor(() => expect(failRequest).toBeTypeOf('function'))
    await act(async () => { failRequest(new Error('invalid_time_query')) })
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid_time_query')
  })
})
