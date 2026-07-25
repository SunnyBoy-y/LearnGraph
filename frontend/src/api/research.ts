import type { ResearchEvent, ResearchJob, ResearchPlan, ResearchRequest } from '@/types/research'

import { apiClient } from './client'

export function listResearchJobs(): Promise<ResearchJob[]> {
  return apiClient.get<ResearchJob[]>('/research')
}

export function createResearchJob(payload: ResearchRequest): Promise<ResearchJob> {
  return apiClient.post<ResearchJob, ResearchRequest>('/research', payload)
}
export const planResearch = (payload: ResearchRequest) => apiClient.post<ResearchPlan, ResearchRequest>('/research/plan', payload)
export const listResearchEvents = (id: string) => apiClient.get<ResearchEvent[]>(`/research/${encodeURIComponent(id)}/events`)
export const approveResearch = (id: string) => apiClient.post<ResearchJob, { approved: boolean }>(`/research/${encodeURIComponent(id)}/approve`, { approved: true })
export const cancelResearch = (id: string) => apiClient.post<ResearchJob>(`/research/${encodeURIComponent(id)}/cancel`)
