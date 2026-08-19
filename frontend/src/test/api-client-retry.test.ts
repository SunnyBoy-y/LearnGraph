import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiError } from '@/api/client'

function jsonResponse(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ApiClient retry policy', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('retries a 502 from a GET request and succeeds on the second attempt', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(502))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }))
    vi.useFakeTimers()
    const client = new ApiClient({ fetch: fetcher })

    const promise = client.get<{ ok: boolean }>('/workspaces')
    await vi.advanceTimersByTimeAsync(500)

    await expect(promise).resolves.toEqual({ ok: true })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('gives up after the backoff budget is exhausted', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(502, { error: { code: 'upstream', message: 'boom' } }))
    vi.useFakeTimers()
    const client = new ApiClient({ fetch: fetcher })

    const promise = client.get('/workspaces')
    const assertion = expect(promise).rejects.toMatchObject({ status: 502 })
    await vi.advanceTimersByTimeAsync(500 + 1000 + 2000)

    await assertion
    expect(fetcher).toHaveBeenCalledTimes(4)
  })

  it('recovers from a transient network error', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }))
    vi.useFakeTimers()
    const client = new ApiClient({ fetch: fetcher })

    const promise = client.get<{ ok: boolean }>('/settings')
    await vi.advanceTimersByTimeAsync(500)

    await expect(promise).resolves.toEqual({ ok: true })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('never retries a 401 (session expiry must run once)', async () => {
    window.history.pushState({}, '', '/auth/login')
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(401, { error: { code: 'unauthorized', message: 'expired' } }))
    vi.useFakeTimers()
    const client = new ApiClient({ fetch: fetcher })

    const promise = client.get('/auth/me')
    const assertion = expect(promise).rejects.toMatchObject({ status: 401 })
    await vi.advanceTimersByTimeAsync(10_000)

    await assertion
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('never retries a mutating POST even on 502', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(502, { error: { code: 'upstream', message: 'boom' } }))
    vi.useFakeTimers()
    const client = new ApiClient({ fetch: fetcher })

    const promise = client.post('/sessions', { message: 'x' })
    const assertion = expect(promise).rejects.toMatchObject({ status: 502 })
    await vi.advanceTimersByTimeAsync(10_000)

    await assertion
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('aborts the backoff wait when the caller aborts the request', async () => {
    const controller = new AbortController()
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(502))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }))
    vi.useFakeTimers()
    const client = new ApiClient({ fetch: fetcher })

    const promise = client.get('/workspaces', { signal: controller.signal })
    await vi.advanceTimersByTimeAsync(100)
    controller.abort()

    await expect(promise).rejects.toBeInstanceOf(DOMException)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('surfaces non-retryable ApiError instances untouched', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse(400, { detail: [{ msg: 'bad input' }] }))
    vi.useFakeTimers()
    const client = new ApiClient({ fetch: fetcher })

    const promise = client.get('/workspaces')
    const assertion = expect(promise).rejects.toBeInstanceOf(ApiError)
    const statusAssertion = expect(promise).rejects.toMatchObject({ status: 400 })
    await vi.advanceTimersByTimeAsync(10_000)

    await assertion
    await statusAssertion
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
