import { apiClient } from './client'
import { createUuid } from '@/lib/uuid'

export interface UpdateJob {
  id: string
  target_version: string
  previous_version?: string
  kind: 'update' | 'rollback'
  status: string
  message: string
  created_at: string
  error?: string
}

export interface UpdateStatus {
  enabled: boolean
  current: { version: string; image?: string; compatibility?: string | null }
  repository?: string
  latest?: { version: string; notes: string; url: string; published_at: string }
  has_update?: boolean
  auto_install?: boolean
  checked_at?: string | null
  check_error?: string | null
  deployment_error?: string | null
  message?: string
  jobs?: UpdateJob[]
}

export const getUpdateStatus = () => apiClient.get<UpdateStatus>('/updates/status')
export const getAppVersion = () => apiClient.get<{ version: string; runtime_mode?: 'docker' | 'source' }>('/version', { auth: false, workspace: false })
export const checkUpdates = () => apiClient.post<UpdateStatus>('/updates/check', {})
export const setUpdatePolicy = (auto_install: boolean) => apiClient.post('/updates/policy', { auto_install })
export const startUpdate = (target: { version: string } | { rollback_id: string }) =>
  apiClient.post<UpdateJob>('/updates/jobs', { ...target, request_id: createUuid() })

export const isUpdateRunning = (job: UpdateJob) =>
  !['succeeded', 'failed', 'rolled_back', 'rollback_failed'].includes(job.status)
