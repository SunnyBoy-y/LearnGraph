const CACHE_SCRIPT = '/learngraph-sw.js'
const CACHE_BUSTER = '2026-10-22'

let registered = false

function isSecureShellContext() {
  if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return false
  return window.isSecureContext || ['localhost', '127.0.0.1'].includes(window.location.hostname)
}

/** The shell cache is optional; storage/network restrictions must never break the app. */
export async function registerWebShellCache(): Promise<ServiceWorkerRegistration | null> {
  if (registered || !isSecureShellContext()) return null
  registered = true
  try {
    return await navigator.serviceWorker.register(`${CACHE_SCRIPT}?v=${CACHE_BUSTER}`, {
      scope: '/',
      updateViaCache: 'none',
    })
  } catch {
    registered = false
    return null
  }
}

export function updateWebShellCache(): Promise<ServiceWorkerRegistration | null> {
  return registerWebShellCache().then(async (registration) => {
    if (!registration) return null
    await registration.update().catch(() => undefined)
    return registration
  })
}
