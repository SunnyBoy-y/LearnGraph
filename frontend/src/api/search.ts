import type { SearchRequest, SearchResponse } from '@/types/research'

import { apiClient } from './client'

export function searchWeb(payload: SearchRequest): Promise<SearchResponse> {
  return apiClient.post<SearchResponse, SearchRequest>('/search', payload)
}

