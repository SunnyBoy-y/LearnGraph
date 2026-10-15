import { describe, expect, it } from 'vitest'

import type { MemoryPolicy } from '@/types/memory'

import { workspaceLearningEnabled, workspaceRecallEnabled } from './memory-policy-state'

function policy(overrides: Partial<MemoryPolicy> = {}): MemoryPolicy {
  return {
    workspace_id: 'workspace-1',
    workspace_enabled: true,
    workspace_recall_enabled: true,
    workspace_learning_enabled: true,
    session_id: null,
    session_enabled: null,
    session_recall_enabled: null,
    session_learning_enabled: null,
    effective_enabled: false,
    effective_recall_enabled: false,
    effective_learning_enabled: false,
    ...overrides,
  } as MemoryPolicy
}

describe('workspace memory policy state', () => {
  it('does not treat missing session context as workspace memory paused', () => {
    const value = policy()

    expect(workspaceRecallEnabled(value)).toBe(true)
    expect(workspaceLearningEnabled(value)).toBe(true)
  })

  it('honors the workspace master switch', () => {
    const value = policy({ workspace_enabled: false })

    expect(workspaceRecallEnabled(value)).toBe(false)
    expect(workspaceLearningEnabled(value)).toBe(false)
  })

  it('honors independent workspace recall and learning switches', () => {
    expect(workspaceRecallEnabled(policy({ workspace_recall_enabled: false }))).toBe(false)
    expect(workspaceLearningEnabled(policy({ workspace_learning_enabled: false }))).toBe(false)
  })

  it('returns null while policy data is unavailable', () => {
    expect(workspaceRecallEnabled()).toBeNull()
    expect(workspaceLearningEnabled()).toBeNull()
  })
})
