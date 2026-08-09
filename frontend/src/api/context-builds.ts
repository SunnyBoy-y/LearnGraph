import type {
  ContextBuildRequest,
  ContextBuildView,
  ContextManifestReceipt,
} from '@/types/context-builds'

import { apiClient } from './client'

export function buildMemoryContext(payload: ContextBuildRequest): Promise<ContextBuildView> {
  return apiClient.post<ContextBuildView, ContextBuildRequest>('/memory/context/build', payload)
}

export function listContextManifests(params: {
  session_id?: string
  message_id?: string
  context_build_id?: string
}): Promise<ContextManifestReceipt[]> {
  return apiClient.get<ContextManifestReceipt[]>('/memory/context/manifests', {
    query: params,
  })
}

export function searchMemoryContext(payload: ContextBuildRequest): Promise<Record<string, unknown>> {
  return apiClient.post<Record<string, unknown>, ContextBuildRequest>('/memory/search', payload)
}
