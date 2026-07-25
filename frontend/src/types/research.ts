import type { IsoDateTime, UnknownRecord } from './common'

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

export interface ResearchRequest {
  question: string
  budget_cny?: number
  source_scope?: string[]
  allowed_domains?: string[]
  approved?: boolean
}

export interface ResearchJob {
  id: string
  workspace_id: string
  question: string
  status: string
  provider_id: string
  budget_cny: number
  estimated_cost_cny: number
  actual_cost_cny: number
  approval_status: string
  error_message: string | null
  evidence_pack: UnknownRecord
  created_at: IsoDateTime
}

export interface ResearchPlan { provider_id: string; provider_capabilities: UnknownRecord; question: string; budget_cny: number; estimated_cost_cny: number; requires_approval: boolean }
export interface ResearchEvent { id: string; research_job_id: string; sequence: number; event_type: string; payload: UnknownRecord; created_at: IsoDateTime }
