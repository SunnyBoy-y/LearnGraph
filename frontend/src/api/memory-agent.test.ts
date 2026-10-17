import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiClient } from './client'
import { runMemoryProfileAgent } from './memory'

afterEach(() => vi.restoreAllMocks())

describe('memory Agent SSE transport', () => {
  it('delivers server steps as they arrive and waits for the actual result', async () => {
    const onStep = vi.fn()
    const result = { status: 'ok', drafts_created: 1, auto_committed: 1, affected_memory_ids: ['m'], profile_status: 'ready' }
    vi.spyOn(apiClient, 'postSse').mockImplementation(async function* () {
      yield { event: 'step', data: { id: 'read', status: 'running' }, rawData: '' }
      expect(onStep).toHaveBeenCalledOnce()
      yield { event: 'result', data: result, rawData: '' }
    })
    expect(await runMemoryProfileAgent({ text: '记住偏好' }, onStep)).toEqual(result)
    expect(apiClient.postSse).toHaveBeenCalledWith('/memory/profile/intents/stream', { text: '记住偏好' })
  })

  it('rejects truncated streams instead of inventing a successful write', async () => {
    vi.spyOn(apiClient, 'postSse').mockImplementation(async function* () {
      yield { event: 'step', data: { id: 'read', status: 'completed' }, rawData: '' }
    })
    await expect(runMemoryProfileAgent({ text: '删除记忆' }, vi.fn())).rejects.toThrow('连接中断')
  })

  it('keeps partial result details on streamed errors', async () => {
    vi.spyOn(apiClient, 'postSse').mockImplementation(async function* () {
      yield { event: 'error', data: { code: 'memory_profile_agent_failed', message: '执行中断', partial_result: { auto_committed: 1 } }, rawData: '' }
    })
    const error = await runMemoryProfileAgent({ text: '更新记忆' }, vi.fn()).catch((error: unknown) => error)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).details).toEqual({ partial_result: { auto_committed: 1 } })
  })
})
