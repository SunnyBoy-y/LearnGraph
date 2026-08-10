import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createTestQueryClient, renderWithProviders } from './render'
import { deferred, type Deferred } from './async'
import { workspaceQueryKey } from '@/lib/query-keys'

import { describe, expect, it } from 'vitest'
import { waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

function CounterProbe({
  workspaceId,
  mutationFn,
}: {
  workspaceId: string
  mutationFn?: () => Promise<number>
}) {
  const queryClient = useQueryClient()
  const key = workspaceQueryKey(workspaceId, 'counter')
  const { data } = useQuery({
    queryKey: key,
    queryFn: () => queryClient.getQueryData<number>(key) ?? 0,
  })
  const mutation = useMutation<number, Error, void, { previous?: number }>({
    mutationFn: mutationFn ?? (() => Promise.resolve(0)),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData<number>(key) ?? 0
      // Optimistically apply the pending update.
      queryClient.setQueryData<number>(key, previous + 100)
      return { previous }
    },
    onError: (_error, _variables, context) => {
      if (context?.previous !== undefined) {
        queryClient.setQueryData(key, context.previous)
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: key })
    },
  })
  return (
    <div>
      <span data-testid={`counter-${workspaceId}`}>{data ?? 0}</span>
      <button type="button" onClick={() => mutation.mutate()}>
        bump-{workspaceId}
      </button>
    </div>
  )
}

describe('optimistic mutation rollback', () => {
  it('shows the optimistic value, restores it on failure, and never touches the sibling workspace', async () => {
    const user = userEvent.setup()
    const deferredMutation: Deferred<number> = deferred()
    const queryClient = createTestQueryClient()
    // Seed workspace A and B before the probes mount so both caches exist.
    queryClient.setQueryData(workspaceQueryKey('workspace-a', 'counter'), 5)
    queryClient.setQueryData(workspaceQueryKey('workspace-b', 'counter'), 50)

    const { getByTestId, getByRole } = renderWithProviders(
      <>
        <CounterProbe workspaceId="workspace-a" mutationFn={() => deferredMutation.promise} />
        <CounterProbe workspaceId="workspace-b" />
      </>,
      { queryClient },
    )

    expect(getByTestId('counter-workspace-a')).toHaveTextContent('5')
    expect(getByTestId('counter-workspace-b')).toHaveTextContent('50')

    await user.click(getByRole('button', { name: 'bump-workspace-a' }))
    // Optimistic cache update is visible while the request is pending.
    expect(getByTestId('counter-workspace-a')).toHaveTextContent('105')
    expect(getByTestId('counter-workspace-b')).toHaveTextContent('50')

    deferredMutation.reject(new Error('network down'))
    await waitFor(() => expect(getByTestId('counter-workspace-a')).toHaveTextContent('5'))

    // Rollback restored workspace A; workspace B cache was never touched.
    expect(queryClient.getQueryData(workspaceQueryKey('workspace-a', 'counter'))).toBe(5)
    expect(queryClient.getQueryData(workspaceQueryKey('workspace-b', 'counter'))).toBe(50)
  })
})
