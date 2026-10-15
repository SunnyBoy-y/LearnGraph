import { describe, expect, it, vi } from 'vitest'

import { ApiClient } from './client'
import { createWorkspaceApiClient, jsonResponse } from '@/test/api'

describe('ApiClient workspace scoping', () => {
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
