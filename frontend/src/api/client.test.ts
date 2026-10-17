import { describe, expect, it, vi } from 'vitest'

import { ApiClient } from './client'
import { createWorkspaceApiClient, jsonResponse } from '@/test/api'

describe('ApiClient workspace scoping', () => {
  it('downloads a password-authorized backup as a POST blob', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('encrypted-zip', { headers: { 'Content-Type': 'application/zip' } }))
    const client = createWorkspaceApiClient('workspace-a', fetcher)
    const blob = await client.postBlob('/migrations/backup', { current_password: 'current-fixture', backup_password: 'backup-fixture' })
    expect(blob.type).toBe('application/zip')
    expect(blob.size).toBe(13)
    const [url, request] = fetcher.mock.calls[0]!
    expect(String(url)).not.toContain('current-fixture')
    expect(request?.method).toBe('POST')
    expect(JSON.parse(String(request?.body))).toEqual({ current_password: 'current-fixture', backup_password: 'backup-fixture' })
  })
  it('caps small-preview downloads even when file metadata or headers understate size', async () => {
    const cancel = vi.fn()
    const stream = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(9)) }, cancel })
    const client = createWorkspaceApiClient('workspace-range', vi.fn<typeof fetch>().mockResolvedValue(new Response(stream)))
    await expect(client.getBlob('/files/small/content', {}, 8)).rejects.toThrow('超过直接预览限制')
    expect(cancel).toHaveBeenCalledOnce()
  })
  it('validates and bounds authenticated byte-range responses', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('2345', { status: 206, headers: { 'Content-Range': 'bytes 2-5/10' } }))
    const client = createWorkspaceApiClient('workspace-range', fetcher)
    const response = await client.getByteRange('/files/book/content', 2, 6)
    expect([...response.data]).toEqual([50, 51, 52, 53])
    expect(response.size).toBe(10)
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get('Range')).toBe('bytes=2-5')
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get('X-Workspace-ID')).toBe('workspace-range')
  })

  it('cancels an ignored Range response instead of buffering the whole file', async () => {
    const cancel = vi.fn()
    const response = new Response(new ReadableStream({ cancel }), { status: 200 })
    const client = createWorkspaceApiClient('workspace-range', vi.fn<typeof fetch>().mockResolvedValue(response))
    await expect(client.getByteRange('/files/book/content', 0, 65536)).rejects.toThrow('分段数据')
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('rejects a truncated range response', async () => {
    const response = new Response('23', { status: 206, headers: { 'Content-Range': 'bytes 2-5/10' } })
    const client = createWorkspaceApiClient('workspace-range', vi.fn<typeof fetch>().mockResolvedValue(response))
    await expect(client.getByteRange('/files/book/content', 2, 6)).rejects.toThrow('未完整传输')
  })
  it('sends the active workspace for each request without sharing tenant state', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ workspace: 'workspace-a' }))
      .mockResolvedValueOnce(jsonResponse({ workspace: 'workspace-b' }))
    const clientA = createWorkspaceApiClient('workspace-a', fetcher)
    const clientB = createWorkspaceApiClient('workspace-b', fetcher)

    await clientA.get('/sessions')
    await clientB.get('/sessions')

    const requestA = fetcher.mock.calls[0]![1]!
    const requestB = fetcher.mock.calls[1]![1]!
    expect(new Headers(requestA.headers).get('X-Workspace-ID')).toBe('workspace-a')
    expect(new Headers(requestB.headers).get('X-Workspace-ID')).toBe('workspace-b')
  })

  it('honors an explicit workspace header and the deliberate opt-out', async () => {
    const fetcher = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse({ ok: true })))
    const client = createWorkspaceApiClient('workspace-a', fetcher)

    await client.get('/sessions', { headers: { 'X-Workspace-ID': 'workspace-override' } })
    await client.get('/auth/me', { workspace: false })

    expect(new Headers(fetcher.mock.calls[0]![1]!.headers).get('X-Workspace-ID')).toBe(
      'workspace-override',
    )
    expect(new Headers(fetcher.mock.calls[1]![1]!.headers).has('X-Workspace-ID')).toBe(false)
  })

  it('keeps an injected access token separate from the workspace dependency', async () => {
    const fetcher = vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse({ ok: true })))
    const client = new ApiClient({
      fetch: fetcher,
      workspaceId: () => 'workspace-a',
      accessToken: () => 'access-token',
    })

    await client.get('/sessions')

    const headers = new Headers(fetcher.mock.calls[0]![1]!.headers)
    expect(headers.get('Authorization')).toBe('Bearer access-token')
    expect(headers.get('X-Workspace-ID')).toBe('workspace-a')
  })
})
