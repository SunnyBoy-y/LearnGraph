import { QueryClient } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cancelSessionRefresh, scheduleSessionRefresh } from './session-refresh'

afterEach(() => vi.useRealTimers())
describe('session refresh fixed window', () => {
  it('merges bursts without continually delaying refresh', async () => {
    vi.useFakeTimers()
    const client = new QueryClient()
    const refresh = vi.spyOn(client, 'invalidateQueries').mockResolvedValue()
    const first = scheduleSessionRefresh(client, 'w')
    await vi.advanceTimersByTimeAsync(150)
    expect(scheduleSessionRefresh(client, 'w')).toBe(first)
    await vi.advanceTimersByTimeAsync(50)
    await first
    expect(refresh).toHaveBeenCalledTimes(1)
    client.clear()
  })
  it('cancels pending work on workspace/account changes', async () => {
    vi.useFakeTimers()
    const client = new QueryClient()
    const refresh = vi.spyOn(client, 'invalidateQueries').mockResolvedValue()
    const pending = scheduleSessionRefresh(client, 'old')
    cancelSessionRefresh(client, 'old')
    await vi.runAllTimersAsync(); await pending
    expect(refresh).not.toHaveBeenCalled()
    client.clear()
  })
})
