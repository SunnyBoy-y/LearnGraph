import { useSyncExternalStore } from 'react'
import type { QueryClient } from '@tanstack/react-query'

import { apiClient, ApiError } from '@/api/client'
import { authStore } from '@/api/auth-store'
import { toast } from '@/lib/notifications'
import { computeJitteredBackoffMs, networkQualityStore } from '@/lib/network-quality'
import { createUuid } from '@/lib/uuid'

export type OutboxMethod = 'POST' | 'PATCH' | 'PUT' | 'DELETE'
export type OutboxStatus = 'pending' | 'syncing' | 'failed'

export interface OutboxEntry {
  id: string
  workspaceId: string
  label: string
  method: OutboxMethod
  path: string
  body?: unknown
  idempotencyKey: string
  createdAt: number
  attempts: number
  status: OutboxStatus
  lastError: string | null
  invalidateKeys?: ReadonlyArray<readonly unknown[]>
}

export interface OutboxSnapshot {
  entries: OutboxEntry[]
  pendingCount: number
  failedCount: number
  syncing: boolean
}

const STORAGE_PREFIX = 'learngraph:offline-outbox:v1:'
const MAX_ENTRIES = 64
const MAX_AUTO_ATTEMPTS = 5

export function isOfflineOrNetworkError(error: unknown): boolean {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return true
  if (error instanceof ApiError) {
    return (
      error.status === 0 ||
      error.code === 'network_error' ||
      error.code === 'request_timeout' ||
      error.status === 502 ||
      error.status === 503 ||
      error.status === 504
    )
  }
  return error instanceof TypeError
}

function getStorageKey(workspaceId: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(workspaceId || 'default')}`
}

function activeWorkspaceId(): string {
  return authStore.getWorkspaceId() ?? 'default'
}

class OfflineOutboxStore {
  private listeners = new Set<() => void>()
  private queryClient: QueryClient | null = null
  private initialized = false
  private syncing = false
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private cachedWorkspaceId: string | null = null
  private entries: OutboxEntry[] = []
  private snapshot: OutboxSnapshot = {
    entries: [],
    pendingCount: 0,
    failedCount: 0,
    syncing: false,
  }

  private ensureInitialized(): void {
    if (this.initialized) return
    this.initialized = true
    this.loadForWorkspace(activeWorkspaceId())
    if (typeof window !== 'undefined') {
      window.addEventListener('online', () => {
        void this.flush()
      })
    }
    networkQualityStore.subscribe(() => {
      const { online } = networkQualityStore.getSnapshot()
      if (online && this.hasPending()) {
        void this.flush()
      }
    })
  }

  private loadForWorkspace(workspaceId: string): void {
    if (this.cachedWorkspaceId === workspaceId) return
    this.cachedWorkspaceId = workspaceId
    this.entries = []
    try {
      if (typeof window !== 'undefined') {
        const raw = window.localStorage.getItem(getStorageKey(workspaceId))
        if (raw) {
          const parsed: unknown = JSON.parse(raw)
          if (Array.isArray(parsed)) {
            this.entries = parsed
              .filter(
                (item): item is OutboxEntry =>
                  typeof item === 'object' &&
                  item !== null &&
                  typeof (item as OutboxEntry).id === 'string' &&
                  typeof (item as OutboxEntry).path === 'string' &&
                  typeof (item as OutboxEntry).method === 'string',
              )
              .map((item) => ({
                ...item,
                status: item.status === 'syncing' ? 'pending' : item.status,
              }))
          }
        }
      }
    } catch {
      this.entries = []
    }
    this.rebuildSnapshot()
  }

  private persist(): void {
    const workspaceId = this.cachedWorkspaceId ?? activeWorkspaceId()
    try {
      if (typeof window !== 'undefined') {
        const key = getStorageKey(workspaceId)
        if (this.entries.length === 0) {
          window.localStorage.removeItem(key)
        } else {
          window.localStorage.setItem(key, JSON.stringify(this.entries))
        }
      }
    } catch {
      // Storage quota/private-mode errors never break live memory queue.
    }
  }

  private rebuildSnapshot(): void {
    const pendingCount = this.entries.filter(
      (entry) => entry.status === 'pending' || entry.status === 'syncing',
    ).length
    const failedCount = this.entries.filter((entry) => entry.status === 'failed').length
    this.snapshot = {
      entries: [...this.entries],
      pendingCount,
      failedCount,
      syncing: this.syncing,
    }
  }

  private emit(): void {
    this.persist()
    this.rebuildSnapshot()
    this.listeners.forEach((listener) => listener())
  }

  private hasPending(): boolean {
    return this.entries.some((entry) => entry.status === 'pending')
  }

  bindQueryClient(client: QueryClient): void {
    this.queryClient = client
    this.ensureInitialized()
  }

  getSnapshot = (): OutboxSnapshot => {
    this.ensureInitialized()
    const currentWorkspace = activeWorkspaceId()
    if (this.cachedWorkspaceId !== currentWorkspace) {
      this.loadForWorkspace(currentWorkspace)
    }
    return this.snapshot
  }

  subscribe = (listener: () => void): (() => void) => {
    this.ensureInitialized()
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  enqueue(input: {
    label: string
    method: OutboxMethod
    path: string
    body?: unknown
    idempotencyKey?: string
    workspaceId?: string
    invalidateKeys?: ReadonlyArray<readonly unknown[]>
  }): OutboxEntry {
    this.ensureInitialized()
    const workspaceId = input.workspaceId ?? activeWorkspaceId()
    this.loadForWorkspace(workspaceId)

    const entry: OutboxEntry = {
      id: `outbox-${createUuid()}`,
      workspaceId,
      label: input.label,
      method: input.method,
      path: input.path,
      body: input.body,
      idempotencyKey: input.idempotencyKey ?? `outbox-${createUuid()}`,
      createdAt: Date.now(),
      attempts: 0,
      status: 'pending',
      lastError: null,
      invalidateKeys: input.invalidateKeys,
    }
    this.entries = [...this.entries.slice(-(MAX_ENTRIES - 1)), entry]
    this.emit()
    return entry
  }

  retryAll(): void {
    this.ensureInitialized()
    this.entries = this.entries.map((entry) =>
      entry.status === 'failed'
        ? { ...entry, status: 'pending', attempts: 0, lastError: null }
        : entry,
    )
    this.emit()
    void this.flush()
  }

  clearFailed(): void {
    this.ensureInitialized()
    this.entries = this.entries.filter((entry) => entry.status !== 'failed')
    this.emit()
  }

  clearAll(): void {
    this.ensureInitialized()
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    this.entries = []
    this.syncing = false
    this.emit()
  }

  private async dispatchEntry(entry: OutboxEntry): Promise<void> {
    const options = {
      headers: {
        'Idempotency-Key': entry.idempotencyKey,
        ...(entry.workspaceId && entry.workspaceId !== 'default'
          ? { 'X-Workspace-ID': entry.workspaceId }
          : {}),
      },
      noRetry: true,
    }
    switch (entry.method) {
      case 'POST':
        await apiClient.post(entry.path, entry.body, options)
        return
      case 'PATCH':
        await apiClient.patch(entry.path, entry.body, options)
        return
      case 'PUT':
        await apiClient.put(entry.path, entry.body, options)
        return
      case 'DELETE':
        await apiClient.delete(entry.path, options)
        return
    }
  }

  async flush(): Promise<{ succeeded: number; failed: number; remaining: number }> {
    this.ensureInitialized()
    if (this.syncing) {
      return { succeeded: 0, failed: 0, remaining: this.snapshot.pendingCount }
    }
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      return { succeeded: 0, failed: 0, remaining: this.snapshot.pendingCount }
    }
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }

    this.loadForWorkspace(activeWorkspaceId())
    if (!this.hasPending()) {
      return { succeeded: 0, failed: 0, remaining: 0 }
    }

    this.syncing = true
    this.emit()

    let succeeded = 0
    let failed = 0
    const invalidateBatch: Array<readonly unknown[]> = []

    try {
      while (true) {
        if (typeof navigator !== 'undefined' && navigator.onLine === false) break
        const nextIndex = this.entries.findIndex((entry) => entry.status === 'pending')
        if (nextIndex === -1) break

        const current = this.entries[nextIndex]!
        this.entries[nextIndex] = { ...current, status: 'syncing' }
        this.emit()

        try {
          await this.dispatchEntry(current)
          this.entries = this.entries.filter((entry) => entry.id !== current.id)
          succeeded += 1
          if (current.invalidateKeys?.length) {
            invalidateBatch.push(...current.invalidateKeys)
          }
          this.emit()
        } catch (error) {
          const message = error instanceof Error ? error.message : '同步失败'
          const nextAttempts = current.attempts + 1
          const transient = isOfflineOrNetworkError(error)
          if (transient && nextAttempts < MAX_AUTO_ATTEMPTS) {
            this.entries = this.entries.map((entry) =>
              entry.id === current.id
                ? { ...entry, status: 'pending', attempts: nextAttempts, lastError: message }
                : entry,
            )
            this.emit()
            const delay = computeJitteredBackoffMs(1_500, nextAttempts - 1, 20_000)
            this.retryTimer = setTimeout(() => {
              this.retryTimer = null
              void this.flush()
            }, delay)
            break
          }
          this.entries = this.entries.map((entry) =>
            entry.id === current.id
              ? { ...entry, status: 'failed', attempts: nextAttempts, lastError: message }
              : entry,
          )
          failed += 1
          this.emit()
        }
      }
    } finally {
      this.syncing = false
      this.emit()
    }

    if (succeeded > 0 && this.queryClient) {
      for (const queryKey of invalidateBatch) {
        void this.queryClient.invalidateQueries({ queryKey })
      }
      toast.success(`已自动同步 ${succeeded} 项离线保存的更改`)
    }
    if (failed > 0) {
      toast.error(`有 ${failed} 项离线更改同步失败，请在顶部状态条重试`)
    }

    return { succeeded, failed, remaining: this.snapshot.pendingCount }
  }
}

export const offlineOutbox = new OfflineOutboxStore()

const SERVER_OUTBOX_SNAPSHOT: OutboxSnapshot = {
  entries: [],
  pendingCount: 0,
  failedCount: 0,
  syncing: false,
}

export function useOfflineOutbox(): OutboxSnapshot {
  return useSyncExternalStore(
    offlineOutbox.subscribe,
    offlineOutbox.getSnapshot,
    () => SERVER_OUTBOX_SNAPSHOT,
  )
}
