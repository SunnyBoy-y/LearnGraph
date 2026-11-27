import { useSyncExternalStore } from 'react'

export type NetworkTier = 'good' | 'weak' | 'offline'

export interface NetworkQualitySnapshot {
  tier: NetworkTier
  online: boolean
  ewmaRttMs: number | null
  consecutiveFailures: number
  effectiveType: string | null
  saveData: boolean
  reason: 'offline' | 'save_data' | 'slow_connection' | 'high_latency' | 'network_errors' | null
}

interface NavigatorConnectionLike {
  effectiveType?: string
  rtt?: number
  downlink?: number
  saveData?: boolean
  addEventListener?: (type: string, listener: () => void) => void
  removeEventListener?: (type: string, listener: () => void) => void
}

function getNavigatorConnection(): NavigatorConnectionLike | null {
  if (typeof navigator === 'undefined') return null
  const nav = navigator as Navigator & {
    connection?: NavigatorConnectionLike
    mozConnection?: NavigatorConnectionLike
    webkitConnection?: NavigatorConnectionLike
  }
  return nav.connection ?? nav.mozConnection ?? nav.webkitConnection ?? null
}

function isBrowserOnline(): boolean {
  if (typeof navigator === 'undefined') return true
  return navigator.onLine !== false
}

const EWMA_ALPHA = 0.3
const WEAK_RTT_ENTER_MS = 1_500
const WEAK_RTT_EXIT_MS = 900
const WEAK_FAILURE_THRESHOLD = 2
const SLOW_EFFECTIVE_TYPES = new Set(['slow-2g', '2g', '3g'])

class NetworkQualityStore {
  private listeners = new Set<() => void>()
  private initialized = false
  private ewmaRttMs: number | null = null
  private consecutiveFailures = 0
  private latchedWeakByRtt = false
  private snapshot: NetworkQualitySnapshot

  constructor() {
    this.snapshot = this.computeSnapshot()
  }

  private ensureListeners(): void {
    if (this.initialized || typeof window === 'undefined') return
    this.initialized = true
    const onOnline = () => {
      this.consecutiveFailures = 0
      this.recompute()
    }
    const onOffline = () => {
      this.recompute()
    }
    const onConnectionChange = () => {
      this.recompute()
    }
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    const conn = getNavigatorConnection()
    conn?.addEventListener?.('change', onConnectionChange)
  }

  private computeSnapshot(): NetworkQualitySnapshot {
    const online = isBrowserOnline()
    const conn = getNavigatorConnection()
    const effectiveType = typeof conn?.effectiveType === 'string' ? conn.effectiveType : null
    const saveData = conn?.saveData === true
    const reportedRtt = typeof conn?.rtt === 'number' && Number.isFinite(conn.rtt) && conn.rtt > 0 ? conn.rtt : null

    if (!online) {
      return {
        tier: 'offline',
        online: false,
        ewmaRttMs: this.ewmaRttMs,
        consecutiveFailures: this.consecutiveFailures,
        effectiveType,
        saveData,
        reason: 'offline',
      }
    }

    if (saveData) {
      return {
        tier: 'weak',
        online: true,
        ewmaRttMs: this.ewmaRttMs,
        consecutiveFailures: this.consecutiveFailures,
        effectiveType,
        saveData: true,
        reason: 'save_data',
      }
    }

    if (
      (effectiveType && SLOW_EFFECTIVE_TYPES.has(effectiveType)) ||
      (reportedRtt !== null && reportedRtt >= 800)
    ) {
      return {
        tier: 'weak',
        online: true,
        ewmaRttMs: this.ewmaRttMs,
        consecutiveFailures: this.consecutiveFailures,
        effectiveType,
        saveData,
        reason: 'slow_connection',
      }
    }

    if (this.consecutiveFailures >= WEAK_FAILURE_THRESHOLD) {
      return {
        tier: 'weak',
        online: true,
        ewmaRttMs: this.ewmaRttMs,
        consecutiveFailures: this.consecutiveFailures,
        effectiveType,
        saveData,
        reason: 'network_errors',
      }
    }

    if (this.ewmaRttMs !== null) {
      if (this.ewmaRttMs >= WEAK_RTT_ENTER_MS) {
        this.latchedWeakByRtt = true
      } else if (this.ewmaRttMs <= WEAK_RTT_EXIT_MS) {
        this.latchedWeakByRtt = false
      }
    } else {
      this.latchedWeakByRtt = false
    }

    if (this.latchedWeakByRtt) {
      return {
        tier: 'weak',
        online: true,
        ewmaRttMs: this.ewmaRttMs,
        consecutiveFailures: this.consecutiveFailures,
        effectiveType,
        saveData,
        reason: 'high_latency',
      }
    }

    return {
      tier: 'good',
      online: true,
      ewmaRttMs: this.ewmaRttMs,
      consecutiveFailures: this.consecutiveFailures,
      effectiveType,
      saveData,
      reason: null,
    }
  }

  private recompute(): void {
    const next = this.computeSnapshot()
    const prev = this.snapshot
    const changed =
      prev.tier !== next.tier ||
      prev.online !== next.online ||
      prev.reason !== next.reason ||
      prev.saveData !== next.saveData ||
      prev.effectiveType !== next.effectiveType ||
      prev.consecutiveFailures !== next.consecutiveFailures
    this.snapshot = next
    if (changed) {
      this.listeners.forEach((listener) => listener())
    }
  }

  getSnapshot = (): NetworkQualitySnapshot => {
    this.ensureListeners()
    const online = isBrowserOnline()
    if (online !== this.snapshot.online) {
      this.snapshot = this.computeSnapshot()
    }
    return this.snapshot
  }

  subscribe = (listener: () => void): (() => void) => {
    this.ensureListeners()
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  recordSample(sample: {
    durationMs?: number
    ok: boolean
    networkError?: boolean
    timedOut?: boolean
  }): void {
    this.ensureListeners()
    if (sample.ok) {
      this.consecutiveFailures = 0
      if (typeof sample.durationMs === 'number' && Number.isFinite(sample.durationMs) && sample.durationMs >= 0) {
        const clamped = Math.min(sample.durationMs, 30_000)
        this.ewmaRttMs =
          this.ewmaRttMs === null
            ? clamped
            : Math.round(EWMA_ALPHA * clamped + (1 - EWMA_ALPHA) * this.ewmaRttMs)
      }
    } else if (sample.networkError || sample.timedOut) {
      this.consecutiveFailures += 1
      if (sample.timedOut) {
        const penalty = Math.max(sample.durationMs ?? WEAK_RTT_ENTER_MS * 2, WEAK_RTT_ENTER_MS * 2)
        this.ewmaRttMs =
          this.ewmaRttMs === null
            ? penalty
            : Math.round(EWMA_ALPHA * penalty + (1 - EWMA_ALPHA) * this.ewmaRttMs)
      }
    }
    this.recompute()
  }

  reset(): void {
    this.ewmaRttMs = null
    this.consecutiveFailures = 0
    this.latchedWeakByRtt = false
    this.snapshot = this.computeSnapshot()
    this.listeners.forEach((listener) => listener())
  }
}

export const networkQualityStore = new NetworkQualityStore()

const SERVER_SNAPSHOT: NetworkQualitySnapshot = {
  tier: 'good',
  online: true,
  ewmaRttMs: null,
  consecutiveFailures: 0,
  effectiveType: null,
  saveData: false,
  reason: null,
}

export function useNetworkQuality(): NetworkQualitySnapshot {
  return useSyncExternalStore(
    networkQualityStore.subscribe,
    networkQualityStore.getSnapshot,
    () => SERVER_SNAPSHOT,
  )
}

export function adaptiveTimeoutMs(baseMs: number, tier?: NetworkTier): number {
  if (baseMs <= 0) return 0
  const effectiveTier = tier ?? networkQualityStore.getSnapshot().tier
  return effectiveTier === 'good' ? baseMs : Math.round(baseMs * 1.8)
}

export function adaptivePollInterval(
  baseMs: number | false,
  options: {
    weakMultiplier?: number
    pauseWhenOffline?: boolean
    pauseWhenHidden?: boolean
  } = {},
): number | false {
  if (baseMs === false || baseMs <= 0) return false
  const { weakMultiplier = 3, pauseWhenOffline = true, pauseWhenHidden = true } = options
  if (
    pauseWhenHidden &&
    typeof document !== 'undefined' &&
    document.visibilityState === 'hidden'
  ) {
    return false
  }
  const { tier } = networkQualityStore.getSnapshot()
  if (pauseWhenOffline && tier === 'offline') return false
  if (tier === 'weak') return Math.round(baseMs * weakMultiplier)
  return baseMs
}

/**
 * Equal-jitter exponential backoff: spreads concurrent reconnects while keeping
 * a non-zero lower bound so retries never spin tightly.
 */
export function computeJitteredBackoffMs(
  baseMs: number,
  attemptIndex: number,
  maxMs = 30_000,
  random: () => number = Math.random,
): number {
  const cap = Math.min(maxMs, baseMs * 2 ** Math.max(0, attemptIndex))
  const half = cap / 2
  return Math.round(half + random() * half)
}

export function adaptiveUploadChunkSize(tier?: NetworkTier): number {
  const effectiveTier = tier ?? networkQualityStore.getSnapshot().tier
  return effectiveTier === 'good' ? 2 * 1024 * 1024 : 512 * 1024
}

export function adaptiveChunkUploadThreshold(tier?: NetworkTier): number {
  const effectiveTier = tier ?? networkQualityStore.getSnapshot().tier
  return effectiveTier === 'good' ? 4 * 1024 * 1024 : 1024 * 1024
}
