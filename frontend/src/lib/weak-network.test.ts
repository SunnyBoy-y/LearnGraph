import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClient } from '@/api/client'
import { parseSseResponse, SseIdleTimeoutError } from '@/api/sse'
import {
  adaptiveChunkUploadThreshold,
  adaptivePollInterval,
  adaptiveTimeoutMs,
  adaptiveUploadChunkSize,
  computeJitteredBackoffMs,
  networkQualityStore,
} from './network-quality'
import { importWithRetry, isChunkLoadError } from './lazy-with-retry'
import { offlineOutbox } from './offline-outbox'

describe('NetworkQualityStore & adaptive helpers', () => {
  beforeEach(() => {
    networkQualityStore.reset()
  })

  it('transitions to weak on high EWMA latency and recovers with hysteresis', () => {
    expect(networkQualityStore.getSnapshot().tier).toBe('good')
    networkQualityStore.recordSample({ ok: true, durationMs: 2_000 })
    expect(networkQualityStore.getSnapshot().tier).toBe('weak')
    expect(adaptiveTimeoutMs(10_000)).toBe(18_000)
    expect(adaptivePollInterval(2_000, { pauseWhenHidden: false })).toBe(6_000)
    expect(adaptiveUploadChunkSize()).toBe(512 * 1024)
    expect(adaptiveChunkUploadThreshold()).toBe(1024 * 1024)

    // Still above 900ms exit threshold after one fast sample
    networkQualityStore.recordSample({ ok: true, durationMs: 200 })
    expect(networkQualityStore.getSnapshot().tier).toBe('weak')

    // Multiple fast samples drop EWMA <= 900ms and recover to good
    for (let i = 0; i < 6; i += 1) {
      networkQualityStore.recordSample({ ok: true, durationMs: 120 })
    }
    expect(networkQualityStore.getSnapshot().tier).toBe('good')
    expect(adaptiveTimeoutMs(10_000)).toBe(10_000)
    expect(adaptiveUploadChunkSize()).toBe(2 * 1024 * 1024)
  })

  it('transitions to weak on consecutive network errors and resets on success', () => {
    networkQualityStore.recordSample({ ok: false, networkError: true })
    expect(networkQualityStore.getSnapshot().tier).toBe('good')
    networkQualityStore.recordSample({ ok: false, networkError: true })
    expect(networkQualityStore.getSnapshot().tier).toBe('weak')
    networkQualityStore.recordSample({ ok: true, durationMs: 80 })
    expect(networkQualityStore.getSnapshot().tier).toBe('good')
  })

  it('computes equal-jitter exponential backoff within [cap/2, cap]', () => {
    expect(computeJitteredBackoffMs(500, 0, 8_000, () => 0)).toBe(250)
    expect(computeJitteredBackoffMs(500, 0, 8_000, () => 1)).toBe(500)
    expect(computeJitteredBackoffMs(500, 2, 8_000, () => 0.5)).toBe(1500)
    expect(computeJitteredBackoffMs(500, 10, 8_000, () => 1)).toBe(8_000)
  })
})

describe('ApiClient weak-network timeout and idempotent retries', () => {
  beforeEach(() => {
    networkQualityStore.reset()
  })

  it('retries POST with Idempotency-Key on transient 503 and succeeds', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('unavailable', { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    const client = new ApiClient({ fetch: fetcher, accessToken: () => 't', workspaceId: () => 'ws' })

    const result = await client.post<{ ok: boolean }>(
      '/files/1/parse',
      {},
      { headers: { 'Idempotency-Key': 'idem-1' }, timeoutMs: 5_000 },
    )
    expect(result).toEqual({ ok: true })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('does NOT retry non-idempotent POST on network error', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch'))
    const client = new ApiClient({ fetch: fetcher, accessToken: () => 't', workspaceId: () => 'ws' })

    await expect(client.post('/items', { name: 'a' })).rejects.toMatchObject({
      code: 'network_error',
      status: 0,
    })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('aborts stalled requests with request_timeout when exceeding timeoutMs', async () => {
    const fetcher = vi.fn<typeof fetch>(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(init.signal?.reason ?? new DOMException('Aborted', 'AbortError'))
          })
        }),
    )
    const client = new ApiClient({ fetch: fetcher, accessToken: () => 't', workspaceId: () => 'ws' })

    await expect(client.get('/slow', { timeoutMs: 40, noRetry: true })).rejects.toMatchObject({
      code: 'request_timeout',
      status: 0,
    })
  })
})

describe('parseSseResponse idle timeout', () => {
  it('throws SseIdleTimeoutError when no bytes arrive within idleTimeoutMs', async () => {
    const stalled = new Response(
      new ReadableStream({
        start() {
          // Intentionally never enqueue or close.
        },
      }),
    )

    const consume = async () => {
      for await (const _ev of parseSseResponse(stalled, { idleTimeoutMs: 40 })) {
        // no-op
      }
    }

    await expect(consume()).rejects.toBeInstanceOf(SseIdleTimeoutError)
  })
})

describe('lazyWithRetry / importWithRetry', () => {
  it('detects dynamic import chunk errors and retries until success', async () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: /assets/a.js'))).toBe(true)
    expect(isChunkLoadError(new Error('ordinary runtime bug'))).toBe(false)

    let calls = 0
    const mod = await importWithRetry(
      async () => {
        calls += 1
        if (calls < 3) {
          throw new TypeError('Failed to fetch dynamically imported module: /assets/chunk.js')
        }
        return { default: 'loaded' }
      },
      { retries: 3, baseDelayMs: 5, allowReload: false },
    )
    expect(mod).toEqual({ default: 'loaded' })
    expect(calls).toBe(3)
  })
})

describe('OfflineOutboxStore', () => {
  beforeEach(() => {
    offlineOutbox.clearAll()
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    offlineOutbox.clearAll()
  })

  it('queues offline mutations and flushes them in order when online', async () => {
    const { apiClient } = await import('@/api/client')
    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValue({ id: 'n1' })
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ ok: true })

    offlineOutbox.enqueue({
      label: '保存节点',
      method: 'PATCH',
      path: '/graphs/g1/nodes/n1',
      body: { label: 'New' },
      workspaceId: 'default',
    })
    offlineOutbox.enqueue({
      label: '提交作答',
      method: 'POST',
      path: '/practice/sessions/p1/answer',
      body: { exercise_id: 'e1', answer: 'A' },
      workspaceId: 'default',
    })

    expect(offlineOutbox.getSnapshot().pendingCount).toBe(2)

    const result = await offlineOutbox.flush()
    expect(result).toEqual({ succeeded: 2, failed: 0, remaining: 0 })
    expect(patchSpy).toHaveBeenCalledTimes(1)
    expect(postSpy).toHaveBeenCalledTimes(1)
    expect(offlineOutbox.getSnapshot().pendingCount).toBe(0)
  })
})
