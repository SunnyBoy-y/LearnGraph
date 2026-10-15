import type { BillingCurrency } from "./usage"
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
  currency?: BillingCurrency
  budget_amount?: string
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
  currency: BillingCurrency | null
  budget_amount: string | null
  estimated_cost_amount: string | null
  actual_cost_amount: string | null
  approval_status: string
  billing_snapshot: UnknownRecord
  error_message: string | null
  evidence_pack: UnknownRecord
  created_at: IsoDateTime
}

export interface ResearchPlan { provider_id: string; provider_capabilities: UnknownRecord; question: string; currency: BillingCurrency; budget_amount: string; estimated_cost_amount: string | null; requires_approval: boolean }
export interface ResearchEvent { id: string; research_job_id: string; sequence: number; event_type: string; payload: UnknownRecord; created_at: IsoDateTime }
