// 证据 API（旧版只读/审核通道）
// -----------------------------------------------------------
// 证据创建已废弃（决策 D-20260808-01）：统一使用事件溯源版
// POST /api/v1/learning/evidence。本文件只保留旧版证据的只读列表
// 与审核决策，前端不得再新增 createEvidence 调用。
// -----------------------------------------------------------

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
