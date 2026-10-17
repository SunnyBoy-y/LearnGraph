import { describe, expect, it, vi } from 'vitest'
import { abortSessionStream, clearSessionStream, getSessionStream, registerSessionStream, setSessionStreamMessageId, subscribeSessionStreams } from './session-streams'

describe('live session stream subscribers', () => {
  it('notifies a remounted view about acceptance and completion without clearing a newer send', () => {
    const changed = vi.fn(), unsubscribe = subscribeSessionStreams(changed)
    const first = new AbortController(), second = new AbortController()
    try {
      registerSessionStream('stream-review', first)
      setSessionStreamMessageId('stream-review', 'accepted')
      expect(getSessionStream('stream-review')?.messageId).toBe('accepted')
      registerSessionStream('stream-review', second, 'newer')
      expect(first.signal.aborted).toBe(true)
      const before = changed.mock.calls.length
      clearSessionStream('stream-review', first)
      expect(getSessionStream('stream-review')?.controller).toBe(second)
      expect(changed).toHaveBeenCalledTimes(before)
      clearSessionStream('stream-review', second)
      expect(getSessionStream('stream-review')).toBeUndefined()
      expect(changed).toHaveBeenCalledTimes(before + 1)
    } finally { unsubscribe(); abortSessionStream('stream-review') }
  })

  it('stops only the selected session and releases subscriptions', () => {
    const changed = vi.fn(), unsubscribe = subscribeSessionStreams(changed)
    const first = new AbortController(), second = new AbortController()
    try {
      registerSessionStream('review-a', first)
      registerSessionStream('review-b', second)
      unsubscribe()
      const before = changed.mock.calls.length
      abortSessionStream('review-a')
      expect(first.signal.aborted).toBe(true)
      expect(second.signal.aborted).toBe(false)
      expect(getSessionStream('review-b')?.controller).toBe(second)
      expect(changed).toHaveBeenCalledTimes(before)
    } finally { unsubscribe(); abortSessionStream('review-a'); abortSessionStream('review-b') }
  })
})
