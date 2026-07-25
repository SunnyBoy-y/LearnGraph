import type {
  Evidence,
  EvidenceDecisionRequest,
} from '@/types/learning'

import { apiClient } from './client'

export function listEvidence(): Promise<Evidence[]> {
  return apiClient.get<Evidence[]>('/evidence')
}

export function decideEvidence(evidenceId: string, payload: EvidenceDecisionRequest): Promise<Evidence> {
  return apiClient.post<Evidence, EvidenceDecisionRequest>(
    `/evidence/${encodeURIComponent(evidenceId)}/decision`,
    payload,
  )
}
