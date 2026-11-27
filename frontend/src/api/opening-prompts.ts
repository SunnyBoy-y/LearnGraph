import type { SuggestedPrompt } from '@/types/sessions'
import { apiClient, type ApiRequestOptions } from './client'

export interface OpeningPromptsRequest {
  provider_id?: string
  model_id?: string
  project_id?: string | null
  count?: number
  exhausted_pool_id?: string
  mode?: 'goal'
}

export interface OpeningPromptsResponse {
  prompts: SuggestedPrompt[]
  pool?: SuggestedPrompt[]
  metadata?: { cached?: boolean; request_id?: string; refresh_after_seconds?: number }
}

export function generateOpeningPrompts(
  payload: OpeningPromptsRequest,
  options: ApiRequestOptions = {},
): Promise<OpeningPromptsResponse> {
  return apiClient.post<OpeningPromptsResponse, OpeningPromptsRequest>(
    '/sessions/opening-prompts',
    payload,
    options,
  )
}
