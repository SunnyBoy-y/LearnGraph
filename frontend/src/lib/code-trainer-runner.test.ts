import { afterEach, describe, expect, it, vi } from 'vitest'
import { runCode } from './code-trainer-runner'

afterEach(() => {
  Reflect.deleteProperty(window, 'LearnGraphRenderer')
  Reflect.deleteProperty(window, 'LearnGraphNative')
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('code trainer runtime location', () => {
  it.each(['LearnGraphRenderer', 'LearnGraphNative', undefined])('loads from the server public path for %s', async (bridge) => {
    vi.stubEnv('BASE_URL', '/__lg_renderer_v1/')
    if (bridge) Object.defineProperty(window, bridge, { configurable: true, value: {} })
    const fetch = vi.fn().mockRejectedValue(new Error('offline fixture'))
    vi.stubGlobal('fetch', fetch)
    const result = await runCode({ language: 'javascript', code: '1 + 1', stdin: '', timeLimitMs: 500 })
    expect(fetch).toHaveBeenCalledWith(`${bridge ? '/' : '/__lg_renderer_v1/'}code-trainer/javascript.js`, { credentials: 'omit' })
    expect(result.status).toBe('load_error')
    expect(document.querySelector('iframe')).toBeNull()
  })
})
