import { lazy, type ComponentType, type LazyExoticComponent } from 'react'

import { computeJitteredBackoffMs } from './network-quality'

const RELOAD_GUARD_PREFIX = 'lg:chunk-reload:'
const RELOAD_GUARD_WINDOW_MS = 30_000

const CHUNK_ERROR_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /loading chunk [\w-]+ failed/i,
  /loading css chunk [\w-]+ failed/i,
  /chunkloaderror/i,
  /unable to preload css/i,
]

export function isChunkLoadError(error: unknown): boolean {
  if (!error) return false
  const name = error instanceof Error ? error.name : ''
  const message = error instanceof Error ? error.message : String(error)
  if (name === 'ChunkLoadError') return true
  return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(message))
}

function waitForRetryDelay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window !== 'undefined' && navigator.onLine === false) {
      let settled = false
      const done = () => {
        if (settled) return
        settled = true
        window.removeEventListener('online', done)
        clearTimeout(timer)
        resolve()
      }
      window.addEventListener('online', done, { once: true })
      const timer = setTimeout(done, Math.max(ms, 8_000))
      return
    }
    setTimeout(resolve, ms)
  })
}

function tryOneShotReload(): boolean {
  if (typeof window === 'undefined') return false
  const key = `${RELOAD_GUARD_PREFIX}${window.location.pathname}`
  try {
    const previous = Number(window.sessionStorage.getItem(key) || '0')
    const now = Date.now()
    if (Number.isFinite(previous) && now - previous < RELOAD_GUARD_WINDOW_MS) {
      return false
    }
    window.sessionStorage.setItem(key, String(now))
  } catch {
    return false
  }
  window.location.reload()
  return true
}

export async function importWithRetry<TModule>(
  factory: () => Promise<TModule>,
  options: { retries?: number; baseDelayMs?: number; allowReload?: boolean } = {},
): Promise<TModule> {
  const { retries = 3, baseDelayMs = 400, allowReload = true } = options
  let lastError: unknown
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await factory()
    } catch (error) {
      lastError = error
      if (!isChunkLoadError(error)) throw error
      if (attempt < retries) {
        const delay = computeJitteredBackoffMs(baseDelayMs, attempt, 5_000)
        await waitForRetryDelay(delay)
        continue
      }
      if (allowReload && tryOneShotReload()) {
        // Keep the lazy boundary suspended while the browser reloads.
        return await new Promise<TModule>(() => undefined)
      }
    }
  }
  throw lastError
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyWithRetry<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
  options?: { retries?: number; baseDelayMs?: number; allowReload?: boolean },
): LazyExoticComponent<T> {
  return lazy(() => importWithRetry(factory, options))
}
