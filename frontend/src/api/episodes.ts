import type { EpisodeGenerateRequest, MemoryEpisode } from '@/types/episodes'

import { apiClient } from './client'

export function generateMemoryEpisode(payload: EpisodeGenerateRequest): Promise<MemoryEpisode> {
  return apiClient.post<MemoryEpisode, EpisodeGenerateRequest>('/episodes/generate', payload)
}

export function searchMemoryEpisodes(payload: {
  conversation_id?: string
  task_id?: string
  query?: string
  limit?: number
}): Promise<MemoryEpisode[]> {
  return apiClient.post<MemoryEpisode[]>('/episodes/search', payload)
}
