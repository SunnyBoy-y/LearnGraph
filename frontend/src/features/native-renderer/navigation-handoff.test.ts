import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'

const script = readFileSync('../mobile/native/app/src/main/assets/native-navigation.js', 'utf8')

describe('native search message focus uses the existing Router state contract', () => {
  afterEach(() => window.history.replaceState(null, '', '/'))
  it('preserves Router metadata, query filters and hash while delivering the target', () => {
    window.history.replaceState({ idx: 3, key: 'saved', usr: { pendingPrompt: 'draft' } }, '', '/w/workspace/chat/session?mode=goal&message_id=m-123#answer')
    const update = vi.fn(); window.addEventListener('popstate', update)
    window.eval(script)
    expect(window.history.state).toEqual({ idx: 3, key: 'saved', usr: { pendingPrompt: 'draft', targetMessageId: 'm-123' } })
    expect(window.location.search).toBe('?mode=goal')
    expect(window.location.hash).toBe('#answer')
    expect(update).toHaveBeenCalledTimes(1)
    window.eval(script)
    expect(update).toHaveBeenCalledTimes(1)
    window.removeEventListener('popstate', update)
  })
  it('does not mutate another page or accept an invalid message ID', () => {
    window.history.replaceState({ idx: 1 }, '', '/w/workspace/sources?message_id=m1')
    window.eval(script)
    expect(window.history.state).toEqual({ idx: 1 })
    window.history.replaceState({ idx: 1 }, '', '/w/workspace/chat/session?message_id=%22bad%22')
    window.eval(script)
    expect(window.history.state).toEqual({ idx: 1 })
  })
})
