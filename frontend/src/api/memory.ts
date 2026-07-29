import type {
  ContextSummarizationRunResult,
  EffectiveMemoryPackage,
  MemoryCreateRequest,
  MemoryBinding,
  MemoryEmbeddingReindexResult,
  MemoryEnhancement,
  MemoryEnhancementUpdateRequest,
  MemoryEntry,
  MemoryExtractionRunResult,
  MemoryNamespace,
  MemoryPolicy,
  MemoryPolicyUpdateRequest,
  MemoryProfile,
  MemoryProfileIntentRequest,
  MemoryProfileIntentResult,
  MemoryProviderMigrationResult,
  MemoryProviderStatus,
  MemoryPurgeResult,
  MemoryRevision,
  MemoryState,
  MemoryTypeDefinition,
  MemoryUpdateRequest,
  MemoryZone,
} from '@/types/memory'

import { apiClient } from './client'

export function listMemories(params: {
  zone?: MemoryZone
  state?: MemoryState
  namespace?: MemoryNamespace
  session_id?: string
  include_content?: boolean
} = {}): Promise<MemoryEntry[]> {
  return apiClient.get<MemoryEntry[]>('/memory', { query: params })
}

export function getMemory(memoryId: string): Promise<MemoryEntry> {
  return apiClient.get<MemoryEntry>(`/memory/${encodeURIComponent(memoryId)}`)
}

export function createMemory(payload: MemoryCreateRequest): Promise<MemoryEntry> {
  return apiClient.post<MemoryEntry, MemoryCreateRequest>('/memory', payload)
}

export function updateMemory(memoryId: string, payload: MemoryUpdateRequest): Promise<MemoryEntry> {
  return apiClient.patch<MemoryEntry, MemoryUpdateRequest>(
    `/memory/${encodeURIComponent(memoryId)}`,
    payload,
  )
}

export function deleteMemory(memoryId: string): Promise<MemoryEntry> {
  return apiClient.delete<MemoryEntry>(`/memory/${encodeURIComponent(memoryId)}`)
}

export function restoreDeletedMemory(memoryId: string): Promise<MemoryEntry> {
  return apiClient.post<MemoryEntry>(`/memory/${encodeURIComponent(memoryId)}/restore`)
}

export function listMemoryRevisions(memoryId: string): Promise<MemoryRevision[]> {
  return apiClient.get<MemoryRevision[]>(`/memory/${encodeURIComponent(memoryId)}/revisions`)
}

export function restoreMemoryRevision(
  memoryId: string,
  revision: number,
  expectedRevision: number,
): Promise<MemoryEntry> {
  return apiClient.post<MemoryEntry, { expected_revision: number; reason: string }>(
    `/memory/${encodeURIComponent(memoryId)}/revisions/${revision}/restore`,
    { expected_revision: expectedRevision, reason: 'user_revision_restore' },
  )
}

export function listMemoryBindings(memoryId: string): Promise<MemoryBinding[]> {
  return apiClient.get<MemoryBinding[]>(`/memory/${encodeURIComponent(memoryId)}/bindings`)
}

export function getMemoryPolicy(sessionId?: string): Promise<MemoryPolicy> {
  return apiClient.get<MemoryPolicy>('/memory/policy', {
    query: sessionId ? { session_id: sessionId } : {},
  })
}

export function updateMemoryPolicy(payload: MemoryPolicyUpdateRequest): Promise<MemoryPolicy> {
  return apiClient.put<MemoryPolicy, MemoryPolicyUpdateRequest>('/memory/policy', payload)
}

export function getMemoryProviderStatus(): Promise<MemoryProviderStatus> {
  return apiClient.get<MemoryProviderStatus>('/memory/provider')
}

export function probeMemoryProvider(): Promise<MemoryProviderStatus> {
  return apiClient.post<MemoryProviderStatus>('/memory/provider/probe')
}

export function purgeExpiredMemoryContent(): Promise<MemoryPurgeResult> {
  return apiClient.post<MemoryPurgeResult>('/memory/maintenance/purge-expired')
}

export function migrateMemoryProvider(): Promise<MemoryProviderMigrationResult> {
  return apiClient.post<MemoryProviderMigrationResult>('/memory/maintenance/migrate-provider')
}

export function exportMemoryMarkdown(): Promise<Blob> {
  return apiClient.getBlob('/memory/export')
}

export function listMemoryTypes(): Promise<MemoryTypeDefinition[]> {
  return apiClient.get<MemoryTypeDefinition[]>('/memory/types')
}

export function getEffectiveMemoryPackage(params: {
  session_id?: string
  goal_id?: string
} = {}): Promise<EffectiveMemoryPackage> {
  return apiClient.get<EffectiveMemoryPackage>('/memory/package', { query: params })
}

export function getMemoryProfile(): Promise<MemoryProfile> {
  return apiClient.get<MemoryProfile>('/memory/profile')
}

export function refreshMemoryProfile(): Promise<MemoryProfile> {
  return apiClient.post<MemoryProfile>('/memory/profile/refresh')
}

export function applyMemoryProfileIntent(
  payload: MemoryProfileIntentRequest,
): Promise<MemoryProfileIntentResult> {
  return apiClient.post<MemoryProfileIntentResult, MemoryProfileIntentRequest>(
    '/memory/profile/intents',
    payload,
  )
}

export function reconcileMemoryTime(): Promise<{ reviewed: number; lapsed: number }> {
  return apiClient.post<{ reviewed: number; lapsed: number }>(
    '/memory/maintenance/reconcile-time',
  )
}

export function migrateLegacyMemoryAtoms(
  limit = 20,
): Promise<{ reviewed: number; migrated: number; created: number; deferred: number }> {
  return apiClient.post<{
    reviewed: number
    migrated: number
    created: number
    deferred: number
  }>('/memory/maintenance/migrate-atoms', undefined, { query: { limit } })
}

export function getMemoryEnhancement(): Promise<MemoryEnhancement> {
  return apiClient.get<MemoryEnhancement>('/memory/enhancement')
}

export function updateMemoryEnhancement(
  payload: MemoryEnhancementUpdateRequest,
): Promise<MemoryEnhancement> {
  return apiClient.put<MemoryEnhancement, MemoryEnhancementUpdateRequest>(
    '/memory/enhancement',
    payload,
  )
}

export function reindexMemoryEmbeddings(): Promise<MemoryEmbeddingReindexResult> {
  return apiClient.post<MemoryEmbeddingReindexResult>('/memory/enhancement/reindex')
}

export function extractSessionMemories(sessionId: string): Promise<MemoryExtractionRunResult> {
  return apiClient.post<MemoryExtractionRunResult>(
    `/memory/enhancement/extract/${encodeURIComponent(sessionId)}`,
  )
}

export function summarizeSessionContext(
  sessionId: string,
): Promise<ContextSummarizationRunResult> {
  return apiClient.post<ContextSummarizationRunResult>(
    `/memory/enhancement/summarize/${encodeURIComponent(sessionId)}`,
  )
}
