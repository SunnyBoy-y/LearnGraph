import { QueryClient } from '@tanstack/react-query'
import { afterEach, describe, expect, it } from 'vitest'

import {
  clearAuthenticatedClientState,
  clearWorkspaceClientState,
  registerAuthQueryClient,
} from '@/lib/auth-query-cache'
import { workspaceQueryKey } from '@/lib/query-keys'

/**
 * P0 acceptance: switching workspaces / logout must clear React Query cache by
 * the canonical workspace prefix so a stale tenant can never render. Uses a
 * long gcTime so unobserved seeded entries are not garbage-collected mid-test.
 */
function registerFreshClient(): QueryClient {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 60_000, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
  registerAuthQueryClient(client)
  return client
}

describe('workspace cache isolation lifecycle', () => {
  afterEach(() => {
    registerAuthQueryClient(null as unknown as QueryClient)
  })

  it('switching workspaces clears only the previous tenant cache', async () => {
    const client = registerFreshClient()
    client.setQueryData(workspaceQueryKey('workspace-a', 'sessions'), [{ id: 'a-1' }])
    client.setQueryData(workspaceQueryKey('workspace-a', 'settings'), { theme: 'dark' })
    client.setQueryData(workspaceQueryKey('workspace-b', 'sessions'), [{ id: 'b-1' }])

    // Simulate selectWorkspace: drop the previous tenant before the new one renders.
    await clearWorkspaceClientState('workspace-a')

    expect(client.getQueryData(workspaceQueryKey('workspace-a', 'sessions'))).toBeUndefined()
    expect(client.getQueryData(workspaceQueryKey('workspace-a', 'settings'))).toBeUndefined()
    expect(client.getQueryData(workspaceQueryKey('workspace-b', 'sessions'))).toEqual([{ id: 'b-1' }])
  })

  it('clearing one tenant never touches identity-scoped or other-tenant data', async () => {
    const client = registerFreshClient()
    client.setQueryData(workspaceQueryKey('workspace-a', 'goals'), [{ id: 'g-1' }])
    client.setQueryData(['identity', 'user-1', 'profile'], { name: 'u' })

    await clearWorkspaceClientState('workspace-a')

    expect(client.getQueryData(workspaceQueryKey('workspace-a', 'goals'))).toBeUndefined()
    expect(client.getQueryData(['identity', 'user-1', 'profile'])).toEqual({ name: 'u' })
  })

  it('logout clears every tenant and identity entry', async () => {
    const client = registerFreshClient()
    client.setQueryData(workspaceQueryKey('workspace-a', 'sessions'), [])
    client.setQueryData(workspaceQueryKey('workspace-b', 'sessions'), [])
    client.setQueryData(['identity', 'user-1', 'profile'], {})

    await clearAuthenticatedClientState()

    expect(client.getQueryData(workspaceQueryKey('workspace-a', 'sessions'))).toBeUndefined()
    expect(client.getQueryData(workspaceQueryKey('workspace-b', 'sessions'))).toBeUndefined()
    expect(client.getQueryData(['identity', 'user-1', 'profile'])).toBeUndefined()
  })
})
