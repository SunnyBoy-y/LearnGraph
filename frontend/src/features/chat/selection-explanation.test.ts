import { describe, expect, it } from 'vitest'

import { authStore } from '@/api/auth-store'

import {
  bindExplanationSession,
  buildSelectionExplainPrompt,
  consumePendingSelectionExplanation,
  getSelectionExplanation,
  inferSelectionAction,
  listSelectionExplanations,
  openSelectionExplanation,
  splitTextWithSelectionMarks,
  upsertSelectionExplanation,
  type SelectionExplanationRecord,
} from './selection-explanation'

const record: SelectionExplanationRecord = {
  id: 'selection-1',
  parentSessionId: 'parent-1',
  sourceMessageId: 'message-1',
  selectedText: 'React Query',
  prefix: 'Learn ',
  suffix: ' cache isolation',
  contentMatched: true,
  action: 'define',
  createdAt: '2026-08-02T00:00:00.000Z',
}

describe('selection explanations', () => {
  it('partitions persisted records by authenticated user and workspace', () => {
    authStore.setSession({
      accessToken: 'token-a',
      userId: 'user-a',
      workspaceId: 'workspace-a',
    })
    upsertSelectionExplanation(record)

    expect(listSelectionExplanations(record.parentSessionId)).toEqual([record])

    authStore.setSession({
      accessToken: 'token-b',
      userId: 'user-b',
      workspaceId: 'workspace-b',
    })
    expect(listSelectionExplanations(record.parentSessionId)).toEqual([])
  })

  it('updates a record when its isolated explanation session is created', () => {
    upsertSelectionExplanation(record)

    expect(
      bindExplanationSession(record.parentSessionId, record.id, 'side-session-1'),
    ).toMatchObject({
      ...record,
      explanationSessionId: 'side-session-1',
    })
    expect(getSelectionExplanation(record.parentSessionId, record.id)).toMatchObject({
      explanationSessionId: 'side-session-1',
    })
  })

  it('only lets the matching parent session consume an open request', () => {
    openSelectionExplanation({
      parentSessionId: record.parentSessionId,
      sourceMessageId: record.sourceMessageId,
      selectedText: record.selectedText,
    })

    expect(consumePendingSelectionExplanation('another-parent')).toBeNull()
    expect(consumePendingSelectionExplanation(record.parentSessionId)).toMatchObject({
      parentSessionId: record.parentSessionId,
      selectedText: record.selectedText,
    })
    expect(consumePendingSelectionExplanation(record.parentSessionId)).toBeNull()
  })

  it('anchors duplicate marks using the saved prefix and suffix context', () => {
    const content = 'First React Query cache. Later React Query cache.'

    expect(
      splitTextWithSelectionMarks(content, [
        {
          id: record.id,
          selectedText: 'React Query',
          prefix: 'First React Query cache. Later ',
          suffix: ' cache.',
        },
      ]),
    ).toEqual([
      { type: 'text', value: 'First React Query cache. Later ' },
      { type: 'mark', id: record.id, value: 'React Query' },
      { type: 'text', value: ' cache.' },
    ])
  })

  it('selects definition or explanation prompts based on user-visible text', () => {
    expect(inferSelectionAction('缓存')).toBe('define')
    expect(inferSelectionAction('这是一个完整句子。')).toBe('explain')
    expect(buildSelectionExplainPrompt('define', '缓存')).toContain('「缓存」')
  })
})
