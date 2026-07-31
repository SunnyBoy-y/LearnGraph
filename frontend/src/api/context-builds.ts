import type { ContextBuildRequest, ContextBuildView } from '@/types/context-builds'

import { apiClient } from './client'

export function buildMemoryContext(payload: ContextBuildRequest): Promise<ContextBuildView> {
  return apiClient.post<ContextBuildView, ContextBuildRequest>('/memory/context/build', payload)
}

export function searchMemoryContext(payload: ContextBuildRequest): Promise<Record<string, unknown>> {
  return apiClient.post<Record<string, unknown>, ContextBuildRequest>('/memory/search', payload)
}
