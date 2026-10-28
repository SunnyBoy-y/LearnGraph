import { useLayoutEffect, useRef } from 'react'
import { authStore } from '@/api/auth-store'
import { activeVoiceSessionIds } from '@/features/voice/voice-session-markers'
import { seedSessionActivity, subscribeSessionActivityEvents, type SessionActivity, type SessionActivityEvent } from './session-activity'

type Scope = { token: string; workspaceId: string }
export type NativeSidebarEvent = SessionActivityEvent |
  { kind: 'voice'; sessionId: string; active: boolean } | { kind: 'view'; sessionId: string | null }
type Seed = { activities: Record<string, SessionActivity>; preserveIds?: string[] }
export interface NativeSidebarActivityContract {
  version: 1
  seed(scope: Scope, seed: Seed): boolean
  subscribe(scope: Scope, listener: (event: NativeSidebarEvent) => void): (() => void) | null
}
declare global { interface Window { LearnGraphSidebarActivity?: NativeSidebarActivityContract } }

/** Relay real Web activity and voice markers, never server status or guessed completion. */
export function useNativeSidebarActivityContract(workspaceId: string, pathname: string) {
  const token = authStore.getAccessToken()
  const route = useRef(pathname)
  const viewListeners = useRef(new Set<(event: NativeSidebarEvent) => void>())
  useLayoutEffect(() => {
    route.current = pathname
    const sessionId = viewedSession(pathname, workspaceId)
    viewListeners.current.forEach(listener => listener({ kind: 'view', sessionId }))
  }, [pathname, workspaceId])
  useLayoutEffect(() => {
    const cleanups = new Set<() => void>()
    const authorized = (scope: Scope) => !!token && scope?.token === token && scope.workspaceId === workspaceId &&
      authStore.getAccessToken() === token && authStore.getWorkspaceId() === workspaceId
    const contract: NativeSidebarActivityContract = {
      version: 1,
      seed(scope, seed) {
        if (!authorized(scope) || !seed?.activities || typeof seed.activities !== 'object') return false
        const entries = new Map<string, SessionActivity>()
        for (const [id, value] of Object.entries(seed.activities)) {
          if (!validId(id) || !value || typeof value !== 'object') continue
          entries.set(id, { running: value.running === true, unreadCompleted: value.unreadCompleted === true,
            completedAt: finiteTime(value.completedAt), lastTouchedAt: finiteTime(value.lastTouchedAt) ?? 0 })
        }
        seedSessionActivity(entries, seed.preserveIds?.filter(validId) ?? [])
        return true
      },
      subscribe(scope, listener) {
        if (!authorized(scope)) return null
        const relay = (event: NativeSidebarEvent) => { if (authorized(scope)) { try { listener(event) } catch { /* native relay is optional */ } } }
        const unsubscribe = subscribeSessionActivityEvents(relay)
        const voice = (event: Event) => {
          const detail = (event as CustomEvent<{ key?: string; active?: boolean }>).detail
          if (typeof detail?.key !== 'string' || !detail.key.startsWith(`${workspaceId}:`) || typeof detail.active !== 'boolean') return
          const sessionId = detail.key.slice(workspaceId.length + 1)
          if (validId(sessionId)) relay({ kind: 'voice', sessionId, active: detail.active })
        }
        viewListeners.current.add(relay)
        window.addEventListener('learngraph:voice-session-marker', voice)
        relay({ kind: 'view', sessionId: viewedSession(route.current, workspaceId) })
        activeVoiceSessionIds(workspaceId).filter(validId).forEach(sessionId => relay({ kind: 'voice', sessionId, active: true }))
        const cleanup = () => { unsubscribe(); viewListeners.current.delete(relay); window.removeEventListener('learngraph:voice-session-marker', voice); cleanups.delete(cleanup) }
        cleanups.add(cleanup)
        return cleanup
      },
    }
    window.LearnGraphSidebarActivity = contract
    return () => { cleanups.forEach(cleanup => cleanup()); if (window.LearnGraphSidebarActivity === contract) delete window.LearnGraphSidebarActivity }
  }, [workspaceId, token])
}
function validId(id: string) { return typeof id === 'string' && id !== 'new' && /^[A-Za-z0-9_-]{1,100}$/.test(id) }
function finiteTime(value: unknown): number | null { return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null }
function viewedSession(pathname: string, workspaceId: string): string | null {
  const prefix = `/w/${workspaceId}/chat/`
  const id = pathname.startsWith(prefix) ? pathname.slice(prefix.length) : ''
  return validId(id) ? id : null
}
