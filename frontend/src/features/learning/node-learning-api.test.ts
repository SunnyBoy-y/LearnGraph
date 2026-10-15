import { afterEach, describe, expect, it, vi } from 'vitest'
import { learningApi } from './node-learning-api'

const client = vi.hoisted(() => ({get: vi.fn(), post: vi.fn()}))
vi.mock('@/api/client', () => ({apiClient: client}))
afterEach(() => vi.useRealTimers())

describe('learning build connection recovery', () => {
  it('bounds status waits and aborts the underlying request', async () => {
    vi.useFakeTimers()
    let signal: AbortSignal | undefined
    client.get.mockImplementation((_url, options) => new Promise((_resolve, reject) => {
      signal = options.signal
      signal!.addEventListener('abort', () => reject(new Error('aborted')))
    }))
    const result = learningApi.page('n')
    const rejection = expect(result).rejects.toThrow('连接超时')
    await vi.advanceTimersByTimeAsync(20_000)
    await rejection
    expect(signal?.aborted).toBe(true)
    expect(client.get).toHaveBeenCalledTimes(1)
  })
  it('cancels obsolete page reads when the query is disposed', async () => {
    client.get.mockImplementation((_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('query cancelled')))
    }))
    const parent = new AbortController()
    const result = learningApi.page('n', parent.signal)
    const rejection = expect(result).rejects.toThrow('query cancelled')
    parent.abort()
    await rejection
  })
})
