import type { ActionResponse } from '@/types/common'
import type { AuditEvent, AuditQuery } from '@/types/audit'

import { apiClient } from './client'

export function listAuditEvents(query: AuditQuery = {}): Promise<AuditEvent[]> {
  return apiClient.get<AuditEvent[]>('/audit', { query: { action: query.action } })
}

export function deleteAuditEvent(eventId: string): Promise<ActionResponse> {
  return apiClient.delete<ActionResponse>(`/audit/${encodeURIComponent(eventId)}`)
}

export function deleteAuditEvents(ids: string[]): Promise<ActionResponse> {
  return apiClient.post<ActionResponse, { ids: string[] }>('/audit/delete', { ids })
}
