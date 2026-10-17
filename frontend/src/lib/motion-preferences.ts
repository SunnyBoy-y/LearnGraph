import { useSyncExternalStore } from 'react'

const query = '(prefers-reduced-motion: reduce)'

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(query).matches
    : false
}

function subscribe(onChange: () => void) {
  const media = window.matchMedia?.(query)
  media?.addEventListener('change', onChange)
  return () => media?.removeEventListener('change', onChange)
}

/** Synchronous first paint, with live updates when the OS preference changes. */
export function useReducedMotionPreference() {
  return useSyncExternalStore(subscribe, prefersReducedMotion, () => false)
}

/** Read at invocation time so memoized camera/scroll callbacks cannot go stale. */
export function motionDuration(milliseconds: number) {
  return prefersReducedMotion() ? 0 : milliseconds
}
