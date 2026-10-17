import type { IsoDateTime } from './common'

export interface SearchRequest {
  query: string
  max_results?: number
}

export interface SearchResult {
  title: string
  url: string
  snippet: string
  source_type: string
  fetched_at: IsoDateTime
}

export interface SearchResponse {
  provider_id: string
  remote_capability: boolean
  query: string
  results: SearchResult[]
  notice: string
}
