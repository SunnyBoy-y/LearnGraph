import { useLayoutEffect, useRef, useState } from 'react'
import { authStore } from '@/api/auth-store'
import { normalizeComposerPrefs, type SessionComposerPrefs } from './session-composer-prefs'

export type ComposerScope = { workspaceId: string; sessionId: string }
export type NativeComposerSnapshot = ComposerScope & {
  version: 1
  ready: boolean
  prefs: SessionComposerPrefs
  appliedRequestId: string | null
}
export type NativeComposerApply = ComposerScope & { id: string; prefs: unknown }
export interface NativeComposerContract {
  version: 1
  snapshot(scope: ComposerScope): NativeComposerSnapshot | null
  apply(request: NativeComposerApply): 'pending' | 'accepted' | 'rejected'
}

declare global {
  interface Window { LearnGraphComposer?: NativeComposerContract }
}

/** Invalid bridge data must never turn into defaults and erase an explicit selection. */
export function validateNativeComposerPrefs(value: unknown): SessionComposerPrefs | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  const oneOf = (item: unknown, values: readonly string[]) => typeof item === 'string' && values.includes(item)
  if (!oneOf(record.responseMode, ['fast', 'thinking', 'agentic']) ||
      !oneOf(record.thinkingMode, ['off', 'low', 'medium', 'high', 'xhigh']) ||
      !oneOf(record.searchRoute, ['disabled', 'model_native', 'external', 'local', 'auto']) ||
      record.generationMode !== undefined && !oneOf(record.generationMode, ['text', 'image'])) return null
  for (const [key, max] of [['providerId', 36], ['modelId', 160], ['imageProviderId', 36], ['imageModelId', 160]] as const) {
    const item = record[key]
    if (item != null && (typeof item !== 'string' || item.length > max)) return null
  }
  return normalizeComposerPrefs(value)
}

/** Public, scoped API backed by committed React state, including unsaved chat/new choices. */
export function useNativeComposerContract(
  state: ComposerScope & { ready: boolean; prefs: SessionComposerPrefs },
  applyPrefs: (prefs: SessionComposerPrefs) => void,
) {
  const token = authStore.getAccessToken()
  const [appliedRequestId, setAppliedRequestId] = useState<string | null>(null)
  const seen = useRef(new Set<string>())
  const latest = useRef({ ...state, applyPrefs, appliedRequestId })
  useLayoutEffect(() => { latest.current = { ...state, applyPrefs, appliedRequestId } })
  useLayoutEffect(() => {
    const authorized = (scope: ComposerScope) => {
      const current = latest.current
      return !!token && authStore.getAccessToken() === token && authStore.getWorkspaceId() === current.workspaceId &&
        scope.workspaceId === current.workspaceId && scope.sessionId === current.sessionId
    }
    const contract: NativeComposerContract = {
      version: 1,
      snapshot(scope) {
        if (!scope || !authorized(scope)) return null
        const current = latest.current
        return { version: 1, workspaceId: current.workspaceId, sessionId: current.sessionId, ready: current.ready,
          prefs: { ...current.prefs }, appliedRequestId: current.appliedRequestId }
      },
      apply(request) {
        if (!request || !authorized(request) || typeof request.id !== 'string' || !request.id || request.id.length > 100) return 'rejected'
        if (!latest.current.ready) return 'pending'
        const key = `${request.workspaceId}:${request.sessionId}:${request.id}`
        if (seen.current.has(key)) return 'accepted'
        const prefs = validateNativeComposerPrefs(request.prefs)
        if (!prefs) return 'rejected'
        seen.current.add(key)
        if (seen.current.size > 100) seen.current.delete(seen.current.values().next().value!)
        latest.current.applyPrefs(prefs)
        setAppliedRequestId(request.id)
        return 'accepted'
      },
    }
    window.LearnGraphComposer = contract
    return () => { if (window.LearnGraphComposer === contract) delete window.LearnGraphComposer }
  }, [token])
}
