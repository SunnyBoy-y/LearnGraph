import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from '@/lib/notifications'
import { CopyMessageAction } from './copy-feedback'
vi.mock('@/lib/notifications', () => ({ toast: { error: vi.fn() } }))
vi.mock('@/components/ai-elements/message', () => ({ MessageAction: ({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) => <button aria-label={label} onClick={onClick}>{children}</button> }))
const writeText = vi.fn()
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); writeText.mockResolvedValue(undefined); Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } }) })
afterEach(() => { cleanup(); vi.clearAllTimers(); vi.useRealTimers() })
describe('local copy feedback', () => {
  it('shows a local check for 2.5 seconds without a global success notification', async () => {
    render(<CopyMessageAction text="回答" scope="a" />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '复制全文' })) })
    expect(writeText).toHaveBeenCalledWith('回答')
    expect(screen.getByRole('button', { name: '已复制' })).toBeTruthy()
    act(() => { vi.advanceTimersByTime(2500) })
    expect(screen.getByRole('button', { name: '复制全文' })).toBeTruthy()
  })
  it('does not show success when clipboard permission is denied', async () => {
    writeText.mockRejectedValue(new Error('denied'))
    render(<CopyMessageAction text="回答" scope="a" />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '复制全文' })) })
    expect(screen.queryByRole('button', { name: '已复制' })).toBeNull()
    expect(toast.error).toHaveBeenCalledOnce()
  })
  it('does not apply a late clipboard completion to a different message', async () => {
    let finish!: () => void
    writeText.mockImplementation(() => new Promise<void>(resolve => { finish = resolve }))
    const view = render(<CopyMessageAction text="旧回答" scope="a" />)
    fireEvent.click(screen.getByRole('button', { name: '复制全文' }))
    view.rerender(<CopyMessageAction text="新回答" scope="b" />)
    await act(async () => finish())
    expect(screen.queryByRole('button', { name: '已复制' })).toBeNull()
  })
})
