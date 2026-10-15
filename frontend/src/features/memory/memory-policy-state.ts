import type { MemoryPolicy } from '@/types/memory'

export function workspaceRecallEnabled(policy?: MemoryPolicy): boolean | null {
  if (!policy) return null
  return policy.workspace_enabled && (policy.workspace_recall_enabled ?? true)
}

export function workspaceLearningEnabled(policy?: MemoryPolicy): boolean | null {
  if (!policy) return null
  return policy.workspace_enabled && (policy.workspace_learning_enabled ?? true)
}
