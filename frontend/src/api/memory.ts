import type {
  MemoryCreateRequest,
  MemoryBinding,
  MemoryDraft,
  MemoryDraftCreateRequest,
  MemoryDraftDecisionRequest,
  MemoryDraftStatus,
  MemoryEntry,
  MemoryNamespace,
  MemoryPolicy,
  MemoryPolicyUpdateRequest,
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

export function exportMemoryMarkdown(): Promise<Blob> {
  return apiClient.getBlob('/memory/export')
}

export function listMemoryTypes(): Promise<MemoryTypeDefinition[]> {
  return apiClient.get<MemoryTypeDefinition[]>('/memory/types')
}

export function listMemoryDrafts(params: {
  status?: MemoryDraftStatus | null
  session_id?: string
  goal_id?: string
} = {}): Promise<MemoryDraft[]> {
  return apiClient.get<MemoryDraft[]>('/memory/drafts', {
    query: {
      status: params.status === null ? undefined : (params.status ?? 'PENDING'),
      session_id: params.session_id,
      goal_id: params.goal_id,
    },
  })
}

export function createMemoryDraft(payload: MemoryDraftCreateRequest): Promise<MemoryDraft> {
  return apiClient.post<MemoryDraft, MemoryDraftCreateRequest>('/memory/drafts', payload)
}

export function decideMemoryDraft(
  draftId: string,
  payload: MemoryDraftDecisionRequest,
): Promise<MemoryDraft> {
  return apiClient.post<MemoryDraft, MemoryDraftDecisionRequest>(
    `/memory/drafts/${encodeURIComponent(draftId)}/decision`,
    payload,
  )
}
