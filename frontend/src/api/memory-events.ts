import type {
  MemoryArchitectureStatus,
  MemoryFeedbackRequest,
  MemoryForgetRequest,
} from '@/types/memory-events'

import { apiClient } from './client'

export function recordMemoryFeedback(memoryId: string, payload: MemoryFeedbackRequest) {
  return apiClient.post<{ feedback_id: string; applied_event_id: string }, MemoryFeedbackRequest>(
    `/memory/${encodeURIComponent(memoryId)}/feedback`,
    payload,
  )
}

export function retractMemory(memoryId: string, reason: string) {
  return apiClient.post<{ memory_id: string; lifecycle_status: string }, MemoryFeedbackRequest>(
    `/memory/${encodeURIComponent(memoryId)}/retract`,
    { feedback_type: 'stale', payload: { reason } },
  )
}

export function forgetMemory(memoryId: string, payload: MemoryForgetRequest) {
  return apiClient.post<Record<string, unknown>, MemoryForgetRequest>(
    `/memory/${encodeURIComponent(memoryId)}/forget`,
    payload,
  )
}

export function getMemoryArchitectureStatus() {
  return apiClient.get<MemoryArchitectureStatus>('/memory/architecture/status')
}

export function replayValidateMemory() {
  return apiClient.post<Record<string, unknown>>('/memory/maintenance/replay-validate')
}
