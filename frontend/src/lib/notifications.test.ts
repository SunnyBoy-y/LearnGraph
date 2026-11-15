import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast as raw, type Action, type ExternalToast, type ToastT } from 'sonner'
import { mountNotificationSurface, setNotificationFocus, toast } from './notifications'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), {
  message: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn(), error: vi.fn(), loading: vi.fn(), dismiss: vi.fn(), getHistory: vi.fn(), getToasts: vi.fn(),
}) }))
let unmount: () => void
const data = (method: 'success' | 'error' | 'warning' | 'message', index = 0) => vi.mocked(raw[method]).mock.calls[index][1] as ExternalToast
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); unmount = mountNotificationSurface() })
afterEach(() => { unmount(); vi.clearAllTimers(); vi.useRealTimers() })

describe('shared notification lifecycle', () => {
  it('uses short success and readable warning/error lifetimes', () => {
    toast.success('已保存'); toast.warning('默认封面')
    expect(data('success').duration).toBe(2800)
    expect(data('warning').duration).toBe(5500)
  })
  it('queues a third item without starting its lifetime invisibly', () => {
    toast.success('one'); toast.success('two'); toast.error('three')
    expect(raw.error).not.toHaveBeenCalled()
    data('success').onAutoClose?.({ id: data('success').id! } as ToastT)
    vi.advanceTimersByTime(219)
    expect(raw.error).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(raw.error).toHaveBeenCalledOnce()
    expect(data('error').duration).toBe(6000)
  })
  it('deduplicates the same message for the same object, not across sessions', () => {
    const first = toast.error('暂不可用', { scope: 'a' })
    expect(toast.error('暂不可用', { scope: 'a' })).toBe(first)
    expect(toast.error('暂不可用', { scope: 'b' })).not.toBe(first)
    toast.warning('queued')
    expect(raw.warning).not.toHaveBeenCalled()
  })
  it('updates a queued notification in place', () => {
    toast.success('one'); toast.success('two')
    toast.warning('等待', { id: 'job' }); toast.success('完成', { id: 'job' })
    toast.dismiss(data('success').id)
    vi.advanceTimersByTime(220)
    expect(vi.mocked(raw.success).mock.calls.at(-1)?.[0]).toBe('完成')
    expect(raw.warning).not.toHaveBeenCalled()
  })
  it('dismisses queued items before they are ever rendered', () => {
    toast.success('one'); toast.success('two')
    const id = toast.error('queued'); toast.dismiss(id)
    toast.dismiss(data('success').id); vi.advanceTimersByTime(220)
    expect(raw.error).not.toHaveBeenCalled()
  })
  it('keeps actionable failures until handled and releases the queue on action', () => {
    const retry = vi.fn()
    toast.error('未能保存', { action: { label: '重试', onClick: retry } })
    expect(data('error').duration).toBe(Infinity)
    toast.success('two'); toast.warning('queued')
    const event = { defaultPrevented: false } as Parameters<Action['onClick']>[0]
    ;(data('error').action as Action).onClick(event)
    expect(retry).toHaveBeenCalledOnce()
    vi.advanceTimersByTime(220)
    expect(raw.warning).toHaveBeenCalledOnce()
  })
  it('does not close an action whose event was prevented', () => {
    toast.error('未能保存', { action: { label: '查看', onClick: event => event.preventDefault() } })
    toast.success('two'); toast.warning('queued')
    const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true } }
    ;(data('error').action as Action).onClick(event as Parameters<Action['onClick']>[0])
    vi.advanceTimersByTime(1000)
    expect(raw.warning).not.toHaveBeenCalled()
  })
  it('keeps object context and respects explicitly requested durations', () => {
    toast.error('连接中断', { context: '学习会话 A', scope: 'session-a', description: '更改尚未保存', duration: 9000 })
    expect(data('error')).toMatchObject({ description: '学习会话 A\n更改尚未保存', duration: 9000 })
  })
  it('reissues an explicit id safely during its exit', () => {
    toast.error('第一次', { id: 'same' }); toast.dismiss('same'); toast.error('第二次', { id: 'same' })
    expect(raw.error).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(220)
    expect(raw.error).toHaveBeenCalledTimes(2)
    expect(vi.mocked(raw.error).mock.calls.at(-1)?.[0]).toBe('第二次')
  })
  it('pauses all visible lifetimes during keyboard interaction and restores them on blur', () => {
    toast.success('one'); toast.error('two')
    setNotificationFocus(true)
    expect(data('success', 1).duration).toBe(Infinity)
    expect(data('error', 1).duration).toBe(Infinity)
    setNotificationFocus(false)
    expect(data('success', 2).duration).toBe(2800)
    expect(data('error', 2).duration).toBe(6000)
  })
  it('clears the entire queue on dismiss all', () => {
    toast.error('one'); toast.success('two'); toast.warning('three')
    toast.dismiss(); vi.advanceTimersByTime(1000)
    expect(raw.warning).not.toHaveBeenCalled()
  })
})
