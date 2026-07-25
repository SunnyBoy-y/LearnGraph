import type {
  CandidateGraphRequest,
  Goal,
  GoalClarifyRequest,
  GoalClarifyResponse,
  GoalConfirmRequest,
  GoalPlanningUpdate,
  PublishGoalRequest,
  PublishGoalResponse,
} from '@/types/goals'
import type { GraphSummary } from '@/types/graphs'
import type { DeleteImpact } from '@/types/workflow'

import { apiClient } from './client'

export function listGoals(): Promise<Goal[]> {
  return apiClient.get<Goal[]>('/goals')
}

export function clarifyGoal(payload: GoalClarifyRequest): Promise<GoalClarifyResponse> {
  return apiClient.post<GoalClarifyResponse, GoalClarifyRequest>('/goals/clarify', payload)
}

export function confirmGoal(goalId: string, payload: GoalConfirmRequest): Promise<Goal> {
  return apiClient.put<Goal, GoalConfirmRequest>(`/goals/${encodeURIComponent(goalId)}/confirm`, payload)
}

export function generateCandidateGraph(
  goalId: string,
  payload: CandidateGraphRequest = {},
): Promise<GraphSummary> {
  return apiClient.post<GraphSummary, CandidateGraphRequest>(
    `/goals/${encodeURIComponent(goalId)}/candidate-graph`,
    payload,
  )
}

export function publishGoal(
  goalId: string,
  payload: PublishGoalRequest,
): Promise<PublishGoalResponse> {
  return apiClient.post<PublishGoalResponse, PublishGoalRequest>(
    `/goals/${encodeURIComponent(goalId)}/publish`,
    payload,
  )
}

export function updateGoalPlanning(
  goalId: string,
  payload: GoalPlanningUpdate,
): Promise<Goal> {
  return apiClient.patch<Goal, GoalPlanningUpdate>(
    `/goals/${encodeURIComponent(goalId)}/planning`,
    payload,
  )
}

export function getGoalDeleteImpact(goalId: string): Promise<DeleteImpact> {
  return apiClient.get<DeleteImpact>(`/goals/${encodeURIComponent(goalId)}/delete-impact`)
}

export function deleteGoal(goalId: string, confirmationText: string): Promise<void> {
  return apiClient.post<void, { confirmation_text: string }>(
    `/goals/${encodeURIComponent(goalId)}/delete`,
    { confirmation_text: confirmationText },
  )
}
