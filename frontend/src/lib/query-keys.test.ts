import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import {
  workspaceQueryKey,
  workspaceQueryPrefix,
} from './query-keys'

describe('workspace query keys', () => {
  it('keeps data and optimistic updates isolated to their workspace', () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    })
    const workspaceAProjects = workspaceQueryKey('workspace-a', 'projects')
    const workspaceBProjects = workspaceQueryKey('workspace-b', 'projects')

    queryClient.setQueryData(workspaceAProjects, [{ id: 'project-a' }])
    queryClient.setQueryData(workspaceBProjects, [{ id: 'project-b' }])
    queryClient.setQueryData(workspaceAProjects, (projects: Array<{ id: string }> = []) => [
      ...projects,
      { id: 'optimistic-a' },
    ])

    expect(queryClient.getQueryData(workspaceAProjects)).toEqual([
      { id: 'project-a' },
      { id: 'optimistic-a' },
    ])
    expect(queryClient.getQueryData(workspaceBProjects)).toEqual([{ id: 'project-b' }])
  })

  it('uses a workspace-specific prefix for tenant cleanup', () => {
    const queryClient = new QueryClient()
    const workspaceAProjects = workspaceQueryKey('workspace-a', 'projects')
    const workspaceBProjects = workspaceQueryKey('workspace-b', 'projects')

    queryClient.setQueryData(workspaceAProjects, ['project-a'])
    queryClient.setQueryData(workspaceBProjects, ['project-b'])
    queryClient.removeQueries({ queryKey: workspaceQueryPrefix('workspace-a') })

    expect(queryClient.getQueryData(workspaceAProjects)).toBeUndefined()
    expect(queryClient.getQueryData(workspaceBProjects)).toEqual(['project-b'])
  })
})
