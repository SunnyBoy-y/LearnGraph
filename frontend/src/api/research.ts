import type { ResearchEvent, ResearchJob, ResearchPlan, ResearchRequest } from '@/types/research'

import { apiClient } from './client'
import { authStore } from './auth-store'

function scoped(workspaceId = authStore.getWorkspaceId() ?? '', signal?: AbortSignal) {
  return { workspace: false, headers: { 'X-Workspace-ID': workspaceId }, signal }
}

export function listResearchJobs(workspaceId?: string, signal?: AbortSignal): Promise<ResearchJob[]> {
  return apiClient.get<ResearchJob[]>('/research', scoped(workspaceId, signal))
}

export function createResearchJob(payload: ResearchRequest, workspaceId?: string): Promise<ResearchJob> {
  return apiClient.post<ResearchJob, ResearchRequest>('/research', payload, scoped(workspaceId))
}
export const planResearch = (payload: ResearchRequest, workspaceId?: string) => apiClient.post<ResearchPlan, ResearchRequest>('/research/plan', payload, scoped(workspaceId))
export const getResearchJob = (id: string, workspaceId?: string, signal?: AbortSignal) => apiClient.get<ResearchJob>(`/research/${encodeURIComponent(id)}`, scoped(workspaceId, signal))
export const listResearchEvents = (id: string, workspaceId?: string, signal?: AbortSignal) => apiClient.get<ResearchEvent[]>(`/research/${encodeURIComponent(id)}/events`, scoped(workspaceId, signal))
export const approveResearch = (id: string, quoteId?: string, workspaceId?: string) => apiClient.post<ResearchJob, { approved: boolean; quote_id?: string }>(`/research/${encodeURIComponent(id)}/approve`, { approved: true, quote_id: quoteId }, scoped(workspaceId))
export const cancelResearch = (id: string, workspaceId?: string) => apiClient.post<ResearchJob>(`/research/${encodeURIComponent(id)}/cancel`, undefined, scoped(workspaceId))
