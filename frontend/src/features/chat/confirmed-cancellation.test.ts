import { describe, expect, it, vi } from 'vitest'
import { requestConfirmedStop } from './confirmed-cancellation'

describe('authoritative cancellation', () => {
  it('does not treat accepted cancel as a safely stopped task', async () => {
    const cancel = vi.fn().mockResolvedValue(undefined)
    const snapshot = vi.fn().mockResolvedValue({ status: 'streaming' })
    await expect(requestConfirmedStop({ cancel, snapshot, wait: async () => {} })).rejects.toThrow('停止尚未得到服务端确认')
    expect(snapshot).toHaveBeenCalledTimes(5)
  })
  it('preserves cancel failures instead of turning them into success', async () => {
    await expect(requestConfirmedStop({ cancel: async () => { throw new Error('offline') }, snapshot: async () => ({ status: 'streaming' }) })).rejects.toThrow('offline')
  })
  it('accepts only an authoritative terminal snapshot', async () => {
    const snapshot = vi.fn().mockResolvedValueOnce({ status: 'streaming' }).mockResolvedValue({ status: 'cancelled', content: 'partial answer' })
    await expect(requestConfirmedStop({ cancel: async () => {}, snapshot, wait: async () => {} })).resolves.toEqual({ status: 'cancelled', content: 'partial answer' })
  })
  it('retains a concurrently completed answer even if cancel transport failed', async () => {
    await expect(requestConfirmedStop({ cancel: async () => { throw new Error('network') }, snapshot: async () => ({ status: 'completed' }) })).resolves.toEqual({ status: 'completed' })
  })
})
