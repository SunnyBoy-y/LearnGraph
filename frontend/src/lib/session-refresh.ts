import type { QueryClient } from '@tanstack/react-query'
import { workspaceQueryKey } from './query-keys'

type Pending = { timer: ReturnType<typeof setTimeout>; resolve: () => void; promise: Promise<void> }
const clients = new WeakMap<QueryClient, Map<string, Pending>>()
/** Fixed window, not a trailing debounce: a stream of events cannot starve refresh.
 * UI mutations are patched immediately; this only coalesces server reconciliation.
 */
export function scheduleSessionRefresh(client: QueryClient, workspaceId: string): Promise<void> {
  let workspaces = clients.get(client)
  if (!workspaces) { workspaces = new Map(); clients.set(client, workspaces) }
  const existing = workspaces.get(workspaceId)
  if (existing) return existing.promise
  let resolve!: () => void
  const promise = new Promise<void>(done => { resolve = done })
  const timer = setTimeout(() => {
    workspaces!.delete(workspaceId)
    void client.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'sessions') }).finally(resolve)
  }, 200)
  workspaces.set(workspaceId, { timer, resolve, promise })
  return promise
}
export function cancelSessionRefresh(client: QueryClient, workspaceId?: string) {
  const workspaces = clients.get(client)
  if (!workspaces) return
  for (const [id, entry] of workspaces) {
    if (workspaceId && id !== workspaceId) continue
    clearTimeout(entry.timer); entry.resolve(); workspaces.delete(id)
  }
}
