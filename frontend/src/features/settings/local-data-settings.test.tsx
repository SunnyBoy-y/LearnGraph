import { beforeEach, describe, expect, it, vi } from 'vitest'
import { findByRole, fireEvent } from '@testing-library/react'

import { listSettings, updateSetting } from '@/api'
import { authStore } from '@/api/auth-store'
import {
  listSelectionExplanations,
  upsertSelectionExplanation,
  type SelectionExplanationRecord,
} from '@/features/chat/selection-explanation'
import { renderWithProviders } from '@/test/render'
import type { WorkspaceSetting } from '@/types/settings'

// The settings page only uses these two functions from the @/api barrel. A
// synchronous factory guarantees the page module sees the mock once imported;
// implementations are (re)applied in beforeEach because the suite's
// `mockReset: true` wipes mock implementations between tests.
vi.mock('@/api', () => ({
  listSettings: vi.fn(),
  updateSetting: vi.fn(),
}))

vi.mock('@/features/auth/auth-context-value', () => ({
  useAuth: () => ({
    authenticated: true,
    username: 'user-a',
    workspaceId: 'workspace-a',
    workspaceName: 'A',
    setWorkspaceId: async () => undefined,
    login: async () => ({ workspaceId: 'workspace-a', mustChangePassword: false }),
    register: async () => ({ workspaceId: 'workspace-a', mustChangePassword: false }),
    changePassword: async () => undefined,
    deleteAccount: async () => undefined,
    logout: async () => undefined,
  }),
}))

const record: SelectionExplanationRecord = {
  id: 'selection-1',
  parentSessionId: 'parent-1',
  sourceMessageId: 'message-1',
  selectedText: 'React Query',
  prefix: 'Learn ',
  suffix: ' cache isolation',
  contentMatched: true,
  action: 'define',
  createdAt: '2026-08-03T00:00:00.000Z',
}

describe('LocalDataSettings R-017 clear-traces button', () => {
  beforeEach(() => {
    authStore.setSession({
      accessToken: 'token-a',
      userId: 'user-a',
      workspaceId: 'workspace-a',
    })
    vi.mocked(listSettings).mockResolvedValue([])
    const savedSetting: WorkspaceSetting = {
      key: 'response_style',
      value: { base_style: 'default' },
      updated_at: '2026-08-03T00:00:00.000Z',
    }
    vi.mocked(updateSetting).mockResolvedValue(savedSetting)
  })

  it('clears the current (user, workspace) selection namespace when clicked', async () => {
    // Load the page AFTER the barrel mock is registered so the page's own
    // static import cannot capture the real module first.
    const { LocalDataSettings } = await import('./local-data-settings')

    // Seed the current partition with a private excerpt.
    upsertSelectionExplanation(record)
    expect(listSelectionExplanations(record.parentSessionId)).toEqual([record])

    const { container } = renderWithProviders(<LocalDataSettings />)
    const clearButton = await findByRole(container, 'button', { name: '清除记录' })
    fireEvent.click(clearButton)
    // Local data cleanup now asks for an explicit second confirmation.
    fireEvent.click(await findByRole(container, 'button', { name: '确认清除记录' }))

    // R-017: the clear action removes the current partition (and the legacy
    // key), so no excerpt survives in the active user+workspace namespace.
    expect(listSelectionExplanations(record.parentSessionId)).toEqual([])
  })

  it('does not clear another workspace partition', async () => {
    const { LocalDataSettings } = await import('./local-data-settings')

    upsertSelectionExplanation(record)
    // A second workspace partition holds its own record.
    authStore.setSession({
      accessToken: 'token-b',
      userId: 'user-b',
      workspaceId: 'workspace-b',
    })
    const otherRecord = { ...record, id: 'selection-b', selectedText: 'Other' }
    upsertSelectionExplanation(otherRecord)
    // Restore the active partition for the page render.
    authStore.setSession({
      accessToken: 'token-a',
      userId: 'user-a',
      workspaceId: 'workspace-a',
    })

    const { container } = renderWithProviders(<LocalDataSettings />)
    const clearButton = await findByRole(container, 'button', { name: '清除记录' })
    fireEvent.click(clearButton)
    // Local data cleanup now asks for an explicit second confirmation.
    fireEvent.click(await findByRole(container, 'button', { name: '确认清除记录' }))

    expect(listSelectionExplanations(record.parentSessionId)).toEqual([])
    authStore.setSession({
      accessToken: 'token-b',
      userId: 'user-b',
      workspaceId: 'workspace-b',
    })
    expect(listSelectionExplanations(record.parentSessionId)).toEqual([otherRecord])
  })
})
