import type { MessageCreateRequest } from '@/types/sessions'
import { apiClient } from './client'

export type ChatGoalStatus = 'queued' | 'running' | 'pausing' | 'clearing' | 'paused'
  | 'completed' | 'blocked' | 'failed' | 'budget_exhausted'
export type ChatGoalAction = 'pause' | 'resume' | 'clear'
export type ChatGoal = {
  id: string
  objective: string
  status: ChatGoalStatus
  rounds_used: number
  max_rounds: number
  summary: string
  last_message_id: string | null
  updated_at: string
}
export type ChatGoalStart = Pick<MessageCreateRequest, 'model_id' | 'provider_id' | 'thinking_mode' | 'search_route'> & {
  objective: string
  max_rounds?: number
}

const path = (sessionId: string) => `/sessions/${encodeURIComponent(sessionId)}/goal`

export async function readChatGoal(sessionId: string, signal?: AbortSignal): Promise<ChatGoal | null> {
  const goal = await apiClient.get<ChatGoal | null>(path(sessionId), { signal })
  return goal && typeof goal.id === 'string' ? goal : null
}

export function startChatGoal(sessionId: string, payload: ChatGoalStart) {
  return apiClient.post<ChatGoal, ChatGoalStart>(path(sessionId), payload)
}

export function controlChatGoal(sessionId: string, goalId: string, action: ChatGoalAction) {
  return apiClient.post<ChatGoal | null, { goal_id: string; action: ChatGoalAction }>(
    `${path(sessionId)}/control`, { goal_id: goalId, action },
  )
}
