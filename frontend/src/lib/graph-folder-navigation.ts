import type { SessionSummary } from '@/api/session-browser'

const STORAGE_KEY = 'learngraph:graph-learning-history:v1'
type History = Record<string, { sessionId?: string; openedAt: number }>
const keyOf = (workspaceId: string, graphId: string) => `${workspaceId}:${graphId}`

function readHistory(): History {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? value as History : {}
  } catch { return {} }
}
export function rememberGraphVisit(workspaceId: string, graphId: string, sessionId?: string) {
  const history = readHistory()
  const key = keyOf(workspaceId, graphId)
  history[key] = { sessionId: sessionId ?? history[key]?.sessionId, openedAt: Date.now() }
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(history)) } catch { /* History is optional. */ }
}
export function graphVisit(workspaceId: string, graphId: string) {
  return readHistory()[keyOf(workspaceId, graphId)]
}
/** Never resume another project's, graph's, archived or side conversation. */
export function pickGraphConversation(
  sessions: SessionSummary[], projectId: string, graphId: string,
  options: { nodeId?: string; rememberedSessionId?: string } = {},
) {
  const eligible = sessions.filter(session => session.project_id === projectId && session.graph_id === graphId
    && session.status !== 'archived' && session.session_kind !== 'side'
    && (!options.nodeId || ('current_node_id' in session && session.current_node_id === options.nodeId)))
  return eligible.find(session => session.id === options.rememberedSessionId)
    ?? eligible.sort((a, b) => b.updated_at.localeCompare(a.updated_at) || a.id.localeCompare(b.id))[0]
}

/** Coalesce repeated clicks; the latest distinct intent owns navigation. */
export function createGraphOpenCoordinator() {
  let sequence = 0
  const pending = new Map<string, { promise: Promise<void>; ticket: number }>()
  return {
    invalidate() { sequence += 1; pending.clear() },
    run(key: string, action: (isLatest: () => boolean) => Promise<void>) {
      const previous = pending.get(key)
      if (previous?.ticket === sequence) return previous.promise
      const ticket = ++sequence
      const promise = Promise.resolve().then(() => action(() => ticket === sequence)).finally(() => {
        if (pending.get(key)?.promise === promise) pending.delete(key)
      })
      pending.set(key, { promise, ticket })
      return promise
    },
  }
}
