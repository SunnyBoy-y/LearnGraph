import type {
  MemoryTaskCreateRequest,
  MemoryTaskState,
  MemoryTaskUpdateRequest,
} from '@/types/tasks'

import { apiClient } from './client'

export function createMemoryTask(payload: MemoryTaskCreateRequest): Promise<MemoryTaskState> {
  return apiClient.post<MemoryTaskState, MemoryTaskCreateRequest>('/tasks', payload)
}

export function getMemoryTask(taskId: string): Promise<MemoryTaskState> {
  return apiClient.get<MemoryTaskState>(`/tasks/${encodeURIComponent(taskId)}/state`)
}

export function updateMemoryTask(
  taskId: string,
  payload: MemoryTaskUpdateRequest,
): Promise<MemoryTaskState> {
  return apiClient.put<MemoryTaskState, MemoryTaskUpdateRequest>(
    `/tasks/${encodeURIComponent(taskId)}/state`,
    payload,
  )
}
