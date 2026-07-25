import type { DashboardResponse } from '@/types/dashboard'

import { apiClient } from './client'

export function getDashboard(): Promise<DashboardResponse> {
  return apiClient.get<DashboardResponse>('/dashboard')
}

